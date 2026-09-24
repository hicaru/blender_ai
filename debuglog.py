"""Append-only debug log — every agent event for post-mortems.

The system console is gone by the time a stall is investigated; this
file survives. Default path: <tempdir>/blender_ai_debug.log (macOS:
/var/folders/.../T/). ``BLENDER_AI_LOG`` overrides it — the smoke test
points it elsewhere so test runs never pollute the user's log.

When the file exceeds ``_MAX_BYTES`` it is rotated to ``.1`` (one
generation kept). Logging never raises: it must not break the agent.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Final

__all__ = ("log", "path", "warn")

_MAX_BYTES: Final = 2_000_000
_MAX_LINE: Final = 4000


def path() -> str:
    return os.environ.get("BLENDER_AI_LOG") or os.path.join(
        tempfile.gettempdir(), "blender_ai_debug.log")


def _rotate(target: str) -> None:
    try:
        if os.path.getsize(target) > _MAX_BYTES:
            os.replace(target, f"{target}.1")
    except OSError:
        pass  # missing file or no permission: keep appending


def log(event: str, **kv: object) -> None:
    """One line per event: ``HH:MM:SS event {json}``."""
    line = f"{time.strftime('%H:%M:%S')} {event}"
    if kv:
        line = f"{line} {json.dumps(kv, ensure_ascii=False, default=str)}"
    target = path()
    _rotate(target)
    try:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line[:_MAX_LINE] + "\n")
    except OSError:
        pass  # no writable temp dir / disk full — logging is best-effort


def warn(event: str, **kv: object) -> None:
    """Severity-marked log line; same sink."""
    log(f"warn {event}", **kv)
