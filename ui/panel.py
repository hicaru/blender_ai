"""N-panel: one chat. Parked approvals/questions render FIRST.
# mypy: ignore-errors

A parked build_model approval or ask_user question drawn below a long
chat is invisible — the agent then looks "stopped" while it actually
waits for the user. So the pending box is the first thing in the panel.
"""

import functools
import json
import textwrap

import bpy

from .. import agent
from ..prefs import get_prefs
from . import operators

_WRAP_WIDTH = 42
_COLLAPSED_LINES = 3
_COLLAPSE_HINT = 6  # messages with more wrapped lines than this get a toggle
_TOOL_RUN_PREVIEW = 3  # tool-log tail shown before "… N earlier tool calls"
_TOOL_PREVIEW = 60     # characters of a tool result shown per log line


def _preview(content):
    return (content[:_TOOL_PREVIEW] + "…") if len(content) > _TOOL_PREVIEW else content


_ROLE_LABELS = {
    "user": "You",
    "assistant": "AI",
    "error": "Error",
    "auto": "Auto-continue",
}


def _draw_text_lines(layout, lines):
    """One label per wrapped chunk (cheap; no text boxes)."""
    for chunk in lines:
        layout.label(text=chunk)


def _wrap_lines(text, width=_WRAP_WIDTH):
    return _wrap_cached(text, width)


@functools.lru_cache(maxsize=256)
def _wrap_cached(text, width):
    """Region-width-aware wrapping, cached per (text, width) for cheap redraws."""
    lines = []
    for line in text.splitlines() or [""]:
        lines.extend(textwrap.wrap(line, width=width) or [""])
    return tuple(lines)


def _region_wrap_width(context):
    region = context.region
    if region is None:
        return _WRAP_WIDTH
    scale = context.preferences.system.ui_scale if context.preferences else 1.0
    return max(20, int(region.width / (max(scale, 0.1) * 7.0)))


class AI_PT_chat(bpy.types.Panel):
    bl_label = "Blender AI"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AI"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        prefs = get_prefs()
        provider = getattr(prefs, "provider", "?") if prefs else "?"
        model = getattr(prefs, "model", "") if prefs else ""
        row = layout.row(align=True)
        row.label(text="%s · %s" % (provider, model or "default model"), icon='MESH_MONKEY')
        row.operator("screen.userpref_show", text="", icon='PREFERENCES')
        self._draw_pending(layout, wm)
        self._draw_chat(layout, context, wm, _region_wrap_width(context))

    def _draw_pending(self, layout, wm):
        """Parked call (code approval or question), derived from agent state.

        Blender's undo reverts WM props and would blank these boxes while
        the loop stays parked, so the source of truth is agent.pending_view().
        """
        pending = agent.pending_view()
        if pending is None:
            return
        box = layout.box()
        box.alert = True
        if pending["kind"] == "code":
            args = pending["args"]
            box.label(text="Approve build script: %s" % args.get("name", pending["name"]),
                      icon='SCRIPT')
            col = box.column(align=True)
            text = str(args.get("code", "")) or json.dumps(args, ensure_ascii=False, indent=1)
            lines = text.splitlines()
            for line in lines[:12]:
                for chunk in textwrap.wrap(line, width=_WRAP_WIDTH) or [""]:
                    col.label(text=chunk)
            if len(lines) > 12:
                col.label(text="… %d more lines" % (len(lines) - 12))
            row = box.row(align=True)
            row.scale_y = 1.4
            row.operator(operators.AI_OT_approve_code.bl_idname, icon='CHECKMARK')
            row.operator(operators.AI_OT_reject_code.bl_idname, icon='X')
            box.label(text="Tip: Preferences > Auto-approve skips this step", icon='INFO')
            return
        box.label(text="The AI asks:", icon='QUESTION')
        _draw_text_lines(box.column(align=True),
                         _wrap_lines(str(pending["args"].get("question", ""))))
        options = pending["args"].get("options")
        for option in options if isinstance(options, list) else []:
            box.operator(operators.AI_OT_answer.bl_idname,
                         text=str(option)).option = str(option)
        box.prop(wm, "blender_ai_ask_answer", text="")
        box.operator(operators.AI_OT_answer.bl_idname, text="Answer")

    def _draw_chat(self, layout, context, wm, width):
        # Messages (long ones collapse; runs of tool messages render as
        # a compact log so they don't flood the panel)
        items = list(wm.blender_ai_messages)
        i = 0
        while i < len(items):
            item = items[i]
            if item.role == "tool":
                j = i
                while j < len(items) and items[j].role == "tool":
                    j += 1
                self._draw_tool_run(layout, wm, items[i:j])
                i = j
                continue
            self._draw_message(layout, index=i, item=item)
            i += 1

        # 4. Thinking box: streams live while busy, then stays in the
        #     panel (collapsed, open on click) after the answer
        if wm.blender_ai_live:
            expanded = wm.blender_ai_busy or wm.blender_ai_show_live
            box = layout.box()
            header = box.row(align=True)
            label = "Thinking"
            if wm.blender_ai_busy:
                prefs = get_prefs()
                level = getattr(prefs, "reasoning_effort", "") if prefs else ""
                # live header shows the configured reasoning level
                label = "Thinking (%s)" % level if level else "Thinking (live)"
            header.prop(
                wm, "blender_ai_show_live",
                text=label,
                icon='TRIA_DOWN' if expanded else 'TRIA_RIGHT',
                toggle=True, emboss=False,
            )
            if expanded:
                col = box.column(align=True)
                for chunk in _wrap_lines(wm.blender_ai_live)[-6:]:
                    col.label(text=chunk)

        # Attachments staging row
        if wm.blender_ai_attachments:
            row = layout.row(align=True)
            for idx, att in enumerate(wm.blender_ai_attachments):
                row.label(text=att.label, icon='IMAGE_DATA')
                props = row.operator(operators.AI_OT_remove_attachment.bl_idname,
                                     text="", icon='X')
                props.index = idx
        row = layout.row(align=True)
        row.operator(operators.AI_OT_attach_image.bl_idname, text="", icon='FILE_FOLDER')
        row.operator(operators.AI_OT_paste_image.bl_idname, text="", icon='PASTEDOWN')

        # Input
        layout.textbox(
            wm, "blender_ai_input",
            initial_visible_lines=3,
            placeholder="Describe what to build...",
        )

        # Buttons
        row = layout.row(align=True)
        row.operator(operators.AI_OT_send.bl_idname, icon='PLAY')
        col = row.column()
        col.operator(operators.AI_OT_stop.bl_idname, icon='PAUSE')
        col.enabled = wm.blender_ai_busy
        row.operator(operators.AI_OT_new_chat.bl_idname, icon='FILE_NEW')

    def _draw_tool_run(self, layout, wm, run):
        """Consecutive tool messages as a slim log; long runs fold to the tail."""
        box = layout.box()
        col = box.column(align=True)
        shown = run if (len(run) <= _TOOL_RUN_PREVIEW or wm.blender_ai_show_all_tools) else run[-_TOOL_RUN_PREVIEW:]
        for item in shown:
            col.label(text="▸ %s: %s" % (item.tool_name or "tool", _preview(item.content)))
        if len(run) > _TOOL_RUN_PREVIEW:
            hidden = len(run) - _TOOL_RUN_PREVIEW
            row = box.row(align=True)
            row.prop(
                wm, "blender_ai_show_all_tools",
                text=("Hide tool log" if wm.blender_ai_show_all_tools
                      else "… %d earlier tool calls" % hidden),
                icon='TRIA_UP' if wm.blender_ai_show_all_tools else 'TRIA_DOWN',
                toggle=True,
            )

    def _draw_message(self, layout, index, item):
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
        if item.reasoning:
            body.prop(
                item, "show_reasoning",
                text="Thinking", toggle=True,
                icon='TRIA_DOWN' if item.show_reasoning else 'TRIA_RIGHT',
            )
            if item.show_reasoning:
                thoughts = box.column(align=True)
                for chunk in _wrap_lines(item.reasoning)[:60]:
                    thoughts.label(text=chunk, icon='DOT')
        if collapsible and item.collapsed:
            _draw_text_lines(body, lines[:_COLLAPSED_LINES])
            body.label(text="… %d more lines" % (len(lines) - _COLLAPSED_LINES))
        else:
            _draw_text_lines(body, lines)
        if item.approval == "pending":
            box.label(text="(waiting for your approval)", icon='INFO')
        elif item.approval == "rejected":
            box.label(text="(rejected)", icon='X')


classes = (AI_PT_chat,)


def register():
    operators.register_operators()
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    operators.unregister_operators()
