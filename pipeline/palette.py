"""Deterministic color palette for game-asset parts.

Named keys are what skills and the model refer to ("bark", "iron", ...).
``auto_color`` hashes the object name to a hue on the golden-ratio
sequence: the same part keeps the same color across undo/redo and
reloads, and neighbouring parts get well-separated hues.
"""

from __future__ import annotations

import colorsys
import hashlib
from typing import Final

RGBA = tuple[float, float, float, float]

__all__ = ("RGBA", "auto_color", "named", "resolve_color")


def _srgb_to_linear(c: float) -> float:
    # Blender works in linear space; palette keys are authored in sRGB hex.
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _hex(hexstr: str) -> RGBA:
    h = hexstr.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    return (_srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b), 1.0)


NAMED: Final[dict[str, str]] = {
    "bark": "6b4a2f",
    "wood_light": "b08d5e",
    "wood_dark": "5c3d22",
    "leaf": "4f8f3a",
    "leaf_dark": "2f6b28",
    "iron": "4a4f55",
    "steel": "7a8087",
    "gold": "c9a227",
    "copper": "b0683f",
    "stone": "8d8d86",
    "stone_dark": "5f5f59",
    "concrete": "9a9a94",
    "brick": "9c5040",
    "sand": "d4b981",
    "skin": "d8a48a",
    "cloth_red": "a33327",
    "cloth_blue": "33547a",
    "cloth_green": "4a7a4f",
    "plastic_red": "c0392b",
    "plastic_white": "d9d9d4",
    "plastic_black": "1f1f22",
    "rubber": "2a2a2c",
    "glass": "b8d8e0",
    "dirt": "6e5a3e",
    "grass": "5d9440",
    "water": "4a7f9f",
    "roof": "7a3b2e",
}

GOLDEN_RATIO: Final = 0.618033988749895
_S: Final = 0.55
_V: Final = 0.85


def named() -> dict[str, str]:
    """The named palette (key -> sRGB hex). Skills refer to these keys."""
    return dict(NAMED)


def auto_color(name: str) -> RGBA:
    """Deterministic readable RGBA from the object name (linear space)."""
    digest = hashlib.blake2s(name.encode("utf-8"), digest_size=4).digest()
    seed = int.from_bytes(digest, "big") / 2**32
    hue = (seed * GOLDEN_RATIO) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, _S, _V)
    return (r, g, b, 1.0)


def resolve_color(value: object, fallback_name: str = "") -> RGBA:
    """Accept a palette key ("iron") or an RGBA list; else auto_color().

    Unknown keys fall through to auto_color so a forgotten/unknown
    color never blocks the pipeline.
    """
    if isinstance(value, str) and value.lower() in NAMED:
        return _hex(NAMED[value.lower()])
    if isinstance(value, (list, tuple)) and len(value) in (3, 4):
        rgbaf = [float(c) for c in value]
        if all(0.0 <= c <= 1.0 for c in rgbaf):
            if len(rgbaf) == 3:
                return (rgbaf[0], rgbaf[1], rgbaf[2], 1.0)
            return (rgbaf[0], rgbaf[1], rgbaf[2], rgbaf[3])
    return auto_color(fallback_name or (value if isinstance(value, str) else ""))
