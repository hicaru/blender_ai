"""Build report: connectivity groups, linked-copy folding, error text."""
# mypy: ignore-errors

import unittest

import _common

report = _common.load_module("report")
Part = report.Part


def box(name, lo, hi, tris=12, mesh=""):
    return Part(name=name, tris=tris, lo=lo, hi=hi, mesh=mesh)


class TestConnectivity(unittest.TestCase):
    def test_touching_parts_form_one_group(self):
        parts = [box("Base", (0, 0, 0), (2, 2, 1)),
                 box("Top", (0, 0, 1), (2, 2, 2))]
        self.assertEqual(len(report.connected_groups(parts)), 1)

    def test_scattered_parts_are_reported(self):
        # the observed failure: parts laid out in a row, not assembled
        parts = [box(f"P{i}", (i * 5, 0, 0), (i * 5 + 1, 1, 1)) for i in range(4)]
        groups = report.connected_groups(parts)
        self.assertEqual(len(groups), 4)
        text = report.render_report("Kit", parts)
        self.assertIn("4 disconnected groups", text)

    def test_tolerance_bridges_tiny_gaps(self):
        parts = [box("A", (0, 0, 0), (1, 1, 1)), box("B", (1.03, 0, 0), (2, 1, 1))]
        self.assertEqual(len(report.connected_groups(parts)), 1)

    def test_chain_is_transitive(self):
        parts = [box("A", (0, 0, 0), (1, 1, 1)), box("B", (1, 0, 0), (2, 1, 1)),
                 box("C", (2, 0, 0), (3, 1, 1))]
        self.assertEqual(report.connected_groups(parts), [["A", "B", "C"]])


class TestRender(unittest.TestCase):
    def test_ok_header_has_size_and_tris(self):
        text = report.render_report("Crate", [box("Body", (0, 0, 0), (1, 1, 1), tris=12)])
        self.assertIn("BUILD OK model='Crate' parts=1 tris=12 size=1.00x1.00x1.00", text)
        self.assertIn("call finish", text)

    def test_linked_copies_fold_into_one_line(self):
        parts = [box(f"Step_{i}", (0, i, 0), (1, i + 1, 1), mesh="Step") for i in range(50)]
        text = report.render_report("Stairs", parts)
        self.assertIn("x50 (linked copies)", text)
        self.assertEqual(text.count("Step_"), 1)

    def test_error_keeps_partial_parts(self):
        text = report.render_report("Tower", [box("Leg", (0, 0, 0), (1, 1, 1))],
                                    error="line 3: mk.bx(...)\nAttributeError: bx")
        self.assertIn("BUILD ERROR", text)
        self.assertIn("line 3", text)
        self.assertIn("PARTIAL", text)

    def test_empty_build(self):
        self.assertIn("BUILD EMPTY", report.render_report("Nothing", []))

    def test_faceless_part_is_an_issue(self):
        text = report.render_report("M", [box("A", (0, 0, 0), (1, 1, 1))], empties=["Wall"])
        self.assertIn("'Wall' has no faces", text)

    def test_part_list_is_bounded(self):
        parts = [box(f"P{i}", (i, 0, 0), (i + 1, 1, 1)) for i in range(200)]
        text = report.render_report("Big", parts)
        self.assertIn("more parts", text)
        self.assertLess(text.count("\n"), 80)


if __name__ == "__main__":
    unittest.main()
