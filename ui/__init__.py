"""UI package: WindowManager properties shared by the panel and operators."""
# mypy: ignore-errors

import bpy

from . import (
    operators,
    panel,
)


class AI_Attachment(bpy.types.PropertyGroup):
    """One staged image attachment (path points at the downscaled PNG)."""

    path: bpy.props.StringProperty()
    label: bpy.props.StringProperty()
    width: bpy.props.IntProperty()
    height: bpy.props.IntProperty()
    sha256: bpy.props.StringProperty()


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
    "blender_ai_attachments",
    "blender_ai_brief_name",
    "blender_ai_brief_desc",
    "blender_ai_brief_class",
    "blender_ai_brief_engine",
    "blender_ai_brief_style",
    "blender_ai_brief_colors",
    "blender_ai_brief_size",
    "blender_ai_obj_desc",
    "blender_ai_capture_path",
)

_BRIEF_CLASS_ITEMS = (
    ("small_prop", "Small prop", "< 1k tris"),
    ("prop", "Prop", "0.5-5k tris"),
    ("hero_prop", "Hero prop", "5-20k tris"),
    ("environment", "Environment", "1-15k tris"),
    ("modular_piece", "Modular piece", "50-2k tris"),
    ("vehicle", "Vehicle", "5-40k tris"),
    ("character", "Character", "5-40k tris"),
)
_BRIEF_ENGINE_ITEMS = (
    ("gltf", "glTF / GLB", "Open standard; Godot-native"),
    ("godot", "Godot 4", "GLB; collision via -colonly"),
    ("unity", "Unity", "FBX; _Collider naming"),
    ("unreal", "Unreal", "FBX; UCX_/UBX_ naming"),
)
_BRIEF_STYLE_ITEMS = (
    ("lowpoly", "Low-poly", "Faceted, vertex colors, quarter budget"),
    ("stylized", "Stylized", "Smooth + bevels, simple materials"),
    ("realistic", "Realistic", "PBR materials, UVs mandatory"),
)
_BRIEF_COLORS_ITEMS = (
    ("vertex_color", "Vertex colors", "One shared material, colors in 'Col'"),
    ("materials", "Materials", "One Principled material per part"),
)


def _obj_desc_get(self) -> str:  # noqa: ANN001 — bpy getter callback signature (self: Any)
    """Draw-time read: the proxy value comes FROM the active object.

    Property get/set callbacks are the only draw-safe way to mirror an
    object field in the UI — writing to bpy IDs inside draw() raises
    "Writing to ID classes in this context is not allowed".
    """
    obj = bpy.context.active_object
    if obj is None:
        return ""
    try:
        from ..pipeline import meta
        return meta.get_description(obj)
    except (ImportError, ValueError):
        return ""


def _obj_desc_set(self, value: str) -> None:  # noqa: ANN001 — bpy setter callback signature (self: Any)
    """Edit-time write-through: the description proxy edits the active object."""
    obj = bpy.context.active_object
    if obj is None:
        return
    try:
        from ..pipeline import meta
        meta.set_description(obj, value)
    except (ImportError, ValueError):
        pass


def register() -> None:
    panel.register()
    bpy.utils.register_class(AI_Message)
    bpy.utils.register_class(AI_Attachment)
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
    wm.blender_ai_attachments = bpy.props.CollectionProperty(type=AI_Attachment)
    wm.blender_ai_brief_name = bpy.props.StringProperty(name="Name", maxlen=60)
    wm.blender_ai_brief_desc = bpy.props.StringProperty(name="Description", maxlen=300)
    wm.blender_ai_brief_class = bpy.props.EnumProperty(
        name="Class", items=_BRIEF_CLASS_ITEMS, default="prop")
    wm.blender_ai_brief_engine = bpy.props.EnumProperty(
        name="Engine", items=_BRIEF_ENGINE_ITEMS, default="gltf")
    wm.blender_ai_brief_style = bpy.props.EnumProperty(
        name="Style", items=_BRIEF_STYLE_ITEMS, default="lowpoly")
    wm.blender_ai_brief_colors = bpy.props.EnumProperty(
        name="Colors", items=_BRIEF_COLORS_ITEMS, default="vertex_color")
    wm.blender_ai_brief_size = bpy.props.FloatVectorProperty(
        name="Size m", size=3, min=0.0, default=(0.0, 0.0, 0.0),
        description="Real-world bounding size in meters (0 = auto)")
    wm.blender_ai_obj_desc = bpy.props.StringProperty(
        name="Description", maxlen=500,
        get=_obj_desc_get, set=_obj_desc_set,
        description="ai_description of the active object (writes through)",
    )
    wm.blender_ai_capture_path = bpy.props.StringProperty(
        name="Last capture",
        description="File path of the newest capture_view contact sheet",
    )


def unregister() -> None:
    wm = bpy.types.WindowManager
    for name in _WM_PROPS:
        if hasattr(wm, name):
            delattr(wm, name)
    bpy.utils.unregister_class(AI_Attachment)
    bpy.utils.unregister_class(AI_Message)
    panel.unregister()
