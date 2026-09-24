"""capture_view: the visual self-check.

Workbench renders composed into ONE 2x2 contact sheet via
Image.pixels.foreach_set — one image token budget instead of four.
The image travels to the model as an image content part in the NEXT
USER message (OpenAI-compatible APIs reject images in role:tool), and
is shown in the panel so the user sees what the model saw.
"""

from __future__ import annotations

import contextlib
import tempfile
import time
from pathlib import Path
from typing import Final

import bpy
from mathutils import Vector

__all__ = ("capture_contact_sheet",)

_SHEET: Final = 1024  # 2x2 of 512

_DIRS: Final[dict[str, Vector]] = {
    "iso": Vector((1.0, -1.0, 0.8)),
    "front": Vector((0.0, -1.0, 0.0)),
    "side": Vector((1.0, 0.0, 0.0)),
    "top": Vector((0.0, 0.0, 1.0)),
}


def _render_view(view: str, center: Vector, radius: float,
                 size: int, color_type: str) -> bpy.types.Image:
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("AI_capture_cam")
    cam = bpy.data.objects.new("AI_capture_cam", cam_data)
    scene.collection.objects.link(cam)
    try:
        direction = _DIRS.get(view, _DIRS["iso"]).normalized()
        cam_data.type = "ORTHO" if view in ("front", "side", "top") else "PERSP"
        cam_data.lens = 35
        cam.location = center + direction * radius * 3
        cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        # exact framing over the asset bbox corners
        dg = bpy.context.evaluated_depsgraph_get()
        corners: list[float] = []
        for sx in (-1, 1):
            for sy in (-1, 1):
                for sz in (-1, 1):
                    p = center + Vector((sx * radius, sy * radius, sz * radius))
                    corners += [p.x, p.y, p.z]
        _fit_loc, fit_scale = cam.camera_fit_coords(dg, corners)
        if cam_data.type == "ORTHO":
            cam_data.ortho_scale = max(2.0, fit_scale)
        else:
            cam.location = _fit_loc
        scene.camera = cam
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.display.shading.color_type = color_type
        scene.display.shading.show_cavity = True
        scene.display.shading.show_object_outline = True
        scene.render.resolution_x = size
        scene.render.resolution_y = size
        scene.render.film_transparent = True
        scene.render.filepath = tempfile.mktemp(suffix=".png")
        bpy.ops.render.render(write_still=True)
        return bpy.data.images.load(scene.render.filepath, check_existing=False)
    finally:
        bpy.data.objects.remove(cam)
        if cam_data.users == 0:
            bpy.data.cameras.remove(cam_data)


def capture_contact_sheet(views: list[str] | None = None,
                          size: int = 512,
                          color_type: str = "OBJECT",
                          bbox_center: tuple[float, float, float] = (0.0, 0.0, 0.0),
                          bbox_radius: float = 1.0) -> str:
    """Render up to 4 views, compose a 2x2 sheet PNG, return its file path."""
    views = (views or ["iso", "front", "side", "top"])[:4]
    center = Vector(bbox_center)
    tiles = [_render_view(v, center, bbox_radius, size, color_type) for v in views]
    sheet = bpy.data.images.new("AI_capture_sheet", _SHEET, _SHEET, alpha=True)
    px = [0.0] * (_SHEET * _SHEET * 4)
    for i, tile in enumerate(tiles):
        tile.scale(size, size)
        tx = (i % 2) * size
        ty = (i // 2) * size
        src = list(tile.pixels[:])
        for y in range(size):
            src_y = size - 1 - y            # blender images are bottom-up
            dst_y = _SHEET - 1 - (ty + y)
            dst = (dst_y * _SHEET + tx) * 4
            src_at = src_y * size * 4
            px[dst:dst + size * 4] = src[src_at:src_at + size * 4]
    sheet.pixels.foreach_set(px)
    out_dir = Path(bpy.app.tempdir) / "blender_ai_captures"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"capture_{int(time.time() * 1000)}.png"
    sheet.filepath_raw = str(out_path)
    sheet.file_format = "PNG"
    sheet.save()
    for img in tiles:
        tile_path = img.filepath
        bpy.data.images.remove(img)
        with contextlib.suppress(OSError):  # best-effort temp cleanup
            Path(bpy.path.abspath(tile_path)).unlink(missing_ok=True)
    bpy.data.images.remove(sheet)
    return str(out_path)
