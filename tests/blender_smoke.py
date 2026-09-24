"""Headless smoke test for the blender_ai extension.
# mypy: ignore-errors

Run:
    blender --background --python tests/blender_smoke.py

Covers: registration, the agent loop against a mock provider (structural
tool call -> final answer), the run_python approval gate (code NOT executed
until Approve; reject branch returns control to the agent), ask_user,
per-file history (stored inside the .blend), and the extension management
tools.

The mock replaces ``providers.chat_completions``; the agent loop's timer is
pumped manually because timers do not fire during ``--background`` scripts.
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # parent of the blender_ai package
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import blender_ai
import bpy
from blender_ai import agent, providers

FAILURES = []


def check(name, condition, extra=""):
    print("  [%s] %s%s" % ("OK " if condition else "FAIL", name,
                          (" | " + str(extra)) if extra else ""))
    if not condition:
        FAILURES.append(name)


def pump(timeout=60):
    """Manually drive the agent loop's poll until it settles."""
    wm = bpy.context.window_manager
    deadline = time.time() + timeout
    while time.time() < deadline:
        settled = (
            not wm.blender_ai_busy
            and agent._STATE.thread is None
            and agent._STATE.result is None
        )
        if settled:
            return True
        agent._poll()
        time.sleep(0.05)
    return False


def mock_response(content="", tool_calls=None, extra_message_fields=None):
    message = {"role": "assistant", "content": content,
               "tool_calls": tool_calls or None}
    message.update(extra_message_fields or {})
    return {"message": message, "usage": {}}


def tool_call(call_id, name, arguments):
    return {
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def scenario_structural_loop(wm):
    print("- scenario: structural tool call loop")
    calls = {"n": 0}

    def mock(provider_id, api_key, model, messages, tools=None,
             temperature=0.4, timeout=90, thinking=False, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return mock_response(tool_calls=[
                tool_call("call_1", "create_primitive",
                          {"kind": "cube", "name": "MockCube",
                           "location": [0, 0, 1]}),
            ])
        return mock_response(content="Created the cube named MockCube.")

    providers.chat_completions = mock
    wm.blender_ai_input = "make me a cube"
    bpy.ops.blender_ai.send()
    check("loop settled", pump())
    check("cube created", "MockCube" in bpy.data.objects)
    roles = [m.role for m in wm.blender_ai_messages]
    check("history shape", roles == ["user", "assistant", "tool", "assistant"],
          roles)
    final = wm.blender_ai_messages[-1].content
    check("final answer in history", final == "Created the cube named MockCube.")
    return mock


def scenario_code_gate(wm):
    print("- scenario: run_python approval gate")
    calls = {"n": 0}

    def mock_gate(provider_id, api_key, model, messages, tools=None,
                  temperature=0.4, timeout=90, thinking=False, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return mock_response(tool_calls=[
                tool_call("call_py", "run_python",
                          {"code": "print('SIDE EFFECT'); "
                                   "bpy.data.meshes.new('EvilMesh')"}),
            ])
        # after resolution the agent continues and answers
        return mock_response(content="Understood, continuing.")

    providers.chat_completions = mock_gate
    wm.blender_ai_input = "run some code"
    bpy.ops.blender_ai.send()
    check("loop parked for approval", pump())
    check("code NOT executed yet", "EvilMesh" not in bpy.data.meshes)
    check("pending code in panel", (agent.pending_view() or {}).get("kind") == "code")
    check("status waits for approval", wm.blender_ai_status == "waiting for approval")
    from blender_ai.ui.operators import AI_OT_send
    check("send disabled while pending", not AI_OT_send.poll(bpy.context))

    # --- reject branch
    bpy.ops.blender_ai.reject_code()
    check("reject settled", pump())
    check("code still NOT executed", "EvilMesh" not in bpy.data.meshes)
    tool_msgs = [m for m in wm.blender_ai_messages if m.role == "tool"]
    check("reject tool result", tool_msgs and "REJECTED" in tool_msgs[-1].content,
          tool_msgs[-1].content[:60] if tool_msgs else None)
    check("pending box cleared", agent.pending_view() is None)

    # --- approve branch
    calls["n"] = 0
    wm.blender_ai_input = "run some code again"
    bpy.ops.blender_ai.send()
    check("parked again", pump())
    check("pending code shown", (agent.pending_view() or {}).get("kind") == "code")
    bpy.ops.blender_ai.approve_code()
    check("approve settled", pump())
    check("code executed after approve", "EvilMesh" in bpy.data.meshes)
    tool_msgs = [m for m in wm.blender_ai_messages if m.role == "tool"]
    check("tool result has stdout", tool_msgs and "SIDE EFFECT" in tool_msgs[-1].content)
    check("approval recorded", any(m.approval == "ok" for m in wm.blender_ai_messages))


def scenario_ask_user(wm):
    print("- scenario: ask_user pause and answer")
    calls = {"n": 0}

    def mock_ask(provider_id, api_key, model, messages, tools=None,
                 temperature=0.4, timeout=90, thinking=False, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return mock_response(tool_calls=[
                tool_call("call_ask", "ask_user",
                          {"question": "Round or square?",
                           "options": ["round", "square"]}),
            ])
        tool_results = [m for m in messages if m.get("role") == "tool"]
        answer = tool_results[-1]["content"] if tool_results else ""
        return mock_response(content="You chose: %s" % answer)

    providers.chat_completions = mock_ask
    wm.blender_ai_input = "make a thing"
    bpy.ops.blender_ai.send()
    check("parked for question", pump())
    view = agent.pending_view() or {}
    check("question in panel", view.get("args", {}).get("question") == "Round or square?")
    check("options in panel", view.get("args", {}).get("options") == ["round", "square"])

    op = bpy.ops.blender_ai.answer
    result = op(option="round")
    check("answer settled", result == {'FINISHED'} and pump())
    check("ask fields cleared", agent.pending_view() is None)
    final = wm.blender_ai_messages[-1].content
    check("answer reached model", final == "You chose: round", final)


def scenario_reasoning(wm):
    print("- scenario: reasoning captured, shown, persists after answer")
    def mock_reasoning(provider_id, api_key, model, messages, tools=None,
                       temperature=0.4, timeout=90, thinking=False, **kwargs):
        on_delta = kwargs.get("on_delta")
        if on_delta:
            on_delta("reasoning", "persisted live tail ")
        return mock_response(
            content="A cube is a six-sided primitive.",
            extra_message_fields={"reasoning_content": "User wants a cube; "
                                  "create_primitive covers it; no python needed."})

    providers.chat_completions = mock_reasoning
    wm.blender_ai_input = "make a cube"
    bpy.ops.blender_ai.send()
    check("settled", pump())
    last = wm.blender_ai_messages[-1]
    check("reasoning captured in panel", "create_primitive covers it" in last.reasoning)
    check("reasoning hidden by default", not last.show_reasoning)
    check("live tail persists after answer",
          "persisted live tail" in wm.blender_ai_live)

    # reasoning-only response (budget eaten) -> one silent retry with
    # lowered effort, then the hint — never a blank reply
    efforts = []
    def mock_empty(provider_id, api_key, model, messages, tools=None,
                   temperature=0.4, timeout=90, thinking=False, **kwargs):
        efforts.append(kwargs.get("reasoning_effort"))
        on_delta = kwargs.get("on_delta")
        if on_delta:
            on_delta("reasoning", "all budget spent")
        return mock_response(
            extra_message_fields={"reasoning_content": "all budget spent"})

    providers.chat_completions = mock_empty
    wm.blender_ai_input = "why empty"
    bpy.ops.blender_ai.send()
    check("empty settled", pump())
    check("auto retry used lowered effort", "low" in efforts, efforts)
    last = wm.blender_ai_messages[-1]
    check("empty answer shows hint",
          last.content.startswith("(Empty answer"), last.content[:50])
    check("empty turn not stored twice",
          sum(1 for m in agent.messages()
              if m.get("role") == "assistant"
              and m.get("content", "").startswith("(Empty answer")) == 1)
    check("live tail refreshed by new request",
          "all budget spent" in wm.blender_ai_live)
    stored = [m for m in agent.messages() if m.get("role") == "assistant"]
    check("reasoning stored in history",
          stored and any("create_primitive" in (m.get("reasoning") or "")
                         for m in stored))

    # outbound request must not carry reasoning back to the provider
    captured = {}
    def spy(provider_id, api_key, model, messages, tools=None,
            temperature=0.4, timeout=90, thinking=False, **kwargs):
        captured["messages"] = messages
        return mock_response(content="done")
    providers.chat_completions = spy
    wm.blender_ai_input = "again"
    bpy.ops.blender_ai.send()
    pump()
    outbound = captured.get("messages", [])
    check("reasoning stripped from outbound",
          outbound and all("reasoning" not in m and "reasoning_content" not in m
                           for m in outbound))
    # New chat must clear the persisted live tail
    bpy.ops.blender_ai.new_chat()
    check("new chat clears live tail", wm.blender_ai_live == "")


def scenario_poll_keepalive(wm):
    """Regression: a tool round must keep the poll loop alive.

    _spawn() runs INSIDE the timer callback, where Blender still counts
    _poll as registered; a continuation path that returns None unregisters
    the timer — provider finished, spinner stuck forever.
    Headless here _poll is driven manually, so assert the return-value
    contract directly (0.2 = keep looping, None = terminal).
    """
    print("- scenario: tool round keeps poll loop alive")
    calls = {"n": 0}

    def mock(provider_id, api_key, model, messages, tools=None,
             temperature=0.4, timeout=90, thinking=False, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return mock_response(tool_calls=[tool_call("c1", "scene_info", {})])
        return mock_response(content="done")

    providers.chat_completions = mock
    wm.blender_ai_input = "inspect the scene"
    bpy.ops.blender_ai.send()

    deadline = time.time() + 30
    while time.time() < deadline and agent._STATE.result is None:
        time.sleep(0.05)
    ret = agent._poll()
    check("continuation keeps loop alive", ret == 0.2, "got %r" % ret)
    check("continuation spawned next round", agent._STATE.thread is not None)

    check("second round settles", pump())
    roles = [m.role for m in wm.blender_ai_messages]
    check("full round history", roles == ["user", "assistant", "tool", "assistant"],
          roles)


def scenario_collapse(wm):
    print("- scenario: long messages collapse")
    providers.chat_completions = lambda *a, **k: mock_response(content="ok")
    wm.blender_ai_input = "X" * 1000
    bpy.ops.blender_ai.send()
    check("settled", pump())
    first = wm.blender_ai_messages[0]
    check("long message auto-collapsed", first.collapsed)
    check("short messages not collapsed",
          not wm.blender_ai_messages[-1].collapsed)
    bpy.ops.blender_ai.toggle_message(index=0)
    check("expand works", not wm.blender_ai_messages[0].collapsed)
    bpy.ops.blender_ai.toggle_message(index=0)
    check("collapse works", wm.blender_ai_messages[0].collapsed)


def scenario_extension_tools(wm):
    print("- scenario: extension list + gated install/uninstall")
    from blender_ai import executor
    check("addon tools registered",
          "list_extensions" in executor.TOOL_REGISTRY
          and "install_extension" in executor.TOOL_REGISTRY
          and "uninstall_extension" in executor.TOOL_REGISTRY)

    listing = executor.dispatch("list_extensions", {"query": "blender"})
    # Environment-dependent: only asserts when this build was installed
    # as an extension; otherwise the check is skipped, not failed.
    installed_as_ext = (__package__ or "").startswith("bl_ext.")
    check("list_extensions finds blender_ai",
          (not installed_as_ext)
          or (listing["ok"] and "bl_ext.user_default.blender_ai" in listing["result"]),
          listing["result"][:80])

    # Build a tiny valid extension zip to install from disk.
    import os
    import tempfile
    import zipfile
    manifest = (
        'schema_version = "1.0.0"\nid = "dummy_ext"\nversion = "0.0.1"\n'
        'name = "Dummy Ext"\ntagline = "smoke test dummy"\n'
        'maintainer = "smoke"\ntype = "add-on"\nblender_version_min = "4.2.0"\n'
        'license = ["SPDX:GPL-3.0-or-later"]\n'
    )
    zpath = os.path.join(tempfile.gettempdir(), "blender_ai_dummy_ext.zip")
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("dummy_ext/blender_manifest.toml", manifest)
        zf.writestr("dummy_ext/__init__.py",
                    "bl_info = {'name': 'Dummy Ext'}\n"
                    "def register():\n    pass\n"
                    "def unregister():\n    pass\n")

    # Gate closed: park, do not install.
    outcome = executor.dispatch("install_extension", {"source": zpath})
    check("install parked for approval",
          outcome.get("pending") and outcome.get("kind") == "code")
    mods = [m.__name__ for m in __import__("addon_utils").modules()
            if m.__name__.endswith("dummy_ext")]
    check("NOT installed before approve", not mods)

    import json as _json
    agent._STATE.pending = {
        "tool_call": {"id": "call_inst", "type": "function",
                      "function": {"name": "install_extension",
                                   "arguments": _json.dumps({"source": zpath})}},
        "kind": "code",
    }
    # approve through the same path the operator uses (no provider involved)
    agent._STATE.messages.append({"role": "assistant", "content": "",
                                     "approval": "pending"})
    bpy.ops.blender_ai.approve_code()
    check("approve settled", pump())

    listing = executor.dispatch("list_extensions", {"query": "dummy"})
    check("dummy installed", listing["ok"] and "dummy_ext" in listing["result"],
          listing["result"][:120])

    # uninstall (auto-approve pref is off in the fake prefs, so force it)
    result = executor.execute_tool("uninstall_extension", {"module": "dummy_ext"})
    check("uninstall ok", "uninstalled" in result, result)
    mods = [m.__name__ for m in __import__("addon_utils").modules()
            if m.__name__.endswith("dummy_ext")]
    check("dummy gone", not mods)


def scenario_perfile(wm):
    print("- scenario: history is per-project-file")
    import json as _json
    scene = bpy.context.scene
    check("history stored in .blend", "blender_ai_chat" in scene)
    saved = _json.loads(scene["blender_ai_chat"])
    check("scene history parses", isinstance(saved, list) and saved
          and any(m.get("content") == "persist check" for m in saved))

    # emulate opening a fresh project: no stored chat -> empty state
    saved_value = scene.get("blender_ai_chat")
    del scene["blender_ai_chat"]
    agent._on_load_post(None, None)  # the real load_post handler
    check("fresh project starts empty", len(wm.blender_ai_messages) == 0)

    # emulate reopening the saved project: the real handler must load the
    # stored chat back — not new_chat(), which would wipe the key first
    # (that is the reopen-erases-chat failure).
    scene["blender_ai_chat"] = saved_value
    agent._on_load_post(None, None)
    check("reopen restores history", len(wm.blender_ai_messages) == len(saved))
    check("reopen keeps stored key", "blender_ai_chat" in scene)

    # new_chat removes the stored chat from the file
    bpy.ops.blender_ai.new_chat()
    check("new chat clears stored history", "blender_ai_chat" not in scene)


def scenario_error(wm):
    print("- scenario: provider error surfaces as error message")

    def mock_fail(provider_id, api_key, model, messages, tools=None,
                  temperature=0.4, timeout=90, thinking=False, **kwargs):
        raise RuntimeError("HTTP 500: simulated outage")

    providers.chat_completions = mock_fail
    wm.blender_ai_input = "hello"
    bpy.ops.blender_ai.send()
    check("error settled", pump())
    last = wm.blender_ai_messages[-1]
    check("error message shown", last.role == "error" and "HTTP 500" in last.content)
    check("busy cleared", not wm.blender_ai_busy)


def scenario_pipeline_tools(wm):
    print("- scenario: pipeline tools (spec, create, validate, lod, collision, export, capture)")
    import json as _json

    # every export kwarg exists in the operator RNA (API renames break CI, not users)
    import bpy.app
    from blender_ai.pipeline import checks, facts
    from blender_ai.pipeline.export import plan_for
    from blender_ai.pipeline.spec import SPEC_KEY, Engine
    from blender_ai.tools import mesh_ops
    from blender_ai.tools import pipeline as pipe
    from blender_ai.tools import scene as scene_tools
    for engine in Engine:
        plan = plan_for(engine)
        op = bpy.ops.export_scene.gltf if plan.operator == "gltf" else bpy.ops.export_scene.fbx
        rna_props = {p.identifier for p in op.get_rna_type().properties}
        missing = [k for k in plan.kwargs if k not in rna_props]
        check("export kwargs in RNA (%s)" % engine.value, not missing, missing)

    result = pipe.set_asset_spec(
        "Barrel", "Wooden storage barrel with two iron hoops",
        asset_class="prop", engine="godot", style="lowpoly",
        size_m=[0.6, 0.6, 0.9])
    check("spec created", '"collection":"SM_Barrel"' in result, result)
    coll = bpy.data.collections["SM_Barrel"]
    spec_json = coll.get(SPEC_KEY)
    check("spec stored on collection", bool(spec_json))
    from blender_ai.pipeline.spec import AssetSpec
    spec = AssetSpec.from_json(spec_json)
    check("godot lods default to none", spec.lods == (1.0,), spec.lods)
    low, high = spec.budget
    check("lowpoly budget is quarter", (low, high) == (125, 1250), (low, high))

    scene_tools.create_primitive(
        "cylinder", name="SM_Barrel_Body", vertices=12,
        dimensions=[0.6, 0.6, 0.9], origin="bottom",
        description="Oak barrel body", role="body", color="wood_light")
    body = bpy.data.objects["SM_Barrel_Body"]
    check("dimensions in meters", abs(body.dimensions.z - 0.9) < 0.02,
          tuple(round(d, 3) for d in body.dimensions))
    check("origin bottom", body.location.z < 0.01, body.location.z)
    check("min z at zero",
          abs(min((body.matrix_world @ v.co).z for v in body.data.vertices)) < 1e-4)
    check("description stored", body.get("ai_description") == "Oak barrel body")
    check("asset stored", body.get("ai_asset") == "SM_Barrel")
    check("color is palette wood_light", body.color[0] > 0.2 and body.color[2] < 0.5,
          tuple(round(c, 2) for c in body.color))
    check("Col attribute written",
          body.data.color_attributes.get("Col") is not None)
    check("shared vertex material",
          any(m and m.name == "M_SM_Barrel_VertexColor" for m in body.data.materials))
    # auto color fallback
    scene_tools.create_primitive(
        "torus", name="SM_Barrel_Hoop_Top", dimensions=[0.62, 0.62, 0.04],
        location=[0, 0, 0.75], major_segments=12, minor_segments=4,
        description="Iron hoop, top", role="trim", color="iron")
    hoop = bpy.data.objects["SM_Barrel_Hoop_Top"]
    check("in asset collection", hoop.name in coll.objects)

    # mesh_op: barrel bulge via band scale
    mesh_ops.mesh_op("SM_Barrel_Body", "subdivide", "side", {"cuts": 2})
    mesh_ops.mesh_op("SM_Barrel_Body", "scale_faces", "band",
                     {"factor": 1.1, "axis": "XY", "band": [0.3, 0.7]})
    check("bulge widened body", body.dimensions.x > 0.605,
          round(body.dimensions.x, 4))

    # validation on the raw blockout: unapplied scale is the expected FAIL;
    # primitives ship with a UV map, so uv.missing must NOT fire
    scene_facts = facts.collect_asset_facts(bpy.context.scene)
    results = checks.evaluate(scene_facts, scene_facts["spec"])
    fails = [c.id for c in results if c.status == "FAIL"]
    warns = [c.id for c in results if c.status == "WARN"]
    check("scale.applied FAIL on blockout", "scale.applied" in fails, fails)
    check("normals pass on stock primitives", "normals.flipped" not in fails, fails)
    check("no uv.missing on primitives", "uv.missing" not in fails + warns,
          fails + warns)

    # origin fix (hoop parented to body: only roots need a base origin)
    hoop.parent = body
    mesh_ops.set_origin("SM_Barrel_Body", "bottom")
    scene_facts = facts.collect_asset_facts(bpy.context.scene)
    check("origin.base passes after fix",
          checks.evaluate(scene_facts, spec, only="origin.base")[0].status == "PASS")

    # LODs + collision
    created = pipe.generate_lods()
    check("godot default skips lods", _json.loads(created)["created"] == [], created)
    lods = pipe.generate_lods(ratios=[0.5])
    check("one lod created", "SM_Barrel_Body_LOD1" in lods, lods)
    lod_obj = bpy.data.objects["SM_Barrel_Body_LOD1"]
    check("lod has decimate modifier",
          any(m.type == "DECIMATE" for m in lod_obj.modifiers))
    check("lod role", lod_obj.get("ai_role") == "lod")
    cols = _json.loads(pipe.make_collision("box"))["created"]
    check("godot collision name", cols and "-convcolonly" in cols[0], cols)
    check("collision skips lods", all("_LOD" not in c for c in cols), cols)
    col_obj = bpy.data.objects[cols[0]]
    check("collision wire + no render",
          col_obj.display_type == "WIRE" and col_obj.hide_render)

    # done-gate: capture sheet is written
    cap = pipe.capture_view(["iso", "front"])
    cap_payload = _json.loads(cap)
    cap_path = cap_payload["image"]
    check("capture png exists", os.path.isfile(cap_path), cap_path)
    wm.blender_ai_capture_path = cap_path

    # export: must REFUSE while scale.applied FAILs (the done-gate)
    out = pipe.export_asset()
    check("export refuses on FAIL", out.startswith("REFUSED"), out[:80])

    # finalize, then export for real
    from blender_ai.tools import mesh_ops as _mo
    _mo.finalize("SM_Barrel", apply_transform=True)
    out = pipe.export_asset()
    payload = _json.loads(out)
    check("export ok", payload["ok"] is True, out)
    check("export file exists", os.path.isfile(payload["path"]), payload["path"])
    before = {o["name"]: o["description"] for o in scene_facts["objects"]}
    bpy.ops.import_scene.gltf(filepath=payload["path"])
    reimported = next((o for o in bpy.data.objects
                       if o.name.startswith("SM_Barrel_Body")), None)
    check("glb re-import has body", reimported is not None)
    if reimported is not None:
        check("glb extras carry ai_description",
              reimported.get("ai_description") == before.get("SM_Barrel_Body"),
              reimported.get("ai_description"))


def scenario_skills(wm):
    print("- scenario: skills (parse, index, auto-match, load_skill)")
    from blender_ai.skills import SkillIndex

    index = SkillIndex.load(None)
    check("built-in skills parsed", len(index.skills) >= 18, len(index.skills))
    check("no parse errors", index.errors == (), index.errors)
    tree = index.get("lowpoly-tree")
    check("tree skill exists", tree is not None)
    if tree:
        check("tree triggers", "pine" in tree.triggers and tree.tri_budget == (150, 600))
    match = index.best_match("make a low-poly pine tree for my godot game")
    check("auto-match picks tree", match is not None and match.name == "lowpoly-tree",
          match.name if match else None)
    match2 = index.best_match("a wooden crate please")
    check("auto-match picks prop", match2 is not None and match2.name == "lowpoly-prop",
          match2.name if match2 else None)
    body = None
    from blender_ai.tools import pipeline as pipe
    try:
        body = pipe.load_skill("lowpoly-prop")
    except Exception as exc:
        check("load_skill runs", False, exc)
    check("load_skill returns body", body is not None and "## Steps" in body)
    check("skill pinned", agent_module_pin() == "lowpoly-prop")
    # index reaches the prompt
    from blender_ai import prompts
    block = index.index_block()
    text = prompts.system_prompt(block)
    check("index in prompt", "lowpoly-prop" in text)


def agent_module_pin():
    from blender_ai.pipeline.session import session
    return session.pinned_skill


def scenario_panel(wm):
    print("- scenario: panel draw-path helpers on live data")
    from blender_ai.ui import panel as ui_panel

    # wrap cache: region-width-aware, deterministic
    lines = ui_panel._wrap_lines("word " * 40, 20)
    check("wrap produces lines", len(lines) >= 10 and
          all(len(line) <= 20 for line in lines), lines[:2])
    check("wrap cached", ui_panel._wrap_lines("word " * 40, 20) is lines)

    # region width derives from ui_scale without exploding
    width = ui_panel._region_wrap_width(bpy.context)
    check("region width sane", 20 <= width <= 400, width)

    # pipeline summary derives stage/progress from the live scene
    summary = ui_panel._pipeline_summary()
    if bpy.context.scene.get("blender_ai_active_asset"):
        check("pipeline summary computed", summary is not None and
              0 <= summary[2] <= 1, summary[:3] if summary else None)
    else:
        check("pipeline summary None without asset", summary is None)

    # skills snapshot is cached and re-reads within TTL
    snap1 = ui_panel._skills_snapshot()
    snap2 = ui_panel._skills_snapshot()
    check("skills snapshot cached", snap1 is snap2 and len(snap1.skills) >= 18)


def main():
    print("== blender_ai smoke ==")
    bpy.context.preferences.system.use_online_access = True  # agent guard

    # The repair loop persists to the skill store; sandbox it so this run
    # never writes loop files or notes into the user's real store.
    import tempfile
    loop_tmp = tempfile.mkdtemp(prefix="blender_ai_smoke_")
    agent._loop_store_dir = lambda: loop_tmp

    # If this addon is also installed+enabled as an extension in this
    # Blender config, disable it for the run — otherwise two copies share
    # the same operator/WM property names and quit-time unregister fails.
    import addon_utils
    for mod in addon_utils.modules():
        if mod.__name__.endswith(".blender_ai"):
            addon_utils.disable(mod.__name__, default_set=False)

    # From-source runs have no add-on preferences entry; inject a stand-in
    # so the agent loop and the approval gate are exercised end to end.
    import types
    fake_prefs = types.SimpleNamespace(
        provider="zai",
        api_key_zai="test-key", api_key_deepseek="", api_key_openrouter="",
        model="", temperature=0.4, auto_approve_code=False, history_limit=80,
        reasoning_effort="medium",
        export_dir="", skills_dir="", vision_provider="", vision_model="",
        tool_profile="full",
        get_api_key=lambda: "test-key",
        get_model=lambda: "glm-4.6",
    )
    agent.get_prefs = lambda: fake_prefs
    import blender_ai.executor as _executor
    _executor.get_prefs = lambda: fake_prefs
    import blender_ai.prefs as _prefs_mod
    _prefs_mod.get_prefs = lambda: fake_prefs

    blender_ai.register()
    check("operators registered", hasattr(bpy.ops.blender_ai, "send"))
    bpy.ops.blender_ai.new_chat()  # drop leftovers from previous runs

    wm = bpy.context.window_manager
    try:
        mock = scenario_structural_loop(wm)
        bpy.ops.blender_ai.new_chat()

        scenario_code_gate(wm)
        bpy.ops.blender_ai.new_chat()

        scenario_ask_user(wm)
        bpy.ops.blender_ai.new_chat()

        scenario_collapse(wm)
        bpy.ops.blender_ai.new_chat()

        scenario_poll_keepalive(wm)
        bpy.ops.blender_ai.new_chat()

        scenario_reasoning(wm)
        bpy.ops.blender_ai.new_chat()

        scenario_extension_tools(wm)
        bpy.ops.blender_ai.new_chat()

        scenario_error(wm)

        scenario_pipeline_tools(wm)
        bpy.ops.blender_ai.new_chat()

        scenario_skills(wm)
        bpy.ops.blender_ai.new_chat()

        scenario_panel(wm)

        # per-file persistence: history lives inside the .blend
        wm.blender_ai_input = "persist check"
        providers.chat_completions = mock
        bpy.ops.blender_ai.send()
        pump()
        scenario_perfile(wm)
    finally:
        blender_ai.unregister()

    print("== SMOKE %s ==" % ("FAILED: %s" % FAILURES if FAILURES else "OK"))
    if FAILURES:
        sys.exit(1)


main()
