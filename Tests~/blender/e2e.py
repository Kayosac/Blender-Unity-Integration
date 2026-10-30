"""End-to-end step run by Unity's self-test in background Blender, after bootstrap.py.

  Blender -b [file.blend] --python bootstrap.py --python e2e.py -- --ub-manifest <session.json> --ub-e2e <scenario>

Runs the session synchronously (timers do not fire in background mode), applies one scenario,
exports and quits. Unity then imports the result and checks it.
"""

import sys
import traceback

import bpy
from mathutils import Quaternion

import unity_bridge
from unity_bridge import clips, exporter, importer, session, util


def _arg(name):
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    return argv[argv.index(name) + 1] if name in argv else None


def main():
    if not unity_bridge._registered:
        unity_bridge.register()
    m = util.read_json(_arg("--ub-manifest"))
    scenario = _arg("--ub-e2e") or "noop"
    if m.get("fresh", True) or not bpy.data.filepath:
        session.fresh_session(m)
    else:
        session.refresh_session(m)
    scene = bpy.context.scene
    mode = "SMART"
    if scenario == "edit_vertex":
        col = importer.model_collection(scene)
        mesh = next(o for o in col.all_objects if o.type == "MESH" and len(o.data.vertices))
        mesh.data.vertices[0].co.z += 0.01
    elif scenario == "new_clip":
        clip = clips.new_clip(scene, "E2EWave", 20)
        arm = importer.main_armature(importer.model_collection(scene))
        pb = arm.pose.bones[len(arm.pose.bones) // 2]
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion((0, 1, 0), 0.4)
        pb.keyframe_insert("rotation_quaternion", frame=10)
        util.log("E2E new clip", clip.name, "bone", pb.name)
    else:  # noop, anim: write everything that is loaded, unchanged
        mode = "ALL"
    res = exporter.run(scene, mode=mode, allow_errors=True)
    util.log("E2E", scenario, "->", res["status"], [i["assetPath"] for i in res["items"]])
    session.release_lock()


try:
    main()
except Exception:  # noqa: BLE001
    traceback.print_exc()
    sys.exit(1)
