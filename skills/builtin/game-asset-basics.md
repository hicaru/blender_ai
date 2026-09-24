+++
name = "game-asset-basics"
description = "Constitution for ALL game assets: real-world scale, naming, pivot, budgets, what engines tolerate."
triggers = ["game", "asset", "engine"]
category = "core"
+++

## Rules (always apply)
1. 1 Blender unit = 1 meter. Reference sizes: door 2.1 m, character 1.8 m, table 0.75 m,
   crate 0.5-1 m, wall module 4x3 m, car 4.5x1.8x1.5 m.
2. Name every object `SM_<Asset>_<Part>` (SM = static mesh). Never leave `Cube.017`.
3. Origin: props at bottom-center, modular pieces at a grid corner, vehicles at ground center.
4. Apply scale before export (finalize). Non-unit scale breaks physics, normals, instancing.
5. Triangle budgets (LOD0, realistic/normal style): small prop 100-1k, prop 0.5-5k,
   hero prop 5-20k, modular piece 50-2k, vehicle/character 5-40k. Low-poly style: a quarter.
6. Modifiers stay live while iterating; only the exporter applies them (export_apply).
7. Every part gets a one-sentence ai_description (what it is, material, function) and a color.
8. Flat shading for low-poly; smooth-by-angle 30 degrees for hard-surface.
9. Validate -> capture_view -> fix -> export. Never say "done" without both.

## Verify
- validate_asset: 0 FAIL. capture_view: silhouette reads correctly.

## Pitfalls
- Modeling detail on wrong proportions: blockout dimensions FIRST, details later.
- Segment counts are the budget killer: cylinder 12 verts = ~24 tris rings; decide before creating.
