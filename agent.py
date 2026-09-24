"""Agent loop: worker-thread + timer polling, executed on the main thread.

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

from . import debuglog, history, loop_state, providers
from .prefs import get_prefs

# Single agent state. ``messages`` is the source of truth (list[dict]);
# the WindowManager collection is a render copy kept in sync.
_STATE = {
    "messages": [],
    "thread": None,
    "stop_event": None,
    "result": None,
    "stop_requested": False,
    # pending tool call awaiting user resolution: {"tool_call": {...}, "kind": "code"|"ask"}
    "pending": None,
    # live stream progress, updated from the worker: {"reasoning": int, "content": int, "tail": str}
    "live": {},
    # one automatic retry with lowered reasoning effort per user message
    # when a thinking model spends the whole output budget on reasoning
    "empty_retries": 0,
    # bounded auto-continues per user message when the provider cut the
    # answer off at the output limit (finish_reason == "length")
    "continues": 0,
}


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
        scene[_SCENE_KEY] = history.to_json(_STATE["messages"])
    except Exception:  # noqa: BLE001 — persistence must never break the chat
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
        _STATE["messages"] = history.from_json(raw) if raw else []
    except ValueError:
        _STATE["messages"] = []
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
    "off": 8192,
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


def sync_ui():
    wm = _wm()
    col = wm.blender_ai_messages
    # Preserve per-item collapse flags when the message list is unchanged
    # up to that index (messages are append-only within a chat).
    old = [(m.role, m.content, m.collapsed, m.show_reasoning) for m in col]
    col.clear()
    for i, msg in enumerate(_STATE["messages"]):
        item = col.add()
        item.role = msg.get("role", "")
        item.content = msg.get("content", "")
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
        _STATE["live"] = {}
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
    return _STATE["pending"] is not None


def is_busy():
    return _wm().blender_ai_busy


# --------------------------------------------------------------------------- history

def messages():
    return _STATE["messages"]


def _append(msg):
    history.append(_STATE["messages"], msg)
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
    pending = _STATE["pending"]
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


def _loop_store_dir() -> str:
    """Skill-store dir from preferences ('' lets loop_state pick a default)."""
    prefs = get_prefs()
    return str(getattr(prefs, "skill_store", "") or "")


def _repair_bound() -> int:
    prefs = get_prefs()
    try:
        return max(1, int(getattr(prefs, "repair_bound", 3)))
    except (TypeError, ValueError):
        return 3


def send_user_message(text):
    if is_busy() or has_pending():
        return
    text = text.strip()
    if not text:
        return
    debuglog.log("send", chars=len(text))
    _STATE["empty_retries"] = 0
    _STATE["continues"] = 0
    _append(history.message("user", content=text))

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


def _request_messages(params):
    from .prompts import SYSTEM_PROMPT  # lazy: prompts may not exist yet

    req = [history.message("system", content=SYSTEM_PROMPT)]
    req += history.trim(_STATE["messages"], params["history_limit"])
    return req


def _spawn(params):
    from .executor import tools_schema  # lazy: executor may not exist yet

    # Provider-bound snapshot: internal keys stripped, local "error" roles
    # dropped, dangling tool_calls repaired by reconcile().
    # Skill notes ride this request-only copy — never the persisted history.
    snapshot = history.outgoing_snapshot(_request_messages(params))
    loop_state.inject_into_request(snapshot, _loop_store_dir())
    stop_event = threading.Event()
    _STATE["stop_event"] = stop_event
    _STATE["stop_requested"] = False
    _STATE["result"] = None
    _STATE["params"] = dict(params)
    debuglog.log("spawn", model=params["model"], messages=len(snapshot),
                 effort=params.get("reasoning_effort"),
                 max_tokens=_resolve_max_tokens(params))
    # busy + live reset BEFORE the thread starts — a fast provider could
    # otherwise deliver deltas that the reset then wipes
    _set_busy(True)
    thread = threading.Thread(
        target=_worker,
        args=(params, snapshot, tools_schema(), stop_event),
        daemon=True,
    )
    _STATE["thread"] = thread
    thread.start()
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=0.2)


def _worker(params, snapshot, tools, stop_event):
    def on_delta(kind, text):
        # runs in the worker thread; dict ops are GIL-atomic
        live = _STATE["live"]
        live[kind] = live.get(kind, 0) + len(text)
        if kind == "reasoning":
            live["tail"] = (live.get("tail", "") + text)[-600:]

    try:
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
        _STATE["result"] = result
        message = result.get("message") or {}
        debuglog.log("worker done",
                     content=len(message.get("content") or ""),
                     tool_calls=len(message.get("tool_calls") or []),
                     finish=str(result.get("finish_reason") or ""))
    except Exception as exc:  # noqa: BLE001 — worker must never raise into the void
        debuglog.log("worker error", error=str(exc))
        if not stop_event.is_set():
            _STATE["result"] = {"error": str(exc)}


def _update_live_status():
    """Feed the live reasoning tail to the panel while the worker runs.

    Deliberately does not touch the status text — the animated ring plus
    the persistent Thinking box already show progress.
    """
    try:
        _wm().blender_ai_live = (_STATE.get("live") or {}).get("tail", "")
    except AttributeError:
        pass


def _poll():
    # A timer callback that raises is silently unregistered by Blender,
    # which would leave the spinner running forever. Fail loudly
    # (chat + /tmp log) instead.
    try:
        return _poll_run()
    except Exception as exc:  # noqa: BLE001 — the loop must end visibly
        debuglog.log("poll crash", error="%s: %s" % (type(exc).__name__, exc))
        try:
            _append(history.message(
                "error", content="internal error: %s: %s" % (type(exc).__name__, exc)))
        except Exception:
            pass
        _set_busy(False)
        _set_status("Error.")
        return None


def _poll_run():
    thread = _STATE["thread"]
    if thread is not None and thread.is_alive():
        if _STATE["stop_requested"]:
            thread.join(timeout=2.0)
            if thread.is_alive():
                return 0.2  # stream still draining; keep polling
        else:
            _update_live_status()
            return 0.2

    # thread finished: keep the final reasoning tail in the panel
    _update_live_status()
    result = _STATE["result"]
    _STATE["result"] = None
    _STATE["thread"] = None

    if _STATE["stop_requested"]:
        debuglog.log("stopped by user")
        _set_busy(False)
        _set_status("Stopped.")
        return None

    if result is None:
        _set_busy(False)
        return None

    if "error" in result:
        debuglog.log("error surfaced", error=result["error"][:200])
        _append(history.message("error", content=result["error"]))
        store = _loop_store_dir()
        decision = loop_state.on_round_failure(result["error"], _repair_bound(), store)
        if decision.action == "continue":
            # Repair loop: bounded automatic retry with the failure
            # signature carried in durable context (one iteration = one
            # round; loop files live under the skill store). The retry's
            # _spawn() injects the block into its request snapshot.
            _set_status(f"repairing ({decision.iteration}/{decision.bound})…")
            try:
                params = _request_params()
            except providers.ProviderError as exc:
                _append(history.message("error", content=str(exc)))
                _set_busy(False)
                _set_status("Error.")
                return None
            _spawn(params)
            return 0.2
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
        current = (_STATE.get("params") or {}).get("reasoning_effort", "")
        if _STATE["empty_retries"] < 1 and current != "off":
            _STATE["empty_retries"] += 1
            params = dict(_STATE.get("params") or {})
            params["reasoning_effort"] = "off" if current == "low" else "low"
            # Two levers, one rescue: less reasoning AND a bigger output
            # budget (doubled vs the old effort's allocation) so content
            # and tool calls have room even if the model keeps thinking.
            params["max_tokens"] = min(
                2 * _MAX_TOKENS_BY_EFFORT.get(current, 8192), _MAX_TOKENS_CAP)
            debuglog.log("empty answer: retry with lower reasoning effort",
                         max_tokens=params["max_tokens"])
            _set_status("retrying with lower reasoning effort…")
            _spawn(params)
            # _spawn ran INSIDE the timer callback: Blender still counts
            # _poll as registered, and returning None would unregister it
            # (spinner stuck forever). 0.2 keeps the timer alive.
            return 0.2
        content = ("(Empty answer: the output token budget was most likely "
                   "consumed entirely by reasoning. Ask the user to lower "
                   "the Reasoning effort in preferences, then continue.)")

    if (not tool_calls and finish_reason == "length"
            and content and not content.startswith("(Empty answer")
            and _STATE["continues"] < _MAX_CONTINUES):
        # Output budget exhausted mid-answer: standard harness behavior is
        # to commit the partial answer, nudge the model and go on instead
        # of stalling. History grows monotonically, so the next request
        # carries the partial answer plus the continuation nudge and
        # history.reconcile() keeps the protocol shape valid.
        _STATE["continues"] += 1
        _append(history.message(
            "assistant", content=content, reasoning=reasoning,
        ))
        params = dict(_STATE.get("params") or {})
        budget = int(params.get("max_tokens") or 8192)
        params["max_tokens"] = min(max(budget * 2, 16384), _MAX_TOKENS_CAP)
        _append(history.message(
            "user",
            content="(Output token limit reached mid-answer. Continue "
                    "exactly where you stopped; do not repeat what you "
                    "already wrote.)",
        ))
        debuglog.log("length cutoff: auto-continue", n=_STATE["continues"],
                     max_tokens=params["max_tokens"])
        _set_status("continuing (%s/%s)…" % (_STATE["continues"], _MAX_CONTINUES))
        _spawn(params)
        # keep the polling timer registered (see rescue note above)
        return 0.2

    _append(history.message(
        "assistant", content=content, tool_calls=tool_calls or None,
        reasoning=reasoning,
    ))

    if not tool_calls:
        if content and not content.startswith("(Empty answer"):
            loop_state.on_round_success(content, _loop_store_dir())
        _set_busy(False)
        _set_status("Ready.")
        return None

    return _run_tool_calls(tool_calls)


def _run_tool_calls(tool_calls):
    from . import executor  # lazy

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

        if _STATE["pending"] is not None:
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

        _set_status("running tool: %s" % name)
        outcome = executor.dispatch(name, arguments)
        debuglog.log("tool dispatched", tool=name,
                     pending=bool(outcome.get("pending")), ok=outcome.get("ok"))

        if outcome.get("pending"):
            # run_python awaiting user approval (or ask_user awaiting
            # answer): park the call; the loop resumes from the
            # Approve/Reject/Answer operators. No tool result is appended
            # yet — the real result arrives on resolution.
            kind = outcome.get("kind", "ask")
            _STATE["pending"] = {"tool_call": call, "kind": kind}
            debuglog.log("park for user", kind=kind, tool=name)
            if kind == "code":
                assistant = _STATE["messages"][-1]
                if assistant.get("role") == "assistant":
                    assistant["approval"] = "pending"
                    sync_ui()
                    _persist()
            _set_busy(False)
            _set_status("waiting for approval" if kind == "code" else "waiting for your answer")
            return None

        _append(history.message(
            "tool", content=outcome.get("result", ""),
            tool_name=name, tool_call_id=call.get("id", ""),
        ))

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
    pending = _STATE["pending"]
    if pending is None or pending["kind"] != kind:
        return
    call = pending["tool_call"]
    name = call.get("function", {}).get("name", "")
    _STATE["pending"] = None
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
            for msg in reversed(_STATE["messages"]):
                if msg.get("approval") == "pending":
                    msg["approval"] = "ok"
                    break
        else:
            for msg in reversed(_STATE["messages"]):
                if msg.get("approval") == "pending":
                    msg["approval"] = "rejected"
                    break
            result = ("REJECTED by user: do not run this code; "
                      "propose a different approach using the structural tools.")
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
    debuglog.log("stop requested")
    _STATE["stop_requested"] = True
    loop_state.on_user_interrupt(_loop_store_dir())
    event = _STATE.get("stop_event")
    if event is not None:
        event.set()
    thread = _STATE.get("thread")
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    _STATE["thread"] = None
    _STATE["result"] = None
    _STATE["stop_requested"] = False
    _set_busy(False)


def _reset_session():
    """Drop the in-memory session: worker, pending approvals, messages.

    Never touches the persisted scene key — new_chat() deletes it, while
    load_post must not (the freshly opened .blend holds the chat that
    restore_history() reads back).
    """
    stop()
    _STATE["messages"] = []
    _STATE["pending"] = None
    _STATE["empty_retries"] = 0
    _STATE["continues"] = 0
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
    restore_history()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    stop()
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
