"""Deterministic validation checks + the computed checklist.

A checklist is DERIVED FROM THE SCENE every turn, never stored as a
flag the model must remember to update. Each item is
Check(id, stage, status, message, fix_hint) where fix_hint is an
EXACT tool call — validation is an instruction, not a report.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Literal

import bmesh
import bpy
from mathutils import Vector

from . import color as _color
from .facts import evaluated_mesh
from .spec import AssetSpec, ColorMode

__all__ = ("CHECKS", "Check", "check", "evaluate", "per_object_issues", "render_asset_state", "render_scene_block")

Status = Literal["PASS", "WARN", "FAIL"]
_SCALE_TOL: Final = 1e-4
_VOLUME_TOL: Final = 1e-7


@dataclass(slots=True, frozen=True)
class Check:
    id: str
    stage: str
    status: Status
    message: str
    fix_hint: str = ""


CheckFunc = Callable[[dict[str, Any], AssetSpec | None], Check]
CHECKS: list[CheckFunc] = []


def check(cid: str, stage: str) -> Callable[[CheckFunc], CheckFunc]:
    def deco(fn: CheckFunc) -> CheckFunc:
        def wrapped(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
            result = fn(facts, spec)
            return Check(id=cid, stage=stage, status=result.status,
                         message=result.message, fix_hint=result.fix_hint)
        wrapped.check_id = cid  # type: ignore[attr-defined]
        CHECKS.append(wrapped)
        return wrapped
    return deco


def _mesh_objs(facts: dict[str, Any]) -> list[dict[str, Any]]:
    return [o for o in facts["objects"] if o["type"] == "MESH"]


def _game_objs(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Parts only: LODs are counted, collision proxies are not."""
    return [o for o in facts["objects"]
            if o["role"] != "collision" and o["display_type"] != "WIRE"]


def _bmesh_of(obj: bpy.types.Object, depsgraph: bpy.types.Depsgraph) -> tuple[bmesh.types.BMesh, bool]:
    mesh, owned = evaluated_mesh(obj, depsgraph)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    if owned:
        obj.evaluated_get(depsgraph).to_mesh_clear()
    return bm, True  # caller frees


# ---------------------------------------------------------------- budget

@check("budget.over", stage="5 Validate")
def _budget(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    if spec is None:
        return Check("budget.over", "5 Validate", "PASS", "no spec")
    total = sum(o["tris"] for o in _game_objs(facts) if o["role"] != "lod")
    low, high = spec.budget
    if total > high * 1.5:
        return Check("budget.over", "5 Validate", "FAIL",
                     f"{total} tris > 1.5x budget ({high})",
                     "reduce primitive segments or add_modifier DECIMATE ratio=0.5")
    if total > high:
        return Check("budget.over", "5 Validate", "WARN",
                     f"{total} tris > {high} max",
                     "lower cylinder vertices or Decimate 0.9")
    if total < low:
        return Check("budget.over", "5 Validate", "WARN",
                     f"{total} tris < {low} min (detail budget unused)",
                     "add BEVEL or detail where the camera gets close")
    return Check("budget.over", "5 Validate", "PASS", f"{total} tris in [{low}, {high}]")


# ---------------------------------------------------------------- transforms

@check("scale.applied", stage="5 Validate")
def _scale(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    bad = [o["name"] for o in _game_objs(facts)
           if any(abs(s - 1.0) > _SCALE_TOL for s in o["scale"])]
    if bad:
        return Check("scale.applied", "5 Validate", "FAIL",
                     f"non-unit scale on {', '.join(bad)} — breaks physics, normals, engine scaling",
                     "finalize asset apply_transform=true")
    return Check("scale.applied", "5 Validate", "PASS", "scale applied")


@check("rotation.applied", stage="5 Validate")
def _rotation(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    roots = set(facts.get("roots", ()))
    bad = [o["name"] for o in _game_objs(facts)
           if o["name"] in roots and any(abs(a) > 0.01 for a in o["rotation"])]
    if bad:
        return Check("rotation.applied", "5 Validate", "WARN",
                     f"rotation not applied on {', '.join(bad)} — engines import rotation offsets as surprises",
                     "finalize asset apply_transform=true")
    return Check("rotation.applied", "5 Validate", "PASS", "rotation applied on roots")


@check("origin.base", stage="5 Validate")
def _origin(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    for name in facts.get("roots", ()):
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "MESH":
            continue
        loc = obj.matrix_world.translation
        min_z = min((obj.matrix_world @ v.co).z for v in obj.data.vertices)
        corner_pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        cx = sum(p.x for p in corner_pts) / 8.0
        cy = sum(p.y for p in corner_pts) / 8.0
        if abs(loc.z - min_z) > 1e-3 or abs(loc.x - cx) > 0.05 or abs(loc.y - cy) > 0.05:
            return Check("origin.base", "5 Validate", "WARN",
                         f"{name} origin not at bottom center ({loc.z:.3f} vs base {min_z:.3f})",
                         f"set_origin object={name!r} mode='bottom'")
    return Check("origin.base", "5 Validate", "PASS", "origins at base")


# ---------------------------------------------------------------- geometry

@check("normals.flipped", stage="5 Validate")
def _normals(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    """Signed-volume test: a CLOSED mesh with outward winding has V > 0.

    Robust for non-convex shapes (tori, tubes) where a centroid-ray
    heuristic false-positives on legitimately inward-facing walls.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for o in _game_objs(facts):
        obj = bpy.data.objects.get(o["name"])
        if obj is None or obj.type != "MESH" or not o["verts"]:
            continue
        mesh, owned = evaluated_mesh(obj, depsgraph)
        try:
            mesh.calc_loop_triangles()
            volume = 0.0
            for tri in mesh.loop_triangles:
                a, b, c = (mesh.vertices[i].co for i in tri.vertices)
                volume += a.dot(b.cross(c)) / 6.0
            if volume < -_VOLUME_TOL:
                return Check("normals.flipped", "5 Validate", "FAIL",
                             f"{o['name']} has inverted winding (signed volume {volume:.5f} < 0) — "
                             "back-face culling will hide it",
                             f"mesh_op object={o['name']!r} op='recalc_normals'")
        finally:
            if owned:
                obj.evaluated_get(depsgraph).to_mesh_clear()
    return Check("normals.flipped", "5 Validate", "PASS", "outward winding")


@check("mesh.nonmanifold", stage="5 Validate")
def _nonmanifold(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bad: list[str] = []
    for o in _game_objs(facts):
        obj = bpy.data.objects.get(o["name"])
        if obj is None or obj.type != "MESH":
            continue
        bm, _owned = _bmesh_of(obj, depsgraph)
        try:
            if any(not e.is_manifold for e in bm.edges):
                bad.append(o["name"])
        finally:
            bm.free()
    if bad:
        return Check("mesh.nonmanifold", "5 Validate", "WARN",
                     f"non-manifold edges on {', '.join(bad)} — bad shadows, bake artifacts",
                     f"mesh_op object={bad[0]!r} op='merge_by_distance'")
    return Check("mesh.nonmanifold", "5 Validate", "PASS", "manifold")


@check("mesh.loose", stage="5 Validate")
def _loose(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bad: list[str] = []
    for o in _game_objs(facts):
        obj = bpy.data.objects.get(o["name"])
        if obj is None or obj.type != "MESH":
            continue
        bm, _owned = _bmesh_of(obj, depsgraph)
        try:
            loose_v = sum(1 for v in bm.verts if not v.link_edges)
            loose_e = sum(1 for e in bm.edges if not e.link_faces)
            if loose_v or loose_e:
                bad.append(f"{o['name']} ({loose_v}v/{loose_e}e)")
        finally:
            bm.free()
    if bad:
        return Check("mesh.loose", "5 Validate", "FAIL",
                     f"loose geometry: {', '.join(bad)} — exports garbage",
                     f"mesh_op object={bad[0].split(' ')[0]!r} op='delete_loose'")
    return Check("mesh.loose", "5 Validate", "PASS", "no loose geometry")


@check("mesh.degenerate", stage="5 Validate")
def _degenerate(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bad: list[str] = []
    for o in _game_objs(facts):
        obj = bpy.data.objects.get(o["name"])
        if obj is None or obj.type != "MESH":
            continue
        bm, _owned = _bmesh_of(obj, depsgraph)
        try:
            tiny = sum(1 for f in bm.faces if f.calc_area() < 1e-8)
            if tiny:
                bad.append(f"{o['name']} ({tiny})")
        finally:
            bm.free()
    if bad:
        return Check("mesh.degenerate", "5 Validate", "WARN",
                     f"zero-area faces: {', '.join(bad)}",
                     f"mesh_op object={bad[0].split(' ')[0]!r} op='dissolve_degenerate'")
    return Check("mesh.degenerate", "5 Validate", "PASS", "no degenerate faces")


@check("mesh.ngons", stage="5 Validate")
def _ngons(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    bad = [f"{o['name']} ({n})" for o in _game_objs(facts)
           if (n := _ngon_count(o["name"]))]
    if bad:
        return Check("mesh.ngons", "5 Validate", "WARN",
                     f"n-gons on {', '.join(bad)} — unpredictable triangulation",
                     f"add_modifier object={bad[0].split(' ')[0]!r} type='TRIANGULATE' "
                     "params={'quad_method':'BEAUTY'}")
    return Check("mesh.ngons", "5 Validate", "PASS", "tris/quads only")


def _ngon_count(name: str) -> int:
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH":
        return 0
    return sum(1 for p in obj.data.polygons if len(p.vertices) > 4)


# ---------------------------------------------------------------- UV

@check("uv.missing", stage="4 UV")
def _uv(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    lowpoly_vc = spec is not None and spec.style == "lowpoly" \
        and (spec.color_mode == ColorMode.VERTEX_COLOR if spec else True)
    severity: Status = "WARN" if lowpoly_vc else "FAIL"
    bad = [o["name"] for o in _game_objs(facts)
           if o["role"] != "lod" and o["uv_layers"] == 0]
    if bad:
        fix = (severity == "WARN" and "for lightmaps even in vertex-color workflows") or ""
        return Check("uv.missing", "4 UV", severity,
                     f"{', '.join(bad)} has no UV map — engines expect UV0 {fix}".strip(),
                     f"uv_unwrap objects={bad!r} method='smart' pack=true")
    return Check("uv.missing", "4 UV", "PASS", "UVs present")


@check("uv.overlap", stage="4 UV")
def _uv_overlap(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    bad: list[str] = []
    for o in _game_objs(facts):
        obj = bpy.data.objects.get(o["name"])
        if obj is None or obj.type != "MESH" or not obj.data.uv_layers:
            continue
        uvs = [loop_uv.uv for loop_uv in obj.data.uv_layers.active.data]
        if uvs and (min(u for u, _ in uvs) < -0.001 or max(u for u, _ in uvs) > 1.001
                    or min(v for _, v in uvs) < -0.001 or max(v for _, v in uvs) > 1.001):
            bad.append(o["name"])
    if bad:
        return Check("uv.overlap", "4 UV", "WARN",
                     f"UVs outside 0..1 on {', '.join(bad)} — lightmaps need packed islands",
                     f"uv_unwrap objects={bad!r} method='smart' pack=true margin=0.01")
    return Check("uv.overlap", "4 UV", "PASS", "UVs in 0..1")


# ---------------------------------------------------------------- materials

@check("material.gltf_unsafe", stage="3 Color/Material")
def _materials(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    bad: list[str] = []
    for o in _game_objs(facts):
        obj = bpy.data.objects.get(o["name"])
        if obj is None or obj.type != "MESH":
            continue
        for slot_mat in obj.data.materials:
            if slot_mat is None:
                continue
            unsafe = _color.gltf_unsafe_nodes(slot_mat)
            if unsafe:
                bad.append(f"{slot_mat.name} ({', '.join(unsafe)})")
                break
    if bad:
        return Check("material.gltf_unsafe", "3 Color/Material", "WARN",
                     f"non-glTF-safe node trees: {', '.join(bad)} — lost on export",
                     "rebuild with create_material (Principled/Image Texture/Normal Map/Color Attribute only)")
    return Check("material.gltf_unsafe", "3 Color/Material", "PASS", "glTF-safe materials")


# ---------------------------------------------------------------- metadata

@check("meta.description", stage="0 Brief")
def _descriptions(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    bad = [o["name"] for o in facts["objects"]
           if o["role"] not in ("collision",) and not o["description"].strip()]
    if bad:
        return Check("meta.description", "0 Brief", "FAIL",
                     f"no ai_description on {', '.join(bad)}",
                     f"set_object_info objects={bad!r} description='what it is, material, function'")
    return Check("meta.description", "0 Brief", "PASS", "all described")


@check("meta.color", stage="3 Color/Material")
def _colors(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    # collision proxies are wireframe functional objects, not art parts
    bad = [o["name"] for o in _game_objs(facts)
           if all(abs(c - 1.0) < 1e-5 for c in o["color"])]
    if bad:
        return Check("meta.color", "3 Color/Material", "FAIL",
                     f"default color (untouched) on {', '.join(bad)}",
                     f"set_object_info objects={bad!r} color='iron' (palette key or RGBA)")
    return Check("meta.color", "3 Color/Material", "PASS", "all colored")


@check("name.convention", stage="1 Blockout")
def _names(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    if spec is None:
        return Check("name.convention", "1 Blockout", "PASS", "no spec")
    prefix = f"SM_{spec.name}_"
    bad = [o["name"] for o in facts["objects"]
           if o["role"] not in ("collision",) and not o["name"].startswith(prefix)]
    if bad:
        return Check("name.convention", "1 Blockout", "WARN",
                     f"names not matching SM_{spec.name}_<Part>: {', '.join(bad)}",
                     f"rename_object objects={bad!r} — engine import rules want SM_<Asset>_<Part>")
    return Check("name.convention", "1 Blockout", "PASS", f"SM_{spec.name}_*")


@check("size.spec", stage="1 Blockout")
def _size(facts: dict[str, Any], spec: AssetSpec | None) -> Check:
    if spec is None or spec.size_m is None:
        return Check("size.spec", "1 Blockout", "PASS", "no size in spec")
    dims = [0.0, 0.0, 0.0]
    for o in facts["objects"]:
        if o["role"] in ("collision", "lod"):
            continue
        for i in range(3):
            dims[i] = max(dims[i], o["dimensions"][i])
    for axis, (got, want) in enumerate(zip(dims, spec.size_m, strict=False)):
        if want > 0 and abs(got - want) > want * 0.25:
            axes = "XYZ"[axis]
            return Check("size.spec", "1 Blockout", "WARN",
                         f"{axes} size {got:.2f} m vs spec {want:.2f} m (>25% off)",
                         f"transform_object object=<root> dimensions={list(spec.size_m)!r}")
    return Check("size.spec", "1 Blockout", "PASS", "within 25% of spec size")


# ---------------------------------------------------------------- evaluate + render

def evaluate(scene_facts: dict[str, Any], spec: AssetSpec | None,
             only: str | None = None) -> list[Check]:
    """Run all checks; only=<check id> runs a single one."""
    if only is not None:
        return [c(scene_facts, spec) for c in CHECKS
                if getattr(c, "check_id", "") == only]
    return [c(scene_facts, spec) for c in CHECKS]


def per_object_issues(facts: dict[str, Any]) -> dict[str, list[str]]:
    """Lightweight per-object issues for get_scene_state / <scene>."""
    issues: dict[str, list[str]] = {}
    for o in facts["objects"]:
        out: list[str] = []
        if o["type"] == "MESH":
            if o["uv_layers"] == 0:
                out.append("no_uv")
            if any(abs(s - 1.0) > _SCALE_TOL for s in o["scale"]):
                out.append("scale_not_applied")
        if not o["description"].strip():
            out.append("no_description")
        if all(abs(c - 1.0) < 1e-5 for c in o["color"]):
            out.append("no_color")
        if out:
            issues[o["name"]] = out
    return issues


def render_asset_state(facts: dict[str, Any], spec: AssetSpec | None) -> str:
    """The computed <asset_state> block injected every turn."""
    if spec is None:
        return "<asset_state>\nno asset brief yet — call set_asset_spec first (engine, style, budget, size)\n</asset_state>"
    checks = evaluate(facts, spec)
    fails = [c for c in checks if c.status == "FAIL"]
    warns = [c for c in checks if c.status == "WARN"]
    passes = [c for c in checks if c.status == "PASS"]
    low, high = spec.budget
    lines = [
        f'<asset_state name="{spec.collection_name()}" engine="{spec.engine.value}" '
        f'style="{spec.style.value}" budget="{low}-{high} tris">',
        "parts: " + "; ".join(
            f'{o["name"]} ({o["tris"]} tris, "{(o["description"] or "UNDESCRIBED")[:60]}")'
            for o in facts["objects"] if o["role"] not in ("collision",)),
    ]
    for c in fails:
        lines.append(f"FAIL {c.id:<20} {c.message} → {c.fix_hint}")
    for c in warns:
        lines.append(f"WARN {c.id:<20} {c.message} → {c.fix_hint}")
    lines.append("PASS " + "  ".join(c.id.split(".")[0] for c in passes))
    lines.append("next: fix FAILs, then validate_asset, then capture_view")
    lines.append("</asset_state>")
    return "\n".join(lines)


def render_scene_block(facts: dict[str, Any]) -> str:
    """Compact <scene> facts for grounding object names."""
    lines = ["<scene>"]
    for o in facts["objects"][: _MAX_SCENE]:
        desc = o["description"][:60]
        lines.append(
            f'{o["name"]} {o["type"]} {o["tris"]}tris dims={o["dimensions"]} '
            f'role={o["role"] or "-"} color={[round(c, 2) for c in o["color"]]} "{desc}"'
        )
    if facts.get("truncated"):
        lines.append(f"(… {facts['objects_total']} objects total, showing first {_MAX_SCENE})")
    lines.append("</scene>")
    return "\n".join(lines)


_MAX_SCENE: Final = 40
