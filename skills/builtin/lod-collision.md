+++
name = "lod-collision"
description = "Decimate ratios per engine, UCX/UBX/-col naming, what Unity/Unreal/Godot auto-generate."
triggers = ["lod", "collision", "collider", "decimate", "performance", "physics"]
category = "pipeline"
+++

## LODs
- generate_lods creates _LOD1/_LOD2... duplicates with DECIMATE (collapse + triangulate).
- Unity: FBX with _LOD0/_LOD1 suffixes auto-builds a LOD Group.
- Unreal: FBX LOD groups import natively.
- Godot: generates LODs at import itself -> spec lods=(1.0,), skip generate_lods.
- Low-poly style: ONE extra LOD at 0.5 max; decimating faceted art destroys silhouettes.

## Collision
- make_collision box -> engine-correct naming automatically:
  - Unreal: UBX_<Obj>_NN (box), UCX_<Obj>_NN (convex). Prefixes are reserved words.
  - Godot: <Obj>-colonly (static trimesh), <Obj>-convcolonly (convex).
  - Unity: <Obj>_Collider (add MeshCollider component in the editor).
- Proxies: wireframe display, hidden from render, ai_role="collision" — validate and
  export include them, tri counts do not.

## Verify
- validate_asset passes with proxies present; export includes them (objects count).

## Pitfalls
- Collision proxies made from DECIMATED art still blow physics budgets: box or convex
  for anything that is not visible-clip.
- Renaming proxies breaks engine prefixes: create them via make_collision, not rename.
