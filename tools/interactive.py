"""Interactive tools: scene introspection and asking the user."""

import json

import bpy

from . import register


def get_scene_state():
    """Return a compact JSON snapshot of the scene (bounded observation)."""
    context = bpy.context
    scene = context.scene
    active = context.view_layer.objects.active if context.view_layer else None

    objects = []
    for obj in scene.objects:
        info = {
            "name": obj.name,
            "type": obj.type,
            "location": [round(v, 4) for v in obj.location],
            "modifiers": [m.name for m in obj.modifiers],
            "materials": [s.material.name if s.material else None
                          for s in obj.material_slots],
        }
        if obj.type == 'MESH':
            info["vertices"] = len(obj.data.vertices)
            info["dimensions"] = [round(v, 4) for v in obj.dimensions]
        if obj.parent:
            info["parent"] = obj.parent.name
        objects.append(info)

    state = {
        "mode": context.mode,
        "unit_scale": scene.unit_settings.scale_length,
        "active": active.name if active else None,
        "selected": [o.name for o in context.selected_objects],
        "objects": objects,
    }
    return json.dumps(state, ensure_ascii=False)


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
        "Inspect the current scene: objects (names, types, locations, "
        "vertex counts, modifiers, materials), selection, active object, "
        "mode and unit scale. Call this first when the request depends on "
        "scene context.",
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
