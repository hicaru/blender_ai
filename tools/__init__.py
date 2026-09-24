"""Tool registry for the AI agent.
# mypy: ignore-errors

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

__all__ = ("TOOL_REGISTRY", "ToolError", "register", "tools_schema")


class ToolError(RuntimeError):
    """Raised by tools on invalid input; converted to an ERROR tool result."""


TOOL_REGISTRY = {}


def register(name, description, parameters, func,
             approval="never", pause=False):
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


def tools_schema(profile: str = "full") -> list[dict]:
    if profile == "compact":
        return [entry["schema"] for name, entry in TOOL_REGISTRY.items()
                if name not in _COMPACT_DROP]
    return [entry["schema"] for entry in TOOL_REGISTRY.values()]


def _vec3_schema(description: str) -> dict:
    return {
        "type": "array",
        "items": {"type": "number"},
        "minItems": 3,
        "maxItems": 3,
        "description": description,
    }


# Populated by the tool modules below (import side effect); registry
# must exist first, so these module-level imports follow it on purpose.
from . import (
    addons,
    interactive,
    materials,
    mesh_ops,
    modifiers,
    scene,
    sculpt,
    uv,
)
from . import pipeline as _pipeline_tools

# Dropped from the schema by the "compact" tool profile: rarely used by
# game-asset work, and fewer schemas measurably helps smaller models.
_COMPACT_DROP = frozenset({"sculpt_setup", "install_addon", "list_addons"})
