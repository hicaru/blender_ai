"""Model tools: build_model (script + kit), read_script, get_scene_state,
export_glb (Bevy), capture_view, finish.

One model = one collection marked with ``blender_ai_model``. build_model
wipes that collection and re-runs the FULL script, so every iteration is
a deterministic rebuild; the script is kept as a Text datablock
(``build_<name>.py``) so the model and the user can read it back.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import traceback
from pathlib import Path
from typing import Final

import bmesh
import bpy
import mathutils

from .. import report
from ..modelkit import PALETTE, Kit
from . import ToolError, register

__all__ = ("build_model", "export_glb", "get_scene_state", "model_parts", "read_script")

MODEL_KEY: Final = "blender_ai_model"
_MAX_OUTPUT: Final = 1500
_MAX_SCENE_OBJECTS: Final = 40


# ------------------------------------------------------------------ helpers

def _script_name(model: str) -> str:
    return f"build_{model}.py"


def _clean_name(name: object) -> str:
    text = str(name or "").strip()
    if not text or any(c in text for c in "/\\:"):
        raise ToolError(f"model name {name!r} must be a plain name like 'Bunker'")
    return text


def _model_collection(model: str, *, create: bool) -> bpy.types.Collection | None:
    coll = bpy.data.collections.get(model)
    if coll is None and create:
        coll = bpy.data.collections.new(model)
        bpy.context.scene.collection.children.link(coll)
    if coll is not None:
        coll[MODEL_KEY] = True
    return coll


def _clear(coll: bpy.types.Collection) -> None:
    for obj in list(coll.all_objects):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)


def _tris(mesh: bpy.types.Mesh) -> int:
    return sum(len(poly.vertices) - 2 for poly in mesh.polygons)


def model_parts(coll: bpy.types.Collection) -> tuple[list[report.Part], list[str]]:
    """World-space parts of a model plus names of faceless meshes."""
    bpy.context.view_layer.update()
    parts: list[report.Part] = []
    empties: list[str] = []
    for obj in coll.all_objects:
        if obj.type != "MESH":
            continue
        if not obj.data.polygons:
            empties.append(obj.name)
            continue
        corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
        parts.append(report.Part(
            name=obj.name, tris=_tris(obj.data),
            lo=(min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)),
            hi=(max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)),
            mesh=obj.data.name if obj.data.users > 1 else ""))
    return parts, empties


def _script_error(exc: BaseException, code: str, filename: str) -> str:
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == filename]
    lines = code.splitlines()
    where = ""
    if frames and frames[-1].lineno is not None:
        number = frames[-1].lineno
        source = lines[number - 1].strip() if 0 < number <= len(lines) else ""
        where = f"line {number}: {source}\n"
    elif isinstance(exc, SyntaxError) and exc.lineno is not None:
        where = f"line {exc.lineno}: {(exc.text or '').strip()}\n"
    return f"{where}{type(exc).__name__}: {exc}"


def _ensure_object_mode() -> None:
    if bpy.context.mode != "OBJECT" and bpy.context.object is not None:
        bpy.ops.object.mode_set(mode="OBJECT")


# ------------------------------------------------------------------ tools

def build_model(name: str, code: str) -> str:
    """Wipe the model's collection and run the full build script with ``mk``."""
    model = _clean_name(name)
    _ensure_object_mode()
    coll = _model_collection(model, create=True)
    if coll is None:  # pragma: no cover — create=True always returns one
        raise ToolError(f"could not create collection {model!r}")
    _clear(coll)
    script = bpy.data.texts.get(_script_name(model)) or bpy.data.texts.new(_script_name(model))
    script.from_string(code)
    kit = Kit(coll)
    filename = f"<build:{model}>"
    namespace: dict[str, object] = {
        "bpy": bpy, "bmesh": bmesh, "math": math, "mathutils": mathutils,
        "Vector": mathutils.Vector, "Matrix": mathutils.Matrix, "mk": kit,
    }
    stdout = io.StringIO()
    error = ""
    try:
        with contextlib.redirect_stdout(stdout):
            exec(compile(code, filename, "exec"), namespace)
    except Exception as exc:  # noqa: BLE001 — sandbox boundary: the error IS the observation
        error = _script_error(exc, code, filename)
    parts, empties = model_parts(coll)
    return report.render_report(model, parts, error=error,
                                output=stdout.getvalue().strip()[:_MAX_OUTPUT],
                                notes=kit.notes, empties=empties)


def read_script(name: str) -> str:
    """Return the stored build script of a model."""
    model = _clean_name(name)
    text = bpy.data.texts.get(_script_name(model))
    if text is None:
        raise ToolError(f"no build script for {model!r}; models: {', '.join(_models()) or 'none'}")
    return str(text.as_string())


def _models() -> list[str]:
    return [c.name for c in bpy.data.collections if c.get(MODEL_KEY)]


def get_scene_state() -> str:
    """Models with size/tris, plus loose objects (bounded)."""
    models = []
    for model in _models():
        coll = bpy.data.collections[model]
        parts, _empties = model_parts(coll)
        if parts:
            lo = [min(p.lo[i] for p in parts) for i in range(3)]
            hi = [max(p.hi[i] for p in parts) for i in range(3)]
            size = [round(hi[i] - lo[i], 2) for i in range(3)]
        else:
            size = [0.0, 0.0, 0.0]
        models.append({"name": model, "parts": len(parts),
                       "tris": sum(p.tris for p in parts), "size_m": size,
                       "has_script": bpy.data.texts.get(_script_name(model)) is not None})
    in_models = {o.name for m in _models() for o in bpy.data.collections[m].all_objects}
    loose = [o for o in bpy.context.scene.objects if o.name not in in_models]
    return json.dumps({
        "models": models,
        "other_objects": [{"name": o.name, "type": o.type,
                           "size_m": [round(d, 2) for d in o.dimensions]}
                          for o in loose[:_MAX_SCENE_OBJECTS]],
        "other_objects_total": len(loose),
        "palette": list(PALETTE),
    }, separators=(",", ":"))


def _export_dir() -> Path:
    from ..prefs import get_prefs

    prefs = get_prefs()  # type: ignore[no-untyped-call]
    raw = str(getattr(prefs, "export_dir", "") or "") if prefs is not None else ""
    if raw:
        return Path(bpy.path.abspath(raw))
    if bpy.data.filepath:
        return Path(bpy.data.filepath).parent / "exports"
    return Path(bpy.app.tempdir) / "exports"


# Bevy loads glTF 2.0: +Y up, meters, Principled BSDF -> StandardMaterial,
# emission -> emissive, object names -> Name components, parents -> children.
_BEVY_GLTF: Final[dict[str, object]] = {
    "export_format": "GLB", "use_selection": True, "export_apply": True,
    "export_yup": True, "export_extras": True, "export_cameras": False,
    "export_lights": False, "export_materials": "EXPORT",
}


def export_glb(name: str) -> str:
    """Write <name>.glb for Bevy (selection scoped to the model)."""
    model = _clean_name(name)
    coll = _model_collection(model, create=False)
    if coll is None:
        raise ToolError(f"no model {model!r}; models: {', '.join(_models()) or 'none'}")
    objs = list(coll.all_objects)
    if not objs:
        raise ToolError(f"model {model!r} is empty; build it first")
    _ensure_object_mode()
    out_dir = _export_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{model}.glb"
    layer_objects = bpy.context.view_layer.objects
    previous = [o for o in layer_objects if o.select_get()]
    for obj in previous:
        obj.select_set(False)
    for obj in objs:
        obj.select_set(True)
    try:
        bpy.ops.export_scene.gltf(filepath=str(path), **_BEVY_GLTF)
    finally:
        for obj in objs:
            obj.select_set(False)
        for obj in previous:
            obj.select_set(True)
    parts, _empties = model_parts(coll)
    return json.dumps({
        "ok": True, "path": str(path), "bytes": path.stat().st_size,
        "parts": len(parts), "tris": sum(p.tris for p in parts),
        "bevy": f'asset_server.load(GltfAssetLabel::Scene(0).from_asset("models/{model}.glb"))',
    }, separators=(",", ":"))


def capture_view(name: str) -> str:
    """Render a 2x2 contact sheet of the model (vision models only)."""
    from .. import capture

    model = _clean_name(name)
    coll = _model_collection(model, create=False)
    parts = model_parts(coll)[0] if coll is not None else []
    if not parts:
        raise ToolError(f"model {model!r} has no parts to capture")
    lo = [min(p.lo[i] for p in parts) for i in range(3)]
    hi = [max(p.hi[i] for p in parts) for i in range(3)]
    center = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
    radius = max(math.dist(lo, hi) / 2, 0.5)
    path = capture.capture_contact_sheet(["iso", "front", "side", "top"], 512,
                                         "MATERIAL", center, radius)
    return json.dumps({"ok": True, "image": path,
                       "note": "the 2x2 sheet (iso/front/side/top) is attached to "
                               "your next message; check it against the request"},
                      separators=(",", ":"))


def finish(summary: str) -> str:
    """End the task (the agent loop stops after this call)."""
    return str(summary)


# ------------------------------------------------------------------ registration

_KIT_DOC = """\
Build or REBUILD a whole 3D model from one Python script. The model's
collection is wiped first, then the FULL script runs, so always send the
complete script (never a diff). Units: meters, Z up, ground z=0, front
faces -Y. In scope: mk, bpy, bmesh, math, Vector, Matrix.
mk API (all return the Blender object; mat = palette key or mk.mat(...)):
  mk.box(name, size=(x,y,z), at=(x,y,z), mat=, rot=(deg,deg,deg), anchor="bottom"|"center")
  mk.cylinder(name, radius, height, at, verts=24, axis="Z"|"X"|"Y", mat=)
  mk.cone(name, radius_bottom, radius_top, height, at, verts=24, axis=, mat=)
  mk.tube(name, radius, thickness, height, at, verts=32, axis=, mat=)   # hollow
  mk.sphere(name, radius, at, segments=24, rings=12, hemi=False, mat=)  # hemi = dome
  mk.torus(name, radius, thickness, at, segments=24, sides=8, axis=, mat=)
  mk.profile(name, points=[(u,v),...], depth, at, plane="XZ"|"XY", mat=)
      XZ: front outline (x,z) extruded along Y; XY: footprint extruded up
  mk.stairs(name, width, height, length, steps, at, mat=)  # rises toward +Y
  mk.cut(target, *cutters)  mk.union(target, *others)     # booleans, baked
  mk.join(name, *objs)  mk.bevel(obj, width=0.03, segments=2)  mk.smooth(obj)
  mk.copy(obj, name, at, rot=)  mk.repeat(obj, count, step=(x,y,z))
  mk.radial(obj, count, center=(x,y,z))  mk.mirror(obj, axis="X")
  mk.group(name, *children, at=)  mk.delete(obj)
  mk.mat(name, color="#rrggbb", metallic=0, roughness=0.7, emission=0)
at = bottom-center of the part (anchor="bottom", default) or its center.
Palette: """ + ", ".join(PALETTE) + """.
Returns a report: parts, tris, sizes, and issues (e.g. disconnected parts)."""

register(
    "build_model", _KIT_DOC,
    {"type": "object", "properties": {
        "name": {"type": "string", "description": "Model name, e.g. 'Bunker' (one collection)"},
        "code": {"type": "string", "description": "The COMPLETE build script using mk"},
    }, "required": ["name", "code"]},
    build_model, approval="code",
)
register(
    "read_script",
    "Return the current build script of a model (to edit it and rebuild).",
    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    read_script,
)
register(
    "get_scene_state",
    "List models (parts, tris, size) and other scene objects.",
    {"type": "object", "properties": {}},
    get_scene_state,
)
register(
    "export_glb",
    "Export a finished model as <name>.glb for the Bevy engine (glTF 2.0, Y-up, "
    "meters, materials). Only when the user asks for an export or file.",
    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    export_glb,
)
register(
    "capture_view",
    "Render the model from iso/front/side/top into one image you will see next turn.",
    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    capture_view, vision=True,
)
register(
    "finish",
    "Call ONCE when the model fully matches the request. The summary is shown to the "
    "user as your final answer (what was built, size, tris, parts). Ends the task.",
    {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
    finish,
)
