+++
name = "lowpoly-rock"
description = "Low-poly rocks and boulders: ico spheres deformed by DISPLACE + optional DECIMATE, 30-300 tris."
triggers = ["rock", "stone", "boulder", "pebble", "cliff", "rubble"]
category = "nature"
tri_budget = [30, 300]
color_mode = "vertex_color"
+++

## Parts
| name | role | primitive | description |
|---|---|---|---|
| SM_Rock_Body | body | ico_sphere (subdiv 1-2) | Deformed boulder, origin bottom |

## Steps
1. create_primitive ico_sphere, subdivisions=1 (small) or 2 (large hero rock),
   dimensions per scale, origin="bottom" (then set_origin bottom after scaling).
2. add_modifier DISPLACE: texture CLOUDS, size ~0.5-1.0, strength 0.3-0.5.
3. Optional: add_modifier DECIMATE ratio 0.5-0.7 for harder facets.
4. Non-uniform dimensions for variety ([1.0, 0.8, 0.6] style) BEFORE finalize.
5. Color stone or stone_dark; set_shading flat.
6. validate_asset -> capture_view ["iso"] -> export_asset.

## Verify
- No self-intersections visible in iso capture.
- Sits on the ground (origin at bottom, min z ~ 0).

## Pitfalls
- DISPLACE needs a texture: create a CLOUDS texture datablock first (run_python or
  the texture datablock API) — a missing texture silently does nothing.
- subdivisions=3+ is almost never worth the tris for background rocks.
