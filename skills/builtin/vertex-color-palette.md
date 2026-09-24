+++
name = "vertex-color-palette"
description = "One shared material + 'Col' vertex color attribute: the default low-poly coloring workflow."
triggers = ["vertex", "palette", "flat-color", "toon", "cel"]
category = "material"
+++

## How it works
- ONE material for the whole asset: M_<Asset>_VertexColor, Principled BSDF with
  Base Color wired from a Color Attribute node (layer "Col"), Roughness 0.8.
- Each object's color lives in the 'Col' BYTE_COLOR CORNER attribute AND obj.color
  (so the Solid viewport with color_type=OBJECT shows it without materials).
- The palette tools write both automatically: set_object_info color="iron" or a
  create_primitive call with color="iron".

## Steps
1. Nothing to set up: spec style lowpoly/stylized defaults to vertex_color mode.
2. Give every part a palette key (bark, leaf, iron, stone, cloth_red, ...) or an RGBA
   list in the creating call.
3. No UVs needed for the render; still run uv_unwrap if the engine needs lightmaps.

## Verify
- validate_asset meta.color passes; capture_view shows distinct part colors.

## Pitfalls
- Per-part PBR variation (metal vs cloth roughness) is NOT possible with one shared
  material: use color_mode="materials" instead.
- Renaming objects changes auto-hashed colors only when color was omitted; named keys
  are stable.
