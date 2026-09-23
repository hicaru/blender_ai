"""System prompt tests: template sections present, size budget, reasoning rules."""

import unittest

import _common

prompts = _common.load_module("prompts")


class TestSystemPrompt(unittest.TestCase):
    def setUp(self):
        self.text = prompts.SYSTEM_PROMPT

    def test_template_sections(self):
        # task-context layout: role/context, rules, facts, example, input
        for section in ("# Role", "# Rules", "# Blender facts", "# Example",
                        "# Input"):
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
        self.assertLess(len(self.text), 6000,
                        "SYSTEM_PROMPT must stay under 6000 chars, got %d"
                        % len(self.text))

    def test_one_few_shot_example(self):
        self.assertEqual(self.text.count("# Example"), 1)
        self.assertIn("create_primitive", self.text.split("# Example")[1])


if __name__ == "__main__":
    unittest.main()
