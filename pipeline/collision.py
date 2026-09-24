"""Collision proxies.

Naming by engine (one match): Unreal UCX_/UBX_, Godot -colonly /
-convcolonly, Unity <Obj>_Collider. Display: WIRE + hide_render +
ai_role='collision' so validation and export report them but never
render them.
"""

from __future__ import annotations

import bmesh
import bpy
from mathutils import Vector

from .meta import ensure_meta
from .spec import AssetSpec, Engine

__all__ = ("make_collision",)


def _collision_name(engine: Engine, source: str, kind: str, index: int) -> str:
    match engine:
        case Engine.UNREAL:
            prefix = "UBX" if kind == "box" else "UCX"
            return f"{prefix}_{source}_{index:02d}"
        case Engine.GODOT:
            suffix = "-colonly" if kind == "mesh" else "-convcolonly"
            return f"{source}{suffix}"
        case _:
            return f"{source}_Collider"


def _bbox_corners(obj: bpy.types.Object) -> list[Vector]:
    return [obj.matrix_world @ Vector(c) for c in obj.bound_box]


def _box_mesh(name: str, corners: list[Vector]) -> bpy.types.Mesh:
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners),
                 min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners),
                 max(c.z for c in corners)))
    me = bpy.data.meshes.new(name)
    verts = [(lo.x, lo.y, lo.z), (hi.x, lo.y, lo.z), (hi.x, hi.y, lo.z), (lo.x, hi.y, lo.z),
             (lo.x, lo.y, hi.z), (hi.x, lo.y, hi.z), (hi.x, hi.y, hi.z), (lo.x, hi.y, hi.z)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 3, 7, 4)]
    me.from_pydata(verts, [], faces)
    me.validate()
    return me


def _convex_mesh(name: str, src: bpy.types.Object) -> bpy.types.Mesh:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = src.evaluated_get(depsgraph)
    src_mesh = eval_obj.to_mesh() if src.modifiers else src.data
    bm = bmesh.new()
    bm.from_mesh(src_mesh)
    if src.modifiers:
        eval_obj.to_mesh_clear()
    bmesh.ops.convex_hull(bm, input=bm.verts)  # keeps hull geom, deletes interior
    # remove any leftover verts/edges outside the hull faces
    for v in [v for v in bm.verts if not v.link_faces]:
        bm.verts.remove(v)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.validate()
    return me


def make_collision(asset_coll: bpy.types.Collection, spec: AssetSpec,
                   kind: str = "box") -> list[str]:
    engine = spec.engine
    created: list[str] = []
    from .meta import get_role
    roots = [o for o in asset_coll.objects
             if o.type == "MESH" and not o.hide_render
             and get_role(o) not in ("collision", "lod")
             and (o.parent is None or o.parent not in list(asset_coll.objects))]
    for index, root in enumerate(roots):
        name = _collision_name(engine, root.name, kind, index)
        old = bpy.data.objects.get(name)
        if old is not None:
            data = old.data
            bpy.data.objects.remove(old)
            if data and data.users == 0:
                bpy.data.meshes.remove(data)
        match kind:
            case "box":
                me = _box_mesh(name, _bbox_corners(root))
            case "convex":
                me = _convex_mesh(name, root)
            case "mesh":
                me = root.data.copy()
                me.name = name
            case _:
                raise ValueError("kind must be box|convex|mesh")
        col_obj = bpy.data.objects.new(name, me)
        col_obj.matrix_world = root.matrix_world.copy()
        asset_coll.objects.link(col_obj)
        col_obj.display_type = "WIRE"
        col_obj.hide_render = True
        ensure_meta(col_obj, spec.collection_name(), f"{kind} collision proxy for {root.name}", "collision")
        created.append(name)
    return created
