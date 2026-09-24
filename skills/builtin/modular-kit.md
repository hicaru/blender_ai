+++
name = "modular-kit"
description = "Walls, floors, corners on a 2 m grid: pivot at corner, exact footprint, snap-friendly."
triggers = ["modular", "wall", "floor", "corridor", "dungeon", "kit", "grid", "tile", "corner"]
category = "environment"
tri_budget = [50, 2000]
color_mode = "vertex_color"
+++

## Grid law
- Wall module: 2.0 m wide (or 4.0 m), 3.0 m tall, 0.25-0.5 m thick.
- Floor tile: 2x2 m, 0.1 m thick.
- Corner piece: exactly two wall halves meeting at the pivot.
- ALL origins at the grid corner (set_origin corner), so pieces snap by integer offsets.

## Parts
| name | role | primitive | description |
|---|---|---|---|
| SM_Wall_Straight | body | cube 2x0.3x3 | Straight wall segment |
| SM_Wall_Corner | body | two cubes or boolean L | Inner corner, pivot at outer corner |
| SM_Floor_Tile | body | cube 2x2x0.1 | Floor tile |

## Steps
1. set_asset_spec (class modular_piece, size_m of ONE module).
2. Body cubes with exact grid dimensions, origin="bottom".
3. set_origin corner for every piece. Details (trim, cracks) via inset/extrude, kept thin.
4. Keep side faces of walls (engines cull backfaces; rooms are seen from inside).
5. validate_asset -> export_asset. Do NOT join modules: keep one object per module.

## Verify
- Footprint exact (validate size.spec); origin at corner (validate origin.base).

## Pitfalls
- Off-by-1mm dimensions break snapping: use exact numbers, never scale to eyeball.
- Joined modules destroy the grid: modules stay separate objects.
