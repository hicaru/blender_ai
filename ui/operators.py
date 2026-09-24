"""Chat operators: send / stop / new chat / approve / reject / answer."""
# mypy: ignore-errors

import bpy
from bpy_extras.io_utils import ImportHelper

from .. import agent


class _ChatOperator:
    @classmethod
    def poll(cls, context):
        return True


class AI_OT_send(_ChatOperator, bpy.types.Operator):
    """Send the message (and any staged attachments) to the AI agent"""

    bl_idname = "blender_ai.send"
    bl_label = "Send"

    @classmethod
    def poll(cls, context):
        return not agent.is_busy() and not agent.has_pending()

    def execute(self, context):
        wm = context.window_manager
        text = wm.blender_ai_input
        wm.blender_ai_input = ""
        attachments = []
        if wm.blender_ai_attachments:
            from pathlib import Path

            from ..attachments import Attachment

            for item in wm.blender_ai_attachments:
                attachments.append(Attachment(
                    path=Path(item.path), sha256=item.sha256,
                    width=item.width, height=item.height, label=item.label))
            wm.blender_ai_attachments.clear()
        agent.send_user_message(text, attachments=attachments)
        return {'FINISHED'}


class AI_OT_attach_image(_ChatOperator, bpy.types.Operator, ImportHelper):
    """Attach an image file to the next message"""

    bl_idname = "blender_ai.attach_image"
    bl_label = "Attach image"

    filter_glob: bpy.props.StringProperty(
        default="*.png;*.jpg;*.jpeg;*.webp;*.bmp", options={'HIDDEN'})
    files: bpy.props.CollectionProperty(
        type=bpy.types.OperatorFileListElement, options={'HIDDEN'})
    directory: bpy.props.StringProperty(subtype='DIR_PATH', options={'HIDDEN'})

    def execute(self, context):
        import os

        from .. import attachments

        wm = context.window_manager
        for f in self.files:
            path = os.path.join(self.directory, f.name)
            try:
                att = attachments.load_attachment(path)
            except (OSError, ValueError) as exc:
                self.report({'WARNING'}, "attach %s: %s" % (f.name, exc))
                continue
            item = wm.blender_ai_attachments.add()
            item.path = str(att.path)
            item.label = att.label
            item.width = att.width
            item.height = att.height
            item.sha256 = att.sha256
        return {'FINISHED'}


class AI_OT_paste_image(_ChatOperator, bpy.types.Operator):
    """Paste an image from the clipboard as an attachment"""

    bl_idname = "blender_ai.paste_image"
    bl_label = "Paste image"

    def execute(self, context):
        # image.clipboard_paste needs an Image Editor area; provide one via
        # temp_override. If no area works, tell the user (button stays visible
        # only when a temp area could be fabricated by the caller).
        try:

            from .. import attachments

            with context.temp_override(area=None):
                bpy.ops.image.clipboard_paste()
            img = bpy.data.images.get("Clipboard") or bpy.data.images[-1]
            att = attachments.load_attachment(bpy.path.abspath(img.filepath))
        except (RuntimeError, OSError, ValueError, IndexError) as exc:
            self.report({'WARNING'},
                        "clipboard paste unavailable (%s); use Attach instead" % exc)
            return {'CANCELLED'}
        wm = context.window_manager
        item = wm.blender_ai_attachments.add()
        item.path = str(att.path)
        item.label = att.label
        item.width = att.width
        item.height = att.height
        item.sha256 = att.sha256
        return {'FINISHED'}


class AI_OT_remove_attachment(_ChatOperator, bpy.types.Operator):
    """Remove a staged attachment"""

    bl_idname = "blender_ai.remove_attachment"
    bl_label = "Remove"

    index: bpy.props.IntProperty(default=0)

    def execute(self, context):
        wm = context.window_manager
        if 0 <= self.index < len(wm.blender_ai_attachments):
            wm.blender_ai_attachments.remove(self.index)
        return {'FINISHED'}


class AI_OT_stop(_ChatOperator, bpy.types.Operator):
    """Stop the current agent run"""

    bl_idname = "blender_ai.stop"
    bl_label = "Stop"

    @classmethod
    def poll(cls, context):
        return agent.is_busy()

    def execute(self, context):
        agent.stop()
        return {'FINISHED'}


class AI_OT_new_chat(_ChatOperator, bpy.types.Operator):
    """Start a new chat (clears history)"""

    bl_idname = "blender_ai.new_chat"
    bl_label = "New chat"

    def execute(self, context):
        agent.new_chat()
        return {'FINISHED'}


class AI_OT_approve_code(_ChatOperator, bpy.types.Operator):
    """Run the proposed code"""

    bl_idname = "blender_ai.approve_code"
    bl_label = "Approve"

    @classmethod
    def poll(cls, context):
        return (agent.pending_view() or {}).get("kind") == "code"

    def execute(self, context):
        agent.resolve_pending("code", "approved")
        return {'FINISHED'}


class AI_OT_reject_code(_ChatOperator, bpy.types.Operator):
    """Reject the proposed code"""

    bl_idname = "blender_ai.reject_code"
    bl_label = "Reject"

    @classmethod
    def poll(cls, context):
        return (agent.pending_view() or {}).get("kind") == "code"

    def execute(self, context):
        agent.resolve_pending("code", "rejected")
        return {'FINISHED'}


class AI_OT_answer(_ChatOperator, bpy.types.Operator):
    """Answer the agent's question"""

    bl_idname = "blender_ai.answer"
    bl_label = "Answer"

    option: bpy.props.StringProperty(
        description="Chosen option (set by option buttons)",
    )

    @classmethod
    def poll(cls, context):
        return (agent.pending_view() or {}).get("kind") == "ask"

    def execute(self, context):
        wm = context.window_manager
        answer = self.option or wm.blender_ai_ask_answer.strip()
        if not answer:
            self.report({'WARNING'}, "Type an answer first")
            return {'CANCELLED'}
        wm.blender_ai_ask_answer = ""
        agent.resolve_pending("ask", answer)
        return {'FINISHED'}


class AI_OT_toggle_message(_ChatOperator, bpy.types.Operator):
    """Expand or collapse a long message"""

    bl_idname = "blender_ai.toggle_message"
    bl_label = "Expand / collapse"
    bl_options = {'INTERNAL'}  # noqa: RUF012 — bpy class attribute, not a mutable default

    index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        col = context.window_manager.blender_ai_messages
        if 0 <= self.index < len(col):
            item = col[self.index]
            item.collapsed = not item.collapsed
        return {'FINISHED'}


class AI_FH_image_drop(bpy.types.FileHandler):
    """Drag & drop image files onto the AI panel → stage as attachments.

    Restricted to drops that land on the AI sidebar region: Blender itself
    already handles image drops INTO THE VIEWPORT (reference images) and
    we must not hijack that.
    """

    bl_idname = "AI_FH_image_drop"
    bl_label = "AI attachments"
    bl_import_operator = "BLENDER_AI_OT_attach_image"
    bl_file_extensions = ".png;.jpg;.jpeg;.webp;.bmp"

    @classmethod
    def poll_drop(cls, context):
        area = context.area
        return (area is not None and area.type == 'VIEW_3D'
                and context.region is not None
                and context.region.type == 'UI')


classes: tuple = (
    AI_OT_send, AI_OT_attach_image, AI_OT_paste_image, AI_OT_remove_attachment,
    AI_OT_stop, AI_OT_new_chat, AI_OT_approve_code, AI_OT_reject_code,
    AI_OT_answer, AI_OT_toggle_message, AI_FH_image_drop,
)


def register_operators():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister_operators():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


