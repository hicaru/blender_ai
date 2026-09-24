+++
name = "lowpoly-tree"
description = "Stylized low-poly tree: tapered trunk + 1-3 ico-sphere foliage clusters, 150-600 tris, vertex colors."
triggers = ["tree", "pine", "oak", "birch", "forest", "foliage", "bush", "shrub"]
category = "nature"
tri_budget = [150, 600]
color_mode = "vertex_color"
+++

## Parts
| name | role | primitive | description |
|---|---|---|---|
| SM_Tree_Trunk | trunk | cylinder (8 verts) | Tapered trunk, base at z=0 |
| SM_Tree_Crown_A | foliage | ico_sphere (subdiv 1) | Main foliage mass |
| SM_Tree_Crown_B | foliage | ico_sphere (subdiv 1) | Optional secondary mass |

## Steps
1. create_primitive cylinder, vertices=8, dimensions [0.35,0.35,2.0], origin="bottom",
   location [0,0,0]. Color bark.
2. mesh_op scale_faces selector=top factor=0.6 (taper).
3. create_primitive ico_sphere, subdivisions=1, dimensions [2.2,2.2,1.8], z~2.6. Color leaf.
   add_modifier DISPLACE (strength 0.25, texture CLOUDS size 0.6) for irregular silhouette.
4. duplicate crown with offset for variety (optional third, smaller, higher).
5. Parent crowns to trunk. set_shading flat (low-poly = faceted).
6. validate_asset -> capture_view ["iso"] -> export_asset.

## Verify
- Silhouette readable at 10 m (iso view).
- Crown overlaps trunk top by >= 0.1 m (no floating).
- Tris within budget.

## Pitfalls
- DISPLACE strength > 0.4 on subdiv-1 ico spheres creates self-intersections.
- Do NOT subdivide foliage; budget blows up 4x per level.
- Smooth shading on foliage destroys the low-poly look.
