"""Unity Bridge: edit Unity models and clips in Blender and send them back with one button."""

bl_info = {
    "name": "Unity Bridge",
    "author": "farik",
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Unity",
    "description": "Round-trip models and animation clips between Unity and Blender",
    "category": "Import-Export",
}

VERSION = "0.1.0"

if "props" in locals():
    import importlib

    for _m in (util, paths, fbx_profile, actions, fingerprint, baking, props, importer,  # noqa: F821
               validate, history, exporter, clips, session, ui):  # noqa: F821
        importlib.reload(_m)

from . import (actions, baking, clips, exporter, fbx_profile, fingerprint, history, importer,  # noqa: E402
               paths, props, session, ui, util, validate)

_registered = False


def register():
    global _registered
    if _registered:
        return
    props.register()
    ui.register()
    session.register()
    _registered = True


def unregister():
    global _registered
    if not _registered:
        return
    session.unregister()
    ui.unregister()
    props.unregister()
    _registered = False
