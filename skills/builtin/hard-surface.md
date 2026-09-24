+++
name = "hard-surface"
description = "Bevel + Weighted Normal + smooth-by-angle workflow for weapons, machines, crisp edges."
triggers = ["weapon", "sword", "gun", "robot", "machine", "hard-surface", "blade", "armor", "shield"]
category = "props"
tri_budget = [500, 8000]
color_mode = "vertex_color"
+++

## Parts
| name | role | primitive | description |
|---|---|---|---|
| SM_<Asset>_Body | body | cubes/cylinders | Main silhouette volumes |
| SM_<Asset>_Detail | detail | small cubes/cylinders | Bolts, vents, handles |

## Steps
1. Blockout the whole silhouette from cubes/cylinders FIRST (proportions before detail).
2. Join same-material volumes (join_objects) — one object per material group.
3. add_modifier BEVEL: width 0.01-0.03, segments 1-2, limit ANGLE 30 degrees.
4. add_modifier WEIGHTED_NORMAL keep_sharp=true, weight 50 (order: after bevel).
5. set_shading smooth_by_angle angle=30 (destructive sharp marking, export-clean).
6. validate_asset -> capture_view ["iso","front"] -> export_asset.

## Verify
- Edges catch a highlight (bevel visible in iso capture).
- No shading artifacts at corners.

## Pitfalls
- Bevel width larger than 1/3 of the smallest dimension clamps and looks broken.
- Boolean cuts need merge_by_distance + recalc_normals right after, or bevel breaks.
