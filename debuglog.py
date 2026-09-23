"""Append-only debug log under /tmp — every agent event for post-mortems.

The addon also prints errors to Blender's system console, but that console
is gone by the time a hang is investigated; the log file survives.
"""

import json
import time

_PATH = "/tmp/blender_ai_debug.log"


def path():
    return _PATH


def log(event, **kv):
    """One line per event. Never raises — logging must not break the agent."""
    try:
        line = "%s %s" % (time.strftime("%H:%M:%S"), event)
        if kv:
            line += " " + json.dumps(kv, ensure_ascii=False, default=str)
        with open(_PATH, "a", encoding="utf-8") as fh:
            fh.write(line[:2000] + "\n")
    except OSError:
        pass
