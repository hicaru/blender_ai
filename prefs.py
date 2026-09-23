"""Addon preferences: provider, API keys, model, sampling, safety switches.

Model list: the "Fetch" button downloads the provider's ``/models`` list in
a worker thread (timer-polled on the main thread — official Blender
threading pattern) and caches it per provider in ``models_cache`` (JSON).
The ``model_choice`` dropdown reads only from that cache, so drawing never
blocks on network. A free-text model id stays available for custom models.

API keys are stored by Blender in preferences plaintext. That is standard
Blender behaviour for add-on settings; the preference panel says so.
"""

import json
import threading

import bpy

from . import providers

# Fixed order also defines the enum default (first item).
_PROVIDER_ORDER = ("zai", "deepseek", "openrouter")

# State of the background model-list fetch (at most one at a time).
_FETCH = {"thread": None, "result": None}


def _provider_items(self, context):
    return [
        (pid, providers.PROVIDERS[pid]["label"], providers.PROVIDERS[pid]["base_url"])
        for pid in _PROVIDER_ORDER
    ]


def _cached_models(self):
    """Model ids cached for the current provider (never raises)."""
    try:
        cache = json.loads(self.models_cache)
        ids = cache.get(self.provider, [])
    except (ValueError, AttributeError):
        return []
    return [str(i) for i in ids if isinstance(i, str)]


def _model_items(self, context):
    items = [(mid, mid, "") for mid in _cached_models(self)]
    known = {mid for mid, _label, _desc in items}
    current = self.model.strip()
    if current and current not in known:
        items.insert(0, (current, current, "Current model setting"))
    if not items:
        items = [("", "(none — fetch or type a model id)", "")]
    return items


def _model_choice_update(self, context):
    if self.model_choice:
        self.model = self.model_choice


class AI_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    provider: bpy.props.EnumProperty(
        name="Provider",
        description="LLM provider (OpenAI-compatible chat completions)",
        items=_provider_items,
    )
    api_key_zai: bpy.props.StringProperty(
        name="Z.ai API Key",
        description="API key for api.z.ai (stored in preferences plaintext, standard Blender behaviour)",
        subtype='PASSWORD',
    )
    api_key_deepseek: bpy.props.StringProperty(
        name="DeepSeek API Key",
        description="API key for api.deepseek.com (stored in preferences plaintext, standard Blender behaviour)",
        subtype='PASSWORD',
    )
    api_key_openrouter: bpy.props.StringProperty(
        name="OpenRouter API Key",
        description="API key for openrouter.ai (stored in preferences plaintext, standard Blender behaviour)",
        subtype='PASSWORD',
    )
    models_cache: bpy.props.StringProperty(
        name="Model list cache",
        description="Fetched model ids per provider, as JSON",
        default="{}",
    )
    model_choice: bpy.props.EnumProperty(
        name="Models",
        description="Models reported by the provider (refresh with the button)",
        items=_model_items,
        update=_model_choice_update,
    )
    fetch_status: bpy.props.StringProperty(
        name="Fetch status",
        description="Status of the last model-list fetch",
    )
    model: bpy.props.StringProperty(
        name="Model",
        description="Model id; empty uses the provider default",
    )
    temperature: bpy.props.FloatProperty(
        name="Temperature",
        description="Sampling temperature for the model",
        default=0.4,
        min=0.0,
        max=2.0,
    )
    auto_approve_code: bpy.props.BoolProperty(
        name="Auto-approve generated code",
        description="Run run_python code without confirmation. Only enable if you trust the model",
        default=False,
    )
    history_limit: bpy.props.IntProperty(
        name="History limit",
        description="Trim conversation to this many messages before each request (bounded state)",
        default=80,
        min=10,
        max=1000,
    )

    def get_api_key(self):
        return getattr(self, "api_key_%s" % self.provider, "")

    def get_model(self):
        model = self.model.strip()
        if model:
            return model
        return providers.PROVIDERS[self.provider]["default_model"]

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "provider")
        layout.prop(self, "api_key_%s" % self.provider)

        row = layout.row(align=True)
        row.prop(self, "model_choice", text="Models")
        row.operator("blender_ai.fetch_models", text="", icon='FILE_REFRESH')
        if self.fetch_status:
            layout.label(text=self.fetch_status, icon='INFO')

        default_model = providers.PROVIDERS[self.provider]["default_model"]
        layout.prop(self, "model", placeholder=default_model or "model id, e.g. openai/gpt-4o-mini")
        layout.prop(self, "temperature")
        layout.prop(self, "auto_approve_code")
        layout.prop(self, "history_limit")


def _prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _worker(provider_id, api_key):
    try:
        _FETCH["result"] = providers.list_models(provider_id, api_key)
    except Exception as exc:  # noqa: BLE001 — surfaced by the timer
        _FETCH["result"] = exc


def _fetch_poll():
    thread = _FETCH.get("thread")
    if thread is not None and thread.is_alive():
        return 0.1
    _FETCH["thread"] = None
    result = _FETCH.get("result")
    _FETCH["result"] = None

    prefs = _prefs()
    if prefs is None:
        return None
    if isinstance(result, Exception):
        prefs.fetch_status = "error: %s" % result
        return None

    try:
        cache = json.loads(prefs.models_cache) if prefs.models_cache else {}
    except ValueError:
        cache = {}
    cache[prefs.provider] = result
    prefs.models_cache = json.dumps(cache, ensure_ascii=False)
    prefs.fetch_status = "%d models loaded" % len(result)
    return None


class AI_OT_fetch_models(bpy.types.Operator):
    """Download the model list from the provider"""

    bl_idname = "blender_ai.fetch_models"
    bl_label = "Fetch model list"
    bl_description = "Download available model ids from the provider /models endpoint"

    @classmethod
    def poll(cls, context):
        thread = _FETCH.get("thread")
        return thread is None or not thread.is_alive()

    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        provider_id = prefs.provider
        api_key = prefs.get_api_key()
        if not api_key and provider_id != "openrouter":
            self.report({'WARNING'}, "Enter the API key first")
            return {'CANCELLED'}

        prefs.fetch_status = "fetching models…"
        _FETCH["result"] = None
        thread = threading.Thread(target=_worker, args=(provider_id, api_key), daemon=True)
        _FETCH["thread"] = thread
        thread.start()
        if not bpy.app.timers.is_registered(_fetch_poll):
            bpy.app.timers.register(_fetch_poll, first_interval=0.1)
        return {'FINISHED'}


classes = (AI_OT_fetch_models, AI_AddonPreferences)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    if bpy.app.timers.is_registered(_fetch_poll):
        bpy.app.timers.unregister(_fetch_poll)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
