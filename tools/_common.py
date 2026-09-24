"""Shared helpers for tool implementations (main thread only).

One object resolver, one mode-switch context manager, one error style:
every message tells the model HOW TO RECOVER, which measurably improves
tool-call recall (error text is an instruction, not a dead end).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Final, Literal

import bpy

from . import ToolError

__all__ = (
    "Mode",
    "ToolError",
    "get_mesh_object",
    "get_object",
    "get_objects",
    "object_mode",
)

Mode = Literal["OBJECT", "EDIT", "SCULPT", "VERTEX_PAINT"]
_MODE_SET: Final[dict[str, str]] = {"EDIT_MESH": "EDIT"}


def get_object(name: str) -> bpy.types.Object:
    obj: bpy.types.Object | None = bpy.data.objects.get(name)
    if obj is None:
        raise ToolError(f"object '{name}' not found; call get_scene_state for exact names")
    return obj


def get_objects(names: Sequence[str]) -> tuple[bpy.types.Object, ...]:
    missing = [n for n in names if n not in bpy.data.objects]
    if missing:
        raise ToolError(f"objects not found: {', '.join(missing)}")
    return tuple(bpy.data.objects[n] for n in names)


def get_mesh_object(name: str) -> bpy.types.Object:
    obj = get_object(name)
    if obj.type != "MESH":
        raise ToolError(f"'{name}' is {obj.type}, expected MESH")
    return obj


@contextmanager
def object_mode(obj: bpy.types.Object, mode: Mode) -> Iterator[bpy.types.Object]:
    """Make obj the only selected+active object in `mode`; restore after."""
    ctx = bpy.context
    previous = _MODE_SET.get(ctx.mode, ctx.mode)
    if ctx.object is not None and previous != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for other in ctx.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    ctx.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode=mode)
    try:
        yield obj
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
