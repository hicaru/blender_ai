"""Modifier tools: add / configure / remove / apply / list."""
# mypy: ignore-errors

import json

import bpy

from . import ToolError, register
from .scene import _ensure_object_mode

# Friendly name -> Blender modifier type. Upper-case friendly names match
# Blender's own type ids (one less mapping for the model to learn).
_MODIFIER_TYPES = {
    "SUBSURF": 'SUBSURF',
    "BEVEL": 'BEVEL',
    "MIRROR": 'MIRROR',
    "SOLIDIFY": 'SOLIDIFY',
    "ARRAY": 'ARRAY',
    "BOOLEAN": 'BOOLEAN',
    "REMESH": 'REMESH',
    "SMOOTH": 'SMOOTH',
    "SHRINKWRAP": 'SHRINKWRAP',
    "WELD": 'WELD',
    "DECIMATE": 'DECIMATE',
    "WEIGHTED_NORMAL": 'WEIGHTED_NORMAL',
    "TRIANGULATE": 'TRIANGULATE',
    "SCREW": 'SCREW',
    "SKIN": 'SKIN',
    "DISPLACE": 'DISPLACE',
    "WIREFRAME": 'WIREFRAME',
    "SIMPLE_DEFORM": 'SIMPLE_DEFORM',
    "CURVE": 'CURVE',
    "LATTICE": 'LATTICE',
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


def _resolve_pointer(mod, key, value):
    """Resolve object-name strings for ID-pointer modifier props.

    boolean.object, mirror.mirror_object and shrinkwrap.target are Object
    pointers; setattr with a plain string leaves the pointer None.
    Resolve names to datablocks first.
    """
    if not isinstance(value, str):
        return value
    prop = mod.bl_rna.properties.get(key)
    if (prop is None or prop.type != 'POINTER'
            or prop.fixed_type is None
            or prop.fixed_type.identifier != 'Object'):
        return value
    target = bpy.data.objects.get(value)
    if target is None:
        raise ToolError("no object named %r for %r.%s" % (value, mod.name, key))
    return target


def _apply_settings(mod, settings, applied, skipped):
    for key, value in settings.items():
        if key.startswith("_") or not hasattr(mod, key):
            skipped.append(key)
            continue
        try:
            setattr(mod, key, _resolve_pointer(mod, key, value))
            applied.append(key)
        except ToolError:
            raise
        except (TypeError, ValueError, AttributeError):
            skipped.append(key)


def add_modifier(object, type, params=None, **settings):
    """Add a modifier, optionally setting params in the SAME call.

    type is the game-relevant upper-case enum (BEVEL, MIRROR, DECIMATE,
    WEIGHTED_NORMAL, ...). ``params`` is a dict of modifier RNA attributes
    (e.g. {"width": 0.02, "segments": 2} for BEVEL, {"ratio": 0.5} for
    DECIMATE); legacy keyword arguments are merged with it.
    """
    if params:
        settings = {**settings, **params}
    obj = _get_object(object)
    mod_type = _MODIFIER_TYPES.get(str(type).upper())
    if mod_type is None:
        raise ToolError(
            "unknown modifier type %r; use one of: %s"
            % (type, ", ".join(sorted(_MODIFIER_TYPES)))
        )
    mod = obj.modifiers.new(name=str(type).upper(), type=mod_type)
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
    _ensure_object_mode()
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.modifier_apply(modifier=modifier)
    except RuntimeError as exc:
        raise ToolError("apply failed: %s" % exc) from exc
    return "applied modifier %r on %r" % (modifier, object)


def list_modifiers(object):
    """Return a JSON string describing an object's modifiers and key params."""
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
        "Add a modifier, optionally setting its parameters in the SAME call. "
        "type is one of: BEVEL, MIRROR, SOLIDIFY, ARRAY, BOOLEAN, DECIMATE, "
        "WEIGHTED_NORMAL, TRIANGULATE, REMESH, SUBSURF, WELD, DISPLACE, "
        "SCREW, SKIN, WIREFRAME, SIMPLE_DEFORM, SHRINKWRAP, SMOOTH, CURVE, "
        "LATTICE. Pass a params object with Blender modifier attributes "
        "(e.g. BEVEL: {width: 0.02, segments: 2, limit_method: 'ANGLE', "
        "angle_limit: 0.5236}; DECIMATE: {ratio: 0.5, "
        "use_collapse_triangulate: true}; MIRROR: {use_axis: [true, false, "
        "false], use_clip: true}; WEIGHTED_NORMAL: {keep_sharp: true}; "
        "ARRAY: {count: 3}; DISPLACE needs a texture).\n"
        "Use when: shaping non-destructively (blockout detail, hard-surface "
        "edges, LOD decimation). Stack order matters: MIRROR/ARRAY first, "
        "BEVEL before WEIGHTED_NORMAL, TRIANGULATE last.",
        {
            "type": "object",
            "properties": {
                "object": {"type": "string"},
                "type": {"type": "string", "enum": sorted(_MODIFIER_TYPES)},
                "params": {"type": "object",
                           "description": "Modifier attributes applied right after creation"},
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
