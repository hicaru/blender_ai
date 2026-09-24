"""Chat history: pure list-of-dict logic + JSON persistence.
# mypy: ignore-errors

Deliberately free of ``bpy`` so it is unit-testable outside Blender.
A message is a dict with a subset of:

- ``role``: "user" | "assistant" | "tool" | "error"
- ``content``: str ("" allowed for pure tool_call assistant messages)
- ``tool_calls``: list of OpenAI tool_call dicts (assistant messages only)
- ``tool_name``: str (tool messages)
- ``tool_call_id``: str (tool messages)
- ``approval``: "" | "pending" | "ok" | "rejected" (assistant messages
  whose tool call is ``run_python``)
"""

import json

__all__ = (
    "append",
    "compact",
    "from_json",
    "load",
    "message",
    "reconcile",
    "save",
    "to_json",
    "trim",
)


def message(role, content="", tool_calls=None, tool_name="", tool_call_id="", approval="", reasoning="", auto=False):
    """Build one history dict, omitting empty optional keys.

    ``auto`` marks harness-written user turns (continue nudges): they are
    sent to the model but never count as the user's task.
    """
    msg = {"role": role, "content": content}
    if auto:
        msg["auto"] = True
    if reasoning:
        msg["reasoning"] = reasoning
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if tool_name:
        msg["tool_name"] = tool_name
    if tool_call_id:
        msg["tool_call_id"] = tool_call_id
    if approval:
        msg["approval"] = approval
    return msg


def append(messages, msg):
    """Append ``msg`` to ``messages``; returns the same list (in place)."""
    messages.append(msg)
    return messages


def trim(messages, limit):
    """Keep at most the last ``limit`` messages without breaking tool pairs.

    Providers reject a ``tool`` message whose preceding assistant
    ``tool_calls`` message was trimmed away, so after slicing we drop
    leading orphan ``tool`` messages.
    """
    trimmed = messages[-limit:] if limit > 0 else list(messages)
    while trimmed and trimmed[0].get("role") == "tool":
        trimmed.pop(0)
    # The user's task must survive trimming: without it a long build
    # forgets what it is building (observed: model drifted mid-build).
    task = next((m for m in reversed(messages)
                 if m.get("role") == "user" and not m.get("auto")), None)
    if task is not None and not any(m is task for m in trimmed):
        trimmed.insert(0, task)
    return trimmed


_CODE_TOOLS = frozenset({"build_model"})
_OMITTED_CODE = "# (older version omitted - the newest build_model call below is current)"
_KEEP_FULL_RESULTS = 8
_OLD_RESULT_CHARS = 600


def compact(messages):
    """Request-bound copy with old bulk shrunk (input untouched).

    - build_model scripts: only the newest call per model keeps its code
      (each call sends the FULL script, so older ones are superseded);
    - tool results older than the last few are cut to a short head.
    """
    newest = {}
    for idx, msg in enumerate(messages):
        for call in msg.get("tool_calls") or ():
            fn = call.get("function", {}) if isinstance(call, dict) else {}
            if fn.get("name") in _CODE_TOOLS:
                newest[_model_of(fn)] = (idx, call.get("id"))
    keep_ids = {call_id for _idx, call_id in newest.values()}
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    old_results = set(tool_idx[:-_KEEP_FULL_RESULTS])
    out = []
    for idx, msg in enumerate(messages):
        content = msg.get("content")
        if msg.get("tool_calls"):
            out.append({**msg, "tool_calls": [_strip_code(c, keep_ids)
                                              for c in msg["tool_calls"]]})
        elif (idx in old_results and isinstance(content, str)
              and len(content) > _OLD_RESULT_CHARS):
            out.append({**msg, "content": content[:_OLD_RESULT_CHARS] + " …(truncated)"})
        else:
            out.append(msg)
    return out


def _model_of(fn):
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except ValueError:
        return ""
    return str(args.get("name", "")) if isinstance(args, dict) else ""


def _strip_code(call, keep_ids):
    fn = call.get("function", {}) if isinstance(call, dict) else {}
    if fn.get("name") not in _CODE_TOOLS or call.get("id") in keep_ids:
        return call
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except ValueError:
        return call
    if not isinstance(args, dict) or "code" not in args:
        return call
    args["code"] = _OMITTED_CODE
    return {**call, "function": {**fn, "arguments": json.dumps(args, ensure_ascii=False)}}


# ------------------------------------------------------------- images

def with_image_refs(msg_content, refs):
    """Store a user message whose images are REFERENCE records.

    The stored content becomes a parts list: the text plus
    {"type": "image_ref", ...} dicts. Only fresh turns re-expand to
    base64 (see ``expand_images``); history stays bounded because old
    turns keep the small refs (placeholders in prompts).
    """
    if not refs:
        return msg_content
    parts = []
    if msg_content:
        parts.append({"type": "text", "text": msg_content})
    parts.extend(refs)
    return parts


def expand_images(messages, resolver):
    """Return the request-bound copy: latest user turn's refs -> base64
    parts, every older ref -> placeholder text.

    ``resolver(ref) -> content part dict`` for fresh images (worker
    thread: encodes the PNG). Older refs become
    ``[image: label, WxH]`` text, which keeps tokens bounded — the model
    has already acted on them.
    """
    last_user = max((i for i, m in enumerate(messages)
                     if m.get("role") == "user"), default=-1)
    out = []
    for idx, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list) or not any(
                isinstance(p, dict) and p.get("type") == "image_ref"
                for p in content):
            out.append(msg)
            continue
        parts = []
        fresh = idx == last_user
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_ref":
                if fresh:
                    parts.append(resolver(part))
                else:
                    parts.append({"type": "text", "text":
                                  "[image: %s, %sx%s]" % (part.get("label", "?"),
                                                          part.get("w", 0),
                                                          part.get("h", 0))})
            else:
                parts.append(part)
        new_msg = dict(msg)
        new_msg["content"] = parts
        out.append(new_msg)
    return out


_INTERRUPTED_RESULT = (
    "ERROR: tool call was interrupted before it ran (crash or stop); "
    "no result exists. Do not assume it had any effect."
)


def reconcile(messages):
    """Return a protocol-valid copy of ``messages`` (input untouched).

    Blender can crash, or a session can be saved while an assistant
    ``tool_calls`` block is still open (an approval was parked) — the
    stored history then holds calls without results. Providers reject
    such requests, so dangling calls get a synthetic error result and
    orphan results are dropped. Applied before every request and on
    history restore.
    """
    out = []
    open_ids = []  # tool_call_ids of the current block awaiting a result

    def _close_block():
        for call_id in open_ids:
            out.append(message("tool", content=_INTERRUPTED_RESULT,
                               tool_call_id=call_id))
        del open_ids[:]

    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            _close_block()
            out.append(msg)
            for call in msg["tool_calls"]:
                if isinstance(call, dict) and call.get("id"):
                    open_ids.append(call["id"])
        elif role == "tool":
            call_id = msg.get("tool_call_id")
            if call_id and call_id in open_ids:
                open_ids.remove(call_id)
                out.append(msg)
            # else: orphan or duplicate result — drop it
        else:
            _close_block()
            out.append(msg)
    _close_block()
    return out


def to_json(messages):
    return json.dumps(messages, ensure_ascii=False)


_INTERNAL_KEYS = ("reasoning", "approval", "tool_name", "auto")
_SENDABLE_ROLES = frozenset({"system", "user", "assistant", "tool", "developer"})


def outgoing_snapshot(messages):
    """Provider-bound copy of the history (pure, unit-tested).

    - strips internal keys ("reasoning" / "approval" / "tool_name");
    - drops internal-only roles: OpenAI-compatible APIs reject unknown
      roles such as the addon's local "error" entries, so leaving them in
      would make every subsequent request fail with HTTP 400;
    - reconcile()s dangling tool_calls/results away.
    """
    return [
        {k: v for k, v in msg.items() if k not in _INTERNAL_KEYS}
        for msg in reconcile(messages)
        if msg.get("role") in _SENDABLE_ROLES
    ]


def from_json(text):
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("history JSON must be a list")
    return data


def save(messages, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(messages), encoding="utf-8")


def load(path):
    try:
        return from_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
