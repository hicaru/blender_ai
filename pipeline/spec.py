"""AssetSpec: the brief. Stored as a JSON string custom property on the
asset's Collection (``coll["blender_ai_asset"]``).

Why a collection: one asset equals one collection — the natural export
unit, the natural home for LOD/collision siblings, saved inside the
.blend, and several assets can coexist in one file.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Final

__all__ = (
    "SPEC_KEY",
    "TRI_BUDGET",
    "AssetClass",
    "AssetSpec",
    "ColorMode",
    "Engine",
    "Style",
)


class Engine(StrEnum):
    GLTF = "gltf"
    GODOT = "godot"
    UNITY = "unity"
    UNREAL = "unreal"


class Style(StrEnum):
    LOWPOLY = "lowpoly"        # faceted, vertex colors, 50-2k tris
    STYLIZED = "stylized"      # smooth + bevels, simple materials
    REALISTIC = "realistic"    # PBR materials, UVs mandatory, higher budget


class ColorMode(StrEnum):
    VERTEX_COLOR = "vertex_color"  # one shared material, colors in 'Col' attribute
    MATERIALS = "materials"        # one Principled material per color/part


class AssetClass(StrEnum):
    SMALL_PROP = "small_prop"
    PROP = "prop"
    HERO_PROP = "hero_prop"
    ENVIRONMENT = "environment"
    MODULAR_PIECE = "modular_piece"
    VEHICLE = "vehicle"
    CHARACTER = "character"


# Default triangle budgets for LOD0 (min, max) — typical current-gen
# PC/console ranges; low-poly style uses the first number x 0.25.
TRI_BUDGET: Final[dict[AssetClass, tuple[int, int]]] = {
    AssetClass.SMALL_PROP: (100, 1_000),
    AssetClass.PROP: (500, 5_000),
    AssetClass.HERO_PROP: (5_000, 20_000),
    AssetClass.ENVIRONMENT: (1_000, 15_000),
    AssetClass.MODULAR_PIECE: (50, 2_000),
    AssetClass.VEHICLE: (5_000, 40_000),
    AssetClass.CHARACTER: (5_000, 40_000),
}

SPEC_KEY: Final = "blender_ai_asset"  # Collection custom property


def _pair(raw: object, typ: type[int]) -> tuple[int, int] | None:
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError(f"expected a pair [min, max], got {raw!r}")
    return (typ(raw[0]), typ(raw[1]))


def _triple(raw: object) -> tuple[float, float, float] | None:  # already typed
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ValueError(f"expected a triple [x, y, z], got {raw!r}")
    return (float(raw[0]), float(raw[1]), float(raw[2]))


@dataclass(slots=True, frozen=True)
class AssetSpec:
    name: str
    description: str
    asset_class: AssetClass
    engine: Engine = Engine.GLTF
    style: Style = Style.LOWPOLY
    color_mode: ColorMode = ColorMode.VERTEX_COLOR
    tri_budget: tuple[int, int] | None = None
    size_m: tuple[float, float, float] | None = None
    lods: tuple[float, ...] = (1.0, 0.5, 0.25)
    collision: bool = True
    skills: tuple[str, ...] = field(default_factory=tuple)

    @property
    def budget(self) -> tuple[int, int]:
        if self.tri_budget is not None:
            return self.tri_budget
        low, high = TRI_BUDGET[self.asset_class]
        return (low // 4, high // 4) if self.style is Style.LOWPOLY else (low, high)

    def collection_name(self) -> str:
        return f"SM_{self.name}"

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> AssetSpec:
        data = json.loads(raw)
        return cls(
            name=str(data["name"]),
            description=str(data["description"]),
            asset_class=AssetClass(data["asset_class"]),
            engine=Engine(data.get("engine", Engine.GLTF)),
            style=Style(data.get("style", Style.LOWPOLY)),
            color_mode=ColorMode(data.get("color_mode", ColorMode.VERTEX_COLOR)),
            tri_budget=_pair(data.get("tri_budget"), int),
            size_m=_triple(data.get("size_m")),
            lods=tuple(map(float, data.get("lods", (1.0, 0.5, 0.25)))),
            collision=bool(data.get("collision", True)),
            skills=tuple(map(str, data.get("skills", ()))),
        )
