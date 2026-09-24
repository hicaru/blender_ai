"""UV tools: smart / angle / cube unwrapping."""

import math

import bpy

from . import ToolError, register
from .scene import _ensure_object_mode


def _activate_mesh(object):
    obj = bpy.data.objects.get(object)
    if obj is None:
        raise ToolError("no object named %r" % object)
    if obj.type != 'MESH':
        raise ToolError("%r is not a mesh" % object)
    bpy.context.view_layer.objects.active = obj
    return obj


def uv_unwrap(object, method="smart", angle_limit=None, island_margin=None):
    """Unwrap UVs of a mesh object (all faces).

    method: smart (Smart UV Project), angle (angle-based unwrap) or cube.
    angle_limit is in degrees (smart only), island_margin in world units
    (smart/angle). Operator parameter names differ per method: uv.unwrap
    takes margin only, and uv.cube_project accepts no margin at all.
    """
    _ensure_object_mode()
    obj = _activate_mesh(object)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    try:
        if method == "smart":
            kwargs = {}
            if angle_limit is not None:
                kwargs["angle_limit"] = math.radians(float(angle_limit))
            if island_margin is not None:
                kwargs["island_margin"] = float(island_margin)
            bpy.ops.uv.smart_project(**kwargs)
        elif method == "angle":
            kwargs = {}
            if island_margin is not None:
                kwargs["margin"] = float(island_margin)
            bpy.ops.uv.unwrap(**kwargs)
        elif method == "cube":
            bpy.ops.uv.cube_project()
        else:
            raise ToolError("unknown method %r (smart, angle, cube)" % method)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    return "unwrapped %r with method %r" % (obj.name, method)


def register_tools():
    register(
        "uv_unwrap",
        "Unwrap UVs of a mesh object. method: smart (default, Smart UV "
        "Project), angle (seam/angle based unwrap) or cube (cube "
        "projection). angle_limit (degrees, smart only) tunes the seam "
        "angle threshold; island_margin (world units, smart/angle) adds "
        "padding between UV islands.",
        {
            "type": "object",
            "properties": {
                "object": {"type": "string"},
                "method": {"type": "string",
                           "enum": ["smart", "angle", "cube"]},
                "angle_limit": {"type": "number",
                                "description": "Degrees (smart only)"},
                "island_margin": {"type": "number",
                                  "description": "World units (smart/angle)"},
            },
            "required": ["object"],
        },
        uv_unwrap,
    )


register_tools()
