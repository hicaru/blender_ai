"""AssetSpec round-trip, budget derivation, palette determinism, meta helpers."""
# mypy: ignore-errors

import unittest

import _common

spec_mod = _common.load_package("pipeline.spec")
palette = _common.load_package("pipeline.palette")


class TestAssetSpec(unittest.TestCase):
    def test_round_trip(self):
        s = spec_mod.AssetSpec(
            name="Barrel", description="A barrel", asset_class=spec_mod.AssetClass.PROP,
            engine=spec_mod.Engine.GODOT, style=spec_mod.Style.LOWPOLY,
            size_m=(0.6, 0.6, 0.9), skills=("lowpoly-prop",))
        s2 = spec_mod.AssetSpec.from_json(s.to_json())
        self.assertEqual(s2, s)

    def test_budget_lowpoly_quarter(self):
        s = spec_mod.AssetSpec(name="A", description="d",
                               asset_class=spec_mod.AssetClass.PROP,
                               style=spec_mod.Style.LOWPOLY)
        self.assertEqual(s.budget, (125, 1250))

    def test_budget_realistic_full(self):
        s = spec_mod.AssetSpec(name="A", description="d",
                               asset_class=spec_mod.AssetClass.PROP,
                               style=spec_mod.Style.REALISTIC)
        self.assertEqual(s.budget, (500, 5000))

    def test_budget_explicit_override(self):
        s = spec_mod.AssetSpec(name="A", description="d",
                               asset_class=spec_mod.AssetClass.PROP,
                               tri_budget=(10, 20))
        self.assertEqual(s.budget, (10, 20))

    def test_collection_name(self):
        s = spec_mod.AssetSpec(name="Pine Tree", description="d",
                               asset_class=spec_mod.AssetClass.PROP)
        self.assertEqual(s.collection_name(), "SM_Pine Tree")

    def test_from_json_bad_class_raises(self):
        raw = '{"name":"A","description":"d","asset_class":"alien"}'
        with self.assertRaises(ValueError):
            spec_mod.AssetSpec.from_json(raw)


class TestPalette(unittest.TestCase):
    def test_named_keys_exist(self):
        for key in ("bark", "leaf", "iron", "wood_light", "stone", "skin"):
            self.assertIn(key, palette.named())

    def test_auto_color_deterministic(self):
        a = palette.auto_color("SM_Barrel_Body")
        b = palette.auto_color("SM_Barrel_Body")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 4)
        self.assertTrue(all(0.0 <= c <= 1.0 for c in a))

    def test_resolve_named_key(self):
        rgba = palette.resolve_color("iron", fallback_name="x")
        # iron is desaturated dark gray: r ~ g ~ b
        r, g, b = rgba[:3]
        self.assertAlmostEqual(r, g, delta=0.08)
        self.assertAlmostEqual(g, b, delta=0.08)

    def test_resolve_rgba_list(self):
        self.assertEqual(palette.resolve_color([1.0, 0.0, 0.0]), (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(palette.resolve_color([0.5, 0.5, 0.5, 0.5]),
                         (0.5, 0.5, 0.5, 0.5))

    def test_unknown_key_never_blocks(self):
        # garbage falls through to deterministic auto color
        rgba = palette.resolve_color("definitely-not-a-key", fallback_name="Part")
        self.assertEqual(rgba, palette.auto_color("Part"))

    def test_named_colors_are_linear(self):
        rgba = palette.resolve_color("wood_light")
        self.assertTrue(all(c <= 1.0 for c in rgba))


if __name__ == "__main__":
    unittest.main()
