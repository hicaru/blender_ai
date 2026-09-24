"""LOD generation — duplicates with a DECIMATE modifier.

Low-poly style: only [0.5] (decimating faceted art destroys the
silhouette — decided here in one match, not by the model). Godot
generates LODs at import, so its default lods=(1.0,) is set when the
spec is created. Modifiers stay live on the LOD copies (non-destructive
until export); finalize applies them.
"""

from __future__ import annotations

import bpy

from .meta import ensure_meta
from .spec import AssetSpec, Engine, Style

__all__ = ("generate_lods",)


def generate_lods(asset_coll: bpy.types.Collection, spec: AssetSpec,
                  ratios: tuple[float, ...] | None = None) -> list[str]:
    if ratios is None:
        if spec.engine is Engine.GODOT:
            return []  # Godot generates LODs at import — baking them is waste
        ratios = (0.5,) if spec.style is Style.LOWPOLY else tuple(spec.lods[1:])
    created: list[str] = []
    roots = [o for o in asset_coll.objects
             if o.type == "MESH" and not o.hide_render
             and (o.parent is None or o.parent not in list(asset_coll.objects))
             and not o.name.endswith(("_LOD1", "_LOD2", "_LOD3"))]
    for root in roots:
        for i, ratio in enumerate(ratios, start=1):
            name = f"{root.name}_LOD{i}"
            old = bpy.data.objects.get(name)
            if old is not None:
                data = old.data
                bpy.data.objects.remove(old)
                if data and data.users == 0:
                    bpy.data.meshes.remove(data)
            lod_obj = root.copy()
            lod_obj.data = root.data.copy()
            lod_obj.name = name
            for mod in list(lod_obj.modifiers):
                lod_obj.modifiers.remove(mod)
            dec = lod_obj.modifiers.new("Decimate", "DECIMATE")
            dec.decimate_type = "COLLAPSE"
            dec.ratio = ratio
            dec.use_collapse_triangulate = True
            for coll in list(lod_obj.users_collection):
                coll.objects.unlink(lod_obj)
            asset_coll.objects.link(lod_obj)
            ensure_meta(lod_obj, spec.collection_name(),
                        f"LOD{i} of {root.name} (decimate ratio {ratio})", "lod")
            created.append(name)
    return created
