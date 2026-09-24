# Blender AI

An AI agent inside Blender with one job: **build 3D models for games made with
[Bevy](https://bevyengine.org)**. Describe the model in plain language ("a
silo bunker with an underground shaft and a blast door"). The agent plans the
parts, writes **one Python build script** with the `mk` modeling kit, runs it,
reads the build report, and improves the script until the model matches.
It can then export a Bevy-ready `.glb`.

## Why build scripts

LLMs place parts coherently when **one script holds every coordinate**. Each
part is positioned from shared variables, and every rebuild re-runs the whole
script from an empty collection, so iterations are deterministic. Building
with many small tool calls lost that frame of reference and produced parts
scattered in a row instead of one building.

## Features

- **`mk` modeling kit**: `box`, `cylinder`, `cone`, `tube` (hollow), `sphere`
  (`hemi` domes), `torus`, `profile` (extruded outlines: arches, frames, floor
  plans), `stairs`, booleans (`cut`, `union`, baked immediately), `bevel`,
  `smooth`, `join`, linked `copy` / `repeat` / `radial` (one mesh, many
  instances in Bevy), `mirror`, `group` (parent entity, e.g. a door to
  animate). Every part gets a material from a PBR palette (`concrete`,
  `steel`, `rust`, `hazard_yellow`, emissive `light_*`, …).
- **Build report after every run**: parts, triangle counts, sizes, and
  issues. The key issue is **disconnected groups**: parts that float apart
  from the main body.
- **Bevy export**: `export_glb` writes glTF 2.0 (Y-up, meters, Principled →
  `StandardMaterial`, emission → emissive, names → `Name`, groups → child
  entities).
- **Reliable loop**: when the model stops mid-build with text instead of a tool
  call, it gets a bounded "continue" nudge. `finish` ends the task, and is
  refused when a call in the same batch failed. Dropped connections are
  retried. The user's task message is never trimmed from the history, and
  older scripts are compacted.
- **Chat-only panel**: approvals and questions show at the **top** of the
  panel, so a waiting agent never looks stopped.
- **Images, zero setup**: attach from a file, the clipboard, or by
  drag-and-drop. Models with image input (for example `deepseek-flash`,
  `glm-4.6v`) see images directly, including their own renders from
  `capture_view`. A text-only model gets a caption from a vision model picked
  automatically: the same provider first, then any other provider you have a
  key for. If a provider rejects images, the add-on remembers that model and
  switches to captions for it. Model lists are fetched automatically at
  startup and whenever you enter a key.

## Requirements

- Blender **5.2.0+** (extension manifest format).
- An API key for at least one provider (Z.ai, DeepSeek or OpenRouter).
- Network access: the add-on sends chat requests only to the provider you
  configure. File access is for attached images and exported `.glb` files.

## Install

### Option A — install a prebuilt zip

1. Get `blender_ai-<version>.zip` (built as shown below or from a release).
2. In Blender: **Edit → Preferences → Get Extensions → ▾ (top-right) →
   Install from Disk…** and pick the zip.
3. Enable the extension if it is not enabled automatically.

Or from the command line:

```sh
blender --command extension install-file -r user_default -e blender_ai-<version>.zip
```

### Option B — build and install from source

```sh
git clone <this repository>
cd blender_ai

# build the extension zip (honors blender_manifest.toml exclusions)
blender --command extension build --source-dir . --output-dir _work

# install into your user extensions and enable it
blender --command extension install-file -r user_default -e _work/blender_ai-<version>.zip
```

## Setup

1. Open the **AI** tab in the 3D View sidebar (N).
2. In **Add-on Preferences** (Preferences → Get Extensions → Blender AI):
   - pick a **Provider** (`zai`, `deepseek`, `openrouter`) and paste its **API key**;
   - the model list loads by itself; pick a model or leave it empty for the
     provider default (`deepseek-flash` for DeepSeek).
   - **Auto-approve generated code**: build scripts run without a confirmation
     click. Without it, every build waits for **Approve** at the top of the
     panel.
   - **Export directory**: where `<model>.glb` lands. Empty uses an `exports`
     folder next to the .blend.

## Usage

Type what to build and press **Send**. The agent answers with a short plan,
builds, checks the report, rebuilds with fixes, and calls `finish` with a
summary. Ask for changes in plain language, or say "export for Bevy".

In Bevy:

```rust
commands.spawn(SceneRoot(asset_server.load(
    GltfAssetLabel::Scene(0).from_asset("models/Bunker.glb"))));
```

## Debugging

Every event goes to one log file: `<tempdir>/blender_ai_debug.log` (macOS:
`/var/folders/.../T/blender_ai_debug.log`; set `BLENDER_AI_LOG` to change
it). Each line has the time, the event, and JSON details. The events are:
`send` (task text), `spawn` (request size, tools, effort), `worker done`
(content/reasoning length, tool names, finish reason, usage), `tool` (args
preview, result head), `park for user` / `resolved`, `nudge`,
`transient error: retry`, `finish`, `turn end`, `error surfaced`. When the
agent seems stuck, the last line tells you what it is waiting for.

## Development

```sh
python3 -m unittest discover -s tests        # pure-python unit tests
blender --background --factory-startup --python tests/blender_smoke.py
ruff check .
mypy --strict --config-file pyproject.toml -p blender_ai.modelkit -p blender_ai.report \
    -p blender_ai.tools -p blender_ai.executor   # run from the parent dir
```

The smoke test drives the real agent loop against a mock provider and builds
a real silo bunker with the kit, then exports and re-imports the GLB.

## License

GPL-3.0-or-later (see `blender_manifest.toml`).
