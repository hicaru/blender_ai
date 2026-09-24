"""UV tools: smart / angle / cube unwrapping."""
# mypy: ignore-errors

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


def uv_unwrap(  # noqa: PLR0912
        object=None, objects=None, method="smart", angle_limit=None,
        island_margin=None, pack=False, margin=0.01):
    """Unwrap UVs of one mesh or a batch of meshes (all faces).

    method: smart (Smart UV Project), angle (angle-based), conformal,
    cube, cylinder or lightmap (grid pack for UV1 lightmaps). pack=true
    packs islands into 0..1 with the given margin afterwards.
    """
    names = list(objects) if objects else ([object] if object else [])
    if not names:
        raise ToolError("pass object (single) or objects (list of names)")
    methods = ("smart", "angle", "conformal", "cube", "cylinder", "lightmap")
    if method not in methods:
        raise ToolError("method must be one of %s" % ", ".join(methods))
    _ensure_object_mode()
    done = []
    for name in names:
        obj = _activate_mesh(name)
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
            elif method == "conformal":
                kwargs = {"method": "CONFORMAL"}
                if island_margin is not None:
                    kwargs["margin"] = float(island_margin)
                bpy.ops.uv.unwrap(**kwargs)
            elif method == "cube":
                bpy.ops.uv.cube_project(cube_size=4.0)
            elif method == "cylinder":
                bpy.ops.uv.cylinder_project(direction='ALIGN_TO_OBJECT')
            else:  # lightmap
                bpy.ops.uv.lightmap_pack(PREF_CONTEXT='ALL_FACES',
                                         PREF_PACK_IN_ONE=True,
                                         PREF_NEW_UVLAYER=False,
                                         PREF_APPLY_IMAGE=False,
                                         PREF_MARGIN_DIV=float(margin))
            if pack and method != "lightmap":
                bpy.ops.uv.select_all(action='SELECT')
                bpy.ops.uv.pack_islands(margin=float(margin))
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
        done.append(obj.name)
    return "unwrapped (%s, pack=%s): %s" % (method, pack, ", ".join(done))


def register_tools():
    register(
        "uv_unwrap",
        "Unwrap UVs of one mesh or a batch (objects=[...]). method: smart "
        "(default), angle, conformal, cube, cylinder, lightmap (grid UV1 "
        "for Unity/Unreal lightmaps). pack=true packs islands into 0..1 "
        "with margin (default 0.01). angle_limit (degrees, smart), "
        "island_margin (world units, smart/angle).\n"
        "Use when: validate_asset reports uv.missing, or the engine needs "
        "lightmaps. Returns the unwrapped object names.",
        {
            "type": "object",
            "properties": {
                "object": {"type": "string", "description": "Single object name"},
                "objects": {"type": "array", "items": {"type": "string"},
                            "description": "Batch of object names (preferred)"},
                "method": {"type": "string",
                           "enum": ["smart", "angle", "conformal", "cube", "cylinder", "lightmap"]},
                "angle_limit": {"type": "number",
                                "description": "Degrees (smart only)"},
                "island_margin": {"type": "number",
                                  "description": "World units (smart/angle)"},
                "pack": {"type": "boolean", "description": "Pack islands into 0..1 after unwrap"},
                "margin": {"type": "number", "description": "Pack margin (default 0.01)"},
            },
        },
        uv_unwrap,
    )


register_tools()
