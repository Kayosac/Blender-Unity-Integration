"""Clip list: loading takes from Unity files, creating clips, making one active, simplifying keys."""

import json
import os

import bpy

from . import actions, exporter, importer, util


def file_clips(st, fi):
    return [c for c in st.clips if c.file_index == fi]


_BUSY = [False]


def activate(scene, index):
    """Makes a clip the armature's active action. Safe to call from the list's update callback."""
    if _BUSY[0]:
        return
    _BUSY[0] = True
    try:
        _activate(scene, index)
    finally:
        _BUSY[0] = False


def _activate(scene, index):
    st = scene.ub_state
    if not (0 <= index < len(st.clips)):
        return
    st.clip_index = index
    clip = st.clips[index]
    col = importer.model_collection(scene)
    arm = importer.main_armature(col) if col else None
    if not clip.loaded:
        load_file(scene, clip.file_index)
        clip = st.clips[index]  # adding clips re-allocates the collection
    if arm is not None and clip.action is not None:
        actions.assign(arm, clip.action)
        lo, hi = actions.frame_range(clip.action)
        scene.frame_start = int(round(lo))
        scene.frame_end = max(int(round(hi)), scene.frame_start + 1)
        scene.frame_set(scene.frame_start)
    if clip.key_action is not None and clip.key_target:
        mesh = bpy.data.objects.get(clip.key_target)
        if mesh is not None and mesh.data.shape_keys is not None:
            actions.assign(mesh.data.shape_keys, clip.key_action)


def load_file(scene, fi):
    """Loads every take of a Unity file into the scene."""
    st = scene.ub_state
    f = st.files[fi]
    col = importer.model_collection(scene)
    arm = importer.main_armature(col)
    if arm is None:
        raise RuntimeError("The model has no armature to play clips on")
    abs_path = os.path.join(st.project_root, f.asset_path)
    if f.kind == "ANIM":
        if not f.sample_path or not os.path.isfile(os.path.join(st.project_root, f.sample_path)):
            raise RuntimeError("Open this .anim from Unity to edit it (Unity has to sample it first)")
        clip = next((c for c in st.clips if c.file_index == fi), None)
        name = clip.name if clip else os.path.splitext(os.path.basename(f.asset_path))[0]
        act, resid = importer.load_anim_samples(arm, os.path.join(st.project_root, f.sample_path), name)
        if clip is None:
            clip = st.clips.add()
            clip.file_index = fi
            clip.name = name
        old = clip.action
        clip.action = act
        if old is not None and old != act and old.users <= 1:
            bpy.data.actions.remove(old)
        clip.loaded = True
        clip.orig_start = 0.0
        clip.fp_sync = exporter.clip_fp(clip)
        f.loaded = True
        return [clip]

    if not os.path.isfile(abs_path):
        raise RuntimeError("%s is missing" % f.asset_path)
    results, prof = importer.import_takes_from(abs_path, arm, scene, scene.collection)
    file_profile = {k: prof[k] for k in ("axis_up", "axis_forward", "unit_scale_factor", "fps")}
    static = importer.file_default_pose(abs_path, arm, scene, scene.collection)
    if static:
        file_profile["static_pose"] = static
    if prof.get("dropped"):
        file_profile["dropped"] = prof["dropped"]
    f.profile_json = json.dumps(file_profile)
    f.sha_sync = util.sha256_file(abs_path)
    # Indices, not items: st.clips.add() re-allocates the collection and invalidates references.
    by_name = {c.name: i for i, c in enumerate(st.clips) if c.file_index == fi}
    loaded = []
    for r in results:
        ci = by_name.get(r["take"])
        if ci is None:
            clip = st.clips.add()
            clip.file_index = fi
            clip.name = r["take"]
            ci = by_name[r["take"]] = len(st.clips) - 1
        clip = st.clips[ci]
        old = clip.action
        clip.action = r["action"]
        if old is not None and old != r["action"] and old.users <= 1:
            bpy.data.actions.remove(old)
        clip.orig_start = float(r["start"])
        clip.loaded = True
        clip.fp_sync = exporter.clip_fp(clip)
        loaded.append(ci)
    f.loaded = True
    return [st.clips[i] for i in loaded]


def new_clip(scene, name, length=30):
    st = scene.ub_state
    settings = json.loads(st.settings_json or "{}")
    col = importer.model_collection(scene)
    arm = importer.main_armature(col)
    if arm is None:
        raise RuntimeError("The model has no armature")
    if any(c.name == name for c in st.clips):
        raise RuntimeError("A clip named '%s' already exists" % name)
    model_dir = os.path.dirname(st.model_path)
    stem = os.path.splitext(os.path.basename(st.model_path))[0]
    folder = settings.get("clipsFolder", "Animations").strip("/")
    rel = "%s/%s/%s@%s.fbx" % (model_dir, folder, stem, name) if folder else "%s/%s@%s.fbx" % (model_dir, stem, name)
    f = st.files.add()
    f.kind = "CLIP"
    f.asset_path = rel
    f.is_new = True
    f.rig_guid = st.model_guid
    f.loaded = True
    f.profile_json = st.profile_json
    clip = st.clips.add()
    clip.file_index = len(st.files) - 1
    clip.name = name
    clip.action = importer.new_clip_action(arm, name, length)
    clip.loaded = True
    clip.managed = True
    clip.loop = False
    clip.root_motion = "IN_PLACE"
    clip.fp_sync = ""
    activate(scene, len(st.clips) - 1)
    return clip


# --------------------------------------------------------------------------- simplify


def _sample(fc, lo, hi):
    return [fc.evaluate(f) for f in range(lo, hi + 1)]


def simplify_action(action, tolerance):
    """Removes keys while the curve stays within tolerance of the original at every frame."""
    removed = 0
    for fc in actions.iter_fcurves(action):
        kps = fc.keyframe_points
        if len(kps) < 3:
            continue
        lo, hi = int(round(kps[0].co.x)), int(round(kps[-1].co.x))
        ref = _sample(fc, lo, hi)
        for kp in kps:
            kp.handle_left_type = "AUTO_CLAMPED"
            kp.handle_right_type = "AUTO_CLAMPED"
        fc.update()
        i = 1
        while i < len(kps) - 1:
            co = kps[i].co.copy()
            hl, hr = kps[i].handle_left.copy(), kps[i].handle_right.copy()
            interp = kps[i].interpolation
            a, b = int(round(kps[i - 1].co.x)), int(round(kps[i + 1].co.x))
            kps.remove(kps[i])
            fc.update()
            ok = all(abs(fc.evaluate(fr) - ref[fr - lo]) <= tolerance for fr in range(a, b + 1))
            if ok:
                removed += 1
                continue
            kp = kps.insert(co.x, co.y, options={"FAST"})
            kp.interpolation = interp
            kp.handle_left_type = "AUTO_CLAMPED"
            kp.handle_right_type = "AUTO_CLAMPED"
            fc.update()
            i += 1
    return removed
