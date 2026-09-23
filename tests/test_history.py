"""History logic tests: message shape, trim with tool-pair safety, JSON."""

import os
import tempfile
import unittest

import _common

history = _common.load_module("history")


def tool_call(cid, name, args):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args}}


class TestMessages(unittest.TestCase):
    def test_message_shape(self):
        msg = history.message("user", content="hi")
        self.assertEqual(msg, {"role": "user", "content": "hi"})
        msg = history.message("assistant", content="",
                              tool_calls=[tool_call("1", "create_primitive", "{}")],
                              approval="pending")
        self.assertEqual(msg["tool_calls"][0]["function"]["name"],
                         "create_primitive")
        self.assertEqual(msg["approval"], "pending")

    def test_append_returns_same_list(self):
        msgs = []
        out = history.append(msgs, history.message("user", content="x"))
        self.assertIs(out, msgs)
        self.assertEqual(len(msgs), 1)


class TestTrim(unittest.TestCase):
    def _conversation(self, n_user=50):
        msgs = []
        for i in range(n_user):
            history.append(msgs, history.message("user", content="q%d" % i))
            history.append(msgs, history.message(
                "assistant", content="", tool_calls=[
                    tool_call(str(i), "create_primitive", "{}")]))
            history.append(msgs, history.message(
                "tool", content="ok", tool_name="create_primitive",
                tool_call_id=str(i)))
            history.append(msgs, history.message("assistant", content="done"))
        return msgs

    def test_trim_over_limit(self):
        msgs = self._conversation(50)  # 200 messages
        trimmed = history.trim(msgs, 80)
        self.assertEqual(len(trimmed), 80)
        self.assertEqual(trimmed[-1]["content"], "done")

    def test_trim_never_leaves_orphan_tool_first(self):
        msgs = self._conversation(11)  # 44 messages; 80-limit keeps all
        trimmed = history.trim(msgs, 10)  # forces a cut mid-conversation
        self.assertLessEqual(len(trimmed), 10)
        self.assertNotEqual(trimmed[0].get("role"), "tool")

    def test_trim_returns_all_within_limit(self):
        msgs = self._conversation(5)
        trimmed = history.trim(msgs, 80)
        self.assertEqual(len(trimmed), len(msgs))


class TestJson(unittest.TestCase):
    def test_roundtrip(self):
        msgs = [
            history.message("user", content="make a cube"),
            history.message("assistant", content="",
                            tool_calls=[tool_call("1", "create_primitive",
                                                  '{"kind": "cube"}')]),
            history.message("tool", content="created object 'Cube'",
                            tool_name="create_primitive", tool_call_id="1"),
            history.message("assistant", content="Done!", approval="ok"),
        ]
        restored = history.from_json(history.to_json(msgs))
        self.assertEqual(restored, msgs)

    def test_unicode_roundtrip(self):
        msgs = [history.message("user", content="создай куб — 形状")]
        restored = history.from_json(history.to_json(msgs))
        self.assertEqual(restored[0]["content"], "создай куб — 形状")

    def test_from_json_rejects_non_list(self):
        with self.assertRaises(ValueError):
            history.from_json('{"role": "user"}')


class TestFile(unittest.TestCase):
    def test_save_load(self):
        path = os.path.join(tempfile.mkdtemp(), "sub", "chat.json")
        msgs = [history.message("user", content="hello")]
        history.save(msgs, __import__("pathlib").Path(path))
        self.assertEqual(history.load(__import__("pathlib").Path(path)), msgs)

    def test_load_missing_returns_empty(self):
        self.assertEqual(
            history.load(__import__("pathlib").Path("/nonexistent/x.json")), [])


if __name__ == "__main__":
    unittest.main()


class TestReconcile(unittest.TestCase):
    """Protocol-validity repair: every tool_call gets a result, no orphans."""

    def test_dangling_calls_get_synthetic_results(self):
        msgs = [
            history.message("user", content="go"),
            history.message("assistant", content="",
                            tool_calls=[tool_call("1", "run_python", "{}")]),
        ]
        out = history.reconcile(msgs)
        self.assertEqual(out[-1]["role"], "tool")
        self.assertEqual(out[-1]["tool_call_id"], "1")
        self.assertIn("interrupted", out[-1]["content"])
        # input must be untouched
        self.assertEqual(len(msgs), 2)
        self.assertEqual(len(out), 3)

    def test_orphan_results_dropped(self):
        msgs = [
            history.message("tool", content="x", tool_call_id="9"),
            history.message("user", content="hi"),
        ]
        self.assertEqual(history.reconcile(msgs),
                         [history.message("user", content="hi")])

    def test_duplicate_results_dropped(self):
        msgs = [
            history.message("assistant", content="",
                            tool_calls=[tool_call("1", "run_python", "{}")]),
            history.message("tool", content="a", tool_call_id="1"),
            history.message("tool", content="dup", tool_call_id="1"),
        ]
        out = history.reconcile(msgs)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[1]["content"], "a")

    def test_valid_history_passes_through(self):
        msgs = [
            history.message("user", content="go"),
            history.message("assistant", content="",
                            tool_calls=[tool_call("1", "create_primitive", "{}")]),
            history.message("tool", content="ok", tool_call_id="1"),
            history.message("assistant", content="done"),
        ]
        self.assertEqual(history.reconcile(msgs), msgs)

    def test_consecutive_assistant_blocks_both_closed(self):
        msgs = [
            history.message("assistant", content="",
                            tool_calls=[tool_call("1", "run_python", "{}")]),
            history.message("assistant", content="",
                            tool_calls=[tool_call("2", "run_python", "{}")]),
        ]
        out = history.reconcile(msgs)
        self.assertEqual([m["role"] for m in out],
                         ["assistant", "tool", "assistant", "tool"])
        self.assertEqual([m["tool_call_id"] for m in out if m["role"] == "tool"],
                         ["1", "2"])
