"""Addon preferences: provider, API keys, model, sampling, safety switches.

API keys are stored by Blender in preferences plaintext. That is standard
Blender behaviour for add-on settings; the preference panel says so.
"""

import bpy

from . import providers

# Fixed order also defines the enum default (first item).
_PROVIDER_ORDER = ("zai", "deepseek", "openrouter")


def _provider_items(self, context):
    return [
        (pid, providers.PROVIDERS[pid]["label"], providers.PROVIDERS[pid]["base_url"])
        for pid in _PROVIDER_ORDER
    ]


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
        default_model = providers.PROVIDERS[self.provider]["default_model"]
        layout.prop(self, "model", placeholder=default_model or "model id, e.g. openai/gpt-4o-mini")
        layout.prop(self, "temperature")
        layout.prop(self, "auto_approve_code")
        layout.prop(self, "history_limit")


classes = (AI_AddonPreferences,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
