"""Sculpt tools: enter sculpt mode, dyntopo, voxel remesh, multires."""
# mypy: ignore-errors

import bpy

from . import ToolError, register


def _activate_mesh(object):
    obj = bpy.data.objects.get(object)
    if obj is None:
        raise ToolError("no object named %r" % object)
    if obj.type != 'MESH':
        raise ToolError("%r is not a mesh" % object)
    bpy.context.view_layer.objects.active = obj
    return obj


def sculpt_setup(object, enable_dyntopo=None, detail_size=None,
                 voxel_remesh=None, voxel_size=None, multires_levels=None):
    """Prepare an object for sculpting.

    - enters Sculpt Mode on the object;
    - enable_dyntopo toggles dynamic topology on; detail_size sets its
      detail size (scene sculpt settings);
    - voxel_remesh re-meshes the mesh; voxel_size sets obj.remesh_voxel_size;
    - multires_levels adds a Multires modifier subdivided that many times.
    """
    notes = []
    obj = _activate_mesh(object)

    if voxel_remesh:
        if voxel_size is not None:
            obj.remesh_voxel_size = float(voxel_size)
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.voxel_remesh()
        notes.append("voxel remesh (size %s)" % obj.remesh_voxel_size)

    if multires_levels:
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        mod = obj.modifiers.get("Multires")
        if mod is None or mod.type != 'MULTIRES':
            mod = obj.modifiers.new(name="Multires", type='MULTIRES')
        levels = int(multires_levels)
        for _ in range(levels):
            bpy.ops.object.multires_subdivide(modifier=mod.name)
        notes.append("multires +%d levels (total %d)" % (levels, mod.levels))

    if bpy.context.mode != 'SCULPT':
        bpy.ops.object.mode_set(mode='SCULPT')

    if enable_dyntopo:
        if not obj.use_dynamic_topology_sculpting:
            bpy.ops.sculpt.dynamic_topology_toggle()
        if detail_size is not None:
            bpy.context.tool_settings.sculpt.detail_size = float(detail_size)
        notes.append("dyntopo on (detail %s)"
                     % bpy.context.tool_settings.sculpt.detail_size)

    return "sculpt setup on %r: %s" % (
        obj.name, "; ".join(notes) if notes else "entered sculpt mode")


def register_tools():
    register(
        "sculpt_setup",
        "Prepare a mesh object for sculpting: switches to Sculpt Mode and "
        "optionally enables dynamic topology (enable_dyntopo + detail_size), "
        "voxel-remeshes the mesh (voxel_remesh + voxel_size) or adds a "
        "Multires modifier (multires_levels).",
        {
            "type": "object",
            "properties": {
                "object": {"type": "string"},
                "enable_dyntopo": {"type": "boolean"},
                "detail_size": {"type": "number",
                                "description": "Dyntopo detail size"},
                "voxel_remesh": {"type": "boolean"},
                "voxel_size": {"type": "number"},
                "multires_levels": {"type": "integer", "minimum": 1,
                                    "maximum": 5},
            },
            "required": ["object"],
        },
        sculpt_setup,
    )


register_tools()
