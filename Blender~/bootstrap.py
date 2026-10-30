"""Loaded by Unity with:  Blender [file.blend] --python bootstrap.py -- --ub-manifest <session.json>

Registers the add-on straight from the Unity package (nothing to install) and starts the session.
"""

import os
import sys

import bpy


def _arg(name):
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if name in argv:
        i = argv.index(name)
        return argv[i + 1] if i + 1 < len(argv) else None
    return None


HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import unity_bridge  # noqa: E402

if hasattr(bpy.types.Scene, "ub_state") and not unity_bridge._registered:
    # An installed copy of the add-on is already active; use it rather than registering twice.
    print("[UnityBridge] Using the installed add-on; package version is", unity_bridge.VERSION)
else:
    unity_bridge.register()

_manifest = _arg("--ub-manifest")
if _manifest:
    unity_bridge.session.start(_manifest)
