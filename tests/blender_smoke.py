"""Headless smoke test for the blender_ai extension.
# mypy: ignore-errors

Run:
    blender --background --python tests/blender_smoke.py

Covers: registration, the agent loop against a mock provider (build_model
-> finish), the build_model approval gate (code NOT executed until Approve;
reject branch returns control to the agent), ask_user, the continue nudge,
the finish guard, transient-error retries, per-file history (stored inside
the .blend), and the modeling kit + Bevy GLB export on real geometry.

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


def scenario_structural_loop(wm, prefs):
    print("- scenario: build_model -> finish loop")
    calls = {"n": 0}

    def mock(provider_id, api_key, model, messages, tools=None,
             temperature=0.4, timeout=90, thinking=False, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return mock_response(tool_calls=[
                tool_call("call_1", "build_model",
                          {"name": "MockModel",
                           "code": "mk.box('MockCube', (1, 1, 1), mat='steel')"}),
            ])
        return mock_response(tool_calls=[
            tool_call("call_2", "finish", {"summary": "Built MockModel."})])

    providers.chat_completions = mock
    prefs.auto_approve_code = True
    try:
        wm.blender_ai_input = "make me a cube"
        bpy.ops.blender_ai.send()
        check("loop settled", pump())
    finally:
        prefs.auto_approve_code = False
    check("cube created", "MockCube" in bpy.data.objects)
    roles = [m.role for m in wm.blender_ai_messages]
    check("history shape",
          roles == ["user", "assistant", "tool", "assistant", "tool", "assistant"], roles)
    check("finish summary is the final answer",
          wm.blender_ai_messages[-1].content == "Built MockModel.")
    check("two provider rounds only", calls["n"] == 2, calls["n"])
    return mock


def scenario_code_gate(wm):
    print("- scenario: build_model approval gate")
    calls = {"n": 0}

    def mock_gate(provider_id, api_key, model, messages, tools=None,
                  temperature=0.4, timeout=90, thinking=False, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return mock_response(tool_calls=[
                tool_call("call_py", "build_model",
                          {"name": "Gate", "code": "print('SIDE EFFECT'); "
                                                   "bpy.data.meshes.new('EvilMesh')"}),
            ])
        # after resolution the agent continues and ends the task
        return mock_response(tool_calls=[
            tool_call("call_fin", "finish", {"summary": "Understood."})])

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
    tool_msgs = [m for m in wm.blender_ai_messages if m.tool_name == "build_model"]
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
    tool_msgs = [m for m in wm.blender_ai_messages if m.tool_name == "build_model"]
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
    check("runaway retry turns thinking off", efforts[-1] == "off", efforts)
    check("runaway nudge sent to the model",
          any(m.get("auto") and "spent its whole budget" in m.get("content", "")
              for m in agent.messages()))
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


def scenario_vision(wm, prefs):
    print("- scenario: automatic vision (native, rejection -> caption fallback)")
    import os
    import tempfile

    from blender_ai import attachments

    img = bpy.data.images.new("vision_probe", 8, 8)
    png = os.path.join(tempfile.mkdtemp(), "probe.png")
    img.filepath_raw = png
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)

    saved = (prefs.provider, prefs.get_model, prefs.api_key_deepseek)
    prefs.provider = "deepseek"
    prefs.api_key_deepseek = "ds-key"
    prefs.get_model = lambda: "deepseek-flash"
    calls = []

    def parts(messages):
        return [p for m in messages if isinstance(m.get("content"), list)
                for p in m["content"] if isinstance(p, dict)]

    def mock(provider_id, api_key, model, messages, tools=None, **kwargs):
        calls.append((provider_id, model, tools is not None, parts(messages)))
        if tools is None:  # captioning call
            return mock_response(content="a small grey square")
        if any(p.get("type") == "image_url" for p in parts(messages)):
            raise RuntimeError("DeepSeek returned HTTP 400: image input is not supported")
        return mock_response(content="I see it.")

    providers.chat_completions = mock
    try:
        # 1. deepseek-flash is documented with vision: image goes out natively,
        #    then the rejection teaches the add-on and it retries with a caption
        agent.send_user_message("what is this?", attachments=[attachments.load_attachment(png)])
        check("vision settled", pump())
        first = calls[0]
        check("deepseek-flash got the image natively",
              first[1] == "deepseek-flash" and any(p.get("type") == "image_url" for p in first[3]))
        check("rejection remembered", not providers.supports_vision("deepseek", "deepseek-flash"))
        caption = [c for c in calls if not c[2]]
        check("captioned by another provider automatically",
              caption and caption[0][0] == "zai" and caption[0][1] == "glm-4.6v",
              [(c[0], c[1]) for c in caption])
        last = calls[-1]
        check("retry carries the caption, not the image",
              any("a small grey square" in p.get("text", "") for p in last[3])
              and not any(p.get("type") == "image_url" for p in last[3]))
        check("answer shown", wm.blender_ai_messages[-1].content == "I see it.")
        import json as _json
        check("rejection persisted in prefs",
              "deepseek-flash" in _json.loads(prefs.models_cache)["_vision"]["no"]["deepseek"])

        # 2. next turn: caption cached, no second caption call
        n_captions = len(caption)
        agent.send_user_message("and now?")
        pump()
        check("caption cached across rounds",
              len([c for c in calls if not c[2]]) == n_captions)
    finally:
        providers._NO_VISION.clear()
        prefs.provider, prefs.get_model, prefs.api_key_deepseek = saved



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

    attempts = {"n": 0}

    def mock_fail(provider_id, api_key, model, messages, tools=None,
                  temperature=0.4, timeout=90, thinking=False, **kwargs):
        attempts["n"] += 1
        raise RuntimeError("HTTP 500: simulated outage")

    providers.chat_completions = mock_fail
    wm.blender_ai_input = "hello"
    bpy.ops.blender_ai.send()
    check("error settled", pump())
    check("transient error retried twice", attempts["n"] == 3, attempts["n"])
    last = wm.blender_ai_messages[-1]
    check("error message shown", last.role == "error" and "HTTP 500" in last.content)
    check("busy cleared", not wm.blender_ai_busy)


BUNKER = """
R, WALL, DEPTH = 6.0, 0.5, 12.0
shaft = mk.tube('Shaft', R, WALL, DEPTH, (0, 0, -DEPTH), mat='concrete')
mk.cylinder('Base', R, 0.4, (0, 0, -DEPTH - 0.4), mat='concrete_dark')
for i in (1,):
    mk.tube(f'Floor_{i}', R - WALL, 2.0, 0.3, (0, 0, -6.0 * i), mat='concrete_dark')
col = mk.cylinder('Column', 0.6, DEPTH, (0, 0, -DEPTH), mat='steel')
step = mk.box('Step', (2.0, 0.6, 0.12), (1.6, 0, -DEPTH + 0.2), mat='dark_steel')
for i in range(1, 40):
    a = i * 15
    s = mk.copy(step, f'Step_{i}', rot=(0, 0, a))
    s.location = (1.6 * math.cos(math.radians(a)), 1.6 * math.sin(math.radians(a)),
                  -DEPTH + 0.2 + i * 0.3)
block = mk.box('Entrance', (8, 6, 3.5), (0, 0, 0), mat='concrete')
mk.cut(block, mk.box('door_cut', (2.4, 2.0, 2.6), (0, -2.6, 0)))
mk.bevel(block, 0.04, 1)
door = mk.box('Blast_Door', (2.4, 0.2, 2.6), (0, -3.2, 0), mat='dark_steel')
wheel = mk.torus('Wheel', 0.4, 0.08, (0, -3.35, 1.3), axis='Y', mat='red', anchor='center')
mk.group('Door', door, wheel, at=(-1.2, -3.2, 0))
mk.sphere('Dome', 2.5, (0, 0.5, 3.5), hemi=True, mat='concrete_dark')
rail = mk.box('Rail', (0.05, 0.05, 1.0), (-2.0, -3.4, 0), mat='steel')
mk.mirror(rail, 'X', 'Rail_R')
vent = mk.cylinder('Vent', 0.3, 1.0, (3.0, 2.0, 3.5), verts=12, mat='rust')
mk.repeat(vent, 2, (-6.0, 0, 0))
lamp = mk.box('Lamp', (0.4, 0.2, 0.2), (0, -(R - WALL - 0.1), -3), mat='light_cold', anchor='center')
mk.radial(lamp, 4)
mk.stairs('Stairs', 2.4, 0.9, 1.6, 5, (0, -4.9, 0), mat='concrete')
mk.profile('Frame', [(-1.5, 0), (1.5, 0), (1.5, 3.0), (-1.5, 3.0)], 0.2, (0, -3.05, 0), mat='hazard_yellow')
print('built', len(mk.collection.objects))
"""


def scenario_build_tools(wm):
    print("- scenario: modeling kit, report, rebuild, export for Bevy, capture")
    import json as _json
    import os

    from blender_ai.tools import build

    report = build.build_model("Bunker", BUNKER)
    check("bunker builds", report.startswith("BUILD OK"), report[:300])
    check("bunker is one connected object", "disconnected" not in report, report[-600:])
    check("script output in report", "built" in report)
    check("linked steps fold", "(linked copies)" in report)
    coll = bpy.data.collections["Bunker"]
    names = {o.name for o in coll.all_objects}
    for expected in ("Shaft", "Entrance", "Door", "Dome", "Rail_R", "Vent_1", "Lamp_3", "Stairs"):
        check("part %s" % expected, expected in names)
    check("cutters removed", "door_cut" not in names)
    check("baked mesh keeps its name", bpy.data.objects["Entrance"].data.name == "Entrance")
    rail_r = bpy.data.objects["Rail_R"]
    check("mirror lands at +x", abs(rail_r.location.x - 2.0) < 1e-4, tuple(rail_r.location))
    lamp1 = bpy.data.objects["Lamp_1"]  # 90 deg: (0,-5.4) -> (5.4, 0)
    check("radial rotates around z", abs(lamp1.matrix_world.translation.x - 5.4) < 0.05,
          tuple(lamp1.matrix_world.translation))
    door = bpy.data.objects["Door"]
    check("group has children", len(door.children) == 2)
    check("door cut made a hole",
          len(bpy.data.objects["Entrance"].data.polygons) > 6)
    check("dome is closed", all(e.is_manifold for e in bpy.data.objects["Dome"].data.edges)
          if hasattr(bpy.types.MeshEdge, "is_manifold") else True)
    check("script stored as text", "build_Bunker.py" in bpy.data.texts)
    check("read_script round-trips", build.read_script("Bunker") == BUNKER)

    # rebuild wipes the old parts (deterministic iteration)
    report2 = build.build_model("Bunker", "mk.box('Only', (1, 1, 1))")
    check("rebuild replaces parts",
          {o.name for o in coll.all_objects} == {"Only"}, report2[:200])
    check("no orphan meshes after rebuild", "Shaft" not in bpy.data.meshes)

    # script errors point at the failing line; earlier parts survive
    report3 = build.build_model("Bunker", "mk.box('A', (1, 1, 1))\nmk.bx('B')\n")
    check("error names the line", "BUILD ERROR" in report3 and "line 2" in report3, report3[:200])
    check("partial parts kept", "A" in bpy.data.objects)
    report4 = build.build_model("Bunker", "mk.box('A', (1, 1, 1), mat='unobtainium')")
    check("unknown material explains palette", "palette key" in report4, report4[:200])
    report5 = build.build_model("Bunker", "mk.box('A', (1,1,1))\nmk.box('B', (1,1,1), (10,0,0))")
    check("scattered parts flagged", "2 disconnected groups" in report5, report5)

    # scene state + export
    build.build_model("Bunker", BUNKER)
    state = _json.loads(build.get_scene_state())
    check("scene state lists model", any(m["name"] == "Bunker" and m["parts"] > 20
                                          for m in state["models"]), state["models"])
    props = bpy.ops.export_scene.gltf.get_rna_type().properties
    missing = [k for k in build._BEVY_GLTF if k not in props]
    check("export kwargs exist in RNA", not missing, missing)
    out = _json.loads(build.export_glb("Bunker"))
    check("glb written", out["ok"] and os.path.getsize(out["path"]) > 10_000, out)
    check("bevy load snippet", "GltfAssetLabel::Scene(0)" in out["bevy"])
    before = set(bpy.data.objects.keys())
    bpy.ops.import_scene.gltf(filepath=out["path"])
    imported = set(bpy.data.objects.keys()) - before
    check("glb re-import has the blast door", any(n.startswith("Blast_Door") for n in imported),
          sorted(imported)[:8])
    for name in imported:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)

    cap = _json.loads(build.capture_view("Bunker"))
    check("capture sheet written", os.path.isfile(cap["image"]))
    from blender_ai.tools import tools_schema
    check("capture hidden for text-only models",
          "capture_view" not in {t["function"]["name"] for t in tools_schema(vision=False)})


def scenario_nudge(wm, prefs):
    print("- scenario: build turn that stops in prose gets nudged, then finishes")
    calls = {"n": 0}
    seen = {}

    def mock(provider_id, api_key, model, messages, tools=None,
             temperature=0.4, timeout=90, thinking=False, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return mock_response(tool_calls=[tool_call(
                "b1", "build_model", {"name": "N", "code": "mk.box('Body', (1, 1, 1))"})])
        if calls["n"] == 2:
            return mock_response(content="Next I will add the roof and the door.")
        seen["last_user"] = [m for m in messages if m.get("role") == "user"][-1]["content"]
        return mock_response(tool_calls=[tool_call("f1", "finish", {"summary": "Done: N."})])

    providers.chat_completions = mock
    prefs.auto_approve_code = True
    try:
        wm.blender_ai_input = "make a house"
        bpy.ops.blender_ai.send()
        check("nudge settled", pump())
    finally:
        prefs.auto_approve_code = False
    check("model was nudged to continue", "(harness)" in seen.get("last_user", ""), seen)
    check("nudge shown as auto-continue",
          any(m.role == "auto" for m in wm.blender_ai_messages))
    check("finished after nudge", wm.blender_ai_messages[-1].content == "Done: N.")
    check("three rounds", calls["n"] == 3, calls["n"])

    # plain Q&A (no build) is never nudged
    calls["n"] = 10
    providers.chat_completions = lambda *a, **k: mock_response(content="Bevy uses Y-up.")
    bpy.ops.blender_ai.new_chat()
    wm.blender_ai_input = "which axis is up in bevy?"
    bpy.ops.blender_ai.send()
    check("qa settled", pump())
    check("qa not nudged", [m.role for m in wm.blender_ai_messages] == ["user", "assistant"])


def scenario_runaway_then_build(wm, prefs):
    print("- scenario: reasoning runaway -> build continues with thinking off")
    efforts = []

    def mock(provider_id, api_key, model, messages, tools=None, **kwargs):
        efforts.append(kwargs.get("reasoning_effort"))
        if len(efforts) == 1:
            return mock_response(extra_message_fields={"reasoning_content": "x" * 5000})
        if len(efforts) == 2:
            return mock_response(tool_calls=[tool_call(
                "b1", "build_model", {"name": "R", "code": "mk.box('Body', (1, 1, 1))"})])
        return mock_response(tool_calls=[tool_call("f1", "finish", {"summary": "ok"})])

    providers.chat_completions = mock
    prefs.auto_approve_code = True
    try:
        wm.blender_ai_input = "build a vault"
        bpy.ops.blender_ai.send()
        check("runaway build settled", pump())
    finally:
        prefs.auto_approve_code = False
    check("thinking stays off for the task", efforts == ["medium", "off", "off"], efforts)
    check("runaway task finished", wm.blender_ai_messages[-1].content == "ok")


def scenario_finish_guard(wm, prefs):
    print("- scenario: finish in the same batch as a failed build is refused")
    calls = {"n": 0}

    def mock(provider_id, api_key, model, messages, tools=None,
             temperature=0.4, timeout=90, thinking=False, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return mock_response(tool_calls=[
                tool_call("b1", "build_model", {"name": "G", "code": "mk.nope()"}),
                tool_call("f1", "finish", {"summary": "all good"})])
        return mock_response(tool_calls=[tool_call("f2", "finish", {"summary": "fixed"})])

    providers.chat_completions = mock
    prefs.auto_approve_code = True
    try:
        wm.blender_ai_input = "make g"
        bpy.ops.blender_ai.send()
        check("guard settled", pump())
    finally:
        prefs.auto_approve_code = False
    contents = [m.content for m in wm.blender_ai_messages]
    check("finish refused after error", any("NOT FINISHED" in c for c in contents))
    check("task finished on retry", contents[-1] == "fixed", contents[-1])


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

    check("no pipeline/skills sections",
          not hasattr(ui_panel, "_pipeline_summary")
          and not hasattr(ui_panel, "_skills_snapshot"))


def main():
    print("== blender_ai smoke ==")
    bpy.context.preferences.system.use_online_access = True  # agent guard

    # Test runs must never write into the user's real debug log (they
    # did: fake "HTTP 500" errors and stop bursts buried real sessions).
    import os
    import tempfile
    os.environ["BLENDER_AI_LOG"] = os.path.join(
        tempfile.mkdtemp(prefix="blender_ai_smoke_"), "debug.log")

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
        export_dir="", models_cache="{}",
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
        mock = scenario_structural_loop(wm, fake_prefs)
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

        scenario_nudge(wm, fake_prefs)
        bpy.ops.blender_ai.new_chat()

        scenario_finish_guard(wm, fake_prefs)
        bpy.ops.blender_ai.new_chat()

        scenario_runaway_then_build(wm, fake_prefs)
        bpy.ops.blender_ai.new_chat()

        scenario_vision(wm, fake_prefs)
        bpy.ops.blender_ai.new_chat()

        scenario_error(wm)

        scenario_build_tools(wm)
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
