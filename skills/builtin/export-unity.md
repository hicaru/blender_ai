+++
name = "export-unity"
description = "FBX export settings for Unity: -Z forward, Y up, FBX_SCALE_UNITS, _Collider naming."
triggers = ["unity"]
category = "pipeline"
+++

## Settings (automatic via set_asset_spec engine="unity" + export_asset)
- FBX, apply_unit_scale + FBX_SCALE_UNITS, forward -Z, up Y, FACE smoothing.
- Leaf bones off; custom properties exported.
- Collision: <Name>_Collider objects; add MeshCollider in the editor.
- LODs: _LOD0/_LOD1 suffixes auto-build LOD Groups on FBX import.

## Steps
1. set_asset_spec engine="unity" (once).
2. validate_asset: 0 FAIL (scale.applied is critical for Unity).
3. export_asset -> exports/<Name>.fbx.
4. Unity import: Convert Units ON, Bake Axis Conversion ON if odd transforms appear.

## Pitfalls
- Blender's Z-up leaks as a 90-degree rotation when apply_unit_scale is off — the
  preset handles it; do not hand-tweak axis settings per export.
- Vertex colors: Unity's standard shader ignores COLOR_0; use URP/HDRP
  Vertex Color or a custom shader.
