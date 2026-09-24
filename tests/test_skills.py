"""Skill parsing: valid file, malformed front matter, user-override order."""
# mypy: ignore-errors

import tempfile
import textwrap
import unittest
from pathlib import Path

import _common

skills = _common.load_package("skills")

GOOD = textwrap.dedent("""\
    +++
    name = "test-skill"
    description = "A test recipe."
    triggers = ["gizmo", "widget"]
    category = "props"
    tri_budget = [10, 20]
    +++

    ## Steps
    1. Do the thing.
    """)


def _write(dirpath, filename, content):
    path = Path(dirpath) / filename
    path.write_text(content, encoding="utf-8")
    return path


class TestSkillParsing(unittest.TestCase):
    def test_parse_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "test-skill.md", GOOD)
            skill = skills.parse_skill(path)
            self.assertEqual(skill.name, "test-skill")
            self.assertIn("gizmo", skill.triggers)
            self.assertEqual(skill.tri_budget, (10, 20))
            self.assertTrue(skill.body.startswith("## Steps"))

    def test_missing_front_matter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "bad.md", "no front matter here")
            with self.assertRaises(skills.SkillError):
                skills.parse_skill(path)

    def test_bad_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "bad.md", '+++\nname = [broken\n+++\nbody')
            with self.assertRaises(skills.SkillError):
                skills.parse_skill(path)

    def test_missing_required_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "bad.md", '+++\ndescription = "x"\n+++\nbody')
            with self.assertRaises((skills.SkillError, KeyError)):
                skills.parse_skill(path)


class TestSkillIndex(unittest.TestCase):
    def test_builtin_index_loads(self):
        index = skills.SkillIndex.load(None)
        self.assertGreaterEqual(len(index.skills), 18,
                                "plan requires >= 18 built-in skills")
        self.assertEqual(index.errors, ())
        names = {s.name for s in index.skills}
        for expected in ("game-asset-basics", "lowpoly-prop", "lowpoly-tree",
                         "lowpoly-rock", "modular-kit", "export-godot",
                         "export-unity", "export-unreal", "export-gltf",
                         "lod-collision"):
            self.assertIn(expected, names)

    def test_user_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "lowpoly-tree.md", GOOD.replace(
                'name = "test-skill"', 'name = "lowpoly-tree"'))
            index = skills.SkillIndex.load(Path(tmp))
            tree = index.get("lowpoly-tree")
            self.assertEqual(tree.description, "A test recipe.")
            self.assertEqual(len(index.skills),
                             len(skills.SkillIndex.load(None).skills))

    def test_index_block_lines(self):
        index = skills.SkillIndex.load(None)
        block = index.index_block()
        self.assertTrue(block.startswith("- "))
        self.assertIn("[nature]", block)

    def test_best_match(self):
        index = skills.SkillIndex.load(None)
        self.assertEqual(index.best_match("a low-poly pine tree").name,
                         "lowpoly-tree")
        self.assertEqual(index.best_match("wooden crate").name, "lowpoly-prop")
        self.assertIsNone(index.best_match(""))

    def test_index_block_in_prompt(self):
        prompts = _common.load_module("prompts")
        index = skills.SkillIndex.load(None)
        text = prompts.system_prompt(index.index_block())
        self.assertIn("lowpoly-tree [nature]", text)


if __name__ == "__main__":
    unittest.main()
