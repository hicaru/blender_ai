"""System prompt tests: XML sections present, size budget, reasoning rules."""
# mypy: ignore-errors

import unittest

import _common

prompts = _common.load_module("prompts")


class TestSystemPrompt(unittest.TestCase):
    def setUp(self):
        self.text = prompts.SYSTEM_PROMPT

    def test_template_sections(self):
        # XML-tagged layout: tagged sections are referenced by name
        for section in ("<role>", "<pipeline>", "<object_rules>",
                        "<game_dev_facts>", "<tool_policy>", "<skills>",
                        "<examples>", "<answer_format>"):
            self.assertIn(section, self.text)

    def test_reasoning_and_tooling_rules(self):
        self.assertIn("get_scene_state", self.text)
        self.assertIn("run_python", self.text)
        self.assertIn("ask_user", self.text)
        # reasoning: thought before action, tool result is the observation
        self.assertIn("observation", self.text.lower())

    def test_subject_facts(self):
        # primitives from the manual + quad_sphere bmesh extra
        for kind in ("plane", "cube", "uv_sphere", "quad_sphere", "monkey"):
            self.assertIn(kind, self.text)
        for mod in ("bevel", "mirror", "solidify"):
            self.assertIn(mod, self.text)

    def test_size_budget(self):
        self.assertLess(len(self.text), 8000,
                        "SYSTEM_PROMPT must stay under 8000 chars, got %d"
                        % len(self.text))

    def test_one_few_shot_example(self):
        self.assertEqual(self.text.count("<examples>"), 1)
        self.assertIn("create_primitive",
                      self.text.split("<examples>")[1])

    def test_done_gate(self):
        # the plan's central recall rule: done only after validate + capture
        self.assertIn("validate_asset", self.text)
        self.assertIn("capture_view", self.text)

    def test_dynamic_block_empty_when_no_parts(self):
        self.assertEqual(prompts.build_dynamic_block(), "")

    def test_dynamic_block_joins_parts(self):
        out = prompts.build_dynamic_block("<scene>…</scene>",
                                          "<asset_state>…</asset_state>")
        self.assertIn("<scene>", out)
        self.assertIn("<asset_state>", out)
        self.assertEqual(out.index("<scene>"), 0)


if __name__ == "__main__":
    unittest.main()
