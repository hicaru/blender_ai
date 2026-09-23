"""Tool execution on the main thread: dispatch, undo push, run_python gate.

``dispatch`` is the only entry the agent loop uses. It runs on the main
thread, pushes an undo step per tool call (guarded — ``undo_push`` needs a
window manager and is skipped in ``--background``), and enforces the
approval gate for ``run_python``: without ``auto_approve_code`` the code is
NOT executed; it is parked in the panel and the agent loop stops until the
user approves or rejects.
"""

import contextlib
import io
import textwrap

import bmesh
import bpy
import math
import mathutils

from .tools import TOOL_REGISTRY, ToolError
from .tools import tools_schema as _tools_schema  # re-export

__all__ = ("dispatch", "execute_python", "tools_schema")

_MAX_OUTPUT = 4000


def _prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _format_arguments(arguments):
    """Human-readable summary of gated call arguments (for the panel)."""
    import json
    try:
        return json.dumps(arguments, ensure_ascii=False, indent=1)
    except (TypeError, ValueError):
        return str(arguments)


def dispatch(name, arguments, force=False):
    """Execute one tool call. Returns one of:

    - ``{"ok": True,  "result": str}``
    - ``{"ok": False, "result": str}`` — errors as ERROR strings
    - ``{"pending": True, "kind": "code"|"ask"}`` — parked for the user

    Gated tools (``approval="code"``) — ``run_python``, extension install /
    uninstall — park until the user approves unless ``force=True`` (set by
    :func:`execute_tool` after the user approved) or the auto-approve
    preference is on.
    """
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        return {"ok": False, "result": "ERROR: unknown tool %r" % name}

    prefs = _prefs()
    if tool["approval"] == "code" and not force and not (prefs and prefs.auto_approve_code):
        wm = bpy.context.window_manager
        wm.blender_ai_pending_code = (
            str(arguments.get("code", "")) or _format_arguments(arguments)
        )
        return {"pending": True, "kind": "code"}

    if not bpy.app.background:
        try:
            bpy.ops.ed.undo_push(message="AI: %s" % name)
        except RuntimeError:
            pass

    try:
        result = tool["func"](**arguments)
    except ToolError as exc:
        return {"ok": False, "result": "ERROR: %s" % exc}
    except TypeError as exc:
        return {"ok": False, "result": "ERROR: bad arguments: %s" % exc}
    except Exception as exc:  # noqa: BLE001 — tool results must stay strings
        return {"ok": False, "result": "ERROR: %s: %s" % (type(exc).__name__, exc)}

    if tool.get("pause"):
        return {"pending": True, "kind": "ask"}

    return {"ok": True, "result": str(result)}


def execute_tool(name, arguments):
    """Run a gated tool after the user explicitly approved it (no re-gate)."""
    outcome = dispatch(name, arguments, force=True)
    return outcome.get("result", "")


def execute_python(code):
    """Execute generated code on the main thread (used after approval).

    The code runs with bpy/bmesh/mathutils/math in scope; stdout is
    captured and returned (truncated).
    """
    namespace = {
        "bpy": bpy,
        "bmesh": bmesh,
        "mathutils": mathutils,
        "math": math,
        "textwrap": textwrap,
    }
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            exec(compile(code, "<blender_ai>", "exec"), namespace)  # noqa: S102
        output = stdout.getvalue().strip()
        return output[:_MAX_OUTPUT] or "OK (no output)"
    except Exception as exc:  # noqa: BLE001 — errors go back to the model
        output = stdout.getvalue().strip()
        error = "%s: %s" % (type(exc).__name__, exc)
        if output:
            error = output[:500] + "\n" + error
        return "ERROR: %s" % error


# Register the run_python tool here (not in tools/) so the gate lives next
# to its executor.
def _run_python(code):
    """(gate description only; execution goes through execute_python)"""
    return execute_python(code)


TOOL_REGISTRY["run_python"] = {
    "schema": {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Run arbitrary bpy Python code for anything the structural "
                "tools cannot do. Keep the code short and readable; print "
                "useful results. Runs on the main thread ONLY after the "
                "user approves it in the panel."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string",
                             "description": "Python source using bpy/bmesh/mathutils"},
                },
                "required": ["code"],
            },
        },
    },
    "func": _run_python,
    "approval": "code",
    "pause": False,
}


def tools_schema():
    return _tools_schema()
