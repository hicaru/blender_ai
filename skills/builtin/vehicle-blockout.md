+++
name = "vehicle-blockout"
description = "Car/truck blockout: body volumes + separate wheels, mirrored on X, 5-40k tris."
triggers = ["car", "vehicle", "truck", "van", "bus", "cart", "wagon", "chassis"]
category = "vehicles"
tri_budget = [5000, 40000]
color_mode = "vertex_color"
+++

## Parts
| name | role | primitive | description |
|---|---|---|---|
| SM_Car_Body | body | cube + inset/extrude | Cabin + hood silhouette |
| SM_Car_Wheel_FL/FR/RL/RR | wheel | cylinder (16-24 verts) | SEPARATE objects (engine rigging) |
| SM_Car_Bumper | trim | thin cube | Optional |

## Steps
1. Body: cube [4.5,1.8,1.5], origin bottom-center; scale_faces/extrude to cabin+hood.
2. Wheels: cylinder radius ~0.32 m, width 0.2, rotated 90 deg on Y, 4 copies at
   (+/-0.75 x, +/-0.8 y, 0.32 z). Wheels stay SEPARATE objects, parented to body.
3. Mirror asymmetric details with MIRROR X (use_clip, merge 0.001), apply at finalize.
4. Windows: inset + slight extrude on the cabin faces, color glass.
5. set_shading smooth_by_angle 30. validate_asset -> capture_view ["iso","side"] -> export.

## Verify
- Wheels touch ground (z = radius), body clearance ~0.15-0.25 m.
- Symmetric in the side capture.

## Pitfalls
- Wheels INSIDE the body volume look fine until the engine adds suspension travel:
  keep wheel arches cut (boolean) or wheels slightly proud.
- One joined wheel mesh breaks per-wheel spin in engine: 4 separate objects.
