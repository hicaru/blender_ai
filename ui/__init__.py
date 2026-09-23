"""UI package: WindowManager properties shared by the panel and operators."""

import bpy

from . import panel


class AI_Message(bpy.types.PropertyGroup):
    """Render copy of one history message (synced from agent state)."""

    role: bpy.props.StringProperty()  # user | assistant | tool | error
    content: bpy.props.StringProperty()
    tool_name: bpy.props.StringProperty()
    approval: bpy.props.StringProperty()  # "" | pending | ok | rejected
    collapsed: bpy.props.BoolProperty(
        name="Collapsed",
        description="Long messages render only their first lines",
        default=True,
    )
    reasoning: bpy.props.StringProperty(
        name="Reasoning",
        description="Model's thinking, when the provider reports it",
    )
    show_reasoning: bpy.props.BoolProperty(
        name="Show reasoning",
        description="Expand the model's thinking block",
        default=False,
    )


_WM_PROPS = (
    "blender_ai_messages",
    "blender_ai_input",
    "blender_ai_busy",
    "blender_ai_status",
    "blender_ai_ask_answer",
    "blender_ai_show_all_tools",
    "blender_ai_live",
    "blender_ai_show_live",
)


def register():
    panel.register()
    bpy.utils.register_class(AI_Message)
    wm = bpy.types.WindowManager
    wm.blender_ai_messages = bpy.props.CollectionProperty(type=AI_Message)
    wm.blender_ai_input = bpy.props.StringProperty(
        name="Input", maxlen=4000,
    )
    wm.blender_ai_busy = bpy.props.BoolProperty(default=False)
    wm.blender_ai_status = bpy.props.StringProperty(default="Ready.")
    wm.blender_ai_ask_answer = bpy.props.StringProperty(
        name="Answer", maxlen=2000,
    )
    wm.blender_ai_show_all_tools = bpy.props.BoolProperty(
        name="Show full tool log",
        default=False,
    )
    wm.blender_ai_live = bpy.props.StringProperty(
        name="Live reasoning",
        description="Tail of the model's reasoning while it streams",
        maxlen=2000,
    )
    wm.blender_ai_show_live = bpy.props.BoolProperty(
        name="Show reasoning tail",
        default=False,
    )


def unregister():
    wm = bpy.types.WindowManager
    for name in _WM_PROPS:
        if hasattr(wm, name):
            delattr(wm, name)
    bpy.utils.unregister_class(AI_Message)
    panel.unregister()
