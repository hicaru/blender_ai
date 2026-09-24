"""Tool execution on the main thread: dispatch, undo push, approval gate.

``dispatch`` is the only entry the agent loop uses. It pushes an undo step
per successful tool call (skipped in ``--background``) and enforces the
approval gate: a tool registered with ``approval="code"`` (build_model
runs model-written Python) is NOT executed without ``auto_approve_code``;
it is parked in the panel until the user approves or rejects.
"""

from __future__ import annotations

import contextlib
from typing import Any

import bpy

from .prefs import get_prefs
from .tools import TOOL_REGISTRY, ToolError, tools_schema

__all__ = ("dispatch", "execute_tool", "tools_schema")

Outcome = dict[str, Any]


def dispatch(name: str, arguments: dict[str, Any], force: bool = False) -> Outcome:  # noqa: PLR0911 — gate outcomes
    """Execute one tool call. Returns one of:

    - ``{"ok": True,  "result": str}``
    - ``{"ok": False, "result": "ERROR: ..."}``
    - ``{"pending": True, "kind": "code"|"ask"}`` — parked for the user
    """
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        known = ", ".join(TOOL_REGISTRY)
        return {"ok": False, "result": f"ERROR: unknown tool {name!r}; tools: {known}"}

    prefs = get_prefs()  # type: ignore[no-untyped-call]
    auto = bool(prefs is not None and prefs.auto_approve_code)
    if tool["approval"] == "code" and not force and not auto:
        return {"pending": True, "kind": "code"}

    try:
        result = tool["func"](**arguments)
    except ToolError as exc:
        return {"ok": False, "result": f"ERROR: {exc}"}
    except TypeError as exc:
        return {"ok": False, "result": f"ERROR: bad arguments: {exc}"}
    except (RuntimeError, ValueError, KeyError, OSError) as exc:
        # bpy.ops raises RuntimeError for poll/context failures, ValueError
        # for bad enum values. Anything else is a real bug and propagates
        # to the poll boundary, which logs it.
        return {"ok": False, "result": f"ERROR: {type(exc).__name__}: {exc}"}

    # Undo step AFTER the change succeeded: pushed before, Ctrl+Z would
    # first "eat" the agent's step instead of reverting its effect.
    if not bpy.app.background:
        with contextlib.suppress(RuntimeError):
            bpy.ops.ed.undo_push(message=f"AI: {name}")

    if tool["pause"]:
        return {"pending": True, "kind": "ask"}
    return {"ok": True, "result": str(result)}


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Run a gated tool after the user explicitly approved it (no re-gate)."""
    return str(dispatch(name, arguments, force=True).get("result", ""))
