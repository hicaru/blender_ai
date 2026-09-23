"""Material tools: create Principled BSDF materials, tweak inputs, assign."""

import json

import bpy

from . import ToolError, register

# Principled BSDF numeric/scalar inputs exposed to the agent (subset that is
# well-defined across Blender 4.x/5.x). Color inputs accept [r,g,b,a].
_KNOWN_INPUTS = {
    "Base Color", "Metallic", "Roughness", "IOR", "Alpha",
    "Emission Color", "Emission Strength",
    "Specular IOR Level", "Coat Weight", "Coat Roughness",
    "Sheen Weight", "Transmission Weight",
}


def _get_object(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ToolError("no object named %r" % name)
    return obj


def _principled(mat):
    if not mat.use_nodes or mat.node_tree is None:
        raise ToolError("material %r has no node tree" % mat.name)
    for node in mat.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            return node
    raise ToolError("material %r has no Principled BSDF node" % mat.name)


def _set_input(node, name, value):
    socket = node.inputs.get(name)
    if socket is None:
        raise ToolError("unknown Principled input %r" % name)
    try:
        socket.default_value = value
    except (TypeError, ValueError):
        raise ToolError("invalid value %r for input %r" % (value, name))


def create_material(name, base_color_rgba=None, metallic=None, roughness=None,
                    emission_strength=None):
    """Create (or reuse) a material with a Principled BSDF."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    node = _principled(mat)
    if base_color_rgba is not None:
        _set_input(node, "Base Color", base_color_rgba)
    if metallic is not None:
        _set_input(node, "Metallic", metallic)
    if roughness is not None:
        _set_input(node, "Roughness", roughness)
    if emission_strength is not None:
        _set_input(node, "Emission Strength", emission_strength)
    return "created material %r" % mat.name


def set_material_params(material, params):
    """Set Principled BSDF inputs on a material.

    Known inputs: Base Color [r,g,b,a], Metallic, Roughness, IOR, Alpha,
    Emission Color, Emission Strength, Specular IOR Level, Coat Weight,
    Coat Roughness, Sheen Weight, Transmission Weight.
    """
    mat = bpy.data.materials.get(material)
    if mat is None:
        raise ToolError("no material named %r" % material)
    node = _principled(mat)
    applied, skipped = [], []
    for key, value in params.items():
        try:
            _set_input(node, key, value)
            applied.append(key)
        except ToolError:
            skipped.append(key)
    note = " (unknown inputs ignored: %s)" % ", ".join(sorted(skipped)) if skipped else ""
    return "set %d params on material %r%s" % (len(applied), mat.name, note)


def assign_material(object, material, slot=None):
    obj = _get_object(object)
    mat = bpy.data.materials.get(material)
    if mat is None:
        raise ToolError("no material named %r" % material)
    if slot is None:
        obj.data.materials.append(mat)
        return "assigned material %r to %r (new slot)" % (mat.name, obj.name)
    slot = int(slot)
    if slot < 0 or slot >= len(obj.material_slots):
        raise ToolError("slot %d out of range (object has %d)"
                        % (slot, len(obj.material_slots)))
    obj.material_slots[slot].material = mat
    return "assigned material %r to %r slot %d" % (mat.name, obj.name, slot)


def list_materials(object):
    obj = _get_object(object)
    return json.dumps(
        [slot.material.name if slot.material else None
         for slot in obj.material_slots],
        ensure_ascii=False,
    )


def register_tools():
    register(
        "create_material",
        "Create a material with a Principled BSDF (reuses an existing "
        "material with the same name). base_color_rgba is [r,g,b,a] in 0..1.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "base_color_rgba": {
                    "type": "array", "items": {"type": "number"},
                    "minItems": 3, "maxItems": 4,
                },
                "metallic": {"type": "number", "minimum": 0, "maximum": 1},
                "roughness": {"type": "number", "minimum": 0, "maximum": 1},
                "emission_strength": {"type": "number", "minimum": 0},
            },
            "required": ["name"],
        },
        create_material,
    )
    register(
        "set_material_params",
        "Set Principled BSDF inputs on a material. Keys are socket names "
        "(Base Color, Metallic, Roughness, Emission Strength, ...).",
        {
            "type": "object",
            "properties": {
                "material": {"type": "string"},
                "params": {"type": "object",
                           "description": "Socket name -> value"},
            },
            "required": ["material", "params"],
        },
        set_material_params,
    )
    register(
        "assign_material",
        "Assign a material to an object (appends a new slot, or uses the "
        "given slot index).",
        {
            "type": "object",
            "properties": {
                "object": {"type": "string"},
                "material": {"type": "string"},
                "slot": {"type": "integer"},
            },
            "required": ["object", "material"],
        },
        assign_material,
    )
    register(
        "list_materials",
        "List material slot names of an object (JSON array).",
        {
            "type": "object",
            "properties": {"object": {"type": "string"}},
            "required": ["object"],
        },
        list_materials,
    )


register_tools()
