# Blender AI

An AI chat agent that builds 3D models *inside* Blender. You describe what you
want in plain language; the agent reasons, calls Blender tools (create objects,
apply modifiers, assign materials, edit UVs, sculpt, manage addons), runs the
generated Python in your scene, and iterates until the result is right.

Implemented as a native Blender extension (Blender 4.2+ extension manifest,
tested against 5.2).

## Features

- **Chat panel in the 3D View sidebar** (N-panel, "AI" tab): streaming answers,
  collapsible message history, tool-call log previews.
- **Real tool use, not code dumps** — the model calls registered tools
  (`scene`, `materials`, `modifiers`, `uv`, `sculpt`, `addons`, `interactive`);
  each tool runs on Blender's main thread with undo support.
- **Code gate** — free-form Python suggested by the model is parked behind an
  Approve / Reject dialog unless auto-approval is enabled in preferences.
- **Repair loop** — when a round ends in an error, the agent automatically
  retries with a bounded iteration budget (default 3). Each attempt is recorded
  to `state.md` / `record.md` files; a live status + record preview is shown in
  the panel. A user Stop always pauses the loop.
- **Durable skill state** — failures and successful fixes are distilled into
  merged, weight-ranked notes (JSON on disk, capped at 50) that are injected
  into future requests, so the agent learns across sessions.
- **Reasoning-effort aware budgets** — the output token cap scales with the
  selected reasoning effort; if a thinking model exhausts the budget, the
  request is retried with lower effort, and truncated answers are
  auto-continued (bounded).
- **Multiple providers** — Z.ai, DeepSeek and OpenRouter (OpenAI-compatible
  chat completions, streamed), with model list fetching and per-provider API
  keys.
- **Conversation history** is stored in the `.blend` file, so chats survive
  save/reload; history is trimmed to a preference cap before each request.

## Requirements

- Blender **5.2.0+** (extension manifest format).
- An API key for at least one provider (Z.ai, DeepSeek or OpenRouter).
- Network access — the add-on requests the `network` permission and sends chat
  requests only to the provider you configure.

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
   - **Auto-approve code** — run generated Python without the confirm dialog
     (off by default, recommended off for untrusted workflows).
   - **Reasoning effort** — `off`/`low`/`medium`/`high`; higher values make the
     model think more and get a larger output budget.
   - **Repair loop bound** — max automatic retries after an errored round.
   - **Skill store** — directory for durable notes and loop files; empty uses
     `~/BlenderAI/skills`.

## Usage

- Type a task ("make a low-poly lighthouse with a glowing lamp") and press
  **Send**.
- The agent thinks, calls tools and reports progress in the chat. Tool calls
  are visible as log entries; generated code requires **Approve** unless
  auto-approval is on.
- **Stop** cancels the current round and pauses any active repair loop.
- **New chat** starts a fresh conversation (history is kept in the scene until
  then).
- The **Repair loop** section shows live loop state (`state.md`) and the latest
  record entries (`record.md`) from the skill store.

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

Run the pure-Python test suite (no Blender needed):

```sh
python3 -m unittest discover -s tests
```

Run the headless smoke test (exercises the agent loop inside Blender with a
sandboxed store):

```sh
blender --background --factory-startup --python tests/blender_smoke.py
```

Rebuild after changes (see Option B above) and reinstall the fresh zip.

## License

GPL-3.0-or-later (see `blender_manifest.toml`).
