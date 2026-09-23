"""Headless smoke test for the blender_ai extension.

Run:
    blender --background --python tests/blender_smoke.py

Covers: registration, the agent loop against a mock provider (structural
tool call -> final answer), the run_python approval gate (code NOT executed
until Approve; reject branch returns control to the agent), and chat.json
persistence.

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


def mock_response(content="", tool_calls=None):
    return {
        "message": {"role": "assistant", "content": content,
                    "tool_calls": tool_calls or None},
        "usage": {},
    }


def tool_call(call_id, name, arguments):
    return {
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def scenario_structural_loop(wm):
    print("- scenario: structural tool call loop")
    calls = {"n": 0}

    def mock(provider_id, api_key, model, messages, tools=None,
             temperature=0.4, timeout=90):
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
                  temperature=0.4, timeout=90):
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
                 temperature=0.4, timeout=90):
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


def scenario_error(wm):
    print("- scenario: provider error surfaces as error message")

    def mock_fail(provider_id, api_key, model, messages, tools=None,
                  temperature=0.4, timeout=90):
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

    # From-source runs have no add-on preferences entry; inject a stand-in
    # so the agent loop and the approval gate are exercised end to end.
    import types
    fake_prefs = types.SimpleNamespace(
        provider="zai",
        api_key_zai="test-key", api_key_deepseek="", api_key_openrouter="",
        model="", temperature=0.4, auto_approve_code=False, history_limit=80,
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

        scenario_error(wm)

        # persistence after everything
        from blender_ai.agent import chat_file
        wm.blender_ai_input = "persist check"
        providers.chat_completions = mock
        bpy.ops.blender_ai.send()
        pump()
        from blender_ai import history as hist
        stored = hist.load(chat_file())
        check("chat.json parses", isinstance(stored, list) and stored[0]["role"] == "user")
        check("history trimmed <= limit", len(stored) <= 80)
    finally:
        blender_ai.unregister()

    print("== SMOKE %s ==" % ("FAILED: %s" % FAILURES if FAILURES else "OK"))
    if FAILURES:
        sys.exit(1)


main()
