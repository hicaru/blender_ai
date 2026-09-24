"""N-panel chat: status, message list, pending code/question boxes, input."""

import json
import textwrap
import time

import bpy

from . import operators
from .. import agent, loop_state
from ..prefs import get_prefs

_WRAP_WIDTH = 42
_COLLAPSED_LINES = 3
_COLLAPSE_HINT = 6  # messages with more wrapped lines than this get a toggle
_TOOL_RUN_PREVIEW = 3  # tool-log tail shown before "… N earlier tool calls"
_TOOL_PREVIEW = 60     # characters of a tool result shown per log line
_LOOP_RECORD_PREVIEW = 6   # record.md entries shown in the loop section
_LOOP_LINE_PREVIEW = 3     # body lines shown per record entry
_LOOP_CACHE_TTL = 2.0      # seconds; the spinner redraws every ~80 ms

# The loop section reads state.md / record.md from disk. A cached read
# keeps redraws cheap while the agent runs: the spinner repaints the
# panel about every 80 ms.
_loop_cache = {"at": 0.0, "data": None}


def _preview(content):
    return (content[:_TOOL_PREVIEW] + "…") if len(content) > _TOOL_PREVIEW else content

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

        # 1. Messages (long ones collapse; runs of tool messages render as
        #    a compact log so they don't flood the panel)
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

        # 2+3. Parked tool call (code approval or question), derived from
        # agent state — Blender's undo reverts WM props and would blank
        # these boxes while the loop stays parked (deadlock).
        pending = agent.pending_view()
        if pending and pending["kind"] == "code":
            box = layout.box()
            box.label(text="Proposed action — review:", icon='SCRIPT')
            col = box.column(align=True)
            args = pending["args"]
            text = str(args.get("code", "")) or json.dumps(args, ensure_ascii=False, indent=1)
            for line in text.splitlines()[:30]:
                for chunk in textwrap.wrap(line, width=_WRAP_WIDTH) or [""]:
                    col.label(text=chunk)
            row = box.row(align=True)
            row.operator(operators.AI_OT_approve_code.bl_idname, icon='CHECKMARK')
            row.operator(operators.AI_OT_reject_code.bl_idname, icon='X')

        if pending and pending["kind"] == "ask":
            box = layout.box()
            box.label(text="Question:", icon='QUESTION')
            _draw_text_lines(
                box.column(align=True),
                _wrap_lines(str(pending["args"].get("question", ""))),
            )
            options = pending["args"].get("options")
            for option in options if isinstance(options, list) else []:
                box.operator(
                    operators.AI_OT_answer.bl_idname,
                    text=str(option),
                ).option = str(option)
            box.prop(wm, "blender_ai_ask_answer", text="")
            box.operator(operators.AI_OT_answer.bl_idname, text="Answer")

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

        # 7. Repair loop: live state.md fields + latest record.md entries
        self._draw_loop_section(layout)

    def _draw_loop_section(self, layout):
        """Repair-loop state + record, read straight from the store dir."""
        prefs = get_prefs()
        store = loop_state.resolve_store_dir(
            getattr(prefs, "skill_store", "") if prefs else "",
        )

        box = layout.box()
        box.label(text="Repair loop", icon='LOOP_FORWARDS')
        now = time.monotonic()
        if _loop_cache["data"] is None or now - _loop_cache["at"] > _LOOP_CACHE_TTL:
            _loop_cache["data"] = (
                loop_state.read_state_fields(store),
                loop_state.read_record_entries(store, last_n=_LOOP_RECORD_PREVIEW),
            )
            _loop_cache["at"] = now
        fields, entries = _loop_cache["data"]
        if not fields:
            box.label(text="Idle — no loop has run yet", icon='INFO')
        else:
            col = box.column(align=True)
            for name in loop_state.STATE_FIELDS:
                self._draw_wrapped(col, "%s: %s" % (name, fields.get(name, "")))

        rec = box.box()
        rec.label(text="Record (latest %s)" % len(entries) if entries else "Record")
        if not entries:
            rec.label(text="No entries yet")
        for header, lines in entries:
            self._draw_wrapped(rec, "▸ " + header)
            for line in lines[:_LOOP_LINE_PREVIEW]:
                self._draw_wrapped(rec, "   " + line)
            if len(lines) > _LOOP_LINE_PREVIEW:
                rec.label(text="   … +%s more lines" % (len(lines) - _LOOP_LINE_PREVIEW))

    def _draw_wrapped(self, layout, text):
        for chunk in _wrap_lines(text):
            layout.label(text=chunk)

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
    operators.register()
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    operators.unregister()
