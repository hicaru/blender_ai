+++
name = "export-gltf"
description = "Plain glTF/GLB export: the open-standard default for web, engines, and previews."
triggers = ["gltf", "glb", "web", "three", "babylon", "playcanvas"]
category = "pipeline"
+++

## Settings (automatic via set_asset_spec engine="gltf" + export_asset)
- GLB single file, Y-up, +Y forward handled by export_yup, modifiers applied at export,
  tangents on, custom properties as extras.
- Materials: Principled/Image Texture/Normal Map/Color Attribute/Mix(AO)/Emission only.
- Vertex colors export as COLOR_0 when a material input uses the Color Attribute node.

## Steps
1. set_asset_spec engine="gltf" (default).
2. validate_asset: 0 FAIL.
3. export_asset -> exports/<Name>.glb.

## Verify
- Re-import the GLB (run_python with the glTF importer) if the consumer is unknown:
  node names, extras and COLOR_0 should round-trip.

## Pitfalls
- glTF is meters, Y-up: no unit conversion needed; rotation "surprises" come from
  un-applied object rotations (finalize first).
- Multi-material objects cost a draw call per slot; prefer vertex colors + one material.
