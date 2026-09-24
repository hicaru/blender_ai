"""pipeline: the asset-factory logic, independent of the tool schema.

Tools (tools/pipeline.py), UI quick actions (ui/operators.py) and evals
(tests/run_evals.py) all reuse these modules — one path per concern.

This __init__ deliberately imports NOTHING: the Blender-only modules
(bmesh/bpy) must load inside Blender, while pure modules (spec, palette,
export) must stay testable without it. Import submodules directly.
"""

__all__ = (
           "SPEC_KEY",
           "AssetSpec",
           "capture",
           "checks",
           "collision",
           "color",
           "export",
           "facts",
           "lod",
           "meta",
           "palette",
           "session",
           "spec",
)
