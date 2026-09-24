+++
name = "terrain-tile"
description = "Ground tiles: subdivided grid + DISPLACE + vertex colors by height, 0.5-15k tris."
triggers = ["terrain", "ground", "hill", "mud", "dirt", "landscape", "tile", "cliff", "path"]
category = "environment"
tri_budget = [500, 15000]
color_mode = "vertex_color"
+++

## Parts
| name | role | primitive | description |
|---|---|---|---|
| SM_Terrain_Tile | body | grid 2x2 m | Displaced heightfield, edges flat for tiling |

## Steps
1. create_primitive grid size 2x2 m, then mesh_op subdivide cuts=16-32 (or a
   SUBSURF SIMPLE modifier for square quads).
2. add_modifier DISPLACE with CLOUDS texture (size 1.5-3, strength 0.2-0.5).
   Keep tile EDGES flat: displace only a center-selected band (mesh_op select via
   params band 0.1-0.9 in Z does not apply here — use run_python for a vertex-group
   weighted displace if tiling matters).
3. Colors by height (run_python + bmesh): grass below mid, stone_dark above; write into
   the 'Col' attribute.
4. set_shading flat. validate_asset -> capture_view ["iso","top"] -> export_asset.

## Verify
- Tile edges meet neighbors (corners at exact +/-1 m, top capture).
- Height range within the game's walkable slope.

## Pitfalls
- Dense grids explode tris: 32x32 quads = 2048 tris; that is the ceiling for a tile.
- DISPLACE without a texture does nothing (create the texture datablock first).
