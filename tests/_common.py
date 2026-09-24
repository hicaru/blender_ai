"""Shared helpers for the pure-python unit tests (stdlib only).
# mypy: ignore-errors

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
    import importlib.util

    # A previously-loaded test may have stubbed `requests` in sys.modules;
    # find_spec raises ValueError when a stub has no __spec__.
    try:
        if importlib.util.find_spec("requests") is not None:
            return
    except ValueError:
        return  # already stubbed by an earlier test module
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


def load_package(name):
    """Load a package or a submodule inside one (bpy-dependent parents get stubs)."""
    import importlib
    parts = name.split(".")
    # register/stub parent packages so submodule relative imports resolve
    for depth in range(1, len(parts)):
        parent = ".".join(parts[:depth])
        if parent in sys.modules:
            continue
        pkg_dir = os.path.join(ROOT, *parts[:depth])
        init = os.path.join(pkg_dir, "__init__.py")
        if not os.path.exists(init):
            stub = types.ModuleType(parent)
            stub.__path__ = [pkg_dir]
            sys.modules[parent] = stub
            continue
        spec = importlib.util.spec_from_file_location(
            parent, init, submodule_search_locations=[pkg_dir])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[parent] = mod
        spec.loader.exec_module(mod)
    pkg_dir = os.path.join(ROOT, *parts[:-1]) if len(parts) > 1 else os.path.join(ROOT, *parts)
    if len(parts) == 1:
        init = os.path.join(pkg_dir, "__init__.py")
    else:
        init = os.path.join(pkg_dir, parts[-1] + ".py")
    spec = importlib.util.spec_from_file_location(name, init)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
