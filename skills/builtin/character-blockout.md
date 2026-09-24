+++
name = "character-blockout"
description = "Humanoid proportion blockout (8 heads): capsule volumes, mirrored, no rigging."
triggers = ["character", "humanoid", "npc", "human", "mannequin", "player"]
category = "characters"
tri_budget = [5000, 40000]
color_mode = "vertex_color"
+++

## Proportions (1.8 m total = 8 heads of 0.225 m)
- Head 0.225; chin->shoulders 0.225; shoulders->nipple 0.225; ->navel 0.225;
  ->crotch 0.225 (half height); crotch->mid-thigh 0.225; ->knee ~0.45 total;
  knee->floor ~0.45. Shoulder width ~2 head heights; total arm span ~ height.

## Parts
| name | role | primitive | description |
|---|---|---|---|
| SM_Char_Torso | body | cube/cylinder | Chest+pelvis volume |
| SM_Char_Head | body | sphere-ish | At 1.575 m center |
| SM_Char_Arm_L/R | body | scaled cubes | Pivot at shoulder |
| SM_Char_Leg_L/R | body | scaled cubes | Origin at hip |

## Steps
1. set_asset_spec class="character", size_m=[0.6,0.35,1.8] (A-pose width).
2. Blockout volumes per the proportion table, origin bottom for the root.
3. Mirror L/R: model one arm/leg, duplicate with mirrored X location (or MIRROR modifier).
4. NO rigging/skinning — out of scope; separate limbs for engine rigging.
5. validate_asset -> capture_view ["front","side"] -> export.

## Verify
- Height exactly 1.8 m; head/body ratio passes the eyeball test in the captures.

## Pitfalls
- Detail (fingers, face) before proportion sign-off is wasted work.
- Joined limbs cannot be rigged per-bone: keep L/R/limbs separate objects.
