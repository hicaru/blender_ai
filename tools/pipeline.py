"""Pipeline tools: thin schema adapters around the pipeline/ modules.

set_asset_spec, set_object_info, validate_asset, generate_lods,
make_collision, export_asset, capture_view, add_reference_image,
load_skill. All logic lives in pipeline/* so the UI quick actions and
the eval harness reuse the exact same code.
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy

from . import ToolError, register
from ._common import get_objects

__all__ = ()


# --------------------------------------------------------------- spec

def set_asset_spec(name: str, description: str,
                   asset_class: str = "prop", engine: str = "gltf",
                   style: str = "lowpoly", color_mode: str | None = None,
                   tri_budget: list[int] | None = None,
                   size_m: list[float] | None = None,
                   lods: list[float] | None = None,
                   collision: bool = True) -> str:
    """Create or update the asset brief; makes the SM_<Name> collection active."""
    from ..pipeline import color as _color
    from ..pipeline.spec import SPEC_KEY, AssetClass, AssetSpec, ColorMode, Engine, Style

    try:
        asset_class_v = AssetClass(asset_class)
        engine_v = Engine(engine)
        style_v = Style(style)
    except ValueError as exc:
        raise ToolError(f"unknown enum value: {exc}") from exc
    if color_mode is None:
        color_mode_v = _color.spec_default_color_mode(style_v)
    else:
        try:
            color_mode_v = ColorMode(color_mode)
        except ValueError as exc:
            raise ToolError("color_mode must be vertex_color|materials") from exc
    # Godot generates LODs at import, so it gets none by default.
    lods_v: tuple[float, ...]
    if lods is None:
        lods_v = (1.0,) if engine_v is Engine.GODOT else (1.0, 0.5, 0.25)
    else:
        lods_v = tuple(map(float, lods))
    try:
        spec = AssetSpec(
            name=str(name).strip(),
            description=str(description).strip(),
            asset_class=asset_class_v,
            engine=engine_v,
            style=style_v,
            color_mode=color_mode_v,
            tri_budget=(int(tri_budget[0]), int(tri_budget[1])) if tri_budget else None,
            size_m=(float(size_m[0]), float(size_m[1]), float(size_m[2]))
            if size_m is not None and len(size_m) == 3 else None,
            lods=lods_v,
            collision=bool(collision),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(f"bad spec: {exc}") from exc

    coll_name = spec.collection_name()
    coll = bpy.data.collections.get(coll_name)
    if coll is None:
        coll = bpy.data.collections.new(coll_name)
        bpy.context.scene.collection.children.link(coll)
    coll[SPEC_KEY] = spec.to_json()
    bpy.context.scene["blender_ai_active_asset"] = coll_name
    from ..pipeline.session import session

    session.active_asset = coll_name
    from ..pipeline.color import ensure_viewport_object_colors
    ensure_viewport_object_colors()
    low, high = spec.budget
    return (f"{{\"ok\":true,\"collection\":\"{coll_name}\",\"budget\":[{low},{high}],"
            f"\"color_mode\":\"{spec.color_mode.value}\"}} "
            f"create parts inside this collection now, named {coll_name}_<Part>")


# --------------------------------------------------------------- object info

def set_object_info(objects: list[str], description: str | None = None,
                    role: str | None = None,
                    color: str | list[float] | None = None) -> str:
    """Batch-edit descriptions, roles and colors on existing objects."""
    from ..pipeline import meta
    from ..pipeline.color import apply_object_color
    from ..pipeline.facts import active_spec
    from ..pipeline.palette import resolve_color
    from ..pipeline.spec import ColorMode

    objs = get_objects(list(objects))
    spec = active_spec(bpy.context.scene)[1]
    mode = spec.color_mode if spec else ColorMode.VERTEX_COLOR
    updated = []
    for obj in objs:
        if description is not None:
            meta.set_description(obj, str(description))
        if role is not None:
            try:
                meta.set_role(obj, str(role))
            except ValueError as exc:
                raise ToolError(f"{obj.name}: {exc}") from exc
        if color is not None:
            rgba = resolve_color(color, fallback_name=obj.name)
            apply_object_color(obj, rgba, mode)
        updated.append(obj.name)
    return json.dumps({"ok": True, "updated": updated}, separators=(",", ":"))


# --------------------------------------------------------------- validation

def validate_asset(asset: str | None = None) -> str:
    """Run every deterministic check; report FAILs/WARNs with exact fix hints."""
    from ..pipeline import checks, facts
    scene = bpy.context.scene
    coll_name = asset or facts.active_spec(scene)[0]
    scene_facts = facts.collect_asset_facts(scene) if coll_name else None
    if scene_facts is None:
        raise ToolError("no active asset; call set_asset_spec first")
    spec = scene_facts["spec"]
    results = checks.evaluate(scene_facts, spec)
    fails = [c for c in results if c.status == "FAIL"]
    warns = [c for c in results if c.status == "WARN"]
    payload = {
        "ok": not fails,
        "fail": [f"{c.id}: {c.message} → {c.fix_hint}" for c in fails],
        "warn": [f"{c.id}: {c.message} → {c.fix_hint}" for c in warns],
        "tris": scene_facts["tris_total"],
        "budget": list(spec.budget) if spec else None,
    }
    return json.dumps(payload, separators=(",", ":"))


# --------------------------------------------------------------- LODs / collision

def generate_lods(ratios: list[float] | None = None) -> str:
    """Create LOD duplicates with DECIMATE for every part of the active asset."""
    from ..pipeline import facts, lod
    scene = bpy.context.scene
    coll_name, spec = facts.active_spec(scene)
    if not coll_name or spec is None:
        raise ToolError("no active asset spec; call set_asset_spec first")
    coll = bpy.data.collections[coll_name]
    created = lod.generate_lods(coll, spec, tuple(map(float, ratios)) if ratios else None)
    return json.dumps({"ok": True, "created": created}, separators=(",", ":"))


def make_collision(kind: str = "box") -> str:
    """Create collision proxies for the active asset (engine-correct naming)."""
    from ..pipeline import collision as _collision
    from ..pipeline import facts
    scene = bpy.context.scene
    coll_name, spec = facts.active_spec(scene)
    if not coll_name or spec is None:
        raise ToolError("no active asset spec; call set_asset_spec first")
    coll = bpy.data.collections[coll_name]
    created = _collision.make_collision(coll, spec, str(kind))
    return json.dumps({"ok": True, "created": created}, separators=(",", ":"))


# --------------------------------------------------------------- export

def export_asset(asset: str | None = None, force: bool = False) -> str:
    """Export the active asset with the engine preset. Refuses when validation
    has blocking FAILs unless force=true (after ask_user confirmation)."""
    from ..pipeline import checks, facts
    from ..pipeline.export import plan_for

    scene = bpy.context.scene
    coll_name = asset or facts.active_spec(scene)[0]
    scene_facts = facts.collect_asset_facts(scene) if coll_name else None
    if scene_facts is None:
        raise ToolError("no active asset; call set_asset_spec first")
    spec = scene_facts["spec"]
    if spec is None:
        raise ToolError(f"collection {coll_name!r} has no asset spec")
    if not force:
        results = checks.evaluate(scene_facts, spec)
        fails = [c for c in results if c.status == "FAIL"]
        if fails:
            ids = "; ".join(c.id for c in fails)
            return (f"REFUSED: {len(fails)} validation FAIL(s): {ids}. Fix them "
                    "(see hints), then export again — or confirm with the user "
                    "via ask_user and pass force=true.")
    coll = bpy.data.collections[coll_name]
    plan = plan_for(spec.engine)
    out_dir = _export_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{spec.name}{plan.ext}"
    prev_selected = list(bpy.context.selected_objects)
    selected: list[bpy.types.Object] = list(coll.objects)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in selected:
        obj.select_set(True)
    try:
        if plan.operator == "gltf":
            bpy.ops.export_scene.gltf(filepath=str(path), **plan.kwargs)
        else:
            bpy.ops.export_scene.fbx(filepath=str(path), **plan.kwargs)
    finally:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in prev_selected:
            obj.select_set(True)
    size = path.stat().st_size
    tris = {p["name"]: p["tris"] for p in scene_facts["objects"]}
    return json.dumps({
        "ok": True, "path": str(path), "bytes": size,
        "tris": tris, "objects": len(selected), "engine": spec.engine.value,
    }, separators=(",", ":"))


def _export_dir() -> Path:
    from .. import prefs
    addon_prefs = prefs.get_prefs()  # type: ignore[no-untyped-call]
    raw = getattr(addon_prefs, "export_dir", "") if addon_prefs else ""
    if raw:
        return Path(bpy.path.abspath(raw))
    blend = bpy.data.filepath
    if blend:
        return Path(blend).parent / "exports"
    return Path(bpy.app.tempdir) / "exports"


# --------------------------------------------------------------- capture

def capture_view(views: list[str] | None = None, size: int = 512,
                 shading: str = "solid") -> str:
    """Render the active asset into one 2x2 contact sheet for visual self-check."""
    from ..pipeline import capture, facts
    from ..pipeline.session import session

    scene = bpy.context.scene
    scene_facts = facts.collect_asset_facts(scene)
    if scene_facts is None:
        raise ToolError("no active asset; call set_asset_spec first")
    objs = [bpy.data.objects[n] for n in scene_facts["roots"]
            if n in bpy.data.objects]
    if not objs:
        raise ToolError("asset has no objects to capture")
    center, radius = _bbox_of(objs)
    view_list = [str(v) for v in (views or ["iso", "front", "side", "top"])]
    bad = [v for v in view_list if v not in ("iso", "front", "side", "top")]
    if bad:
        raise ToolError(f"unknown views {bad}; use iso|front|side|top")
    color_type = "MATERIAL" if shading == "material" else "OBJECT"
    path = capture.capture_contact_sheet(view_list, int(size), color_type,
                                         center, radius)
    session.last_capture = path
    return json.dumps({
        "ok": True, "captured": len(view_list), "views": view_list,
        "image": path,
        "note": "the 2x2 sheet is attached to your NEXT message as an image; "
                "inspect it for floating parts, wrong proportions, inverted faces",
    }, separators=(",", ":"))


def _bbox_of(objs: list[bpy.types.Object]) -> tuple[tuple[float, float, float], float]:
    from mathutils import Vector
    pts: list[Vector] = []
    for obj in objs:
        pts += [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    if not pts:
        return Vector((0, 0, 0)), 1.0
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (lo + hi) / 2, max((hi - lo).length / 2, 0.5)


# --------------------------------------------------------------- reference image

def add_reference_image(path: str, view: str = "front",
                        opacity: float = 0.5) -> str:
    """Place an image as an Image Empty behind the geometry for blockout matching."""

    image_path = Path(bpy.path.abspath(str(path)))
    if not image_path.exists():
        raise ToolError(f"image not found: {image_path}")
    img = bpy.data.images.load(str(image_path), check_existing=True)
    empty = bpy.data.objects.new(f"REF_{image_path.stem}", None)
    empty.empty_display_type = "IMAGE"
    empty.data = img
    bpy.context.scene.collection.objects.link(empty)
    empty.empty_image_depth = "BACK"
    if hasattr(empty, "empty_image_side"):
        empty.empty_image_side = {"front": "FRONT", "side": "SIDE", "top": "TOP"}.get(view, "FRONT")
    empty.use_empty_image_alpha = True
    empty.color[3] = float(opacity)
    if view == "side":
        empty.rotation_euler = (1.5707963, 0.0, 1.5707963)
    elif view == "top":
        empty.rotation_euler = (0.0, 0.0, 0.0)
    return json.dumps({
        "ok": True, "reference": empty.name, "view": view,
        "image_size": [img.size[0], img.size[1]],
        "note": "blockout should match this silhouette; capture_view with the "
                "same view compares them",
    }, separators=(",", ":"))


# --------------------------------------------------------------- skills

def load_skill(name: str) -> str:
    """Load a skill recipe body by name (the index is always in the prompt)."""
    from pathlib import Path

    from .. import prefs
    from ..skills import SkillIndex
    addon_prefs = prefs.get_prefs()  # type: ignore[no-untyped-call]
    raw = getattr(addon_prefs, "skills_dir", "") if addon_prefs else ""
    user_dir = Path(bpy.path.abspath(raw)) if raw else None
    index = SkillIndex.load(user_dir)
    skill = index.get(str(name))
    if skill is None:
        raise ToolError(f"no skill {name!r}; available: "
                        f"{', '.join(s.name for s in index.skills)}")
    from ..pipeline.session import session

    session.pinned_skill = skill.name
    return f"<active_skill name=\"{skill.name}\">\n{skill.body}\n</active_skill>"


# --------------------------------------------------------------- registration

register(  # type: ignore[no-untyped-call]
    "set_asset_spec",
    "Create or update the asset brief: engine, style, color mode, class, triangle "
    "budget, real-world size. Creates the SM_<Name> collection and makes it active.\n"
    "Use when: starting ANY game asset — every later decision (detail level, colors, "
    "export settings) is derived from it. Do not use when: a spec already exists and "
    "only one field changes — pass just the fields to change (they are merged).\n"
    "Args: asset_class small_prop|prop|hero_prop|environment|modular_piece|vehicle|"
    "character; engine gltf|godot|unity|unreal; style lowpoly|stylized|realistic; "
    "color_mode vertex_color|materials (default: vertex_color, materials for realistic); "
    "tri_budget [min,max] (default from class; lowpoly uses a quarter); size_m [x,y,z] meters.\n"
    "Returns: {ok, collection, budget, color_mode}.\n"
    "Example: {\"name\":\"Barrel\",\"description\":\"Wooden storage barrel, two iron "
    "hoops\",\"asset_class\":\"prop\",\"engine\":\"godot\",\"style\":\"lowpoly\","
    "\"size_m\":[0.6,0.6,0.9]}",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Asset name, becomes collection SM_<Name>"},
            "description": {"type": "string", "description": "What this asset is, 1 sentence"},
            "asset_class": {"type": "string", "enum": [
                "small_prop", "prop", "hero_prop", "environment",
                "modular_piece", "vehicle", "character"]},
            "engine": {"type": "string", "enum": ["gltf", "godot", "unity", "unreal"]},
            "style": {"type": "string", "enum": ["lowpoly", "stylized", "realistic"]},
            "color_mode": {"type": "string", "enum": ["vertex_color", "materials"]},
            "tri_budget": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
            "size_m": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
            "lods": {"type": "array", "items": {"type": "number"},
                     "description": "Decimate ratios; default (1.0,0.5,0.25), Godot (1.0,)"},
            "collision": {"type": "boolean"},
        },
        "required": ["name", "description"],
    },
    set_asset_spec,
)
register(  # type: ignore[no-untyped-call]
    "set_object_info",
    "Batch-set metadata and colors on existing objects: ai_description (what it is, "
    "material, function), ai_role (body|detail|trim|foliage|trunk|wheel|handle|"
    "collision|lod|socket|pivot), color (palette key like 'iron'/'wood_light' or RGBA "
    "0..1 list; omitted colors are auto-assigned deterministically).\n"
    "Use when: validate_asset reports meta.description/meta.color FAILs, or the user "
    "asks to rename/recolor parts. Prefer one batch call over many single calls.\n"
    "Returns: {ok, updated:[names]}.",
    {
        "type": "object",
        "properties": {
            "objects": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "description": {"type": "string"},
            "role": {"type": "string"},
            "color": {"type": ["string", "array"],
                      "description": "Palette key or [r,g,b,(a)] floats 0..1"},
        },
        "required": ["objects"],
    },
    set_object_info,
)
register(  # type: ignore[no-untyped-call]
    "validate_asset",
    "Run all deterministic game-asset checks: tri budget, applied scale, origin at "
    "base, flipped normals, non-manifold/loose/degenerate geometry, n-gons, missing "
    "UVs, glTF-unsafe materials, missing descriptions/colors, naming, size vs spec.\n"
    "Use when: after blockout and before saying 'done'; REQUIRED before export_asset. "
    "Do not use when: nothing changed since the last validation.\n"
    "Returns: {ok, fail:[...], warn:[...], tris, budget} — every entry carries an "
    "exact fix (a tool call you can issue directly).",
    {
        "type": "object",
        "properties": {"asset": {"type": "string", "description": "Collection name (default: active)"}},
    },
    validate_asset,
)
register(  # type: ignore[no-untyped-call]
    "generate_lods",
    "Create LOD<i> duplicates with a DECIMATE modifier for every part of the active "
    "asset. Low-poly style gets a single 0.5 LOD; Godot skips LODs (importer "
    "generates them).\n"
    "Use when: stage 6 after validation passes. Do not use when: engine is godot or "
    "the asset is a modular piece.\n"
    "Returns: {ok, created:[names]}.",
    {
        "type": "object",
        "properties": {"ratios": {"type": "array", "items": {"type": "number"},
                                  "description": "Decimate ratios, default per style"}},
    },
    generate_lods,
)
register(  # type: ignore[no-untyped-call]
    "make_collision",
    "Create a collision proxy for every part: box (object-aligned bbox, default), "
    "convex (hull), or mesh (copy of the base mesh). Naming follows the target engine "
    "(UCX_/UBX_ for Unreal, -colonly/-convcolonly for Godot, _Collider for Unity); "
    "proxies are wireframe, hidden from render, role 'collision'.\n"
    "Use when: stage 6, unless spec.collision is false.\n"
    "Returns: {ok, created:[names]}.",
    {
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": ["box", "convex", "mesh"]}},
    },
    make_collision,
)
register(  # type: ignore[no-untyped-call]
    "export_asset",
    "Export the active asset to exports/<Name>.glb (gltf/godot) or .fbx "
    "(unity/unreal) with engine-exact settings; modifiers are baked by the "
    "exporter WITHOUT touching the working scene; ai_description reaches the file "
    "as glTF extras.\n"
    "Use when: the user asks for the finished file, and ONLY after validate_asset "
    "reports no FAILs — the tool refuses otherwise (force=true after ask_user "
    "confirmation overrides).\n"
    "Returns: {ok, path, bytes, tris, objects, engine}.",
    {
        "type": "object",
        "properties": {
            "asset": {"type": "string", "description": "Collection name (default: active)"},
            "force": {"type": "boolean", "description": "Export despite FAILs (ask_user first!)"},
        },
    },
    export_asset,
)
register(  # type: ignore[no-untyped-call]
    "capture_view",
    "Render the active asset from up to 4 named views into ONE 2x2 contact sheet "
    "and attach it to your next message as an image.\n"
    "Use when: after validation — LOOK at your result before claiming done; catches "
    "floating parts, broken silhouettes and inverted faces no numeric check finds.\n"
    "Args: views subset of iso|front|side|top; shading solid (object colors) or "
    "material.\n"
    "Returns: {ok, captured, views, image} + the image arrives with your next turn.",
    {
        "type": "object",
        "properties": {
            "views": {"type": "array", "items": {"type": "string",
                                                 "enum": ["iso", "front", "side", "top"]}},
            "size": {"type": "integer", "description": "Tile size px (default 512)"},
            "shading": {"type": "string", "enum": ["solid", "material"]},
        },
    },
    capture_view,
)
register(  # type: ignore[no-untyped-call]
    "add_reference_image",
    "Place an image file into the scene as a reference Image Empty behind the "
    "geometry (front/side/top aligned, semi-transparent) so the blockout can match "
    "its silhouette.\n"
    "Use when: the user attached a concept image and you start blocking out. "
    "capture_view with the same view then shows both overlapped.\n"
    "Returns: {ok, reference, view, image_size}.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute image path (from an attachment)"},
            "view": {"type": "string", "enum": ["front", "side", "top"]},
            "opacity": {"type": "number", "description": "0..1, default 0.5"},
        },
        "required": ["path"],
    },
    add_reference_image,
)
register(  # type: ignore[no-untyped-call]
    "load_skill",
    "Load a named recipe from the skill library; returns the full body inside "
    "<active_skill> tags. The index with one-line descriptions is always in the "
    "system prompt.\n"
    "Use when: the request matches a skill (index line) OR the user names one. The "
    "skill stays active for the whole asset build.\n"
    "Returns: the skill body: Parts table, Steps (exact tool calls), Verify, Pitfalls.",
    {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Skill name from the index"}},
        "required": ["name"],
    },
    load_skill,
)
