"""Headless smoke test for the blender_ai extension.

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

import bpy  # noqa: E402

import blender_ai  # noqa: E402
from blender_ai import agent, providers  # noqa: E402

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
            and agent._STATE["thread"] is None
            and agent._STATE["result"] is None
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
             temperature=0.4, timeout=90, thinking=False):
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
                  temperature=0.4, timeout=90, thinking=False):
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
    check("pending code in panel", bool(wm.blender_ai_pending_code))
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
    check("pending box cleared", not wm.blender_ai_pending_code)

    # --- approve branch
    calls["n"] = 0
    wm.blender_ai_input = "run some code again"
    bpy.ops.blender_ai.send()
    check("parked again", pump())
    check("pending code shown", bool(wm.blender_ai_pending_code))
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
                 temperature=0.4, timeout=90, thinking=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return mock_response(tool_calls=[
                tool_call("call_ask", "ask_user",
                          {"question": "Round or square?",
                           "options": ["round", "square"]}),
            ])
        # The user's answer must arrive as the ask_user tool result.
        tool_results = [m for m in messages if m.get("role") == "tool"]
        answer = tool_results[-1]["content"] if tool_results else ""
        return mock_response(content="You chose: %s" % answer)

    providers.chat_completions = mock_ask
    wm.blender_ai_input = "make a thing"
    bpy.ops.blender_ai.send()
    check("parked for question", pump())
    check("question in panel", wm.blender_ai_ask_question == "Round or square?")
    check("options in panel", wm.blender_ai_ask_options == '["round", "square"]')

    # answer via option button path (operator with .option property)
    op = bpy.ops.blender_ai.answer
    # emulate the panel option button: set the option property via dict
    result = op(option="round")
    check("answer settled", result == {'FINISHED'} and pump())
    check("ask fields cleared", not wm.blender_ai_ask_question)
    final = wm.blender_ai_messages[-1].content
    check("answer reached model", final == "You chose: round", final)


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


def scenario_reasoning(wm):
    print("- scenario: model reasoning is captured and shown")
    providers.chat_completions = lambda *a, **k: mock_response(
        content="A cube is a six-sided primitive.",
        extra_message_fields={"reasoning_content": "User wants a cube; "
                              "create_primitive covers it; no python needed."})
    wm.blender_ai_input = "make a cube"
    bpy.ops.blender_ai.send()
    check("settled", pump())
    last = wm.blender_ai_messages[-1]
    check("reasoning captured in panel", "create_primitive covers it" in last.reasoning)
    check("reasoning hidden by default", not last.show_reasoning)
    from blender_ai import history as hist
    stored = [m for m in agent.messages() if m.get("role") == "assistant"]
    check("reasoning stored in history",
          stored and "create_primitive" in (stored[-1].get("reasoning") or ""))
    # outbound request must not carry reasoning back to the provider
    captured = {}
    def spy(provider_id, api_key, model, messages, tools=None,
            temperature=0.4, timeout=90, thinking=False):
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


def scenario_extension_tools(wm):
    print("- scenario: extension list + gated install/uninstall")
    from blender_ai import executor
    check("addon tools registered",
          "list_extensions" in executor.TOOL_REGISTRY
          and "install_extension" in executor.TOOL_REGISTRY
          and "uninstall_extension" in executor.TOOL_REGISTRY)

    listing = executor.dispatch("list_extensions", {"query": "blender"})
    check("list_extensions finds blender_ai",
          listing["ok"] and "bl_ext.user_default.blender_ai" in listing["result"],
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
    agent._STATE["pending"] = {
        "tool_call": {"id": "call_inst", "type": "function",
                      "function": {"name": "install_extension",
                                   "arguments": _json.dumps({"source": zpath})}},
        "kind": "code",
    }
    # approve through the same path the operator uses (no provider involved)
    wm.blender_ai_ask_question = ""
    agent._STATE["messages"].append({"role": "assistant", "content": "",
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
    agent.restore_history()
    check("fresh project starts empty", len(wm.blender_ai_messages) == 0)

    # emulate reopening the saved project: history comes back
    scene["blender_ai_chat"] = saved_value
    agent.restore_history()
    check("reopen restores history", len(wm.blender_ai_messages) == len(saved))

    # new_chat removes the stored chat from the file
    bpy.ops.blender_ai.new_chat()
    check("new chat clears stored history", "blender_ai_chat" not in scene)


def scenario_error(wm):
    print("- scenario: provider error surfaces as error message")

    def mock_fail(provider_id, api_key, model, messages, tools=None,
                  temperature=0.4, timeout=90, thinking=False):
        raise RuntimeError("HTTP 500: simulated outage")

    providers.chat_completions = mock_fail
    wm.blender_ai_input = "hello"
    bpy.ops.blender_ai.send()
    check("error settled", pump())
    last = wm.blender_ai_messages[-1]
    check("error message shown", last.role == "error" and "HTTP 500" in last.content)
    check("busy cleared", not wm.blender_ai_busy)


def main():
    print("== blender_ai smoke ==")
    bpy.context.preferences.system.use_online_access = True  # agent guard

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
        enable_thinking=True,
        get_api_key=lambda: "test-key",
        get_model=lambda: "glm-4.6",
    )
    agent._prefs = lambda: fake_prefs

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

        scenario_reasoning(wm)
        bpy.ops.blender_ai.new_chat()

        scenario_extension_tools(wm)
        bpy.ops.blender_ai.new_chat()

        scenario_error(wm)

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
