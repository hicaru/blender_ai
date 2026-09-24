"""Export plans per engine.

GLB is the default: open standard, one file, materials + vertex colors +
extras survive, Godot-native, Unity/Unreal via official importers. FBX
only when the engine preset asks for it. Non-destructive until export:
export_apply bakes modifiers in the exporter without touching the scene.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from .spec import Engine

__all__ = ("ExportPlan", "plan_for")


@dataclass(slots=True, frozen=True)
class ExportPlan:
    operator: Literal["gltf", "fbx"]
    ext: Literal[".glb", ".fbx"]
    kwargs: MappingProxyType  # type: ignore[type-arg]


_COMMON_GLTF: Final[dict[str, object]] = {
    "export_format": "GLB", "use_selection": True, "export_apply": True,
    "export_extras": True, "export_yup": True, "export_tangents": True,
}


def plan_for(engine: Engine) -> ExportPlan:
    match engine:
        case Engine.GLTF | Engine.GODOT:
            return ExportPlan("gltf", ".glb", MappingProxyType(_COMMON_GLTF))
        case Engine.UNITY:
            return ExportPlan("fbx", ".fbx", MappingProxyType({
                "use_selection": True, "use_mesh_modifiers": True,
                "apply_scale_options": "FBX_SCALE_UNITS", "apply_unit_scale": True,
                "axis_forward": "-Z", "axis_up": "Y", "bake_space_transform": True,
                "mesh_smooth_type": "FACE", "add_leaf_bones": False,
                "use_custom_props": True,
            }))
        case Engine.UNREAL:
            return ExportPlan("fbx", ".fbx", MappingProxyType({
                "use_selection": True, "use_mesh_modifiers": True,
                "apply_scale_options": "FBX_SCALE_NONE", "axis_forward": "-Y",
                "axis_up": "Z", "mesh_smooth_type": "FACE", "use_tspace": True,
                "add_leaf_bones": False, "use_custom_props": True,
            }))
    raise ValueError(f"unknown engine {engine!r}")
