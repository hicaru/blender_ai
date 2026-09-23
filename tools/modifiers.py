"""Modifier tools: add / configure / remove / apply / list."""

import bpy

from . import ToolError, register

# Friendly name -> Blender modifier type (Modify/Generate/Deform categories).
_MODIFIER_TYPES = {
    "subdivision": 'SUBSURF',
    "bevel": 'BEVEL',
    "mirror": 'MIRROR',
    "solidify": 'SOLIDIFY',
    "array": 'ARRAY',
    "boolean": 'BOOLEAN',
    "remesh": 'REMESH',
    "smooth": 'SMOOTH',
    "shrinkwrap": 'SHRINKWRAP',
    "weld": 'WELD',
}

_INTERNAL_PROPS = {
    "rna_type", "name", "type", "show_expanded", "use_pin_to_last",
    "is_active", "ui_type",  # defensive; filtered by trait checks below
}


def _get_object(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ToolError("no object named %r" % name)
    return obj


def _apply_settings(mod, settings, applied, skipped):
    for key, value in settings.items():
        if key.startswith("_") or not hasattr(mod, key):
            skipped.append(key)
            continue
        try:
            setattr(mod, key, value)
            applied.append(key)
        except (TypeError, ValueError, AttributeError):
            skipped.append(key)


def add_modifier(object, type, **settings):
    """Add a modifier. Known types: subdivision, bevel, mirror, solidify,
    array, boolean, remesh, smooth, shrinkwrap, weld. Extra keyword
    arguments are applied as modifier RNA attributes (e.g.
    ``levels=2`` for subdivision, ``width=0.05`` + ``segments=3`` for bevel,
    ``mirror_object``/``use_axis_x`` for mirror)."""
    obj = _get_object(object)
    mod_type = _MODIFIER_TYPES.get(str(type).lower())
    if mod_type is None:
        raise ToolError(
            "unknown modifier type %r; use one of: %s"
            % (type, ", ".join(sorted(_MODIFIER_TYPES)))
        )
    mod = obj.modifiers.new(name=type, type=mod_type)
    applied, skipped = [], []
    _apply_settings(mod, settings, applied, skipped)
    note = ""
    if skipped:
        note = " (ignored settings: %s)" % ", ".join(sorted(skipped))
    return "added %s modifier %r to %r%s" % (type, mod.name, obj.name, note)


def set_modifier_params(object, modifier, params):
    obj = _get_object(object)
    mod = obj.modifiers.get(modifier)
    if mod is None:
        raise ToolError("%r has no modifier named %r" % (object, modifier))
    applied, skipped = [], []
    _apply_settings(mod, params, applied, skipped)
    note = ""
    if skipped:
        note = " (ignored: %s)" % ", ".join(sorted(skipped))
    return "set params on %r%s" % (modifier, note)


def remove_modifier(object, modifier):
    obj = _get_object(object)
    mod = obj.modifiers.get(modifier)
    if mod is None:
        raise ToolError("%r has no modifier named %r" % (object, modifier))
    obj.modifiers.remove(mod)
    return "removed modifier %r from %r" % (modifier, object)


def apply_modifier(object, modifier):
    obj = _get_object(object)
    if obj.modifiers.get(modifier) is None:
        raise ToolError("%r has no modifier named %r" % (object, modifier))
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.modifier_apply(modifier=modifier)
    except RuntimeError as exc:
        raise ToolError("apply failed: %s" % exc)
    return "applied modifier %r on %r" % (modifier, object)


def list_modifiers(object):
    """Return a JSON string describing an object's modifiers and key params."""
    import json
    obj = _get_object(object)
    result = []
    for mod in obj.modifiers:
        params = {}
        for prop in mod.bl_rna.properties:
            if prop.identifier in _INTERNAL_PROPS or prop.is_readonly:
                continue
            if prop.type in {'INT', 'FLOAT', 'BOOLEAN', 'ENUM'}:
                params[prop.identifier] = getattr(mod, prop.identifier)
        result.append({"name": mod.name, "type": mod.type, "params": params})
    return json.dumps(result, ensure_ascii=False)


def register_tools():
    register(
        "add_modifier",
        "Add a modifier to an object. type is one of: subdivision, bevel, "
        "mirror, solidify, array, boolean, remesh, smooth, shrinkwrap, weld. "
        "Pass settings as extra keyword arguments matching Blender modifier "
        "attributes (e.g. subdivision: levels, render_levels, viewport; "
        "bevel: width, segments, angle_limit; mirror: use_axis_x/y/z, "
        "mirror_object; array: count, relative_offset_displace; "
        "boolean: object, operation; weld: merge_threshold).",
        {
            "type": "object",
            "properties": {
                "object": {"type": "string"},
                "type": {"type": "string", "enum": sorted(_MODIFIER_TYPES)},
            },
            "required": ["object", "type"],
            "additionalProperties": True,
        },
        add_modifier,
    )
    register(
        "set_modifier_params",
        "Change parameters of an existing modifier.",
        {
            "type": "object",
            "properties": {
                "object": {"type": "string"},
                "modifier": {"type": "string",
                             "description": "Modifier name"},
                "params": {"type": "object",
                           "description": "Attribute -> value map"},
            },
            "required": ["object", "modifier", "params"],
        },
        set_modifier_params,
    )
    register(
        "remove_modifier",
        "Remove a modifier from an object.",
        {
            "type": "object",
            "properties": {
                "object": {"type": "string"},
                "modifier": {"type": "string"},
            },
            "required": ["object", "modifier"],
        },
        remove_modifier,
    )
    register(
        "apply_modifier",
        "Apply (bake) a modifier into the mesh. Object becomes active "
        "automatically.",
        {
            "type": "object",
            "properties": {
                "object": {"type": "string"},
                "modifier": {"type": "string"},
            },
            "required": ["object", "modifier"],
        },
        apply_modifier,
    )
    register(
        "list_modifiers",
        "List an object's modifiers with their parameters (JSON).",
        {
            "type": "object",
            "properties": {"object": {"type": "string"}},
            "required": ["object"],
        },
        list_modifiers,
    )


register_tools()
