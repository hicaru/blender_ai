"""Extension/add-on management tools: inspect, install, uninstall.

Install and uninstall are gated (``approval="code"``): the panel shows
what will be installed/removed and the loop parks until the user approves.
"""

import json

import addon_utils
import bpy

from . import ToolError, register

_MAX_LISTED = 60
_MAX_URL_BYTES = 200 * 1024 * 1024  # 200 MB safety cap on downloaded zips


def _is_enabled(module_name):
    return module_name in bpy.context.preferences.addons


def list_extensions(query=None, enabled_only=False):
    """List installed add-ons/extensions (name, module, version, enabled)."""
    query = (query or "").strip().lower()
    result = []
    for mod in addon_utils.modules():
        module = mod.__name__
        if enabled_only and not _is_enabled(module):
            continue
        try:
            info = addon_utils.module_bl_info(mod)
        except Exception:  # noqa: BLE001 — a broken module must not break listing
            info = {}
        name = str(info.get("name") or module)
        if query and query not in (module + " " + name).lower():
            continue
        version = info.get("version") or ()
        result.append({
            "module": module,
            "name": name,
            "version": ".".join(str(v) for v in version) if version else "",
            "enabled": _is_enabled(module),
            "description": str(info.get("description") or "")[:160],
        })
        if len(result) >= _MAX_LISTED:
            break
    return json.dumps(result, ensure_ascii=False)


def _resolve_repo_directory(repo_module):
    repos = bpy.context.preferences.extensions.repos
    for repo in repos:
        if repo.module == repo_module:
            return bpy.path.abspath(repo.directory)
    return None


def _download_zip(url):
    if not url.lower().startswith("https://"):
        # Plain http allows tampered extension zips; Blender's own
        # extension repos are https-only too.
        raise ToolError("source URL must start with https://")
    if not bpy.app.online_access:
        raise ToolError("Blender's online access is disabled")
    import os
    import tempfile

    import requests
    try:
        response = requests.get(url, timeout=60, stream=True)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ToolError("download failed: %s" % exc) from exc
    data = bytearray()
    for chunk in response.iter_content(chunk_size=1 << 16):
        data += chunk
        if len(data) > _MAX_URL_BYTES:
            raise ToolError("download exceeds the %d MB safety cap"
                            % (_MAX_URL_BYTES // (1024 * 1024)))
    fd, path = tempfile.mkstemp(suffix=".zip")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


def install_extension(source, repo="user_default", enable=True):
    """Install an add-on extension from a local .zip path or an https URL.

    Gated: runs only after the user approves the proposed action.
    """
    source = str(source).strip()
    if source.lower().startswith(("http://", "https://")):
        if source.lower().startswith("http://"):
            raise ToolError("only https:// URLs are accepted")
        filepath = _download_zip(source)
        cleanup = True
    else:
        import os
        filepath = bpy.path.abspath(source)
        if not os.path.isfile(filepath):
            raise ToolError("no such file: %r" % source)
        cleanup = False

    if repo not in {r.module for r in bpy.context.preferences.extensions.repos}:
        raise ToolError("unknown repository %r" % repo)

    try:
        bpy.ops.extensions.package_install_files(
            filepath=filepath, repo=repo, enable_on_install=bool(enable))
    except Exception as exc:  # noqa: BLE001 — surfaced as an ERROR result
        raise ToolError("install failed: %s" % exc)
    finally:
        if cleanup:
            import os
            try:
                os.remove(filepath)
            except OSError:
                pass
    return "installed extension from %s into repository %r (enabled=%s)" % (
        source, repo, bool(enable))


def uninstall_extension(module):
    """Disable and remove an installed extension by module name.

    Accepts the full module (``bl_ext.user_default.some_addon``) or the
    plain id (``some_addon``). Gated: runs only after user approval.
    """
    module = str(module).strip()
    if module.startswith("bl_ext."):
        parts = module.split(".", 2)
        if len(parts) != 3:
            raise ToolError("expected module like bl_ext.<repo>.<id>")
        repo_module, pkg_id = parts[1], parts[2]
    else:
        pkg_id = module
        matches = [m.__name__ for m in addon_utils.modules()
                   if m.__name__.endswith("." + pkg_id)]
        if not matches:
            raise ToolError("no installed extension with id %r" % module)
        repo_module = matches[0].split(".")[1]

    directory = _resolve_repo_directory(repo_module)
    if directory is None:
        raise ToolError("repository %r not found" % repo_module)

    addon_utils.disable("bl_ext.%s.%s" % (repo_module, pkg_id),
                        default_set=True)
    try:
        bpy.ops.extensions.package_uninstall(
            repo_directory=directory, pkg_id=pkg_id)
    except Exception as exc:  # noqa: BLE001
        raise ToolError("uninstall failed: %s" % exc)
    return "uninstalled %r from repository %r" % (pkg_id, repo_module)


def register_tools():
    register(
        "list_extensions",
        "List installed Blender add-ons/extensions: module, name, version, "
        "enabled state. Use this to check whether an add-on is already "
        "installed before installing anything.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Case-insensitive substring filter"},
                "enabled_only": {"type": "boolean", "default": False},
            },
        },
        list_extensions,
    )
    register(
        "install_extension",
        "Install a Blender extension (add-on) from a local .zip file path "
        "or a direct https URL to a .zip. Requires the user's approval: "
        "always confirm the source with ask_user before calling this. "
        "Check list_extensions first to avoid duplicates.",
        {
            "type": "object",
            "properties": {
                "source": {"type": "string",
                           "description": "Local .zip path or https URL"},
                "repo": {"type": "string", "default": "user_default"},
                "enable": {"type": "boolean", "default": True},
            },
            "required": ["source"],
        },
        install_extension,
        approval="code",
    )
    register(
        "uninstall_extension",
        "Disable and remove an installed extension. Requires the user's "
        "approval: confirm with ask_user before calling this.",
        {
            "type": "object",
            "properties": {
                "module": {"type": "string",
                           "description": "bl_ext.<repo>.<id> or plain id"},
            },
            "required": ["module"],
        },
        uninstall_extension,
        approval="code",
    )


register_tools()
