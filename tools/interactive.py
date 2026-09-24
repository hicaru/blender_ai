"""ask_user: the one tool that parks the loop for a human answer."""

from __future__ import annotations

from . import register


def ask_user(question: str, options: list[str] | None = None) -> str:
    """Parks the agent loop (pause=True); the Answer operator resumes it."""
    return f"asked the user: {question}"


register(
    "ask_user",
    "Ask the user ONE question and wait. Only when the request is truly ambiguous "
    "(never for things you can decide yourself). Give 2-4 short options.",
    {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["question"],
    },
    ask_user,
    pause=True,
)
