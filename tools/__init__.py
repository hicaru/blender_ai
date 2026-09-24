"""Tool registry for the AI agent.

Every tool registers itself with a JSON schema (OpenAI ``tools`` format),
an implementation callable and a safety policy:

- ``approval="never"`` — safe by construction;
- ``approval="code"``  — runs model-written Python (build_model,
  run_python); executed only after user approval or auto-approve;
- ``pause=True``       — the agent loop parks until the user answers
  (``ask_user``);
- ``vision=True``      — only offered when the model accepts images.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

__all__ = ("TOOL_REGISTRY", "ToolError", "register", "tools_schema")


class ToolError(RuntimeError):
    """Raised by tools on invalid input; converted to an ERROR tool result."""


TOOL_REGISTRY: Final[dict[str, dict[str, Any]]] = {}


def register(name: str, description: str, parameters: dict[str, Any],
             func: Callable[..., str], approval: str = "never",
             pause: bool = False, vision: bool = False) -> None:
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
        "vision": vision,
    }


def tools_schema(vision: bool = False) -> list[dict[str, Any]]:
    return [entry["schema"] for entry in TOOL_REGISTRY.values()
            if vision or not entry["vision"]]


# Populated by the tool modules below (import side effect); the registry
# must exist first, so these imports follow it on purpose.
from . import build, interactive
