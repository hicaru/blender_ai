"""Scenario evals (headless): run eval_cases.json against a live provider.
# mypy: ignore-errors

    blender -b --factory-startup --python tests/run_evals.py -- \
        --provider openrouter --model anthropic/claude-... [--cases id,id2]

Each case runs in a fresh scene with the real agent loop (mock-free),
bounded by --max-rounds. Expectations are asserted with the SAME
production validation code (pipeline.facts/checks) — evals reuse
production checks instead of a parallel implementation.

Metrics per run (JSONL next to this file, eval_results.jsonl):
id, pass, tool_calls, tool_errors, rounds, wall_s, failure reason.
"""

import argparse
import json
import os
import sys
import time

import bpy

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, os.path.dirname(_ROOT))  # repo parent


def _load_cases(only):
    with open(os.path.join(_HERE, "eval_cases.json"), encoding="utf-8") as fh:
        cases = json.load(fh)
    if only:
        wanted = {c.strip() for c in only.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    return cases


def _install_prefs(provider_id):
    from blender_ai import prefs

    prefs.get_prefs().provider = provider_id
    # API keys come from the environment; abort loudly when missing.
    key = os.environ.get("BLENDER_AI_API_KEY", "")
    if not key:
        raise SystemExit("set BLENDER_AI_API_KEY for the chosen provider")
    setattr(prefs.get_prefs(), "api_key_%s" % provider_id, key)


def _tool_trace():
    """(names, errors) from the message history since the run started."""
    from blender_ai import agent

    names, errors = [], 0
    for msg in agent._STATE.messages:
        if msg.get("role") == "assistant" and isinstance(msg.get("tool_calls"), list):
            names += [c.get("function", {}).get("name", "?")
                      for c in msg["tool_calls"]]
        elif msg.get("role") == "tool":
            content = str(msg.get("content", ""))
            if content.startswith("ERROR"):
                errors += 1
    return names, errors


def _check_expectations(case):
    """Production-code assertions over the final scene. Returns failure list."""
    from blender_ai.pipeline import checks, facts

    problems = []
    scene_facts = facts.collect_scene_facts(bpy.context.scene)
    expect = case.get("expect", {})
    asset = scene_facts.get("asset")
    if expect.get("asset") and (asset is None or
                                asset["asset"] != expect["asset"]):
        problems.append("asset %r not active" % expect["asset"])
        return problems
    if asset is not None:
        results = checks.evaluate(asset, asset.get("spec"))
        fails = [c.id for c in results if c.status == "FAIL"]
        if fails:
            problems.append("validation FAILs: %s" % ", ".join(fails))
    for tc in case.get("expected_tool_calls", []):
        if tc["name"] not in _seen_tools:
            problems.append("missing tool call: %s" % tc["name"])
    return problems


_seen_tools = []


def run_case(case, max_rounds):
    from blender_ai import agent

    bpy.ops.wm.read_factory_settings(use_empty=True)
    agent.new_chat()
    _seen_tools.clear()
    start = time.monotonic()

    agent.send_user_message(case["task"])
    rounds = 0
    idle = 0
    while rounds < max_rounds:
        rounds += 1
        # _poll drives the tool executor on this (main) thread, exactly as
        # the bpy.app.timers hook does in the UI; in background mode we call
        # it manually on a fixed cadence.
        try:
            agent._poll()
        except Exception:
            pass
        _snapshot_tools()
        running = agent._STATE.thread is not None or agent._STATE.pending is not None
        idle = 0 if running else idle + 1
        if idle > 3 and not running:
            break
        time.sleep(0.2)
    agent.stop()
    names, errors = _tool_trace()
    failures = _check_expectations(case)
    return {
        "id": case["id"],
        "pass": not failures,
        "tool_calls": len(names),
        "tool_errors": errors,
        "rounds": rounds,
        "wall_s": round(time.monotonic() - start, 2),
        "failures": failures,
    }


def _snapshot_tools():
    from blender_ai import agent

    for msg in agent._STATE.messages:
        if msg.get("role") == "assistant" and isinstance(msg.get("tool_calls"), list):
            for c in msg["tool_calls"]:
                name = c.get("function", {}).get("name")
                if name and name not in _seen_tools:
                    _seen_tools.append(name)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--cases", default="")
    parser.add_argument("--max-rounds", type=int, default=24)
    args = parser.parse_args(argv)

    import blender_ai

    blender_ai.register()
    _install_prefs(args.provider)
    if args.model:
        from blender_ai import prefs

        prefs.get_prefs().model = args.model

    out_path = os.path.join(_HERE, "eval_results.jsonl")
    cases = _load_cases(args.cases)
    passed = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for case in cases:
            print("== eval: %s" % case["id"])
            try:
                row = run_case(case, args.max_rounds)
            except Exception as exc:
                row = {"id": case["id"], "pass": False,
                       "failures": ["harness: %s" % exc]}
            passed += bool(row["pass"])
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            print("   -> %s %s" % ("PASS" if row["pass"] else "FAIL",
                                   row.get("failures", "")))
    print("== evals: %d/%d passed -> %s" % (passed, len(cases), out_path))
    blender_ai.unregister()


if __name__ == "__main__":
    main()
