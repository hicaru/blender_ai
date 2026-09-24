+++
name = "export-godot"
description = "GLB export settings for Godot 4: Y-up, extras, vertex colors, -colonly naming."
triggers = ["godot"]
category = "pipeline"
+++

## Settings (automatic via set_asset_spec engine="godot" + export_asset)
- Format GLB (single file), Y-up, modifiers applied at export, custom props as extras.
- Vertex colors export as COLOR_0 (material uses the Color Attribute node).
- Collision: <Name>-colonly / -convcolonly object names become StaticBody/ConcavePolygon
  at import.
- LODs: Godot generates them at import; do not bake your own.

## Steps
1. set_asset_spec engine="godot" (once).
2. validate_asset: 0 FAIL.
3. export_asset -> exports/<Name>.glb.
4. In Godot: import as scene; check "Mesh > Ensure Animation/Extras" as needed.

## Pitfalls
- Non-unit scale imports as warnings in Godot: finalize first.
- Godot reads extras into node metadata: keep ai_description short and useful.
