"""Agent loop: worker-thread + timer polling, executed on the main thread.
# mypy: ignore-errors

Official Blender threading rule: background threads are only safe when the
main thread blocks on join(), or when results are applied back on the main
thread. Pattern used here (docs.blender.org/api/5.2/info_gotchas_threading.html):

- UI thread: ``send_user_message`` snapshots prefs/history, spawns ONE worker
  thread that only runs ``providers.chat_completions`` (no bpy access).
- ``bpy.app.timers.register(_poll)`` polls on the main thread every 0.2 s;
  when the worker finished, the result is applied here: assistant content is
  stored, tool_calls are dispatched on the main thread, tool results are fed
  back as ``role="tool"`` messages and a new worker is spawned until the
  model returns a final answer without tool_calls.
- The worker is always joined (2 s timeout) in ``stop()``, which is called by
  the Stop operator and by ``unregister()``. At most one worker exists.
"""

import json
import threading

import bpy
from bpy.app.handlers import persistent

from . import debuglog, history, providers
from .prefs import get_prefs


class _Session:
    """All mutable agent state in ONE slotted object — the single sanctioned

    module-level global (Blender add-ons must keep one); reset() is called
    from unregister(). ``messages`` is the source of truth (list[dict]);
    the WindowManager collection is a render copy kept in sync.
    """

    __slots__ = (
        "captions",
        "capture_path",
        "capture_sent",
        "continues",
        "effort_override",
        "empty_retries",
        "live",
        "messages",
        "net_retries",
        "nudges",
        "params",
        "pending",
        "respawn",
        "result",
        "sent_images",
        "stop_event",
        "stop_requested",
        "thread",
        "tools_used",
        "vision_retried",
    )

    def __init__(self):
        self.messages: list = []
        self.thread: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self.result: dict | None = None
        self.stop_requested: bool = False
        # pending tool call awaiting user resolution: {"tool_call": {...}, "kind": "code"|"ask"}
        self.pending: dict | None = None
        # live stream progress from the worker: {"reasoning": int, "content": int, "tail": str}
        self.live: dict = {}
        # one automatic retry with lowered reasoning effort per user message
        # when a thinking model spends the whole output budget on reasoning
        self.empty_retries: int = 0
        # bounded auto-continues per user message when the provider cut the
        # answer off at the output limit (finish_reason == "length")
        self.continues: int = 0
        self.capture_sent: str = ""
        self.capture_path: str = ""
        self.params: dict | None = None
        # per user message: did the model use tools (a build is running),
        # how often it was nudged to continue, network retries used
        self.tools_used: bool = False
        self.nudges: int = 0
        self.net_retries: int = 0
        # params of a delayed retry, spawned by _poll after the backoff
        self.respawn: dict | None = None
        # images in the in-flight request; one automatic re-send with
        # captions when the provider rejects them
        self.sent_images: int = 0
        self.vision_retried: bool = False
        # (image path, captioner model) -> caption; one call per image
        self.captions: dict = {}
        # "off" after a reasoning runaway: thinking stays off this task
        self.effort_override: str = ""

    def reset(self) -> None:
        self.messages = []
        self.thread = None
        self.stop_event = None
        self.result = None
        self.stop_requested = False
        self.pending = None
        self.live = {}
        self.empty_retries = 0
        self.continues = 0
        self.capture_sent = ""
        self.capture_path = ""
        self.params = None
        self.captions = {}
        self.new_turn()

    def new_turn(self) -> None:
        """Counters that are bounded per user message."""
        self.empty_retries = 0
        self.continues = 0
        self.tools_used = False
        self.nudges = 0
        self.net_retries = 0
        self.respawn = None
        self.vision_retried = False
        self.effort_override = ""


_STATE = _Session()


# --------------------------------------------------------------------------- storage

# Chat history is stored INSIDE the .blend file (scene custom property):
# every project carries its own chat, an unsaved/new file starts a fresh
# chat, and Save As naturally forks the history.
_SCENE_KEY = "blender_ai_chat"


def _persist():
    scene = bpy.context.scene
    if scene is None:
        return
    try:
        scene[_SCENE_KEY] = history.to_json(_STATE.messages)
    except Exception:  # noqa: BLE001 — scene persistence is best-effort
        pass


def restore_history():
    # At Blender startup the context is restricted (no scene/window), so
    # every access is guarded; the load_post handler restores properly
    # right after the file opens.
    try:
        scene = bpy.context.scene
        raw = scene.get(_SCENE_KEY, "") if scene else ""
    except AttributeError:
        raw = ""
    try:
        _STATE.messages = history.from_json(raw) if raw else []
    except ValueError:
        _STATE.messages = []
    try:
        sync_ui()
    except AttributeError:
        pass


# --------------------------------------------------------------------------- prefs


def _request_params():
    """Snapshot provider settings on the main thread (no bpy in the worker)."""
    prefs = get_prefs()
    if prefs is None:
        raise providers.ProviderError("Add-on preferences not found.")
    return {
        "provider_id": prefs.provider,
        "api_key": prefs.get_api_key(),
        "model": prefs.get_model(),
        "temperature": prefs.temperature,
        "auto_approve_code": prefs.auto_approve_code,
        "history_limit": prefs.history_limit,
        "reasoning_effort": prefs.reasoning_effort,
    }


# Reasoning tokens share the output budget (providers.py contract), so the
# cap must scale with the effort level: at 8192 a high-effort model can
# spend everything on reasoning and return empty content with no tool
# calls (observed in debug logs).
_MAX_TOKENS_BY_EFFORT: dict[str, int] = {
    "": 8192,
    "off": 16384,  # no thinking: the whole budget is script text
    "low": 8192,
    "medium": 16384,
    "high": 24576,
    "xhigh": 32768,
    "max": 32768,
}
_MAX_TOKENS_CAP: int = 65536
# how many length-cutoff continuations one user message may chain
_MAX_CONTINUES: int = 3


def _resolve_max_tokens(params: dict) -> int:
    """Output budget for a request: explicit override, else effort-scaled."""
    override = int(params.get("max_tokens") or 0)
    if override > 0:
        return min(override, _MAX_TOKENS_CAP)
    effort = str(params.get("reasoning_effort") or "")
    return _MAX_TOKENS_BY_EFFORT.get(effort, 8192)


# --------------------------------------------------------------------------- UI sync

def _wm():
    return bpy.context.window_manager


# Spinner: Blender's UILayout.progress is static — panels only redraw on
# events. While busy, a timer bumps a counter and tags VIEW_3D areas for
# redraw so the RING progress animates.
_SPINNER = {"t": 0}


def _spinner_tick():
    _SPINNER["t"] += 1
    wm = _wm()
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    return 0.08


def _start_spinner():
    if not bpy.app.timers.is_registered(_spinner_tick):
        bpy.app.timers.register(_spinner_tick, first_interval=0.05)


def _stop_spinner():
    if bpy.app.timers.is_registered(_spinner_tick):
        bpy.app.timers.unregister(_spinner_tick)
    _SPINNER["t"] = 0
    for window in _wm().windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# Messages longer than this many characters start collapsed in the panel.
_COLLAPSE_THRESHOLD = 400


def _display_text(content):
    """Panel text for a message: image parts render as a short label."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    out = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            out.append(str(part.get("text", "")))
        elif part.get("type") == "image_ref":
            out.append("[image: %s]" % part.get("label", "image"))
    return "\n".join(out)


def sync_ui():
    wm = _wm()
    col = wm.blender_ai_messages
    # Preserve per-item collapse flags when the message list is unchanged
    # up to that index (messages are append-only within a chat).
    old = [(m.role, m.content, m.collapsed, m.show_reasoning) for m in col]
    col.clear()
    for i, msg in enumerate(_STATE.messages):
        item = col.add()
        # harness nudges are user-role messages the user did not write
        item.role = "auto" if msg.get("auto") else msg.get("role", "")
        item.content = _display_text(msg.get("content", ""))
        item.tool_name = msg.get("tool_name", "")
        item.approval = msg.get("approval", "")
        item.reasoning = msg.get("reasoning", "")
        if i < len(old) and old[i][0] == item.role and old[i][1] == item.content:
            item.collapsed = old[i][2]
            item.show_reasoning = old[i][3]
        else:
            item.collapsed = len(item.content) > _COLLAPSE_THRESHOLD


def _set_status(text):
    _wm().blender_ai_status = text


def _set_busy(busy):
    try:
        _wm().blender_ai_busy = busy
    except AttributeError:
        # WM props can be gone when a second copy of the addon (e.g. the
        # installed one, while smoke tests run from sources) unregisters.
        pass
    if busy:
        # fresh request: drop the previous stream's reasoning tail
        _STATE.live = {}
        try:
            _wm().blender_ai_live = ""
        except AttributeError:
            pass
        _start_spinner()
    else:
        # keep blender_ai_live: the Thinking box stays in the panel
        # (collapsed) after the answer
        _stop_spinner()


def has_pending():
    return _STATE.pending is not None


def is_busy():
    return _wm().blender_ai_busy


# --------------------------------------------------------------------------- history

def messages():
    return _STATE.messages


def _append(msg):
    history.append(_STATE.messages, msg)
    sync_ui()
    _persist()


@persistent
def _on_load_post(_scene, _depsgraph):
    # Opening (or creating) a file switches chats: reset the session —
    # stop the worker, drop pending approvals — then load the history
    # stored inside the freshly opened .blend (none for a new project).
    # Never call new_chat() here: it would delete the very key we are
    # about to restore from.
    _reset_session()
    restore_history()


# --------------------------------------------------------------------------- agent loop

def pending_view():
    """Undo-proof snapshot of the parked call: None or {kind, name, args}.

    WM props get reverted by Blender's undo (each tool dispatch pushes an
    undo step), which would blank the approve/answer boxes while the
    loop stays parked. The pending dict is the single source of truth;
    panel and operators derive from it.
    """
    pending = _STATE.pending
    if pending is None:
        return None
    call = pending["tool_call"]
    try:
        args = json.loads(call["function"]["arguments"])
    except (ValueError, KeyError, TypeError):
        args = {}
    if not isinstance(args, dict):
        args = {"code": str(args)}
    return {
        "kind": pending["kind"],
        "name": call.get("function", {}).get("name", ""),
        "args": args,
    }


def send_user_message(text, attachments=None):
    """User turn. ``attachments`` is a list of records with
    to_ref() (attachments.Attachment) — stored as image_ref parts, the
    LATEST turn's refs expand to base64 at request time."""
    if is_busy() or has_pending():
        return
    text = text.strip()
    if not text and not attachments:
        return
    debuglog.log("send", chars=len(text), images=len(attachments or []),
                 text=text[:300])
    _STATE.new_turn()
    refs = [att.to_ref() for att in (attachments or [])]
    content = history.with_image_refs(text, refs)
    _append(history.message("user", content=content))

    try:
        params = _request_params()
    except providers.ProviderError as exc:
        debuglog.log("send error", error=str(exc))
        _append(history.message("error", content=str(exc)))
        return

    if not bpy.app.online_access:
        debuglog.log("send blocked: online access disabled")
        _append(history.message(
            "error",
            content="Blender's online access is disabled. "
                    "Allow online access in Preferences → Save & Load → Online Access.",
        ))
        return

    _set_status("thinking…")
    _spawn(params)


def _dynamic_system_message() -> str:
    """<scene> summary recomputed EVERY request (never stored).

    Computed state beats remembered state: the model always sees which
    models exist and how big they are, even after history trimming.
    """
    try:
        from .tools.build import get_scene_state

        state = json.loads(get_scene_state())
    except (ImportError, RuntimeError, ValueError, KeyError, TypeError, AttributeError):
        return ""  # no bpy (unit tests) or mid-shutdown: request still goes out
    models = state.get("models") or []
    if not models:
        return "<scene>no models yet</scene>"
    rows = "\n".join(
        "- %s: %s parts, %s tris, size %s m%s" % (
            m["name"], m["parts"], m["tris"], "x".join(str(v) for v in m["size_m"]),
            "" if m.get("has_script") else " (no script)")
        for m in models)
    return "<scene>\nmodels:\n%s\nother objects: %s\n</scene>" % (
        rows, state.get("other_objects_total", 0))


def _request_messages(params):
    from .prompts import SYSTEM_PROMPT

    req = [history.message("system", content=SYSTEM_PROMPT)]
    req += history.compact(history.trim(_STATE.messages, params["history_limit"]))
    dyn = _dynamic_system_message()
    if dyn:
        req.append(history.message("system", content=dyn))
    return req


def _make_image_resolver(params):
    """Main thread: snapshot what image resolution needs; the returned
    resolver runs in the WORKER thread (network, no bpy).

    Vision model: images go out natively. Text-only model: ONE caption per
    image (cached) from an automatically picked vision model replaces it
    with <image_description> text. No vision model anywhere: a placeholder.
    """
    from .prefs import pick_captioner
    from .providers import supports_vision

    prefs = get_prefs()
    native = supports_vision(params["provider_id"], params["model"])
    pick = None if native or prefs is None else pick_captioner(prefs)
    key = getattr(prefs, "api_key_%s" % pick[0], "") if pick else ""

    def resolve(ref):
        import base64
        from pathlib import Path

        from .providers import image_part

        path = Path(str(ref.get("path", "")))
        label = ref.get("label", "image")
        if not path.is_file():
            return {"type": "text", "text": "[image missing: %s]" % label}
        if native:
            url = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
            return image_part(url)
        caption = None
        if pick is not None:
            cache_key = (str(path), pick[1])
            caption = _STATE.captions.get(cache_key)
            if caption is None:
                caption = _caption_via_vision_model(path, pick[0], pick[1], key)
                if caption:
                    _STATE.captions[cache_key] = caption
        if caption:
            return {"type": "text",
                    "text": "<image_description source=\"%s\">\n%s\n</image_description>"
                            % (label, caption)}
        return {"type": "text",
                "text": "[image: %s — no model with image input is available for "
                        "your API keys; ask the user what it shows]" % label}

    return resolve


_CAPTION_PROMPT = (
    "Describe this reference image for a 3D modeler in <=120 words, as plain "
    "facts: object type, proportions (ratios), major parts and their layout, "
    "colors as hex, style (low-poly/stylized/realistic), rough triangle-count "
    "impression. No preamble."
)


def _caption_via_vision_model(image_path, v_provider, v_model, api_key):
    """Worker thread: one-shot caption; None on any failure."""
    import base64

    from .providers import ProviderError, chat_completions, image_part, text_part

    debuglog.log("vision: caption", provider=v_provider, model=v_model)
    try:
        url = "data:image/png;base64," + base64.b64encode(
            image_path.read_bytes()).decode("ascii")
        messages = [{
            "role": "user",
            "content": [text_part(_CAPTION_PROMPT), image_part(url)],
        }]
        response = chat_completions(v_provider, api_key, v_model, messages, tools=None,
                                    temperature=0.2, timeout=60)
        message = response.get("message", {}) if isinstance(response, dict) else {}
        content = message.get("content", "")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content
                              if isinstance(p, dict))
        text = str(content).strip()
        return text or None
    except (ProviderError, OSError, ValueError) as exc:
        debuglog.log("vision: caption failed", error=str(exc)[:300])
        return None


def _attach_fresh_capture(messages):
    """Ride the newest capture_view sheet on THIS request as a user image.

    OpenAI-compatible APIs reject image parts inside role:tool messages,
    so the sheet travels as a synthetic user message right after the tool
    results (worker-side copy only; history keeps the file reference).
    """
    path = _STATE.capture_path
    if not path or _STATE.capture_sent == path:
        return messages
    _STATE.capture_sent = path
    sheet = list(messages)
    sheet.append(history.message("user", content=[
        {"type": "image_ref", "sha256": "capture", "label": "capture 2x2",
         "w": 1024, "h": 1024, "path": path},
        {"type": "text", "text":
         "capture_view result (2x2 sheet: iso/front/side/top). Inspect it for "
         "floating parts, wrong proportions or inverted faces before finishing."},
    ]))
    return sheet


def _spawn(params):
    from .executor import tools_schema  # lazy: executor may not exist yet
    from .providers import supports_vision

    if _STATE.effort_override and params.get("reasoning_effort") != _STATE.effort_override:
        params = dict(params)
        params["reasoning_effort"] = _STATE.effort_override
        params.pop("max_tokens", None)  # effort-scaled budget for the override

    # Provider-bound snapshot: internal keys stripped, local "error" roles
    # dropped, dangling tool_calls repaired by reconcile(). Image refs in the
    # LATEST user turn expand to base64 parts; older refs stay placeholders.
    messages = history.outgoing_snapshot(_request_messages(params))
    snapshot = _attach_fresh_capture(messages)
    resolver = _make_image_resolver(params)
    tools = tools_schema(vision=supports_vision(params["provider_id"], params["model"]))
    stop_event = threading.Event()
    _STATE.stop_event = stop_event
    _STATE.stop_requested = False
    _STATE.result = None
    _STATE.params = dict(params)
    debuglog.log("spawn", model=params["model"], messages=len(snapshot),
                 chars=sum(len(str(m.get("content") or "")) for m in snapshot),
                 tools=len(tools), effort=params.get("reasoning_effort"),
                 max_tokens=_resolve_max_tokens(params))
    # busy + live reset BEFORE the thread starts — a fast provider could
    # otherwise deliver deltas that the reset then wipes
    _set_busy(True)
    thread = threading.Thread(
        target=_worker,
        args=(params, snapshot, tools, stop_event, resolver),
        daemon=True,
    )
    _STATE.thread = thread
    thread.start()
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=0.2)


def _worker(params, snapshot, tools, stop_event, resolver):
    def on_delta(kind, text):
        # runs in the worker thread; dict ops are GIL-atomic
        live = _STATE.live
        live[kind] = live.get(kind, 0) + len(text)
        if kind == "reasoning":
            live["tail"] = (live.get("tail", "") + text)[-600:]

    try:
        # image expansion may caption over the network: worker side only
        snapshot = history.expand_images(snapshot, resolver)
        _STATE.sent_images = sum(
            1 for m in snapshot if isinstance(m.get("content"), list)
            for part in m["content"]
            if isinstance(part, dict) and part.get("type") == "image_url")
        if _STATE.sent_images:
            debuglog.log("vision: native images", model=params["model"],
                         images=_STATE.sent_images)
        result = providers.chat_completions(
            params["provider_id"],
            params["api_key"],
            params["model"],
            snapshot,
            tools=tools,
            temperature=params["temperature"],
            reasoning_effort=params["reasoning_effort"],
            max_tokens=_resolve_max_tokens(params),
            stream=True,
            on_delta=on_delta,
            stop_event=stop_event,
        )
        _STATE.result = result
        message = result.get("message") or {}
        debuglog.log("worker done",
                     content=len(message.get("content") or ""),
                     reasoning=len(message.get("reasoning_content")
                                   or message.get("reasoning") or ""),
                     tool_calls=[(c.get("function") or {}).get("name", "?")
                                 for c in message.get("tool_calls") or []],
                     finish=str(result.get("finish_reason") or ""),
                     usage=result.get("usage") or {})
    except Exception as exc:  # noqa: BLE001 — worker boundary: user-facing error
        debuglog.log("worker error", error=str(exc))
        if not stop_event.is_set():
            _STATE.result = {"error": str(exc)}


def _update_live_status():
    """Feed the live reasoning tail to the panel while the worker runs.

    Deliberately does not touch the status text — the animated ring plus
    the persistent Thinking box already show progress.
    """
    try:
        _wm().blender_ai_live = _STATE.live.get("tail", "")
    except AttributeError:
        pass


def _poll():
    # A timer callback that raises is silently unregistered by Blender,
    # which would leave the spinner running forever. Fail loudly
    # (chat + /tmp log) instead.
    try:
        return _poll_run()
    except Exception as exc:  # noqa: BLE001 — timer boundary: never crash the loop
        debuglog.log("poll crash", error="%s: %s" % (type(exc).__name__, exc))
        try:
            _append(history.message(
                "error", content="internal error: %s: %s" % (type(exc).__name__, exc)))
        except Exception:  # noqa: BLE001 — timer boundary
            pass
        _set_busy(False)
        _set_status("Error.")
        return None


def _poll_run():  # noqa: PLR0911 — timer state machine
    if _STATE.respawn is not None and _STATE.thread is None:
        params, _STATE.respawn = _STATE.respawn, None
        _spawn(params)
        return 0.2
    thread = _STATE.thread
    if thread is not None and thread.is_alive():
        if _STATE.stop_requested:
            thread.join(timeout=2.0)
            if thread.is_alive():
                return 0.2  # stream still draining; keep polling
        else:
            _update_live_status()
            return 0.2

    # thread finished: keep the final reasoning tail in the panel
    _update_live_status()
    result = _STATE.result
    _STATE.result = None
    _STATE.thread = None

    if _STATE.stop_requested:
        debuglog.log("stopped by user")
        _set_busy(False)
        _set_status("Stopped.")
        return None

    if result is None:
        _set_busy(False)
        return None

    if "error" in result:
        error = result["error"]
        if _STATE.sent_images and not _STATE.vision_retried and _is_image_rejection(error):
            # The model refused image input although we believed it had
            # vision: remember that (persisted) and re-send the same turn —
            # the resolver now falls back to a caption automatically.
            _STATE.vision_retried = True
            params = dict(_STATE.params or {})
            providers.mark_no_vision(params.get("provider_id", ""), params.get("model", ""))
            from .prefs import save_vision_state
            save_vision_state()
            debuglog.log("vision: model rejected images, retry with captions",
                         model=params.get("model"), error=error[:300])
            _set_status("model has no image input — describing images instead…")
            _STATE.respawn = params
            return 0.2
        if _is_transient(error) and _STATE.net_retries < _MAX_NET_RETRIES:
            # Mid-stream drops / gateway errors: retry the same request
            # after a backoff instead of leaving the build half-done.
            _STATE.net_retries += 1
            delay = 3.0 * _STATE.net_retries
            debuglog.log("transient error: retry", n=_STATE.net_retries,
                         delay=delay, error=error[:300])
            _set_status("connection problem — retry %d/%d…"
                        % (_STATE.net_retries, _MAX_NET_RETRIES))
            _STATE.respawn = dict(_STATE.params or {}) or None
            if _STATE.respawn is not None:
                return delay
        debuglog.log("error surfaced", error=error[:500])
        _append(history.message("error", content=error))
        _set_busy(False)
        _set_status("Error.")
        return None

    return _apply_assistant_message(
        result.get("message", {}), str(result.get("finish_reason") or ""))


def _apply_assistant_message(message, finish_reason=""):
    tool_calls = message.get("tool_calls") or []
    content = message.get("content") or ""
    # DeepSeek returns reasoning_content; OpenRouter normalizes to reasoning.
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    if not isinstance(reasoning, str):
        reasoning = json.dumps(reasoning, ensure_ascii=False)

    if not tool_calls and not content and reasoning:
        # The thinking model spent its whole output budget on reasoning.
        # One silent rescue per user message: retry with lowered effort
        # before showing the user an explanation.
        current = (_STATE.params or {}).get("reasoning_effort", "")
        if _STATE.empty_retries < 1 and current != "off":
            # Observed (deepseek-flash, big vault request): 16k reasoning
            # tokens at medium, then 32k at low — it drafts the whole
            # script in its head and never acts. Lowering one step is not
            # enough: switch thinking OFF for the rest of this task and
            # tell the model to act now with a small first build.
            _STATE.empty_retries += 1
            _STATE.effort_override = "off"
            params = dict(_STATE.params or {})
            params["reasoning_effort"] = "off"
            params["max_tokens"] = _MAX_TOKENS_BY_EFFORT["medium"]
            _append(history.message("user", content=_RUNAWAY_NUDGE, auto=True))
            debuglog.log("reasoning runaway: retry with thinking off",
                         reasoning_chars=len(reasoning), was=current)
            _set_status("thinking ran too long — building directly…")
            _spawn(params)
            # _spawn ran INSIDE the timer callback: Blender still counts
            # _poll as registered, and returning None would unregister it
            # (spinner stuck forever). 0.2 keeps the timer alive.
            return 0.2
        content = ("(Empty answer: the model used its whole output budget "
                   "for thinking twice. Send 'continue' to retry, or split "
                   "the request into smaller steps.)")

    if (not tool_calls and finish_reason == "length"
            and content and not content.startswith("(Empty answer")
            and _STATE.continues < _MAX_CONTINUES):
        # Output budget exhausted mid-answer: standard harness behavior is
        # to commit the partial answer, nudge the model and go on instead
        # of stalling. History grows monotonically, so the next request
        # carries the partial answer plus the continuation nudge and
        # history.reconcile() keeps the protocol shape valid.
        _STATE.continues += 1
        _append(history.message(
            "assistant", content=content, reasoning=reasoning,
        ))
        params = dict(_STATE.params or {})
        budget = int(params.get("max_tokens") or 8192)
        params["max_tokens"] = min(max(budget * 2, 16384), _MAX_TOKENS_CAP)
        _append(history.message(
            "user",
            content="(Output token limit reached mid-answer. Continue "
                    "exactly where you stopped; do not repeat what you "
                    "already wrote.)",
        ))
        debuglog.log("length cutoff: auto-continue", n=_STATE.continues,
                     max_tokens=params["max_tokens"])
        _set_status("continuing (%s/%s)…" % (_STATE.continues, _MAX_CONTINUES))
        _spawn(params)
        # keep the polling timer registered (see rescue note above)
        return 0.2

    _append(history.message(
        "assistant", content=content, tool_calls=tool_calls or None,
        reasoning=reasoning,
    ))

    if not tool_calls:
        if needs_nudge(_STATE.tools_used, _STATE.nudges, content):
            # The model stopped mid-build with prose ("Next I will add…")
            # instead of a tool call — the "agent just stops" failure.
            # Bounded: nudge it to continue or to call finish.
            _STATE.nudges += 1
            debuglog.log("nudge: text without finish", n=_STATE.nudges,
                         content=content[:300])
            _append(history.message("user", content=_NUDGE, auto=True))
            try:
                params = _request_params()
            except providers.ProviderError as exc:
                _append(history.message("error", content=str(exc)))
                _set_busy(False)
                return None
            _set_status("continuing…")
            _spawn(params)
            return 0.2
        debuglog.log("turn end", content=content[:300])
        _set_busy(False)
        _set_status("Ready.")
        return None

    if any((c.get("function") or {}).get("name") in _BUILD_TOOLS for c in tool_calls):
        _STATE.tools_used = True  # a build is in progress this turn
    return _run_tool_calls(tool_calls)


_MAX_NUDGES = 2
_RUNAWAY_NUDGE = ("(harness) Your previous attempt spent its whole budget thinking and "
                  "produced nothing. Do not draft code in your head. Call build_model "
                  "NOW with a short first blockout (main volumes only, at most ~40 "
                  "lines); add detail in the following builds.")
_BUILD_TOOLS = frozenset({"build_model"})
_MAX_NET_RETRIES = 2
_NUDGE = ("(harness) You ended your turn without calling a tool. If the model "
          "is not finished, continue NOW with the next build_model call. If it "
          "fully matches the request, call finish. If you need the user's "
          "decision, call ask_user.")
_TRANSIENT_MARKERS = ("Network error", "timed out", "timeout", "Connection",
                      "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504",
                      "stream", "IncompleteRead")


def needs_nudge(tools_used: bool, nudges: int, content: str) -> bool:
    """A build turn that ended in prose (no finish, no ask_user) gets nudged."""
    return tools_used and nudges < _MAX_NUDGES and not content.startswith("(Empty answer")


def _is_image_rejection(error: str) -> bool:
    low = error.lower()
    return (("http 400" in low or "http 422" in low)
            and any(word in low for word in ("image", "vision", "multimodal", "modalit")))


def _is_transient(error: str) -> bool:
    return any(marker in error for marker in _TRANSIENT_MARKERS)


def _args_preview(arguments):
    """Log-friendly args: long code is summarized, not dumped."""
    out = {}
    for key, value in arguments.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        out[key] = text if len(text) <= 120 else "%s… (%d chars)" % (text[:120], len(text))
    return out


def _remember_capture(result):
    try:
        _STATE.capture_path = str(json.loads(result).get("image", ""))
    except (ValueError, AttributeError):
        pass


def _finish(call):
    """finish tool: record the result, show the summary, end the turn."""
    try:
        summary = str(json.loads(call["function"]["arguments"]).get("summary", ""))
    except (ValueError, KeyError, TypeError, AttributeError):
        summary = ""
    _append(history.message("tool", content="finished", tool_name="finish",
                            tool_call_id=call.get("id", "")))
    _append(history.message("assistant", content=summary or "Done."))
    debuglog.log("finish", summary=summary[:300])
    _set_busy(False)
    _set_status("Ready.")


def _run_tool_calls(tool_calls):
    from . import executor  # lazy

    finished = None
    failed = False
    for call in tool_calls:
        function = call.get("function", {})
        name = function.get("name", "")
        raw = function.get("arguments", "{}")
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except ValueError:
            arguments = None

        if arguments is None:
            _append(history.message(
                "tool", content="ERROR: invalid JSON arguments",
                tool_name=name, tool_call_id=call.get("id", ""),
            ))
            continue

        if _STATE.pending is not None:
            # A previous call in this block is parked on user approval:
            # every call still needs a result message, or the provider
            # rejects the whole next request (unpaired tool_calls).
            _append(history.message(
                "tool",
                content="SKIPPED: waiting for the user to resolve an "
                        "earlier tool call; call it again if still needed.",
                tool_name=name, tool_call_id=call.get("id", ""),
            ))
            continue

        if name == "finish":
            finished = call
            continue

        _set_status("running tool: %s" % name)
        outcome = executor.dispatch(name, arguments)
        debuglog.log("tool", tool=name, args=_args_preview(arguments),
                     pending=bool(outcome.get("pending")), ok=outcome.get("ok"),
                     result=str(outcome.get("result", ""))[:400])
        if name == "capture_view" and outcome.get("ok"):
            _remember_capture(outcome.get("result", ""))

        if outcome.get("pending"):
            # build_model awaiting user approval (or ask_user awaiting
            # answer): park the call; the loop resumes from the
            # Approve/Reject/Answer operators. No tool result is appended
            # yet — the real result arrives on resolution.
            kind = outcome.get("kind", "ask")
            _STATE.pending = {"tool_call": call, "kind": kind}
            debuglog.log("park for user", kind=kind, tool=name)
            if kind == "code":
                assistant = _STATE.messages[-1]
                if assistant.get("role") == "assistant":
                    assistant["approval"] = "pending"
                    sync_ui()
                    _persist()
            _set_busy(False)
            _set_status("waiting for approval" if kind == "code" else "waiting for your answer")
            return None

        result = str(outcome.get("result", ""))
        failed = failed or not outcome.get("ok") or result.startswith("BUILD ERROR")
        _append(history.message(
            "tool", content=result,
            tool_name=name, tool_call_id=call.get("id", ""),
        ))

    if finished is not None:
        if not failed:
            return _finish(finished)
        # finish in the same batch as a failing call: the model has not
        # seen the error yet — refuse, so it cannot end on a broken build.
        _append(history.message(
            "tool", content="NOT FINISHED: a tool call above returned an ERROR; "
                            "fix it first, then call finish.",
            tool_name="finish", tool_call_id=finished.get("id", "")))

    # All tool calls resolved — continue the loop with a new worker request.
    try:
        params = _request_params()
    except providers.ProviderError as exc:
        _append(history.message("error", content=str(exc)))
        _set_busy(False)
        return None
    _set_status("thinking…")
    _spawn(params)
    # Same as the rescue path: we are inside the timer callback, so keep
    # it registered for the next round (None would kill the loop).
    return 0.2


def resolve_pending(kind, payload):
    """Continue the loop after the user approved/rejected code or answered."""
    pending = _STATE.pending
    if pending is None or pending["kind"] != kind:
        return
    call = pending["tool_call"]
    name = call.get("function", {}).get("name", "")
    _STATE.pending = None
    debuglog.log("resolved", kind=kind, tool=name)

    if kind == "code":
        if payload == "approved":
            from . import executor  # lazy — executes on the main thread
            try:
                arguments = json.loads(call["function"]["arguments"])
            except (ValueError, KeyError, TypeError):
                arguments = None
            if arguments is None:
                result = "ERROR: stored arguments were not valid JSON; nothing ran."
            else:
                result = executor.execute_tool(name, arguments)
            # mark the assistant message as approved
            for msg in reversed(_STATE.messages):
                if msg.get("approval") == "pending":
                    msg["approval"] = "ok"
                    break
        else:
            for msg in reversed(_STATE.messages):
                if msg.get("approval") == "pending":
                    msg["approval"] = "rejected"
                    break
            result = ("REJECTED by user: do not run this code; "
                      "ask the user what to change (ask_user) or revise the script.")
    else:  # ask
        result = payload
        _wm().blender_ai_ask_answer = ""

    _append(history.message(
        "tool", content=result,
        tool_name=name, tool_call_id=call.get("id", ""),
    ))
    sync_ui()
    _persist()

    try:
        params = _request_params()
    except providers.ProviderError as exc:
        debuglog.log("resume error", error=str(exc))
        _append(history.message("error", content=str(exc)))
        return
    _set_status("thinking…")
    _spawn(params)


# --------------------------------------------------------------------------- control

def stop():
    thread = _STATE.thread
    if (thread is not None and thread.is_alive()) or _STATE.respawn is not None:
        debuglog.log("stop requested")  # only real stops: file loads call this too
    _STATE.stop_requested = True
    _STATE.respawn = None
    event = _STATE.stop_event
    if event is not None:
        event.set()
    thread = _STATE.thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    _STATE.thread = None
    _STATE.result = None
    _STATE.stop_requested = False
    _set_busy(False)


def _reset_session():
    """Drop the in-memory session: worker, pending approvals, messages.

    Never touches the persisted scene key — new_chat() deletes it, while
    load_post must not (the freshly opened .blend holds the chat that
    restore_history() reads back).
    """
    stop()
    _STATE.messages = []
    _STATE.pending = None
    _STATE.new_turn()
    wm = _wm()
    wm.blender_ai_ask_answer = ""
    wm.blender_ai_live = ""
    sync_ui()
    _set_status("Ready.")


def new_chat():
    _reset_session()
    scene = bpy.context.scene
    if scene is not None and scene.get(_SCENE_KEY) is not None:
        del scene[_SCENE_KEY]


# --------------------------------------------------------------------------- registration

def register():
    debuglog.log("addon registered", blender=bpy.app.version_string,
                 log=debuglog.path())
    restore_history()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    stop()
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    _STATE.reset()
