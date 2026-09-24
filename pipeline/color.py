"""Per-object colorizing.

Two modes, set by AssetSpec.color_mode. BOTH always set ``obj.color``,
and the viewport shading is switched to ``color_type='OBJECT'`` when a
session starts, so every part is visibly distinct in Solid mode.

- vertex_color: one shared Principled material whose Base Color comes
  from a 'Col' BYTE_COLOR CORNER attribute -> glTF COLOR_0.
- materials:    one Principled material per part with PBR-ish defaults.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import bpy

from .spec import AssetSpec, ColorMode

if TYPE_CHECKING:
    from .palette import RGBA

__all__ = (
    "apply_object_color",
    "ensure_viewport_object_colors",
    "fill_color_attribute",
    "shared_vertex_color_material",
)

VertexColorNode = "ShaderNodeVertexColor"
_GLTF_SAFE_NODES: frozenset[str] = frozenset({
    "ShaderNodeBsdfPrincipled", "ShaderNodeTexImage", "ShaderNodeNormalMap",
    "ShaderNodeVertexColor", "ShaderNodeAttribute", "ShaderNodeMixRGB",
    "ShaderNodeMix", "ShaderNodeEmission", "ShaderNodeTexCoord",
    "ShaderNodeMapping", "ShaderNodeValue", "ShaderNodeRGB",
    "ShaderNodeOutputMaterial",
})


def _socket(node: bpy.types.Node, *names: str) -> bpy.types.NodeSocket:
    """Probe a node input by name with fallbacks (Blender renames sockets)."""
    for name in names:
        sock = node.inputs.get(name)
        if sock is not None:
            return sock
    raise KeyError(f"none of {names} found on {node.bl_idname}")


def fill_color_attribute(obj: bpy.types.Object, rgba: RGBA) -> None:
    """Write the color into a 'Col' BYTE_COLOR CORNER attribute."""
    mesh = obj.data
    attr = mesh.color_attributes.get("Col")
    if attr is None or attr.domain != "CORNER" or attr.data_type != "BYTE_COLOR":
        attr = mesh.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
    flat: list[float] = []
    for _ in range(len(mesh.loops)):
        flat.extend(rgba)
    attr.data.foreach_set("color_srgb", flat)


def _asset_of(obj: bpy.types.Object) -> str:
    from .meta import get_asset
    return get_asset(obj) or "Asset"


def shared_vertex_color_material(asset: str) -> bpy.types.Material:
    """One Principled BSDF, Base Color <- Color Attribute 'Col'."""
    name = f"M_{asset}_VertexColor"
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True  # 5.x materials are node-based; idempotent
    nt = mat.node_tree
    assert nt is not None
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    _socket(bsdf, "Base Color").default_value = (1.0, 1.0, 1.0, 1.0)
    _socket(bsdf, "Roughness").default_value = 0.8
    _socket(bsdf, "Metallic").default_value = 0.0
    vcol = nt.nodes.new(VertexColorNode)
    vcol.layer_name = "Col"
    nt.links.new(vcol.outputs[0], _socket(bsdf, "Base Color"))
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    return mat


def part_material(obj: bpy.types.Object, rgba: RGBA) -> bpy.types.Material:
    """One Principled material per part; PBR defaults per dominant hue."""
    asset = _asset_of(obj)
    name = f"M_{asset}_{obj.name.split('_', 2)[-1]}" if obj.name.startswith("SM_") else f"M_{asset}_{obj.name}"
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    assert nt is not None
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    _socket(bsdf, "Base Color").default_value = rgba
    # Cheap hue-based PBR defaults (metallic hues get Metallic 1).
    import colorsys
    hue, _s, _v = colorsys.rgb_to_hsv(*rgba[:3])
    _socket(bsdf, "Metallic").default_value = 1.0 if 0.08 < hue < 0.14 else 0.0
    _socket(bsdf, "Roughness").default_value = 0.4 if _socket(bsdf, "Metallic").default_value else 0.7
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    return mat


def _ensure_slot(obj: bpy.types.Object, mat: bpy.types.Material) -> None:
    if mat.name in obj.data.materials:
        return
    obj.data.materials.clear() if obj.name.startswith("SM_") and mat.name.endswith("_VertexColor") else None
    obj.data.materials.append(mat)


def apply_object_color(
    obj: bpy.types.Object,
    rgba: RGBA,
    mode: ColorMode,
    asset: str = "",
) -> None:
    """Single helper used by every tool that creates or recolors objects."""
    obj.color = rgba
    if obj.type != "MESH":
        return  # empties/curves: viewport color only
    asset = asset or _asset_of(obj)
    match mode:
        case ColorMode.VERTEX_COLOR:
            fill_color_attribute(obj, rgba)
            _ensure_slot(obj, shared_vertex_color_material(asset))
        case ColorMode.MATERIALS:
            _ensure_slot(obj, part_material(obj, rgba))


def ensure_viewport_object_colors() -> None:
    """Switch every 3D viewport to OBJECT color type (restorable by hand)."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        space.shading.type = "SOLID"
                        space.shading.color_type = "OBJECT"


def gltf_unsafe_nodes(mat: bpy.types.Material) -> list[str]:
    """Node types the glTF exporter cannot represent."""
    if not mat.use_nodes or mat.node_tree is None:
        return []
    return sorted({
        n.bl_idname for n in mat.node_tree.nodes
        if n.bl_idname not in _GLTF_SAFE_NODES
    })


def spec_default_color_mode(style: AssetSpec | str) -> ColorMode:
    """Vertex colors for lowpoly/stylized, materials for realistic."""
    style_value = style.style if isinstance(style, AssetSpec) else style
    return ColorMode.MATERIALS if style_value == "realistic" else ColorMode.VERTEX_COLOR
