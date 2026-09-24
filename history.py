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
    "from_json",
    "load",
    "message",
    "reconcile",
    "save",
    "to_json",
    "trim",
)


def message(role, content="", tool_calls=None, tool_name="", tool_call_id="", approval="", reasoning=""):
    """Build one history dict, omitting empty optional keys."""
    msg = {"role": role, "content": content}
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
    return trimmed


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


_INTERNAL_KEYS = ("reasoning", "approval", "tool_name")
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
