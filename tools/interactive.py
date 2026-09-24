"""Interactive tools: scene introspection and asking the user."""
# mypy: ignore-errors

import json

import bpy

from . import register


def get_scene_state():
    """Compact JSON snapshot of the scene (bounded observation).

    One call grounds the model: names, tris, dimensions, metadata, colors,
    modifiers, materials, UVs and per-object issues — the same facts the
    prompt's <scene> block and validation use, so they never disagree.
    """
    from ..pipeline import checks, facts

    scene = bpy.context.scene
    context = bpy.context
    state = facts.collect_scene_facts(scene)
    issues = checks.per_object_issues(state)
    for info in state["objects"]:
        info["issues"] = issues.get(info["name"], [])
    active = context.view_layer.objects.active if context.view_layer else None
    out = {
        "mode": context.mode,
        "unit_scale": scene.unit_settings.scale_length,
        "active": active.name if active else None,
        "selected": [o.name for o in context.selected_objects],
        "objects": state["objects"],
        "objects_total": state["objects_total"],
    }
    if state.get("asset") is not None:
        asset = state["asset"]
        spec = asset.get("spec")
        out["asset"] = {
            "collection": asset["asset"],
            "spec": {
                "name": spec.name, "engine": spec.engine.value,
                "style": spec.style.value, "color_mode": spec.color_mode.value,
                "budget": list(spec.budget), "size_m": list(spec.size_m) if spec.size_m else None,
            } if spec else None,
            "tris_total": asset["tris_total"],
        }
    return json.dumps(out, ensure_ascii=False)


def ask_user(question, options=None, allow_free_text=True):
    """Ask the user a question; the answer arrives as the tool result.

    The panel shows the question and option buttons. This call parks the
    agent loop (registry entry has pause=True) — the AI_OT_answer operator
    resumes it with the user's answer as the tool result.
    """
    return "asked the user: %s" % question


def register_tools():
    register(
        "get_scene_state",
        "Inspect the current scene: every object's name, type, triangle count, "
        "dimensions, location, scale, description, role, color, asset, modifiers, "
        "materials, UV layers, per-object issues, plus selection, mode, unit scale "
        "and the active asset spec with its budget. Call this first when the "
        "request depends on scene context — the <scene> block in the prompt is a "
        "summary of exactly this data.",
        {"type": "object", "properties": {}},
        get_scene_state,
    )
    register(
        "ask_user",
        "Ask the user a clarifying question and wait for their answer. "
        "Use when a requirement is ambiguous instead of guessing. Provide "
        "2-4 concrete options when possible; allow_free_text lets the user "
        "type a custom answer.",
        {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "allow_free_text": {"type": "boolean", "default": True},
            },
            "required": ["question"],
        },
        ask_user,
        approval="never",
        pause=True,
    )


register_tools()
