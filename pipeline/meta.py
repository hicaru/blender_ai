"""Object metadata: ai_description / ai_role / ai_asset custom properties.

Every object carries semantics so humans AND the model can tell what
``Cube.017`` is; the glTF exporter writes them as ``extras``.
"""

from __future__ import annotations

from typing import Final

import bpy

__all__ = (
    "KEY_ASSET",
    "KEY_DESCRIPTION",
    "KEY_ROLE",
    "ROLES",
    "ensure_meta",
    "get_asset",
    "get_description",
    "get_role",
    "merge_descriptions",
    "set_asset",
    "set_description",
    "set_role",
)

KEY_DESCRIPTION: Final = "ai_description"
KEY_ROLE: Final = "ai_role"
KEY_ASSET: Final = "ai_asset"

ROLES: Final[tuple[str, ...]] = (
    "body", "detail", "trim", "foliage", "trunk", "wheel", "handle",
    "collision", "lod", "socket", "pivot", "cutter",
)


def set_description(obj: bpy.types.Object, text: str) -> None:
    obj[KEY_DESCRIPTION] = str(text).strip()


def get_description(obj: bpy.types.Object) -> str:
    raw = obj.get(KEY_DESCRIPTION, "")
    return raw if isinstance(raw, str) else ""


def set_role(obj: bpy.types.Object, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    obj[KEY_ROLE] = role


def get_role(obj: bpy.types.Object) -> str:
    raw = obj.get(KEY_ROLE, "")
    return raw if isinstance(raw, str) else ""


def set_asset(obj: bpy.types.Object, asset: str) -> None:
    obj[KEY_ASSET] = asset


def get_asset(obj: bpy.types.Object) -> str:
    raw = obj.get(KEY_ASSET, "")
    return raw if isinstance(raw, str) else ""


def ensure_meta(obj: bpy.types.Object, asset: str, description: str, role: str) -> None:
    """Required-by-schema path: create tools call this on every new object."""
    set_asset(obj, asset)
    set_description(obj, description)
    set_role(obj, role)


def merge_descriptions(objects: list[bpy.types.Object]) -> str:
    """join_objects merges the parts' descriptions into one line."""
    parts = [get_description(o) for o in objects]
    return "; ".join(p for p in parts if p)[:500]
