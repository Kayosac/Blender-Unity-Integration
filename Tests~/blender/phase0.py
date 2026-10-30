"""Phase 0: proves the FBX -> Blender -> FBX cycle does not distort data.

Run (macOS example):
  ~/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
      --python "<package>/Tests~/blender/phase0.py" -- --out /tmp/ub_phase0 [--fbx-dir <folder>] [--cycles 5]

It builds a synthetic corpus, adds every FBX found in --fbx-dir (your own models), runs each through
the add-on's real import and export N times inside a scratch Unity-like project, and compares the
files the way Unity reads them. It also checks the smart-export and validation behaviour.
Writes report.json and report.txt into --out.
"""

import json
import os
import shutil
import sys
import traceback
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(PKG, "Blender~"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

import unity_bridge  # noqa: E402

unity_bridge.register()

from unity_bridge import actions, baking, clips, exporter, fbx_profile, importer, session, util, validate  # noqa: E402

import corpus  # noqa: E402
import fbxsem  # noqa: E402


def _arg(name, default=None):
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if name in argv:
        i = argv.index(name)
        return argv[i + 1]
    return default


OUT = os.path.abspath(_arg("--out", "/tmp/ub_phase0"))
FBX_DIR = _arg("--fbx-dir")
CYCLES = int(_arg("--cycles", "5"))
PROJECT = os.path.join(OUT, "project")
REPORT = {"blender": bpy.app.version_string, "files": {}, "checks": {}}


def _fake_meta(abs_path, guid=None):
    guid = guid or uuid.uuid4().hex
    with open(abs_path + ".meta", "w") as f:
        f.write("fileFormatVersion: 2\nguid: %s\nModelImporter:\n  serializedVersion: 1\n" % guid)
    return guid


def _manifest(model_rel, model_guid, files=None, focus=None):
    abs_path = os.path.join(PROJECT, model_rel)
    name = os.path.splitext(os.path.basename(model_rel))[0]
    prof = fbx_profile.read(abs_path)
    model_file = {"guid": model_guid, "assetPath": model_rel, "kind": "MODEL", "hasMesh": True,
                  "sha256": util.sha256_file(abs_path),
                  "takes": [{"name": t["name"], "startFrame": t["start_frame"], "unityClips": [
                      {"name": t["name"], "firstFrame": t["start_frame"], "lastFrame": t["end_frame"],
                       "loop": False, "rootMotion": "CUSTOM"}]} for t in prof["takes"]]}
    return {
        "version": 1, "sessionId": uuid.uuid4().hex, "projectRoot": PROJECT, "artSource": "ArtSource",
        "blendPath": "ArtSource/Cache/Test/%s.blend" % name, "fresh": True, "pinned": False,
        "model": {"guid": model_guid, "assetPath": model_rel, "name": name, "sha256": model_file["sha256"]},
        "files": [model_file] + (files or []), "focus": focus or {}, "materials": [],
        "settings": {"historySize": 3, "clipsFolder": "Animations"},
    }


def _place(src, rel):
    dst = os.path.join(PROJECT, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return dst, _fake_meta(dst)


def _summ(rep):
    ok, bad = fbxsem.verdict(rep)
    return {"ok": ok, "over_tolerance": bad, "structure": rep["structure"][:20],
            "max": {k: float("%.3g" % v) for k, v in rep["max"].items()}}


# --------------------------------------------------------------------------- cycles


def cycle_file(name, src, extra_files=None, focus=None):
    rel = "Assets/Test/%s" % os.path.basename(src)
    dst, guid = _place(src, rel)
    work = os.path.join(OUT, "cycles", os.path.splitext(name)[0])
    os.makedirs(work, exist_ok=True)
    original = fbxsem.read(dst)
    prev = original
    res = {"cycles": [], "fingerprint_stable": [], "noop_after_export": []}
    for k in range(1, CYCLES + 1):
        files = extra_files() if extra_files else None
        session.fresh_session(_manifest(rel, guid, files, focus))
        scene = bpy.context.scene
        res["fingerprint_stable"].append(not exporter.plan(scene)["files"])
        out = exporter.run(scene, mode="ALL", allow_errors=True)
        res["noop_after_export"].append(not exporter.plan(scene)["files"])
        snap = os.path.join(work, "cycle_%d.fbx" % k)
        shutil.copy2(dst, snap)
        cur = fbxsem.read(dst)
        res["cycles"].append({
            "vs_original": _summ(fbxsem.diff(original, cur)),
            "vs_previous": _summ(fbxsem.diff(prev, cur)),
            "written": [os.path.basename(i["assetPath"]) for i in out["items"]],
        })
        prev = cur
    return res


# --------------------------------------------------------------------------- behaviour checks


def check_vertex_edit(src):
    rel = "Assets/Edit/%s" % os.path.basename(src)
    dst, guid = _place(src, rel)
    before = fbxsem.read(dst)
    session.fresh_session(_manifest(rel, guid))
    scene = bpy.context.scene
    col = importer.model_collection(scene)
    mesh = next(o for o in col.all_objects if o.type == "MESH")
    mesh.data.vertices[0].co.z += 0.01
    work = exporter.plan(scene)
    out = exporter.run(scene)
    after = fbxsem.read(dst)
    rep = fbxsem.diff(before, after)
    return {"model_changed_detected": work["model_changed"], "skeleton_untouched": not work["skeleton_changed"],
            "written": [os.path.basename(i["assetPath"]) for i in out["items"]],
            "structure_same": not rep["structure"], "max": {k: float("%.3g" % v) for k, v in rep["max"].items()}}


def check_new_clip(src):
    rel = "Assets/NewClip/%s" % os.path.basename(src)
    dst, guid = _place(src, rel)
    session.fresh_session(_manifest(rel, guid))
    scene = bpy.context.scene
    model_sha = util.sha256_file(dst)
    clip = clips.new_clip(scene, "Wave", 20)
    arm = importer.main_armature(importer.model_collection(scene))
    pb = arm.pose.bones["Head"]
    from mathutils import Quaternion

    pb.rotation_quaternion = Quaternion((0, 1, 0), 0.5)
    pb.keyframe_insert("rotation_quaternion", frame=10)
    out = exporter.run(scene)
    clip_rel = "Assets/NewClip/Animations/%s@Wave.fbx" % os.path.splitext(os.path.basename(src))[0]
    clip_abs = os.path.join(PROJECT, clip_rel)
    info = fbxsem.read(clip_abs) if os.path.exists(clip_abs) else None
    return {
        "written": [i["assetPath"] for i in out["items"]],
        "model_untouched": util.sha256_file(dst) == model_sha,
        "clip_exists": info is not None,
        "clip_has_no_mesh": bool(info) and not info["geometry"],
        "clip_takes": sorted(info["takes"]) if info else [],
        "managed_clip_settings": out["items"][0].get("clips") if out["items"] else None,
    }


def check_bone_rename(src):
    rel = "Assets/Rename/%s" % os.path.basename(src)
    _dst, guid = _place(src, rel)
    session.fresh_session(_manifest(rel, guid))
    scene = bpy.context.scene
    arm = importer.main_armature(importer.model_collection(scene))
    arm.data.bones["Spine"].name = "Spine_Renamed"
    out = exporter.run(scene)
    return {"blocked": out["status"] == "blocked",
            "codes": sorted({i["code"] for i in out["issues"] if i["level"] == "ERROR"})}


def check_clip_roundtrip(rig_src, clip_src):
    """Loads a clip FBX onto the rig and writes it back; compares the clip file."""
    rig_rel = "Assets/ClipRT/%s" % os.path.basename(rig_src)
    _d, rig_guid = _place(rig_src, rig_rel)
    clip_rel = "Assets/ClipRT/%s" % os.path.basename(clip_src)
    clip_abs, clip_guid = _place(clip_src, clip_rel)
    before = fbxsem.read(clip_abs)
    prof = fbx_profile.read(clip_abs)
    files = [{"guid": clip_guid, "assetPath": clip_rel, "kind": "CLIP", "rigGuid": rig_guid,
              "sha256": util.sha256_file(clip_abs),
              "takes": [{"name": t["name"], "startFrame": t["start_frame"], "unityClips": [
                  {"name": t["name"], "firstFrame": t["start_frame"], "lastFrame": t["end_frame"],
                   "loop": True, "rootMotion": "IN_PLACE"}]} for t in prof["takes"]]}]
    session.fresh_session(_manifest(rig_rel, rig_guid, files, {"fileGuid": clip_guid}))
    scene = bpy.context.scene
    stable = not exporter.plan(scene)["files"]
    out = exporter.run(scene, mode="ACTIVE_CLIP")
    after = fbxsem.read(clip_abs)
    return {"fingerprint_stable": stable, "written": [os.path.basename(i["assetPath"]) for i in out["items"]],
            "diff": _summ(fbxsem.diff(before, after))}


def check_unity_samples(src):
    """Feeds the .anim path synthetic 'Unity' matrices (mirrored, Y-up, cm, bone-axis corrected)."""
    from mathutils import Euler, Matrix

    rel = "Assets/Samples/%s" % os.path.basename(src)
    _d, guid = _place(src, rel)
    session.fresh_session(_manifest(rel, guid))
    scene = bpy.context.scene
    st = scene.ub_state
    arm = importer.main_armature(importer.model_collection(scene))
    run = next(c for c in st.clips if c.name == "Run")
    actions.assign(arm, run.action)
    mirror = Matrix.Scale(-1, 4, (1, 0, 0))
    G = mirror @ Matrix.Rotation(-1.5707963, 4, "X") @ Matrix.Scale(100.0, 4)
    C = Euler((0.0, 0.0, 1.5707963)).to_matrix().to_4x4()
    bones = [b.name for b in arm.data.bones]
    rest = []
    for n in bones:
        m = G @ arm.matrix_world @ arm.data.bones[n].matrix_local @ C
        rest += [v for row in m for v in row]
    lo, hi = actions.frame_range(run.action)
    frames = list(range(int(lo), int(hi) + 1))
    data, truth = [], []
    for f in frames:
        scene.frame_set(f)
        poses = {}
        for n in bones:
            pm = arm.pose.bones[n].matrix.copy()
            poses[n] = pm
            m = G @ arm.matrix_world @ pm @ C
            data += [v for row in m for v in row]
        truth.append(poses)
    sample = {"bones": ["Armature/" + n for n in bones], "rest": rest, "frames": len(frames), "data": data}
    poses, resid = baking.unity_samples_to_pose(arm, sample)
    err = 0.0
    for fi, p in enumerate(poses):
        for n, m in p.items():
            d = m - truth[fi][n]
            err = max(err, max(abs(d[i][j]) for i in range(4) for j in range(4)))
    return {"fit_residual": resid, "max_matrix_error": err, "ok": err < 1e-4}


# --------------------------------------------------------------------------- main


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(PROJECT)
    corpus_dir = os.path.join(OUT, "corpus")
    synth = corpus.build_all(corpus_dir)
    sources = {k: v for k, v in synth.items() if not k.startswith("clip_")}
    if FBX_DIR and os.path.isdir(FBX_DIR):
        for root, _dirs, files in os.walk(FBX_DIR):
            for fn in files:
                if fn.lower().endswith(".fbx"):
                    p = os.path.join(root, fn)
                    if fbx_profile.read(p).get("has_mesh"):
                        sources["user/" + fn] = p
                    else:
                        REPORT["files"]["user/" + fn] = {"skipped": "no mesh: clip files need a rig"}
    for name, path in sources.items():
        try:
            REPORT["files"][name] = cycle_file(name, path)
        except Exception as e:  # noqa: BLE001
            REPORT["files"][name] = {"error": repr(e), "trace": traceback.format_exc()}
    checks = {
        "vertex_edit": lambda: check_vertex_edit(synth["static_m.fbx"]),
        "new_clip": lambda: check_new_clip(synth["skinned_m.fbx"]),
        "bone_rename_blocks": lambda: check_bone_rename(synth["skinned_m.fbx"]),
        "clip_roundtrip_units": lambda: check_clip_roundtrip(synth["skinned_cm_zup.fbx"], synth["clip_wave_m.fbx"]),
        "unity_samples_math": lambda: check_unity_samples(synth["skinned_m.fbx"]),
    }
    for name, fn in checks.items():
        try:
            REPORT["checks"][name] = fn()
        except Exception as e:  # noqa: BLE001
            REPORT["checks"][name] = {"error": repr(e), "trace": traceback.format_exc()}
    with open(os.path.join(OUT, "report.json"), "w") as f:
        json.dump(REPORT, f, indent=2, default=str)
    lines = ["Unity Bridge phase 0 — Blender %s" % REPORT["blender"], ""]
    for name, r in REPORT["files"].items():
        if "error" in r:
            lines.append("FAIL  %s: %s" % (name, r["error"]))
            continue
        if "skipped" in r:
            lines.append("SKIP  %s: %s" % (name, r["skipped"]))
            continue
        first = r["cycles"][0]["vs_original"]
        drift = all(c["vs_previous"]["ok"] for c in r["cycles"][1:])
        ok = first["ok"] and drift and all(r["fingerprint_stable"]) and all(r["noop_after_export"])
        lines.append("%s  %s  first=%s drift_free=%s fp_stable=%s noop=%s" % (
            "PASS" if ok else "FAIL", name, first["ok"], drift, all(r["fingerprint_stable"]),
            all(r["noop_after_export"])))
        if not first["ok"]:
            lines.append("      over: %s  structure: %s" % (first["over_tolerance"], first["structure"][:5]))
            lines.append("      max: %s" % first["max"])
    lines.append("")
    for name, r in REPORT["checks"].items():
        lines.append("CHECK %s: %s" % (name, json.dumps(r, default=str)[:600]))
    text = "\n".join(lines)
    with open(os.path.join(OUT, "report.txt"), "w") as f:
        f.write(text)
    print(text)


main()
