"""N-panel chat: status, message list, pending code/question boxes, input."""

import json
import textwrap

import bpy

from .. import agent
from . import operators

_WRAP_WIDTH = 42
_TOOL_PREVIEW = 60
_COLLAPSED_LINES = 3
_COLLAPSE_HINT = 6  # messages with more wrapped lines than this get a toggle

_ROLE_LABELS = {
    "user": "You",
    "assistant": "AI",
    "error": "Error",
}


def _wrap_lines(text):
    lines = []
    for line in text.splitlines() or [""]:
        lines.extend(textwrap.wrap(line, width=_WRAP_WIDTH) or [""])
    return lines


def _draw_text_lines(layout, lines):
    for chunk in lines:
        layout.label(text=chunk)


class AI_PT_chat(bpy.types.Panel):
    bl_label = "Blender AI"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AI"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager

        # 1. Status row: animated ring while the agent is busy
        row = layout.row(align=True)
        if wm.blender_ai_busy:
            row.progress(factor=agent.spinner_factor(), type='RING')
        row.label(text=wm.blender_ai_status or "Ready.")

        # 2. Pending code awaiting approval
        if wm.blender_ai_pending_code:
            box = layout.box()
            box.label(text="Proposed code — review:", icon='SCRIPT')
            col = box.column(align=True)
            for line in wm.blender_ai_pending_code.splitlines()[:30]:
                for chunk in textwrap.wrap(line, width=_WRAP_WIDTH) or [""]:
                    col.label(text=chunk)
            row = box.row(align=True)
            row.operator(operators.AI_OT_approve_code.bl_idname, icon='CHECKMARK')
            row.operator(operators.AI_OT_reject_code.bl_idname, icon='X')

        # 3. Pending question to the user
        if wm.blender_ai_ask_question:
            box = layout.box()
            box.label(text="Question:", icon='QUESTION')
            _draw_text_lines(box.column(align=True), _wrap_lines(wm.blender_ai_ask_question))
            options = []
            try:
                options = json.loads(wm.blender_ai_ask_options)
            except ValueError:
                options = []
            for option in options if isinstance(options, list) else []:
                box.operator(
                    operators.AI_OT_answer.bl_idname,
                    text=option,
                ).option = option
            box.prop(wm, "blender_ai_ask_answer", text="")
            box.operator(operators.AI_OT_answer.bl_idname, text="Answer")

        # 4. Messages (long ones collapse to their first lines)
        for index, item in enumerate(wm.blender_ai_messages):
            if item.role == "tool":
                preview = (item.content[:_TOOL_PREVIEW] + "…") if len(item.content) > _TOOL_PREVIEW else item.content
                box = layout.box()
                box.label(text="%s: %s" % (item.tool_name or "tool", preview))
                continue
            lines = _wrap_lines(item.content)
            collapsible = len(lines) > _COLLAPSE_HINT
            box = layout.box()
            if item.role == "error":
                box.alert = True
            header = box.row(align=True)
            if collapsible:
                toggle = header.operator(
                    operators.AI_OT_toggle_message.bl_idname,
                    text="",
                    icon='TRIA_DOWN' if not item.collapsed else 'TRIA_RIGHT',
                    emboss=False,
                )
                toggle.index = index
            header.label(text=_ROLE_LABELS.get(item.role, item.role))

            body = box.column(align=True)
            if collapsible and item.collapsed:
                _draw_text_lines(body, lines[:_COLLAPSED_LINES])
                body.label(text="… %d more lines" % (len(lines) - _COLLAPSED_LINES))
            else:
                _draw_text_lines(body, lines)
            if item.approval == "pending":
                box.label(text="(waiting for your approval)", icon='INFO')
            elif item.approval == "rejected":
                box.label(text="(rejected)", icon='X')

        # 5. Input
        layout.textbox(
            wm, "blender_ai_input",
            initial_visible_lines=3,
            placeholder="Describe what to build...",
        )

        # 6. Buttons
        row = layout.row(align=True)
        row.operator(operators.AI_OT_send.bl_idname, icon='PLAY')
        col = row.column()
        col.operator(operators.AI_OT_stop.bl_idname, icon='PAUSE')
        col.enabled = wm.blender_ai_busy
        row.operator(operators.AI_OT_new_chat.bl_idname, icon='FILE_NEW')


classes = (AI_PT_chat,)


def register():
    operators.register()
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    operators.unregister()
