"""Chat operators: send / stop / new chat / approve / reject / answer."""

import bpy

from .. import agent


class _ChatOperator:
    @classmethod
    def poll(cls, context):
        return True


class AI_OT_send(_ChatOperator, bpy.types.Operator):
    """Send the message to the AI agent"""

    bl_idname = "blender_ai.send"
    bl_label = "Send"

    @classmethod
    def poll(cls, context):
        return not agent.is_busy() and not agent.has_pending()

    def execute(self, context):
        wm = context.window_manager
        text = wm.blender_ai_input
        wm.blender_ai_input = ""
        agent.send_user_message(text)
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
        return agent.has_pending() and context.window_manager.blender_ai_pending_code

    def execute(self, context):
        agent.resolve_pending("code", "approved")
        return {'FINISHED'}


class AI_OT_reject_code(_ChatOperator, bpy.types.Operator):
    """Reject the proposed code"""

    bl_idname = "blender_ai.reject_code"
    bl_label = "Reject"

    @classmethod
    def poll(cls, context):
        return agent.has_pending() and context.window_manager.blender_ai_pending_code

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
        return agent.has_pending() and bool(context.window_manager.blender_ai_ask_question)

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
    bl_options = {'INTERNAL'}

    index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        col = context.window_manager.blender_ai_messages
        if 0 <= self.index < len(col):
            item = col[self.index]
            item.collapsed = not item.collapsed
        return {'FINISHED'}


classes = (
    AI_OT_send,
    AI_OT_stop,
    AI_OT_new_chat,
    AI_OT_approve_code,
    AI_OT_reject_code,
    AI_OT_answer,
    AI_OT_toggle_message,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
