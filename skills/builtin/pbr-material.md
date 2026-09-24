+++
name = "pbr-material"
description = "glTF-safe Principled presets per surface type: metal, wood, plastic, glass, cloth."
triggers = ["pbr", "material", "metal", "glass", "roughness", "metallic", "plastic", "cloth"]
category = "material"
+++

## Presets (Principled BSDF inputs)
| surface | Metallic | Roughness | notes |
|---|---|---|---|
| iron/steel | 1.0 | 0.3-0.5 | base color 0.5-0.6 gray |
| gold/copper | 1.0 | 0.25-0.4 | saturated base color |
| wood | 0.0 | 0.6-0.8 | warm base color |
| plastic | 0.0 | 0.35-0.5 | saturated base color |
| glass | 0.0 | 0.05-0.15 | Transmission 1.0, IOR 1.45 |
| cloth | 0.0 | 0.9-1.0 | Sheen 0.3 if available |
| stone/concrete | 0.0 | 0.8-0.95 | desaturated base color |

## glTF safety
- Survives export: Principled BSDF, Image Texture, Normal Map, Color Attribute,
  Mix (for AO), Emission. Everything else is LOST.
- Use create_material / set_material_params with these input names; the exporter
  maps them directly.

## Verify
- validate_asset material.gltf_unsafe passes (no foreign node types).

## Pitfalls
- Setting use_nodes=False on Blender 5.x breaks the material (always node-based).
- Probing inputs by exact name across versions: use set_material_params' fallbacks
  (Emission Color/Emission, Coat Weight/Clearcoat) — never raw dicts.
