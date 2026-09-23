"""Shared helpers for the pure-python unit tests (stdlib only).

Loads addon modules directly by file path — importing the ``blender_ai``
package would pull in ``bpy``. Provides a minimal ``requests`` stub because
system python may not have it; the real module is used when present.
"""

import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RequestExceptionStub(Exception):
    pass


def _ensure_requests():
    try:
        import requests  # noqa: F401
        return
    except ImportError:
        pass
    stub = types.ModuleType("requests")
    stub.RequestException = RequestExceptionStub

    def _post(*_args, **_kwargs):  # replaced per-test
        raise RequestExceptionStub("network disabled in tests")

    def _get(*_args, **_kwargs):  # replaced per-test
        raise RequestExceptionStub("network disabled in tests")

    stub.post = _post
    stub.get = _get
    sys.modules["requests"] = stub


def load_module(name, path=None):
    """Load a top-level addon module without importing the package."""
    _ensure_requests()
    if path is None:
        path = os.path.join(ROOT, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
