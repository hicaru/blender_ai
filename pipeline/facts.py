"""Scene facts extraction — the single place that reads meshes.

Shared by validation checks, the ``<scene>`` prompt block and
``get_scene_state``, so tri counts in the UI, the prompt and validation
always agree.
"""

from __future__ import annotations

import math
from typing import Any, Final

import bpy

from .meta import get_asset, get_description, get_role
from .spec import SPEC_KEY, AssetSpec

__all__ = ("collect_asset_facts", "collect_scene_facts", "evaluated_mesh", "object_facts")

_MAX_SCENE_OBJECTS: Final = 40  # <scene> cap; beyond that, collection summaries


def evaluated_mesh(obj: bpy.types.Object, depsgraph: bpy.types.Depsgraph) -> tuple[bpy.types.Mesh, bool]:
    """Return (mesh, owned) for the evaluated object; owned=True means caller must to_mesh_clear()."""
    eval_obj = obj.evaluated_get(depsgraph)
    if not obj.modifiers:
        mesh = obj.data
        return mesh, False
    mesh = eval_obj.to_mesh()
    return mesh, True


def _tri_count(mesh: bpy.types.Mesh) -> int:
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


def object_facts(obj: bpy.types.Object, depsgraph: bpy.types.Depsgraph) -> dict[str, Any]:
    mesh, owned = (None, False)
    if obj.type == "MESH":
        mesh, owned = evaluated_mesh(obj, depsgraph)
    try:
        tris = _tri_count(mesh) if mesh is not None and obj.type == "MESH" else 0
        uv_layers = len(mesh.uv_layers) if mesh is not None and obj.type == "MESH" else 0
        dims = tuple(round(d, 4) for d in obj.dimensions)
        return {
            "name": obj.name,
            "type": obj.type,
            "tris": tris,
            "verts": len(mesh.vertices) if mesh is not None and obj.type == "MESH" else 0,
            "dimensions": dims,
            "location": tuple(round(c, 4) for c in obj.location),
            "rotation": tuple(round(math.degrees(a), 2) for a in obj.rotation_euler),
            "scale": tuple(round(s, 4) for s in obj.scale),
            "description": get_description(obj),
            "role": get_role(obj),
            "color": [round(c, 3) for c in obj.color],
            "asset": get_asset(obj),
            "modifiers": [m.type for m in obj.modifiers],
            "materials": [m.name if m else "" for m in obj.data.materials] if obj.type == "MESH" else [],
            "uv_layers": uv_layers,
            "parent": obj.parent.name if obj.parent else "",
            "hide_render": obj.hide_render,
            "display_type": obj.display_type,
        }
    finally:
        if owned:
            obj.evaluated_get(depsgraph).to_mesh_clear()


def _collection_objects(coll: bpy.types.Collection) -> list[bpy.types.Object]:
    """Objects directly linked to the collection (LOD/collision siblings included)."""
    return list(coll.objects)


def active_spec(scene: bpy.types.Scene) -> tuple[str, AssetSpec | None]:
    """(collection_name, spec) of the active asset, or ("", None)."""
    name = scene.get("blender_ai_active_asset", "")
    if not name or name not in bpy.data.collections:
        return "", None
    coll = bpy.data.collections[name]
    raw = coll.get(SPEC_KEY)
    if not raw:
        return name, None
    try:
        return name, AssetSpec.from_json(str(raw))
    except (ValueError, KeyError, TypeError):
        return name, None


def collect_asset_facts(scene: bpy.types.Scene) -> dict[str, Any] | None:
    """Facts for the active asset collection only; None if no active asset."""
    coll_name, spec = active_spec(scene)
    if not coll_name:
        return None
    coll = bpy.data.collections[coll_name]
    depsgraph = bpy.context.evaluated_depsgraph_get()
    objs = _collection_objects(coll)
    parts = [object_facts(o, depsgraph) for o in objs]
    roots = [o.name for o in objs if o.parent is None or o.parent not in objs]
    return {
        "asset": coll_name,
        "spec": spec,
        "collection": coll_name,
        "objects": parts,
        "roots": roots,
        "tris_total": sum(p["tris"] for p in parts if p["role"] not in ("collision", "lod")),
    }


def collect_scene_facts(scene: bpy.types.Scene) -> dict[str, Any]:
    """All scene objects (capped) plus the active-asset block, for prompts/UI."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    objs = [o for o in scene.objects if o.type not in {"CAMERA", "LIGHT"}]
    objects = [object_facts(o, depsgraph) for o in objs[:_MAX_SCENE_OBJECTS]]
    asset_block = collect_asset_facts(scene)
    return {
        "objects": objects,
        "objects_total": len(objs),
        "truncated": len(objs) > _MAX_SCENE_OBJECTS,
        "asset": asset_block,
    }
