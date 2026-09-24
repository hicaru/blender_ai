"""Mesh operations on the bmesh level: mesh_op, boolean, set_origin, finalize.

bmesh instead of edit-mode operators: no context failures inside timers,
deterministic, and the object's mode never has to change.
"""

from __future__ import annotations

from typing import Any

import bmesh
import bpy

from . import register
from ._common import ToolError, get_mesh_object, object_mode

__all__ = ()


# --------------------------------------------------------------- selectors

def _select_faces(bm: bmesh.types.BMesh, selector: str,
                  params: dict[str, Any]) -> list[bmesh.types.BMFace]:
    if not bm.faces:
        raise ToolError("object has no faces; mesh_op works on faces")
    if selector == "all":
        return list(bm.faces)
    zs = [v.co.z for v in bm.verts]
    zmin, zmax = min(zs), max(zs)
    height = max(zmax - zmin, 1e-6)

    def zfrac(f: bmesh.types.BMFace) -> float:
        return float((f.calc_center_median().z - zmin) / height)

    if selector == "top":
        top = zmax - 0.3 * height
        return [f for f in bm.faces if f.calc_center_median().z >= top]
    if selector == "bottom":
        bot = zmin + 0.3 * height
        return [f for f in bm.faces if f.calc_center_median().z <= bot]
    if selector == "side":
        return [f for f in bm.faces if abs(f.normal.z) < 0.5]
    if selector == "band":
        lo, hi = params.get("band", [0.0, 1.0])[:2]
        return [f for f in bm.faces if lo <= zfrac(f) <= hi]
    if selector in ("+x", "-x", "+y", "-y"):
        sign = 1 if selector.startswith("+") else -1
        comp = {"x": 0, "y": 1}[selector[1]]
        vals = [v.co[comp] for v in bm.verts]
        vmin, vmax = min(vals), max(vals)
        span = max(vmax - vmin, 1e-6)
        picked = vmax if sign > 0 else vmin
        return [f for f in bm.faces
                if abs(f.calc_center_median()[comp] - picked) <= 0.35 * span]
    raise ToolError(
        f"unknown selector {selector!r}; use all|top|bottom|side|band|+x|-x|+y|-y "
        "(band uses params.band=[lo,hi] as Z fractions)")


# --------------------------------------------------------------- ops

def _op_extrude(bm: bmesh.types.BMesh, faces: list[bmesh.types.BMFace],
                params: dict[str, Any]) -> str:
    from mathutils import Vector
    if not faces:
        return "nothing to extrude"
    ret = bmesh.ops.extrude_face_region(bm, geom=faces)
    new_verts = [e for e in ret["geom"] if isinstance(e, bmesh.types.BMVert)]
    avg = Vector()
    for f in faces:
        avg += f.normal
    if avg.length:
        avg /= len(faces)
    dist = float(params.get("distance", 0.1))
    bmesh.ops.translate(bm, verts=new_verts, vec=avg * dist)
    return f"extruded {len(faces)} faces by {dist}"


def _op_inset(bm: bmesh.types.BMesh, faces: list[bmesh.types.BMFace],
              params: dict[str, Any]) -> str:
    bmesh.ops.inset_region(
        bm, faces=faces, thickness=float(params.get("thickness", 0.05)),
        depth=float(params.get("depth", 0.0)), use_even_offset=True)
    return f"inset {len(faces)} regions (thickness {params.get('thickness', 0.05)})"


def _op_bevel(bm: bmesh.types.BMesh, faces: list[bmesh.types.BMFace],
              params: dict[str, Any]) -> str:
    edges = list({e for f in faces for e in f.edges})
    bmesh.ops.bevel(
        bm, geom=edges, offset=float(params.get("width", 0.02)),
        segments=int(params.get("segments", 1)), affect="EDGES",
        clamp_overlap=True)
    return f"beveled {len(edges)} edges"


def _op_scale_faces(bm: bmesh.types.BMesh, faces: list[bmesh.types.BMFace],
                    params: dict[str, Any]) -> str:
    from mathutils import Vector
    if not faces:
        return "no faces selected"
    factor = float(params.get("factor", 0.8))
    axis = str(params.get("axis", "ALL")).upper()
    sel_center = Vector()
    for f in faces:
        sel_center += f.calc_center_median()
    sel_center /= len(faces)
    scaled: list[bmesh.types.BMVert] = []
    seen: set[int] = set()
    for f in faces:
        for v in f.verts:
            if id(v) in seen:
                continue
            seen.add(id(v))
            scaled.append(v)
    for v in scaled:
        delta = v.co - sel_center
        if axis in ("XY", "X"):
            delta.x *= factor
        if axis in ("XY", "Y"):
            delta.y *= factor
        if axis in ("Z",):
            delta.z *= factor
        if axis == "ALL":
            delta *= factor
        v.co = sel_center + delta
    return f"scaled {len(scaled)} verts of {len(faces)} faces by {factor} (axis {axis})"


def _op_subdivide(bm: bmesh.types.BMesh, faces: list[bmesh.types.BMFace],
                  params: dict[str, Any]) -> str:
    edges = list({e for f in faces for e in f.edges})
    res = bmesh.ops.subdivide_edges(
        bm, edges=edges, cuts=int(params.get("cuts", 1)), use_grid_fill=True)
    return f"subdivided (cuts={params.get('cuts', 1)}), {len(res['geom_split'])} new elements"


def _op_delete(bm: bmesh.types.BMesh, faces: list[bmesh.types.BMFace],
               params: dict[str, Any]) -> str:
    bmesh.ops.delete(bm, geom=list(faces), context="FACES")
    return f"deleted {len(faces)} faces"


def _op_recalc_normals(bm: bmesh.types.BMesh, faces: list[bmesh.types.BMFace],
                       params: dict[str, Any]) -> str:
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    return "recalculated all normals outward"


def _op_merge_by_distance(bm: bmesh.types.BMesh, faces: list[bmesh.types.BMFace],
                          params: dict[str, Any]) -> str:
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts),
                             dist=float(params.get("distance", 0.0001)))
    return f"merged {before - len(bm.verts)} verts"


def _op_delete_loose(bm: bmesh.types.BMesh, faces: list[bmesh.types.BMFace],
                     params: dict[str, Any]) -> str:
    loose_v = [v for v in bm.verts if not v.link_edges]
    if loose_v:
        bmesh.ops.delete(bm, geom=loose_v, context="VERTS")
    loose_e = [e for e in bm.edges if not e.link_faces]
    if loose_e:
        bmesh.ops.delete(bm, geom=loose_e, context="EDGES")
    return f"removed {len(loose_v)} loose verts, {len(loose_e)} loose edges"


def _op_dissolve_degenerate(bm: bmesh.types.BMesh, faces: list[bmesh.types.BMFace],
                            params: dict[str, Any]) -> str:
    before = len(bm.faces)
    tiny = [f for f in bm.faces if f.calc_area() < 1e-8]
    if tiny:
        bmesh.ops.delete(bm, geom=tiny, context="FACES")
    bmesh.ops.dissolve_degenerate(bm, dist=float(params.get("distance", 1e-5)), edges=list(bm.edges))
    return f"removed {before - len(bm.faces)} degenerate faces"


def _op_triangulate(bm: bmesh.types.BMesh, faces: list[bmesh.types.BMFace],
                    params: dict[str, Any]) -> str:
    bmesh.ops.triangulate(bm, faces=list(bm.faces), quad_method="BEAUTY")
    return "triangulated all faces"


_OPS = {
    "extrude": _op_extrude,
    "inset": _op_inset,
    "bevel": _op_bevel,
    "scale_faces": _op_scale_faces,
    "subdivide": _op_subdivide,
    "delete": _op_delete,
    "recalc_normals": _op_recalc_normals,
    "merge_by_distance": _op_merge_by_distance,
    "delete_loose": _op_delete_loose,
    "dissolve_degenerate": _op_dissolve_degenerate,
    "triangulate": _op_triangulate,
}


# --------------------------------------------------------------- tools

def mesh_op(object: str, op: str, selector: str = "all",
            params: dict[str, Any] | None = None) -> str:
    """Run one bmesh operation on the selected faces; write the mesh back."""
    obj = get_mesh_object(object)
    params = params or {}
    if op not in _OPS:
        raise ToolError(f"unknown op {op!r}; use one of: {', '.join(sorted(_OPS))}")
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        faces = _select_faces(bm, str(selector), params)
        summary = _OPS[op](bm, faces, params)
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
    bpy.context.view_layer.update()
    return f"{{\"ok\":true,\"op\":\"{op}\",\"object\":\"{obj.name}\",\"selector\":\"{selector}\",\"info\":\"{summary}\"}}"


def boolean(object: str, cutter: str, operation: str = "difference",
            apply: bool = False) -> str:
    """Boolean the cutter into object (modifier, non-destructive unless apply)."""
    obj = get_mesh_object(object)
    cut = get_mesh_object(cutter)
    if obj is cut:
        raise ToolError("cannot boolean an object with itself")
    ops = {"difference": "DIFFERENCE", "union": "UNION", "intersect": "INTERSECT"}
    if operation not in ops:
        raise ToolError(f"operation must be difference|union|intersect, got {operation!r}")
    mod = obj.modifiers.new("AIBoolean", "BOOLEAN")
    mod.operation = ops[operation]
    mod.solver = "EXACT"
    mod.object = cut
    applied = False
    if apply:
        with object_mode(obj, "OBJECT"):
            bpy.ops.object.modifier_apply(modifier=mod.name)
        applied = True
    from ..pipeline import meta
    meta.set_role(cut, "cutter")
    if not meta.get_description(cut):
        meta.set_description(cut, f"Boolean cutter ({operation}) for {obj.name}")
    cut.display_type = "WIRE"
    cut.hide_render = True
    return (f"{{\"ok\":true,\"object\":\"{obj.name}\",\"cutter\":\"{cut.name}\","
            f"\"operation\":\"{operation}\",\"applied\":{str(applied).lower()}}}")


def set_origin(object: str, mode: str = "bottom") -> str:
    """Move the mesh so the origin sits at bottom/center/corner/cursor."""
    obj = get_mesh_object(object)
    if obj.type != "MESH":
        raise ToolError(f"{object!r} is {obj.type}, expected MESH")
    from mathutils import Vector
    mesh = obj.data
    xs = [v.co.x for v in mesh.vertices]
    ys = [v.co.y for v in mesh.vertices]
    zs = [v.co.z for v in mesh.vertices]
    if not zs:
        raise ToolError("object has no vertices")
    if mode == "bottom":
        offset = Vector((0.0, 0.0, min(zs)))
    elif mode == "center":
        offset = Vector(((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2,
                         (max(zs) + min(zs)) / 2))
    elif mode == "corner":
        offset = Vector((min(xs), min(ys), min(zs)))
    elif mode == "cursor":
        offset = bpy.context.scene.cursor.location - Vector(obj.location)
    else:
        raise ToolError("mode must be bottom|center|corner|cursor")
    for v in mesh.vertices:
        v.co -= offset
    mesh.update()
    return f"origin of {obj.name!r} set to {mode} (shifted mesh by {tuple(round(c, 4) for c in offset)})"


def finalize(asset: str | None = None, apply_transform: bool = True,
             apply_modifiers: bool = False) -> str:
    """Apply transforms (and optionally modifiers) on every asset object.

    With apply_modifiers, un-applied copies are kept in a hidden
    '_backup' collection so the user can keep iterating.
    """
    from ..pipeline import facts, meta
    scene = bpy.context.scene
    coll_name = asset or facts.active_spec(scene)[0]
    if not coll_name or coll_name not in bpy.data.collections:
        raise ToolError(f"no asset collection {coll_name!r}; call set_asset_spec first")
    coll = bpy.data.collections[coll_name]
    objs = [o for o in coll.objects
            if o.type == "MESH" and meta.get_role(o) != "collision"]
    if not objs:
        raise ToolError("nothing to finalize: collection has no mesh parts")
    with object_mode(objs[0], "OBJECT"):
        if apply_modifiers:
            backup = _backup_collection()
            for obj in objs:
                copy = obj.copy()
                copy.data = obj.data.copy()
                for c in list(copy.users_collection):
                    c.objects.unlink(copy)
                backup.objects.link(copy)
                copy.hide_viewport = True
                copy.hide_render = True
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objs:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        if apply_transform:
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
        if apply_modifiers:
            for obj in objs:
                for mod in list(obj.modifiers):
                    bpy.ops.object.modifier_apply(modifier=mod.name)
    return (f"{{\"ok\":true,\"finalized\":\"{coll_name}\",\"objects\":{len(objs)},"
            f"\"applied_transform\":{str(bool(apply_transform)).lower()},"
            f"\"applied_modifiers\":{str(bool(apply_modifiers)).lower()}}}")


def _backup_collection() -> bpy.types.Collection:
    name = "_backup"
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(coll)
    layer_coll = bpy.context.view_layer.layer_collection.children.get(name)
    if layer_coll is not None:
        layer_coll.exclude = True
    return coll


register(  # type: ignore[no-untyped-call]
    "mesh_op",
    "Direct face-level mesh edit with bmesh: extrude, inset, bevel, scale_faces, "
    "subdivide, delete, recalc_normals, merge_by_distance, delete_loose, "
    "dissolve_degenerate, triangulate.\n"
    "Use when: shaping details that primitives+modifiers cannot express (barrel bulge, "
    "tapered tops, face cuts). Do not use when: a modifier (MIRROR, BEVEL, ARRAY, "
    "DECIMATE) can do it non-destructively.\n"
    "Args: selector picks faces — all|top|bottom|side|band|+x|-x|+y|-y; band uses "
    "params.band=[lo,hi] as Z fractions of the bbox. params keys per op: extrude{distance}, "
    "inset{thickness,depth}, bevel{width,segments}, scale_faces{factor,axis:XY|Z|ALL,band}, "
    "subdivide{cuts}, merge_by_distance{distance}.\n"
    "Returns: {ok, op, object, selector, info}.\n"
    "Example: {\"object\":\"SM_Barrel_Body\",\"op\":\"scale_faces\",\"selector\":\"band\","
    "\"params\":{\"factor\":1.08,\"axis\":\"XY\",\"band\":[0.3,0.7]}}",
    {
        "type": "object",
        "properties": {
            "object": {"type": "string", "description": "Mesh object name"},
            "op": {"type": "string", "enum": sorted(_OPS)},
            "selector": {"type": "string",
                         "enum": ["all", "top", "bottom", "side", "band", "+x", "-x", "+y", "-y"]},
            "params": {"type": "object",
                       "description": "Op parameters (distance/thickness/width/segments/factor/axis/band/cuts)"},
        },
        "required": ["object", "op"],
    },
    mesh_op,
)
register(  # type: ignore[no-untyped-call]
    "boolean",
    "Boolean operation of a cutter object into a target. Difference cuts the "
    "cutter out (windows, holes), union merges, intersect keeps the overlap.\n"
    "Use when: real holes through walls/panels. Do not use when: the shape can be "
    "modeled directly — booleans add messy topology.\n"
    "The cutter stays as a wireframe object with role 'cutter' (hidden from render); "
    "with apply=false the modifier stays editable.\n"
    "Returns: {ok, object, cutter, operation, applied}.",
    {
        "type": "object",
        "properties": {
            "object": {"type": "string"},
            "cutter": {"type": "string", "description": "Cutter mesh object name"},
            "operation": {"type": "string", "enum": ["difference", "union", "intersect"]},
            "apply": {"type": "boolean", "description": "Apply the modifier now (default false)"},
        },
        "required": ["object", "cutter"],
    },
    boolean,
)
register(  # type: ignore[no-untyped-call]
    "set_origin",
    "Move the object's origin without moving the mesh in world space: "
    "bottom (props: sits on the floor), center, corner (modular pieces snap "
    "on the grid corner), cursor (3D cursor position).\n"
    "Use when: the created primitive's origin is at its center but engines "
    "expect bottom-center.\n"
    "Returns: confirmation with the applied offset.",
    {
        "type": "object",
        "properties": {
            "object": {"type": "string"},
            "mode": {"type": "string", "enum": ["bottom", "center", "corner", "cursor"]},
        },
        "required": ["object"],
    },
    set_origin,
)
register(  # type: ignore[no-untyped-call]
    "finalize",
    "Apply transforms (rotation/scale) and optionally modifiers on every mesh "
    "part of the asset before export. With apply_modifiers=true, copies of the "
    "un-applied objects are kept in a hidden '_backup' collection.\n"
    "Use when: validate_asset reports scale.applied FAIL or before export. "
    "Do not use when: still iterating the shape — keep modifiers live.\n"
    "Returns: {ok, finalized, objects, applied_transform, applied_modifiers}.",
    {
        "type": "object",
        "properties": {
            "asset": {"type": "string", "description": "Asset collection name (default: active)"},
            "apply_transform": {"type": "boolean", "description": "Apply rotation+scale (default true)"},
            "apply_modifiers": {"type": "boolean", "description": "Apply modifiers destructively (default false)"},
        },
    },
    finalize,
)
