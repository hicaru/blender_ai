"""Blender AI — game-asset generation harness inside Blender."""
# mypy: ignore-errors

bl_info = {
    "name": "Blender AI",
    "author": "hicaru",
    "version": (0, 2, 0),
    "blender": (5, 2, 0),
    "location": "3D Viewport > Sidebar > AI",
    "description": "AI agent that builds game-ready 3D assets through Blender tools",
    "doc_url": "",
    "category": "3D View",
}


def register() -> None:
    from . import agent, prefs, ui

    prefs.register()
    ui.register()
    agent.register()


def unregister() -> None:
    from . import agent, prefs, ui

    agent.unregister()
    ui.unregister()
    prefs.unregister()
