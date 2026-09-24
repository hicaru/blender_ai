"""Repair loop + skill state tests: LOOP file contract, gates, merge, inject."""

import os
import tempfile
import unittest

import _common

loop_state = _common.load_module("loop_state")


class TestErrorClassification(unittest.TestCase):
    def test_transport_error_is_environment(self):
        self.assertEqual(
            loop_state.classify_error("Connection timeout talking to provider"),
            "environment",
        )

    def test_task_error_is_failure(self):
        self.assertEqual(loop_state.classify_error("TypeError: bad operand"), "failure")

    def test_signature_is_first_line(self):
        self.assertEqual(
            loop_state.error_signature("A: one\nB: two"), "A: one",
        )


class TestRepairLoopGates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def test_first_failure_activates_and_continues(self):
        decision = loop_state.on_round_failure("TypeError: x", 3, self.dir)
        self.assertEqual(decision.action, "continue")
        self.assertEqual((decision.iteration, decision.bound), (1, 3))

    def test_identical_signature_stops_as_no_progress(self):
        loop_state.on_round_failure("TypeError: x", 3, self.dir)
        decision = loop_state.on_round_failure("TypeError: x", 3, self.dir)
        self.assertEqual(decision.action, "stop")
        self.assertIn("no progress", decision.reason)

    def test_bound_stops_automatic_retries(self):
        for text in ("Err one", "Err two", "Err three", "Err four"):
            decision = loop_state.on_round_failure(text, 3, self.dir)
        self.assertEqual(decision.action, "stop")
        self.assertIn("bound", decision.reason)

    def test_success_completes_and_records_task_pattern(self):
        loop_state.on_round_failure("ValueError: bad", 5, self.dir)
        loop_state.on_round_failure("ValueError: worse", 5, self.dir)
        decision = loop_state.on_round_success("used correct scale", self.dir)
        self.assertEqual(decision.action, "stop")
        store = loop_state.SkillStore(loop_state.resolve_store_dir(self.dir))
        patterns = [n for n in store.load().notes if n.kind == "task_pattern"]
        self.assertEqual(len(patterns), 1)
        self.assertIn("ValueError: bad", patterns[0].content)
        self.assertIn("used correct scale", patterns[0].content)

    def test_interrupt_pauses(self):
        loop_state.on_round_failure("Err one", 3, self.dir)
        loop_state.on_user_interrupt(self.dir)
        loop = loop_state.get_loop(loop_state.resolve_store_dir(self.dir))
        self.assertFalse(loop.active)
        self.assertEqual(loop.status_line(), "")

    def test_success_without_loop_is_idle(self):
        decision = loop_state.on_round_success("done", self.dir)
        self.assertEqual(decision.action, "idle")


class TestLoopFileContract(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        loop_state.on_round_failure("ValueError: a", 3, self.dir)
        loop_state.on_round_failure("ValueError: b", 3, self.dir)
        loop_state.on_round_success("fixed it", self.dir)

    def _loop_dir(self):
        return os.path.join(self.dir, "loops", "repair")

    def test_three_files_exist(self):
        names = sorted(os.listdir(self._loop_dir()))
        self.assertEqual(names, ["LOOP.md", "record.md", "state.md"])

    def test_state_fields_match_spec(self):
        text = open(os.path.join(self._loop_dir(), "state.md"), encoding="utf-8").read()
        for field in loop_state.STATE_FIELDS:
            self.assertIn(f"{field}:", text)
        self.assertIn("Status: completed", text)

    def test_record_is_append_only_with_entries(self):
        text = open(os.path.join(self._loop_dir(), "record.md"), encoding="utf-8").read()
        self.assertIn("## Activation", text)
        self.assertGreaterEqual(text.count("## Iteration"), 2)
        self.assertIn("Outcome: resolved", text)


class TestLoopFileReaders(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        loop_state.on_round_failure("ValueError: a", 3, self.dir)
        loop_state.on_round_failure("ValueError: b", 3, self.dir)
        loop_state.on_round_success("fixed it", self.dir)

    def test_read_state_fields_parses_written_state(self):
        fields = loop_state.read_state_fields(self.dir)
        self.assertEqual(fields.get("Status"), "completed")
        # two failed rounds ran before the round that succeeded
        self.assertEqual(fields.get("Iteration count"), "2")
        self.assertEqual(len(fields), len(loop_state.STATE_FIELDS))

    def test_read_state_fields_empty_when_no_loop(self):
        self.assertEqual(loop_state.read_state_fields(self.dir + "/nowhere"), {})

    def test_read_record_entries_returns_latest_first_entries(self):
        entries = loop_state.read_record_entries(self.dir, last_n=2)
        self.assertEqual(len(entries), 2)
        for header, _ in entries:
            self.assertTrue(header.startswith("Iteration"))
        # the final entry closes the loop with a resolved outcome
        self.assertTrue(
            any("Outcome: resolved" in line for _, lines in entries for line in lines),
        )
        # the full trail still starts with the Activation entry (append-only)
        full = loop_state.read_record_entries(self.dir, last_n=100)
        self.assertEqual(full[0][0], "Activation")

    def test_read_record_entries_empty_when_no_loop(self):
        self.assertEqual(loop_state.read_record_entries(self.dir + "/nowhere"), [])


class TestSkillStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = loop_state.SkillStore(self._tmp.name)

    def test_empty_store_loads_default(self):
        state = self.store.load()
        self.assertEqual(state.notes, ())

    def test_add_and_merge_normalizes_content_key(self):
        self.store.add_note("Use clear names", "task_pattern")
        merged = self.store.add_note("use  Clear   names ", "task_pattern")
        notes = self.store.load().notes
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].count, 2)
        self.assertEqual(notes[0].content, "use Clear names")
        self.assertGreater(notes[0].confidence, merged.confidence - 0.5)

    def test_notes_capped_at_max(self):
        for i in range(loop_state.MAX_NOTES + 5):
            self.store.add_note(f"note number {i}", "failure")
        self.assertEqual(len(self.store.load().notes), loop_state.MAX_NOTES)

    def test_empty_content_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add_note("   ", "failure")

    def test_corrupt_store_quarantined_not_raised(self):
        # A broken skill_state.json must not raise JSONDecodeError out of
        # Send; load() quarantines the file and returns a default state.
        self.store.add_note("keep", "task_pattern")  # create the file
        self._tmp.name and (self.store._path.write_text("{broken json!!", encoding="utf-8"))
        state = self.store.load()
        self.assertEqual(state.notes, ())
        self.assertFalse(self.store._path.exists())  # moved aside
        quarantined = list(self.store._path.parent.glob("skill_state.corrupt-*.json"))
        self.assertEqual(len(quarantined), 1)
        # Store still usable afterwards (save starts fresh).
        self.store.add_note("fresh", "task_pattern")
        self.assertEqual(len(self.store.load().notes), 1)

    def test_on_round_failure_no_permanent_note(self):
        # Provider/transport failures must not become durable notes: the
        # loop retries, but the note bank stays untouched.
        decision = loop_state.on_round_failure(
            "Z.ai returned HTTP 401 unauthorized", bound=2, store_dir=self._tmp.name,
        )
        self.assertEqual(decision.action, "continue")
        self.assertEqual(self.store.load().notes, ())


class TestInjection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.store = loop_state.SkillStore(self.dir)
        self.store.add_note("always check unit scale", "task_pattern")
        self.messages = [
            {"role": "system", "content": "tool guidance"},
            {"role": "user", "content": "hi"},
        ]

    def test_injects_after_leading_system_messages(self):
        loop_state.inject_into_request(self.messages, self.dir)
        self.assertEqual(len(self.messages), 3)
        self.assertEqual(self.messages[1]["role"], "system")
        self.assertIn("always check unit scale", self.messages[1]["content"])

    def test_repeated_injection_is_idempotent(self):
        loop_state.inject_into_request(self.messages, self.dir)
        first = list(self.messages)
        loop_state.inject_into_request(self.messages, self.dir)
        self.assertEqual(len(self.messages), len(first))
        self.assertEqual(
            [m["content"] for m in self.messages],
            [m["content"] for m in first],
        )

    def test_removed_when_store_empty(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        loop_state.inject_into_request(self.messages, empty.name)
        loop_state.inject_into_request(self.messages, self.dir)
        loop_state.inject_into_request(self.messages, empty.name)
        self.assertEqual(len(self.messages), 2)
        self.assertEqual(
            [m["role"] for m in self.messages], ["system", "user"],
        )

    def test_active_loop_adds_repair_context(self):
        loop_state.on_round_failure("TypeError: operand", 3, self.dir)
        loop_state.inject_into_request(self.messages, self.dir)
        injected = self.messages[1]["content"]
        self.assertIn("# Repair loop", injected)
        self.assertIn("TypeError: operand", injected)
        self.assertIn("1/3", injected)
        self.assertIn("change the strategy", injected)
        # after the loop is interrupted, the repair context is gone
        loop_state.on_user_interrupt(self.dir)
        loop_state.inject_into_request(self.messages, self.dir)
        self.assertNotIn("# Repair loop", self.messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
