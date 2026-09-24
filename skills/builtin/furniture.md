+++
name = "furniture"
description = "Tables, chairs, shelves, beds: real-world dimension table + panel construction, 50-2k tris."
triggers = ["table", "chair", "shelf", "bed", "sofa", "desk", "stool", "cabinet", "wardrobe", "furniture"]
category = "props"
tri_budget = [50, 2000]
color_mode = "vertex_color"
+++

## Real-world dimensions (meters)
- Table: 0.75 high, 1.2-1.8 x 0.8-0.9; chair seat 0.45 high, 0.45x0.45, back at 0.9.
- Desk 0.75 x 1.4x0.7; bed 0.5 (frame) 2.0x1.6; shelf 1.8-2.1 high, 0.3-0.4 deep.
- Counter/table top thickness 0.03-0.05; legs 0.04-0.07 square.

## Parts
| name | role | primitive | description |
|---|---|---|---|
| SM_<Asset>_Top | body | cube | Top panel, exact height |
| SM_<Asset>_Legs | body | 4 cubes or 1 joined | At inset corners, origin bottom |

## Steps
1. set_asset_spec with size_m from the table above.
2. Top panel cube; legs as 4 thin cubes at inset corners (join_objects into one "Legs"
   object), origin="bottom".
3. Details: inset on the top, apron cubes under the top, bevel 0.005-0.01.
4. Colors: wood_light/wood_dark/cloth_* per part. set_shading flat.
5. validate_asset -> capture_view ["iso","front"] -> export_asset.

## Verify
- Height matches the table (validate size.spec vs spec.size_m).
- Legs vertical, top level (front capture).

## Pitfalls
- Round legs on square tables: cylinders 8-10 verts are fine, but join them for one draw call.
- Forgetting the floor contact: everything starts at z=0 (origin bottom).
