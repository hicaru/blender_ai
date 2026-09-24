"""Image attachments: load, downscale, stage, encode.

Flow: file/clipboard -> bpy.data.images.load (main thread) -> downscale
to <=1024 px long edge with Image.scale on a COPY -> save PNG to the
extension temp dir -> Attachment record. On send the worker base64-
encodes into an OpenAI image_url part. Only the LATEST user turn's
images expand to base64; older history entries keep placeholders.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = ("MAX_EDGE", "Attachment", "downscale_copy", "load_attachment")

MAX_EDGE: Final = 1024


@dataclass(slots=True, frozen=True)
class Attachment:
    path: Path      # downscaled PNG in temp dir
    sha256: str
    width: int
    height: int
    label: str      # original file name

    def data_url(self) -> str:
        return "data:image/png;base64," + base64.b64encode(
            self.path.read_bytes()).decode("ascii")

    def placeholder(self) -> str:
        return f"[image: {self.label}, {self.width}x{self.height}]"

    def to_ref(self) -> dict[str, object]:
        """Persisted history record; only fresh turns re-expand to base64."""
        return {"type": "image_ref", "sha256": self.sha256, "label": self.label,
                "w": self.width, "h": self.height, "path": str(self.path)}


def _temp_dir() -> Path:
    out = Path("/tmp") / "blender_ai_attachments"
    out.mkdir(parents=True, exist_ok=True)
    return out


def downscale_copy(img, max_edge: int = MAX_EDGE) -> Path:  # type: ignore[no-untyped-def] # noqa: ANN001 — bpy.types.Image, bpy import is main-thread only
    """Scale a COPY of the image datablock to <= max_edge; return its PNG path.

    Runs on the main thread (bpy data access); the user's image is untouched.
    """
    w, h = img.size
    if max(w, h) > max_edge:
        factor = max_edge / max(w, h)
        copy = img.copy()
        copy.name = img.name + "_downscaled"
        copy.scale(max(1, int(w * factor)), max(1, int(h * factor)))
        out = _temp_dir() / f"{hashlib.sha256(img.name.encode()).hexdigest()[:12]}.png"
        copy.filepath_raw = str(out)
        copy.file_format = "PNG"
        copy.save()
        if copy.users == 0:
            import bpy
            bpy.data.images.remove(copy)
    else:
        out = _temp_dir() / f"{hashlib.sha256(bytes(img.pixels[:100]) if False else img.name.encode()).hexdigest()[:12]}.png"
        img.filepath_raw = str(out)
        img.file_format = "PNG"
        img.save()
    return out


def load_attachment(file_path: str) -> Attachment:
    """Main thread: load an image file, downscale a copy, return the record."""
    import bpy
    src = Path(file_path)
    if not src.exists():
        raise FileNotFoundError(f"attachment not found: {src}")
    img = bpy.data.images.load(str(src), check_existing=True)
    try:
        png_path = downscale_copy(img)
        w, h = img.size[:]
        raw = png_path.read_bytes()
        return Attachment(
            path=png_path,
            sha256=hashlib.sha256(raw).hexdigest(),
            width=w, height=h,
            label=src.name,
        )
    finally:
        if img.users == 0:
            bpy.data.images.remove(img)


def image_part(att: Attachment) -> dict[str, object]:
    """OpenAI-compatible multimodal content part."""
    return {"type": "image_url", "image_url": {"url": att.data_url()}}
