"""Addon preferences: provider, API keys, model, sampling, safety switches.
# mypy: ignore-errors

Model list: fetched AUTOMATICALLY in a worker thread (timer-polled on the
main thread — official Blender threading pattern) at startup and whenever
an API key or the provider changes; the "Fetch" button only forces it.
Cached per provider in ``models_cache`` (JSON) together with the learned
vision capabilities (``_vision`` key), so image support survives restarts
and the user never configures a vision model.
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
# result: {provider_id: [ids] | Exception}
_FETCH = {"thread": None, "result": None}
_VISION_KEY = "_vision"


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


def _auto_fetch_update(self, context):
    """API key or provider changed: refresh model lists in the background."""
    start_fetch(self)


class AI_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    provider: bpy.props.EnumProperty(
        name="Provider",
        description="LLM provider (OpenAI-compatible chat completions)",
        items=_provider_items,
        update=_auto_fetch_update,
    )
    api_key_zai: bpy.props.StringProperty(
        update=_auto_fetch_update,
        name="Z.ai API Key",
        description="API key for api.z.ai (stored in preferences plaintext, standard Blender behaviour)",
        subtype='PASSWORD',
    )
    api_key_deepseek: bpy.props.StringProperty(
        update=_auto_fetch_update,
        name="DeepSeek API Key",
        description="API key for api.deepseek.com (stored in preferences plaintext, standard Blender behaviour)",
        subtype='PASSWORD',
    )
    api_key_openrouter: bpy.props.StringProperty(
        update=_auto_fetch_update,
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
        description="Run build scripts without confirmation (faster builds). Only enable if you trust the model",
        default=False,
    )
    export_dir: bpy.props.StringProperty(
        name="Export directory",
        description="Where export_glb writes <model>.glb for Bevy. "
                    "Empty = an 'exports' folder next to the .blend",
        subtype="DIR_PATH",
    )
    reasoning_effort: bpy.props.EnumProperty(
        name="Reasoning effort",
        description="How much the model reasons before answering. "
                    "xhigh/max are capped on providers that don't support them",
        items=(
            ("off", "Off", "No reasoning — fastest and cheapest"),
            ("low", "Low", "Light reasoning"),
            ("medium", "Medium", "Balanced reasoning"),
            ("high", "High", "Thorough reasoning"),
            ("xhigh", "XHigh", "Very deep reasoning (OpenRouter; DeepSeek caps at high)"),
            ("max", "Max", "Deepest reasoning the provider supports"),
        ),
        default="medium",
    )
    history_limit: bpy.props.IntProperty(
        name="History limit",
        description="Trim conversation to this many messages before each request "
                    "(the task message is always kept; old scripts are compacted)",
        default=150,
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
        layout.prop(self, "reasoning_effort")
        layout.prop(self, "auto_approve_code")
        layout.prop(self, "history_limit")
        layout.separator()
        layout.label(text="Export (Bevy)")
        layout.prop(self, "export_dir")
        model = self.get_model()
        vision = ("sees images" if providers.supports_vision(self.provider, model)
                  else "text only - images are described by %s"
                  % (_captioner_label(self) or "no vision model available"))
        layout.label(text="%s: %s" % (model or "no model", vision), icon='IMAGE_DATA')


def get_prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


# legacy private alias
_prefs = get_prefs


def _captioner_label(prefs):
    """provider/model that would describe images for a text-only model."""
    pick = pick_captioner(prefs)
    return "%s/%s" % pick if pick else ""


def pick_captioner(prefs):
    """(provider_id, model) of a vision model the user has a key for, or None.

    Same provider first (no extra account needed), then any other
    provider with a key. Fully automatic — nothing to configure.
    """
    order = [prefs.provider] + [p for p in _PROVIDER_ORDER if p != prefs.provider]
    for provider_id in order:
        key = getattr(prefs, "api_key_%s" % provider_id, "")
        if not key:
            continue
        model = providers.auto_vision_model(provider_id)
        if model:
            return provider_id, model
    return None


def save_vision_state(prefs=None):
    """Persist learned vision capabilities into models_cache (main thread)."""
    prefs = prefs or get_prefs()
    if prefs is None:
        return
    try:
        cache = json.loads(prefs.models_cache) if prefs.models_cache else {}
    except ValueError:
        cache = {}
    cache[_VISION_KEY] = providers.vision_state()
    prefs.models_cache = json.dumps(cache, ensure_ascii=False)


def _load_vision_state(prefs):
    try:
        cache = json.loads(prefs.models_cache) if prefs.models_cache else {}
    except ValueError:
        return
    providers.load_vision_state(cache.get(_VISION_KEY))


def _worker(jobs):
    """Worker thread: fetch every (provider, key) job; network only."""
    result = {}
    for provider_id, api_key in jobs:
        try:
            result[provider_id] = providers.list_models(provider_id, api_key)
        except providers.ProviderError as exc:
            result[provider_id] = exc
    _FETCH["result"] = result


def start_fetch(prefs, only_current=False):
    """Background model-list refresh for every provider with a key."""
    thread = _FETCH.get("thread")
    if thread is not None and thread.is_alive():
        return False
    ids = [prefs.provider] if only_current else list(_PROVIDER_ORDER)
    jobs = [(pid, getattr(prefs, "api_key_%s" % pid, "")) for pid in ids]
    jobs = [(pid, key) for pid, key in jobs if key or pid == prefs.provider == "openrouter"]
    if not jobs:
        return False
    prefs.fetch_status = "fetching models…"
    _FETCH["result"] = None
    thread = threading.Thread(target=_worker, args=(jobs,), daemon=True)
    _FETCH["thread"] = thread
    thread.start()
    if not bpy.app.timers.is_registered(_fetch_poll):
        bpy.app.timers.register(_fetch_poll, first_interval=0.1)
    return True


def _fetch_poll():
    thread = _FETCH.get("thread")
    if thread is not None and thread.is_alive():
        return 0.1
    _FETCH["thread"] = None
    result = _FETCH.get("result") or {}
    _FETCH["result"] = None

    prefs = _prefs()
    if prefs is None:
        return None
    try:
        cache = json.loads(prefs.models_cache) if prefs.models_cache else {}
    except ValueError:
        cache = {}
    status = []
    for provider_id, ids in result.items():
        if isinstance(ids, Exception):
            status.append("%s: error" % provider_id)
            from . import debuglog
            debuglog.log("model list fetch failed", provider=provider_id, error=str(ids)[:300])
            continue
        cache[provider_id] = ids
        status.append("%s: %d models" % (provider_id, len(ids)))
    cache[_VISION_KEY] = providers.vision_state()
    prefs.models_cache = json.dumps(cache, ensure_ascii=False)
    prefs.fetch_status = ", ".join(status)
    return None


def _startup_fetch():
    """Deferred from register(): prefs are readable once Blender is up."""
    prefs = _prefs()
    if prefs is not None:
        _load_vision_state(prefs)
        start_fetch(prefs)


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
        if not start_fetch(prefs, only_current=True):
            self.report({'WARNING'}, "Enter the API key first")
            return {'CANCELLED'}
        return {'FINISHED'}


classes = (AI_OT_fetch_models, AI_AddonPreferences)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    if not bpy.app.background:
        bpy.app.timers.register(_startup_fetch, first_interval=1.0)


def unregister():
    for timer in (_fetch_poll, _startup_fetch):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
