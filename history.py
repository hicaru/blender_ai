"""Chat history: pure list-of-dict logic + JSON persistence.

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

__all__ = ("message", "append", "trim", "reconcile", "to_json", "from_json",
           "save", "load")


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
