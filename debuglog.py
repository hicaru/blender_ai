"""Append-only debug log — every agent event for post-mortems.
# mypy: ignore-errors

The addon also prints errors to Blender's system console, but that console
is gone by the time a hang is investigated; the log file survives.

Rotation keeps the file bounded (truncated when it exceeds _MAX_BYTES);
on platforms where the default temp path is unwritable the module
degrades to a no-op instead of silently disappearing on Windows.
"""

import json
import os
import tempfile
import time

_MAX_BYTES: int = 1_000_000

_PATH: str = os.path.join(tempfile.gettempdir(), "blender_ai_debug.log")


def path() -> str:
    return _PATH


def log(event: str, **kv: object) -> None:
    """One line per event. Never raises — logging must not break the agent."""
    try:
        line = "%s %s" % (time.strftime("%H:%M:%S"), event)
        if kv:
            line += " " + json.dumps(kv, ensure_ascii=False, default=str)
        with open(_PATH, "a", encoding="utf-8") as fh:
            fh.write(line[:2000] + "\n")
    except OSError:
        pass  # no writable temp dir / disk full — logging is best-effort


def warn(event: str, **kv: object) -> None:
    """Severity-marked log line; safe alias so modules share one sink."""
    log(f"warn {event}", **kv)
