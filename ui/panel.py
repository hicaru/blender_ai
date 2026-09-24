"""N-panel: pipeline, brief form, object inspector, chat, skills.
# mypy: ignore-errors

One Panel class with layout.panel() sub-sections (Blender 5.x API:
the call returns a (header, body) tuple): simpler than six panel
subclasses, and the open/closed state is persisted by Blender.
"""

import functools
import json
import textwrap
import time

import bpy

from .. import agent, loop_state
from ..prefs import get_prefs
from . import operators

_WRAP_WIDTH = 42
_COLLAPSED_LINES = 3
_COLLAPSE_HINT = 6  # messages with more wrapped lines than this get a toggle
_TOOL_RUN_PREVIEW = 3  # tool-log tail shown before "… N earlier tool calls"
_TOOL_PREVIEW = 60     # characters of a tool result shown per log line
_LOOP_RECORD_PREVIEW = 6   # record.md entries shown in the loop section
_LOOP_LINE_PREVIEW = 3     # body lines shown per record entry
_LOOP_CACHE_TTL = 2.0      # seconds; the spinner redraws every ~80 ms
_SKILLS_CACHE_TTL = 5.0

_STAGE_NAMES = {
    0: "Brief", 1: "Blockout", 2: "Shape", 3: "Color/Material",
    4: "UV", 5: "Validate", 6: "LOD+Collision", 7: "Export",
}

# The loop section reads state.md / record.md from disk. A cached read
# keeps redraws cheap while the agent runs: the spinner repaints the
# panel about every 80 ms.
_loop_cache = {"at": 0.0, "data": None}
_skills_cache = {"at": 0.0, "data": None}


def _preview(content):
    return (content[:_TOOL_PREVIEW] + "…") if len(content) > _TOOL_PREVIEW else content


_ROLE_LABELS = {
    "user": "You",
    "assistant": "AI",
    "error": "Error",
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


def _pipeline_summary():
    """(stage_no, stage_label, progress, fails, warns) from the live scene."""
    from ..pipeline import checks, facts

    sf = facts.collect_asset_facts(bpy.context.scene)
    if sf is None:
        return None
    results = checks.evaluate(sf, sf["spec"])
    fails = [c for c in results if c.status == "FAIL"]
    warns = [c for c in results if c.status == "WARN"]
    open_items = fails + warns
    stage = 7 if not open_items else max(int(c.stage.split()[0]) for c in open_items)
    progress = (len(results) - len(open_items)) / max(len(results), 1)
    return stage, _STAGE_NAMES.get(stage, ""), progress, fails, warns


def _skills_snapshot():
    from pathlib import Path

    from ..skills import SkillIndex

    now = time.monotonic()
    if _skills_cache["data"] is None or now - _skills_cache["at"] > _SKILLS_CACHE_TTL:
        prefs = get_prefs()
        raw = str(getattr(prefs, "skills_dir", "") or "") if prefs else ""
        _skills_cache["data"] = SkillIndex.load(Path(raw) if raw else None)
        _skills_cache["at"] = now
    return _skills_cache["data"]


class AI_PT_chat(bpy.types.Panel):
    bl_label = "Blender AI"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AI"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        width = _region_wrap_width(context)

        self._draw_pipeline_section(layout, context)
        self._draw_brief_section(layout, wm)
        self._draw_inspector_section(layout, context)

        # Blender 5.x: layout.panel() returns (header, body); body is None
        # while the panel is collapsed.
        header, body = layout.panel(idname="AI_PT_chat", default_closed=False)
        header.label(text="Chat")
        if body is not None:
            self._draw_chat(body, context, wm, width)

        self._draw_skills_section(layout, wm)

    # ------------------------------------------------ pipeline (top)

    def _draw_pipeline_section(self, layout, context):
        wm = context.window_manager
        header, body = layout.panel(idname="AI_PT_pipeline", default_closed=False)
        header.label(text="Pipeline")
        prefs = get_prefs()
        provider = getattr(prefs, "provider", "?") if prefs else "?"
        model = getattr(prefs, "model", "") if prefs else ""
        row = body.row(align=True)
        row.label(text="%s · %s" % (provider, model or "no model"), icon='OUTLINER_OB_MESH')
        row.operator("screen.userpref_show", text="", icon='PREFERENCES')

        summary = None
        try:
            summary = _pipeline_summary()
        except (RuntimeError, ValueError, KeyError, AttributeError):
            summary = None
        if summary is None:
            body.label(text="No active asset — set a brief below", icon='INFO')
        else:
            stage, stage_name, progress, fails, warns = summary
            body.progress(factor=progress, text="Stage %d/7 %s  %d%%"
                          % (stage, stage_name, int(progress * 100)))
            for check_item in fails:
                row = body.row(align=True)
                row.alert = True
                row.label(text="✖ %s" % check_item.id, icon='CANCEL')
                props = row.operator(operators.AI_OT_fix_check.bl_idname, text="Fix")
                props.check_id = check_item.id
            for check_item in warns:
                row = body.row(align=True)
                row.label(text="⚠ %s" % check_item.id, icon='ERROR')
                props = row.operator(operators.AI_OT_fix_check.bl_idname, text="Fix")
                props.check_id = check_item.id
        row = body.row(align=True)
        row.operator(operators.AI_OT_run_pipeline_action.bl_idname,
                     text="Validate").action = 'validate'
        row.operator(operators.AI_OT_run_pipeline_action.bl_idname,
                     text="Capture").action = 'capture'
        row2 = body.row(align=True)
        row2.operator(operators.AI_OT_run_pipeline_action.bl_idname,
                      text="LODs").action = 'lods'
        row2.operator(operators.AI_OT_run_pipeline_action.bl_idname,
                      text="Collision").action = 'collision'
        row3 = body.row(align=True)
        row3.operator(operators.AI_OT_run_pipeline_action.bl_idname,
                      text="Export").action = 'export'
        AI_PT_chat._draw_capture_thumbnail(body, wm)

    @staticmethod
    def _draw_capture_thumbnail(layout, wm) -> None:
        """Show the last capture. DRAW-SAFE: read-only lookups only.

        The image datablock and its preview are created by the Capture
        operator — bpy.data writes and preview_ensure() inside draw()
        raise "Writing to ID classes in this context is not allowed".
        """
        path = wm.blender_ai_capture_path
        if not path:
            return
        want = bpy.path.abspath(path)
        for img in bpy.data.images:
            if img.filepath and bpy.path.abspath(img.filepath) == want:
                if img.preview.icon_id:
                    layout.template_icon(icon_value=img.preview.icon_id, scale=5)
                return

    def _draw_brief_section(self, layout, wm):
        has_asset = bool(bpy.context.scene.get("blender_ai_active_asset"))
        header, body = layout.panel(idname="AI_PT_brief", default_closed=has_asset)
        header.label(text="Brief")
        if body is None:
            return
        body.prop(wm, "blender_ai_brief_name")
        body.prop(wm, "blender_ai_brief_desc")
        col = body.column(align=True)
        col.prop(wm, "blender_ai_brief_class")
        col.prop(wm, "blender_ai_brief_engine")
        col.prop(wm, "blender_ai_brief_style")
        col.prop(wm, "blender_ai_brief_colors")
        body.prop(wm, "blender_ai_brief_size")
        body.operator(operators.AI_OT_apply_brief.bl_idname, icon='CHECKMARK')

    def _draw_inspector_section(self, layout, context):
        obj = context.active_object
        header, body = layout.panel(idname="AI_PT_inspector", default_closed=True)
        header.label(text="Inspector")
        if body is None:
            return
        if obj is None:
            body.label(text="No active object", icon='RESTRICT_SELECT_ON')
            return
        body.label(text=obj.name, icon='OBJECT_DATAMODE')
        body.prop(obj, "color")
        if obj.type == 'MESH':
            # DRAW-SAFE: never calc_loop_triangles() here (it writes the
            # evaluated triangulation to the mesh); show the cached count
            # or fall back to the live face count.
            tris = len(obj.data.loop_triangles)
            faces = len(obj.data.polygons)
            dims = " x ".join("%.2f" % d for d in obj.dimensions)
            detail = (f"{tris} tris x {dims} m" if tris
                      else f"{faces} faces x {dims} m")
            body.label(text=detail)
            body.prop(obj, '["ai_role"]')
        body.prop(context.window_manager, "blender_ai_obj_desc")
        row = body.row(align=True)
        row.operator(operators.AI_OT_random_color.bl_idname, icon='COLOR')

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

        # Parked tool call (code approval or question), derived from
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

        # Attachments staging row
        if wm.blender_ai_attachments:
            row = layout.row(align=True)
            for idx, att in enumerate(wm.blender_ai_attachments):
                row.label(text=att.label, icon='IMAGE_DATA')
                props = row.operator(operators.AI_OT_remove_attachment.bl_idname,
                                     text="", icon='X')
                props.index = idx
                props = row.operator(operators.AI_OT_attachment_to_reference.bl_idname,
                                     text="", icon='MPLANE')
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

        # Repair loop: live state.md fields + latest record.md entries
        self._draw_loop_section(layout)

    def _draw_skills_section(self, layout, wm):
        header, body = layout.panel(idname="AI_PT_skills", default_closed=True)
        header.label(text="Skills")
        try:
            index = _skills_snapshot()
        except (OSError, ValueError):
            index = None
        if body is None or index is None:
            return
            body.label(text="Skills unavailable", icon='ERROR')
            return
        body.label(text="%d built-in skills"
                   % len(index.skills), icon='BOOK')
        for err in index.errors[:3]:
            body.label(text=err[:60], icon='ERROR', alert=True)
        box = body.box()
        for skill in index.skills:
            box.label(text="%s — %s" % (skill.name,
                                        skill.description[:44]),
                      text_ctxt="", translate=False)
        row = body.row(align=True)
        row.operator(operators.AI_OT_reload_skills.bl_idname, icon='FILE_REFRESH')
        row.operator(operators.AI_OT_open_skills_dir.bl_idname,
                     text="", icon='FILE_FOLDER')

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
    operators.register_pipeline_operators()
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    operators.unregister_pipeline_operators()
