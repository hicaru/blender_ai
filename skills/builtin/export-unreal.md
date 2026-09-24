+++
name = "export-unreal"
description = "FBX export settings for Unreal: -Y forward, Z up, FBX_SCALE_NONE, UCX_/UBX_ naming."
triggers = ["unreal", "ue5", "ue4"]
category = "pipeline"
+++

## Settings (automatic via set_asset_spec engine="unreal" + export_asset)
- FBX, FBX_SCALE_NONE (Unreal imports cm), forward -Y, up Z, FACE smoothing,
  tangents on, custom properties exported.
- Collision: UBX_<Obj>_NN (box) / UCX_<Obj>_NN (convex) in the SAME import batch
  become collision automatically.
- LODs: <Obj>_LOD1... imports as FBX LOD groups.

## Steps
1. set_asset_spec engine="unreal" (once).
2. validate_asset: 0 FAIL (naming + scale are enforced hard by Unreal).
3. export_asset -> exports/<Name>.fbx.
4. Unreal import: Import Uniform Scale 1.0, Combine Meshes OFF for multi-part assets.

## Pitfalls
- Unreal is cm: the FBX_SCALE_NONE preset makes 1 Blender m = 100 uu correctly;
  switching to "FBX_SCALE_UNITS" doubles scales on import.
- UCX prefixes are reserved: never name art objects UCX_/UBX_ by accident.
