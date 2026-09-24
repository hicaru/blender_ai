+++
name = "building-exterior"
description = "House/facade blockout: wall shell + boolean windows + array floors, 1-15k tris."
triggers = ["house", "building", "facade", "shop", "tower", "exterior", "cabin", "barn"]
category = "environment"
tri_budget = [1000, 15000]
color_mode = "vertex_color"
+++

## Parts
| name | role | primitive | description |
|---|---|---|---|
| SM_House_Walls | body | cube shell or extruded footprint | Hollow shell |
| SM_House_Roof | body | prism from cube + simple_deform or manual | Gable or flat |
| SM_House_Windows | detail | boolean cut + frame cubes | Recessed openings |

## Steps
1. Footprint cube (e.g. 8x6x3 m walls, 0.3 m thick via solidify or two cubes).
2. Windows/doors: boolean difference with cutter cubes (keep cutters as wireframe).
3. Frames: thin cubes inset into the openings, role detail.
4. Roof: scale a duplicated top face outward (mesh_op scale_faces on top, factor 1.2) then
   move to apex, or a stretched cube rotated 45 degrees for a gable.
5. Mirror symmetric details with MIRROR modifier on X, apply at finalize.
6. validate_asset -> capture_view ["iso","front"] -> export_asset.

## Verify
- Openings go THROUGH the wall (visible in front + back captures).
- Roof meets walls without floating.

## Pitfalls
- Boolean on non-manifold shells produces holes: solidify the shell first.
- Facade detail on all 4 sides wastes budget: spend it on the visible (front) side.
