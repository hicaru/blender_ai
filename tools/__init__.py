"""Tool registry for the AI agent.

Every tool registers itself with a JSON schema (OpenAI ``tools`` format),
an implementation callable and a safety policy:

- ``approval="never"`` — structural tools, safe by construction;
- ``approval="code"``  — ``run_python``; executed only after user approval;
- ``pause=True``       — the agent loop parks until the user resolves the
  call in the panel (``ask_user``; also how ``run_python`` waits for
  Approve/Reject).

Schemas follow ``{"type": "function", "function": {name, description,
parameters}}``.
"""

__all__ = ("ToolError", "register", "tools_schema", "TOOL_REGISTRY")


class ToolError(RuntimeError):
    """Raised by tools on invalid input; converted to an ERROR tool result."""


TOOL_REGISTRY = {}


def register(name, description, parameters, func, approval="never", pause=False):
    TOOL_REGISTRY[name] = {
        "schema": {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        },
        "func": func,
        "approval": approval,
        "pause": pause,
    }


def tools_schema():
    return [entry["schema"] for entry in TOOL_REGISTRY.values()]


def _vec3_schema(description):
    return {
        "type": "array",
        "items": {"type": "number"},
        "minItems": 3,
        "maxItems": 3,
        "description": description,
    }


# Populated by the tool modules below (import side effect).
from . import scene      # noqa: E402,F401
from . import modifiers  # noqa: E402,F401
from . import materials  # noqa: E402,F401
from . import uv         # noqa: E402,F401
from . import sculpt     # noqa: E402,F401
from . import interactive  # noqa: E402,F401
from . import addons     # noqa: E402,F401
