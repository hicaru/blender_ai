"""System prompt tests: sections, one job, Bevy facts, size budget."""
# mypy: ignore-errors

import unittest

import _common

prompts = _common.load_module("prompts")


class TestSystemPrompt(unittest.TestCase):
    def setUp(self):
        self.text = prompts.SYSTEM_PROMPT

    def test_sections(self):
        for section in ("<role>", "<workflow>", "<modeling_rules>", "<bevy>",
                        "<script_rules>", "<example>", "<answer_format>"):
            self.assertIn(section, self.text)

    def test_script_first_workflow(self):
        for tool in ("build_model", "finish", "ask_user", "export_glb"):
            self.assertIn(tool, self.text)
        # the "agent stops" rule: every build turn calls a tool
        self.assertIn("never", self.text.split("<workflow>")[1].split("</workflow>")[0])

    def test_one_connected_object(self):
        self.assertIn("ONE object", self.text)
        self.assertIn("parts catalog", self.text)

    def test_bevy_not_other_engines(self):
        self.assertIn("Bevy", self.text)
        for engine in ("Unity", "Unreal", "Godot"):
            self.assertNotIn(engine, self.text)

    def test_removed_pipeline_is_gone(self):
        for stale in ("set_asset_spec", "validate_asset", "load_skill", "run_python"):
            self.assertNotIn(stale, self.text)

    def test_example_uses_kit(self):
        example = self.text.split("<example>")[1]
        self.assertIn("mk.cut(", example)
        self.assertIn("mk.repeat(", example)

    def test_size_budget(self):
        self.assertLess(len(self.text), 9000, len(self.text))


if __name__ == "__main__":
    unittest.main()
