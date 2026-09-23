"""Blender AI — chat agent extension that builds 3D models inside Blender."""

bl_info = {
    "name": "Blender AI",
    "author": "hicaru",
    "version": (0, 1, 5),
    "blender": (5, 2, 0),
    "location": "3D Viewport > Sidebar > AI",
    "description": "AI chat agent that builds 3D models through Blender tools",
    "doc_url": "",
    "category": "3D View",
}

import bpy

from . import agent
from . import prefs
from . import ui


def register():
    prefs.register()
    ui.register()
    agent.register()


def unregister():
    agent.unregister()
    ui.unregister()
    prefs.unregister()
