# Blender AI

An AI agent that builds **game-ready 3D assets** *inside* Blender. You describe
what you want in plain language ("make a low-poly pine tree for Godot"); the
agent follows a staged pipeline — brief → blockout → shape → color → UV →
validate → LOD/collision → export — calls Blender tools, checks its own work
(numeric validation + a rendered contact sheet), and writes an engine-ready
GLB/FBX with correct naming, origins, and metadata.

Implemented as a native Blender extension (Blender 4.2+ extension manifest,
tested against 5.2).

## Features

- **Asset pipeline with a brief** — `set_asset_spec` pins the target engine
  (glTF/Godot/Unity/Unreal), style, class, triangle budget and real-world size;
  every later decision derives from it.
- **Computed validation, every turn** — deterministic checks (budget, applied
  scale, origin at base, flipped normals, non-manifold/loose geometry, n-gons,
  missing UVs, glTF-unsafe materials, missing descriptions/colors, naming,
  size vs spec) are recomputed from the scene and injected into the prompt with
  exact fix hints; `export_asset` refuses to run while blocking issues remain.
- **Visual self-check** — `capture_view` renders a 2×2 contact sheet
  (Workbench, ~1–2 s) and shows it to the model and you in the panel.
- **Object semantics** — every part carries `ai_description` / `ai_role` /
  `ai_asset` custom properties (required by the create tools) and a color from
  a deterministic palette; descriptions export as glTF `extras`.
- **Per-object colors** — vertex-color mode (one shared material, `Col`
  attribute → glTF `COLOR_0`) or per-part Principled materials; the viewport
  switches to Object-color shading so parts stay readable in Solid mode.
- **Skills** — 18+ built-in recipes (tree, rock, crate/barrel props, modular
  kit, hard-surface, vehicles, furniture, terrain, characters, PBR presets,
  LOD/collision and per-engine export guides). The index is always in the
  prompt; bodies load on trigger match or via `load_skill`. Drop your own
  `*.md` files (TOML front matter) into the user skills folder.
- **Images** — attach concept art (file browser, clipboard, drag & drop);
  images are downscaled to ≤1024 px and sent as multimodal parts; text-only
  models get a captioning fallback via a configurable vision model.
- **LODs + collision** — engine-correct naming automatically (`UCX_`/`UBX_` for
  Unreal, `-colonly` for Godot, `_Collider` for Unity); Godot skips baked LODs
  (the importer generates them).
- **Chat panel in the 3D View sidebar** (N-panel, "AI" tab) with Pipeline /
  Brief / Object / Chat / Skills sections: one-click Validate, Capture, LODs,
  Collision, Export, and per-check Fix buttons — no LLM needed for those.
- **Real tool use, not code dumps** — the model calls registered tools; each
  tool runs on Blender's main thread with undo support. Free-form Python stays
  parked behind an Approve / Reject gate.
- **Repair loop + durable notes** — failed rounds retry with a bounded budget;
  resolved failures are distilled into weight-ranked notes injected into
  future requests.
- **Reasoning-effort aware budgets, multiple providers** (Z.ai, DeepSeek,
  OpenRouter; OpenAI-compatible streaming), history stored in the `.blend`.

## Requirements

- Blender **5.2.0+** (extension manifest format).
- An API key for at least one provider (Z.ai, DeepSeek or OpenRouter).
- Network access — the add-on requests the `network` permission and sends chat
  requests only to the provider you configure. It also requests `files` access
  (attached images, skill notes, exported assets).

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
   - **Fetch models** or type a model id; adjust temperature if needed.
3. Optional:
   - **Export directory** — where GLB/FBX lands; empty uses an `exports`
     folder next to the .blend.
   - **Skills directory** — extra folder with user `*.md` skills (TOML front
     matter: `name`, `description`, `triggers`, optional `tri_budget`).
   - **Vision fallback** — provider/model used to caption attached images when
     the main model has no vision input.
   - **Tool profile** — `compact` drops sculpt/extension tools for smaller
     models.
   - **Auto-approve code**, **Reasoning effort**, **Repair loop bound**,
     **Skill store** as before.

## Usage

1. Set a brief (form or chat): engine, style, class, size. Or just say
   "make a wooden barrel for my godot game" — the agent derives it.
2. The agent blockouts with real-world dimensions, shapes with modifiers,
   colors every part, unwraps UVs, validates, looks at its own render, then
   exports `exports/<Name>.glb|.fbx` — refusing to export while validation
   has blocking issues.
3. The Pipeline section shows stage progress, failing checks with one-click
   **Fix**, and the latest capture thumbnail. Quick actions run without the LLM.

## How it works

1. Your message + trimmed history + a bounded durable-knowledge block go to the
   provider as an OpenAI-compatible chat request with tool schemas.
2. Streamed assistant tool calls are dispatched one by one in Blender's main
   thread; results are appended to the conversation and the loop repeats until
   the model produces a final answer.
3. Errors trigger the bounded repair loop (attempt → record → gate → retry);
   `finish_reason=length` answers are continued automatically, also bounded.
4. Resolved failures are stored as merged notes in the skill store and injected
   (bounded, deduplicated) into future requests.

## Development

Lint and type-check the strict modules (pipeline/, skills/, mesh_ops,
pipeline tools, attachments, prompts — everything else carries an explicit
`# mypy: ignore-errors` marker until migrated):

```sh
python3 -m ruff check .
python3 -m mypy --strict .
```

Run the pure-Python test suite (no Blender needed):

```sh
cd tests && python3 -m unittest discover -s .
```

Run the headless smoke test (exercises the agent loop plus the whole
pipeline — spec, blockout, validation, LODs, collision, capture, export —
inside Blender with a sandboxed store):

```sh
blender --background --factory-startup --python tests/blender_smoke.py
# -> == SMOKE OK ==  (114 checks)
```

### Scenario evals

`tests/eval_cases.json` defines scenario cases (barrel, tree, sword, modular
wall, ambiguity handling, validation-react behavior) with expected tool
sequences. Run them against a live provider:

```sh
BLENDER_AI_API_KEY=... blender -b --factory-startup --python tests/run_evals.py -- \
    --provider openrouter --model <model-id>
# per-case results appended to tests/eval_results.jsonl (JSONL)
```

**Baseline (v0.2.0):** 41 tools registered; full-profile schema ~14k chars.
Static prompt ~6.3k chars (one worked example, XML sections). Smoke: 114/114.
Unit: 109/109. ruff: clean. mypy --strict (strict modules): clean.
Provider-backed eval numbers are recorded per model in
`tests/eval_results.jsonl` after the first live run — every prompt or
tool-description change should move those numbers, not regress them.

Rebuild after changes (see Option B above) and reinstall the fresh zip.

## License

GPL-3.0-or-later (see `blender_manifest.toml`).
