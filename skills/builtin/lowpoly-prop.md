+++
name = "lowpoly-prop"
description = "Crates, barrels, chests, lanterns: blocky props from primitives + bevels, vertex colors, 50-600 tris."
triggers = ["crate", "barrel", "chest", "box", "prop", "lantern", "bucket", "vase", "pot"]
category = "props"
tri_budget = [50, 600]
color_mode = "vertex_color"
+++

## Parts
| name | role | primitive | description |
|---|---|---|---|
| SM_<Asset>_Body | body | cube / cylinder | Main volume, real-world size, origin bottom |
| SM_<Asset>_Trim | trim | thin cube/torus | Bands, edges, lid rim |
| SM_<Asset>_Handle | handle | torus segment | Optional |

## Steps
1. set_asset_spec (class prop or small_prop, style lowpoly).
2. Body: create_primitive with dimensions in meters, origin="bottom", vertices 8-12.
3. Shape details with mesh_op: inset top face (thickness ~5% of width), extrude for lids,
   scale_faces on a Z band for barrel bulge (factor 1.05-1.12).
4. Trim: thin torus (major_segments 12, minor 4) or thin cubes; parent to body.
5. set_shading flat. Colors from palette: wood_light/bark for wood, iron for bands.
6. validate_asset -> capture_view ["iso","front"] -> export_asset.

## Verify
- Silhouette readable; trim sits flush (no z-fighting, no floating).
- Tris in budget; flat shading everywhere.

## Pitfalls
- Bevel on low-poly with width > 0.05 m looks melted on small props.
- Torus minor_segments > 6 triples tris for no visible gain.
