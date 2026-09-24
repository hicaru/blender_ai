"""Export plans per engine + checklist rendering (pure parts)."""
# mypy: ignore-errors

import unittest

import _common

export_mod = _common.load_package("pipeline.export")
spec_mod = _common.load_package("pipeline.spec")


class TestExportPlans(unittest.TestCase):
    def test_godot_is_glb(self):
        plan = export_mod.plan_for(spec_mod.Engine.GODOT)
        self.assertEqual(plan.operator, "gltf")
        self.assertEqual(plan.ext, ".glb")
        self.assertTrue(plan.kwargs["export_apply"])
        self.assertTrue(plan.kwargs["export_extras"])

    def test_gltf_default_is_glb(self):
        plan = export_mod.plan_for(spec_mod.Engine.GLTF)
        self.assertEqual((plan.operator, plan.ext), ("gltf", ".glb"))

    def test_unity_is_fbx_units(self):
        plan = export_mod.plan_for(spec_mod.Engine.UNITY)
        self.assertEqual((plan.operator, plan.ext), ("fbx", ".fbx"))
        self.assertEqual(plan.kwargs["apply_scale_options"], "FBX_SCALE_UNITS")
        self.assertEqual(plan.kwargs["axis_forward"], "-Z")
        self.assertFalse(plan.kwargs["add_leaf_bones"])

    def test_unreal_is_fbx_none(self):
        plan = export_mod.plan_for(spec_mod.Engine.UNREAL)
        self.assertEqual(plan.kwargs["apply_scale_options"], "FBX_SCALE_NONE")
        self.assertEqual(plan.kwargs["axis_up"], "Z")
        self.assertTrue(plan.kwargs["use_tspace"])

    def test_plans_frozen(self):
        plan = export_mod.plan_for(spec_mod.Engine.GODOT)
        with self.assertRaises(TypeError):  # MappingProxy rejects item assignment
            plan.kwargs["hack"] = True


if __name__ == "__main__":
    unittest.main()
