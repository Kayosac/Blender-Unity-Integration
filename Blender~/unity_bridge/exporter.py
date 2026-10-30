"""Smart export: writes only the files whose content changed, atomically, with history."""

import json
import os
from contextlib import contextmanager

import bpy

from . import actions, fbx_profile, fingerprint, history, importer, paths as bpaths, util, validate

MODEL_TYPES = {"EMPTY", "MESH", "ARMATURE", "OTHER"}


# --------------------------------------------------------------------------- FBX writer


def export_fbx(filepath, profile, collection, bake_anim, object_types):
    """One fixed preset. The profile reproduces the source file's axes and units."""
    with importer.active_collection(collection):
        res = importer.call_op(
            bpy.ops.export_scene.fbx,
            filepath=filepath, check_existing=False,
            use_selection=False, use_visible=False, use_active_collection=True,
            global_scale=1.0, apply_unit_scale=True, apply_scale_options="FBX_SCALE_UNITS",
            use_space_transform=True, bake_space_transform=False,
            object_types=set(object_types),
            use_mesh_modifiers=True, use_mesh_modifiers_render=False,
            mesh_smooth_type="OFF", colors_type="SRGB", prioritize_active_color=False,
            use_subsurf=False, use_mesh_edges=False, use_tspace=False, use_triangles=False,
            use_custom_props=True,
            add_leaf_bones=False, primary_bone_axis="Y", secondary_bone_axis="X",
            use_armature_deform_only=False, armature_nodetype="NULL",
            bake_anim=bake_anim, bake_anim_use_all_bones=True,
            bake_anim_use_nla_strips=bake_anim, bake_anim_use_all_actions=False,
            bake_anim_force_startend_keying=True, bake_anim_step=1.0, bake_anim_simplify_factor=0.0,
            path_mode="STRIP", embed_textures=False, batch_mode="OFF",
            axis_forward=profile.get("axis_forward", "-Z"), axis_up=profile.get("axis_up", "Y"))
    if "FINISHED" not in res:
        raise RuntimeError("FBX export failed")


@contextmanager
def export_pose(scene, collection, baseline):
    """See below; shape-key datablocks without animation data are also restored to 'no action'."""
    with _export_pose(scene, collection, baseline):
        keys = [o.data.shape_keys for o in collection.all_objects
                if o.type == "MESH" and o.data.shape_keys is not None and o.data.shape_keys.animation_data is None]
        try:
            yield
        finally:
            for k in keys:
                try:
                    if k.animation_data is not None:
                        k.animation_data.action = None
                except ReferenceError:
                    pass


@contextmanager
def _export_pose(scene, collection, baseline):
    """Default pose from the source file, no active actions; everything restored afterwards."""
    frame = scene.frame_current
    saved = []
    for o in collection.all_objects:
        if o.animation_data is not None:
            saved.append((o, o.animation_data.action, actions.slot_of(o)))
        keys = getattr(o.data, "shape_keys", None) if o.type == "MESH" else None
        if keys is not None and keys.animation_data is not None:
            saved.append((keys, keys.animation_data.action, actions.slot_of(keys)))
    for id_data, _a, _s in saved:
        id_data.animation_data.action = None
    pose = importer.restore_static_pose(collection, baseline)
    try:
        yield
    finally:
        importer.restore_pose_values(collection, pose)
        for id_data, act, slot in saved:
            try:
                actions.assign(id_data, act, slot)
            except (ReferenceError, RuntimeError):
                pass
        scene.frame_set(frame)


@contextmanager
def nla_takes(entries):
    """Temporary NLA strips so every take is written under its exact name and timing.

    entries: [{'id_data', 'action', 'take', 'start', 'end'}]; adds 'exported_name' to each.
    """
    created, muted = [], []
    ours = set()
    try:
        for e in entries:
            id_data = e["id_data"]
            ad = id_data.animation_data or id_data.animation_data_create()
            for tr in ad.nla_tracks:
                if tr.as_pointer() not in ours and not tr.mute:
                    tr.mute = True
                    muted.append(tr)
            track = ad.nla_tracks.new()
            track.name = "UB_EXPORT"
            ours.add(track.as_pointer())
            strip = track.strips.new(e["take"], int(round(e["start"])), e["action"])
            try:
                strip.action_slot = actions.pick_slot(e["action"], id_data)
            except (AttributeError, TypeError, RuntimeError):
                pass
            strip.action_frame_start = e["start"]
            strip.action_frame_end = max(e["end"], e["start"] + 1e-3)
            strip.name = e["take"]
            e["exported_name"] = strip.name
            created.append((ad, track))
        yield entries
    finally:
        for ad, track in created:
            try:
                ad.nla_tracks.remove(track)
            except (ReferenceError, RuntimeError):
                pass
        for tr in muted:
            try:
                tr.mute = False
            except ReferenceError:
                pass


@contextmanager
def temp_export_collection(scene, objects):
    col = bpy.data.collections.new("UB_TMP_EXPORT")
    scene.collection.children.link(col)
    try:
        for o in objects:
            col.objects.link(o)
        yield col
    finally:
        for o in list(col.objects):
            col.objects.unlink(o)
        bpy.data.collections.remove(col)


def _ancestors(ob):
    out = []
    p = ob.parent
    while p is not None:
        out.append(p)
        p = p.parent
    return out


# --------------------------------------------------------------------------- planning


def clip_settings(clip):
    return {"loop": bool(clip.loop), "rm": clip.root_motion, "managed": bool(clip.managed), "name": clip.name}


def clip_fp(clip):
    fp = fingerprint.action_fingerprint(clip.action, clip_settings(clip))
    if clip.key_action is not None:
        fp += fingerprint.action_fingerprint(clip.key_action)
    return fp


def plan(scene, mode="SMART"):
    """Decides which files to write. Returns a dict describing the work."""
    st = scene.ub_state
    col = importer.model_collection(scene)
    fps = fingerprint.model_fingerprints(col)
    model_changed = fps["model"] != st.model_fp_sync
    skeleton_changed = fps["skeleton"] != st.skeleton_fp_sync
    changed_clips = set()
    for i, clip in enumerate(st.clips):
        if not clip.loaded:
            continue
        f = st.files[clip.file_index]
        if f.is_new or clip_fp(clip) != clip.fp_sync:
            changed_clips.add(i)
    files = []
    active = st.clip_index
    for fi, f in enumerate(st.files):
        file_clips = [i for i, c in enumerate(st.clips) if c.file_index == fi]
        dirty_clips = [i for i in file_clips if i in changed_clips]
        write = False
        if mode == "ALL":
            write = f.kind == "MODEL" or any(st.clips[i].loaded for i in file_clips)
        elif mode == "MODEL":
            write = f.kind == "MODEL"
        elif mode == "ACTIVE_CLIP":
            write = 0 <= active < len(st.clips) and st.clips[active].file_index == fi
        else:
            write = bool(dirty_clips) or (f.kind == "MODEL" and model_changed)
        if write:
            files.append(fi)
    return {
        "fps": fps,
        "model_changed": model_changed,
        "skeleton_changed": skeleton_changed,
        "changed_clips": sorted(changed_clips),
        "files": files,
    }


# --------------------------------------------------------------------------- writing


def _resolve_target(st, f):
    """Returns (abs_path, rel_path). Follows the GUID when the asset moved in Unity."""
    root = st.project_root
    rel = f.asset_path
    abs_path = os.path.join(root, rel)
    if f.guid:
        meta_guid = util.read_meta_guid(abs_path) if os.path.exists(abs_path) else None
        if meta_guid != f.guid.lower():
            moved = util.find_asset_by_guid(root, f.guid)
            if moved:
                util.log("Asset moved:", rel, "->", moved)
                f.asset_path = rel = moved
                abs_path = os.path.join(root, rel)
            elif not os.path.exists(abs_path):
                raise RuntimeError("%s was not found by path or GUID" % rel)
    elif not f.is_new and os.path.exists(abs_path):
        f.guid = util.read_meta_guid(abs_path) or ""
    return abs_path, rel


def _model_multi_root(col, arm):
    """True when the model has top-level objects besides the armature (Unity keeps a synthetic root)."""
    objs = set(col.all_objects)
    tops = [o for o in objs if o.parent is None or o.parent not in objs]
    if arm is None:
        return len(tops) > 1
    root = arm
    while root.parent is not None and root.parent in objs:
        root = root.parent
    return any(o != root for o in tops)


def _takes_of_file(st, fi):
    return [c for c in st.clips if c.file_index == fi]


def _entries_for(arm, clips):
    entries = []
    for c in clips:
        if c.action is None:
            continue
        lo, hi = actions.frame_range(c.action)
        entries.append({"id_data": arm, "action": c.action, "take": c.name, "start": lo, "end": hi, "clip": c})
    return entries


def _write_file(scene, paths, fi, settings, staging_rel=None, changed_clips=()):
    st = scene.ub_state
    f = st.files[fi]
    col = importer.model_collection(scene)
    baseline = json.loads(st.baseline_json or "{}")
    model_profile = json.loads(st.profile_json or "{}")
    profile = json.loads(f.profile_json) if f.profile_json else model_profile
    arm = importer.main_armature(col)
    clips = _takes_of_file(st, fi)

    if f.kind == "MODEL":
        target_abs, target_rel = _resolve_target(st, f)
    elif f.kind == "ANIM":
        target_rel = staging_rel
        target_abs = os.path.join(st.project_root, target_rel)
    else:
        target_abs, target_rel = _resolve_target(st, f)

    tmp = os.path.join(paths.tmp, util.new_id() + ".fbx")
    os.makedirs(paths.tmp, exist_ok=True)
    with export_pose(scene, col, baseline):
        if f.kind == "MODEL":
            entries = _entries_for(arm, clips) if arm is not None else []
            key_clip = clips[0] if len(clips) == 1 and clips[0].key_action is not None else None
            if key_clip is not None:
                actions.assign(bpy.data.objects[key_clip.key_target].data.shape_keys, key_clip.key_action)
            with nla_takes(entries):
                export_fbx(tmp, profile, col, bool(entries), MODEL_TYPES)
        else:
            if arm is None:
                raise RuntimeError("The model has no armature")
            objects = [arm] + _ancestors(arm)
            types = {"ARMATURE", "EMPTY"}
            key_clip = clips[0] if len(clips) == 1 and clips[0].key_action is not None else None
            if key_clip is not None:
                mesh = bpy.data.objects.get(key_clip.key_target)
                if mesh is not None:
                    objects += [mesh] + [p for p in _ancestors(mesh) if p not in objects]
                    types.add("MESH")
                    actions.assign(mesh.data.shape_keys, key_clip.key_action)
            entries = _entries_for(arm, clips)
            with temp_export_collection(scene, objects) as tmp_col:
                with nla_takes(entries):
                    export_fbx(tmp, profile, tmp_col, True, types)

    exported = fbx_profile.read(tmp)
    starts = {t["name"]: t["start_frame"] for t in exported["takes"]}

    if f.kind == "ANIM":
        history.backup(paths, f.guid, f.asset_path, st.session_id,
                       settings.get("historySize", 10), settings.get("historyMaxMB", 2048), reason="anim")
    else:
        history.backup(paths, f.guid, target_rel, st.session_id,
                       settings.get("historySize", 10), settings.get("historyMaxMB", 2048))
    util.replace_file_atomic(tmp, target_abs)

    item = {
        "kind": {"MODEL": "model", "CLIP": "clip", "ANIM": "anim"}[f.kind],
        "assetPath": target_rel,
        "guid": f.guid,
        "isNew": bool(f.is_new),
        "hasMesh": bool(f.has_mesh),
        "rigGuid": f.rig_guid or st.model_guid,
        "takes": [],
        "clips": [],
        "changedTakes": [st.clips[i].name for i in changed_clips if st.clips[i].file_index == fi],
        # Unity collapses a single top-level node into the prefab root; clip files must match the model.
        "armatureName": arm.name if arm is not None else "",
        "modelMultiRoot": _model_multi_root(col, arm),
    }
    for e in (entries or []):
        c = e["clip"]
        item["takes"].append({
            "originalName": c.name,
            "exportedName": e.get("exported_name", c.name),
            "originalStart": float(c.orig_start),
            "exportedStart": float(starts.get(e.get("exported_name", c.name), 0.0)),
        })
        if c.managed:
            exp_start = float(starts.get(e.get("exported_name", c.name), 0.0))
            item["clips"].append({
                "name": c.name,
                "takeName": e.get("exported_name", c.name),
                "firstFrame": exp_start,
                "lastFrame": exp_start + (e["end"] - e["start"]),
                "loop": bool(c.loop),
                "rootMotion": c.root_motion,
            })
    if f.kind == "ANIM":
        item["animGuid"] = f.guid
        item["animPath"] = f.asset_path
        item["stagingPath"] = target_rel
        item["pathPrefix"] = f.path_prefix
    return item, target_abs, exported


def run(scene, mode="SMART", allow_errors=False):
    """Returns {'status': 'ok'|'up_to_date'|'blocked'|'error', 'items': [...], 'issues': [...]}."""
    st = scene.ub_state
    paths = bpaths.BridgePaths(st.project_root, st.art_source or "ArtSource")
    paths.ensure()
    settings = json.loads(st.settings_json or "{}")
    work = plan(scene, mode)
    if not work["files"]:
        validate.store(scene, [])
        return {"status": "up_to_date", "items": [], "issues": []}
    targets = {st.files[i].asset_path for i in work["files"]}
    issues = validate.run(scene, targets)
    validate.store(scene, issues)
    if any(i["level"] == validate.ERROR for i in issues) and not allow_errors:
        st.blocked = True
        return {"status": "blocked", "items": [], "issues": issues}
    st.blocked = False

    from . import session
    session.save_blend()

    staging_dir = "%s/%s" % (paths.staging_assets_rel, (st.session_id or util.new_id())[:8])
    items = []
    from . import clips as clips_mod
    for fi in work["files"]:
        f = st.files[fi]
        if f.kind == "CLIP" and not f.is_new and any(not c.loaded for c in _takes_of_file(st, fi)):
            # A file is always rewritten whole: load its other takes so none is dropped.
            clips_mod.load_file(scene, fi)
        staging_rel = None
        if f.kind == "ANIM":
            stem = os.path.splitext(os.path.basename(st.model_path))[0]
            clip = next((c for c in st.clips if c.file_index == fi), None)
            staging_rel = "%s/%s@%s.fbx" % (staging_dir, stem, clip.name if clip else "Clip")
        item, target_abs, exported = _write_file(scene, paths, fi, settings, staging_rel, work["changed_clips"])
        items.append(item)
        # The file now holds what Blender has: move the sync points forward.
        if f.kind != "ANIM":
            f.sha_sync = util.sha256_file(target_abs)
        starts = {t["name"]: t["start_frame"] for t in exported["takes"]}
        for c in _takes_of_file(st, fi):
            c.fp_sync = clip_fp(c)
            c.orig_start = float(starts.get(c.name, c.orig_start))
        if f.is_new:
            f.is_new = False

    if any(st.files[i].kind == "MODEL" for i in work["files"]):
        st.model_fp_sync = work["fps"]["model"]
        st.skeleton_fp_sync = work["fps"]["skeleton"]
        # Names follow the edit; the default pose stays the one read from the source file
        # (the current pose may be an animated frame).
        fresh = importer.capture_baseline(importer.model_collection(scene))
        prev = json.loads(st.baseline_json or "{}").get("pose", {})
        for arm_name, bones in fresh["pose"].items():
            old_pose = prev.get(arm_name, {})
            fresh["pose"][arm_name] = {b: old_pose.get(b, v) for b, v in bones.items()}
        st.baseline_json = json.dumps(fresh)

    manifest = {
        "version": 1,
        "sessionId": st.session_id,
        "createdUtc": util.now_utc_iso(),
        "blendPath": paths.rel(bpy.data.filepath) if bpy.data.filepath else "",
        "modelGuid": st.model_guid,
        "modelChanged": work["model_changed"],
        "skeletonChanged": work["skeleton_changed"],
        "items": items,
        "warnings": [i["message"] for i in issues if i["level"] == validate.WARNING],
    }
    out = os.path.join(paths.outbox, "%s_%s.json" % (util.timestamp_slug(), util.new_id()[:8]))
    util.write_json_atomic(out, manifest)

    model_file = next((f for f in st.files if f.kind == "MODEL"), None)
    if st.model_guid:
        bpaths.update_state(
            paths, st.model_guid,
            assetPath=st.model_path,
            blendPath=paths.rel(bpy.data.filepath) if bpy.data.filepath else "",
            pinned=bool(st.pinned),
            fbxSha256AtSync=model_file.sha_sync if model_file else "",
            dirty=False,
            lastExportUtc=util.now_utc_iso())
    st.dirty_hint = False
    session.save_blend()
    if settings.get("autoPinClips") and not st.pinned and any(i["kind"] == "clip" and i["isNew"] for i in items):
        session.set_pinned(scene, True)
    session.quiet_period()
    return {"status": "ok", "items": items, "issues": issues, "manifest": out}
