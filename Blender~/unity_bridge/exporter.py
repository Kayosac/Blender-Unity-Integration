"""Smart export: writes only the files whose content changed, atomically, with history."""

import json
import os
from contextlib import contextmanager

import bpy

from . import actions, fbx_patch, fbx_profile, fingerprint, history, importer, paths as bpaths, util, validate

MODEL_TYPES = {"EMPTY", "MESH", "ARMATURE", "OTHER"}


# --------------------------------------------------------------------------- FBX writer


def _scale_options(profile):
    """Exact unit handling. Scenes are in metres (see importer.setup_scene).

    UnitScaleFactor 100 (metres) and 1 (centimetres) are written exactly; anything else is written
    with the right numbers and the factor patched in afterwards.
    """
    u = float(profile.get("unit_scale_factor") or 100.0)
    if abs(u - 100.0) < 1e-9:
        return {"apply_unit_scale": False, "apply_scale_options": "FBX_SCALE_UNITS", "global_scale": 1.0}, None
    if abs(u - 1.0) < 1e-9:
        return {"apply_unit_scale": False, "apply_scale_options": "FBX_SCALE_NONE", "global_scale": 1.0}, None
    return {"apply_unit_scale": False, "apply_scale_options": "FBX_SCALE_NONE", "global_scale": 1.0 / u}, u


def export_fbx(filepath, profile, collection, bake_anim, object_types, allowed_clusters=None):
    """One fixed preset. The profile reproduces the source file's axes and units."""
    scale_args, patch_unit = _scale_options(profile)
    with importer.active_collection(collection), cluster_filter(allowed_clusters):
        res = importer.call_op(
            bpy.ops.export_scene.fbx,
            filepath=filepath, check_existing=False,
            use_selection=False, use_visible=False, use_active_collection=True,
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
            axis_forward=profile.get("axis_forward", "-Z"), axis_up=profile.get("axis_up", "Y"),
            **scale_args)
    if "FINISHED" not in res:
        raise RuntimeError("FBX export failed")
    if patch_unit is not None:
        fbx_patch.set_unit_scale(filepath, patch_unit)


@contextmanager
def cluster_filter(allowed):
    """Blender's exporter writes a skin cluster for every bone. Unity turns each into an entry of
    SkinnedMeshRenderer.bones, so a round trip would grow that list. Keep the source's clusters
    (plus bones that now carry weights). allowed: {mesh object name: set of bone names}.
    """
    if not allowed:
        yield
        return
    try:
        from io_scene_fbx import export_fbx_bin as efb
        orig = efb.fbx_skeleton_from_armature
    except (ImportError, AttributeError):
        util.log("Skin cluster filter unavailable in this Blender version")
        yield
        return

    def wrapped(scene, settings, arm_obj, objects, data_meshes, data_bones, data_deformers_skin, *args, **kwargs):
        result = orig(scene, settings, arm_obj, objects, data_meshes, data_bones, data_deformers_skin, *args, **kwargs)
        try:
            per_mesh = data_deformers_skin.get(arm_obj, {})
            for me_key, (skin_key, ob_obj, clusters) in list(per_mesh.items()):
                names = allowed.get(ob_obj.bdata.name)
                if names is None:
                    continue
                keep = {bo: k for bo, k in clusters.items() if bo.bdata.name in names}
                if keep:
                    per_mesh[me_key] = (skin_key, ob_obj, keep)
        except Exception as e:  # noqa: BLE001 - internal layout changed: export unfiltered
            util.log("Skin cluster filter skipped:", e)
        return result

    efb.fbx_skeleton_from_armature = wrapped
    try:
        yield
    finally:
        efb.fbx_skeleton_from_armature = orig


def allowed_clusters(collection, baseline):
    """Per mesh: bones that had a cluster in the source file, plus bones that now have weights."""
    src = baseline.get("clusters") or {}
    out = {}
    for ob in collection.all_objects:
        if ob.type != "MESH" or ob.name not in src:
            continue
        weighted = set()
        idx_names = {vg.index: vg.name for vg in ob.vertex_groups}
        for v in ob.data.vertices:
            for g in v.groups:
                if g.weight > 0.0:
                    weighted.add(idx_names.get(g.group))
        out[ob.name] = set(src[ob.name]) | weighted
    return out


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


_TRANSFORM_PROPS = ("rotation_mode", "location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle",
                    "scale", "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale")


def _transform_props(o):
    """Raw transform channels. Restoring these is exact; matrix_basis would re-decompose with float noise."""
    out = {}
    for k in _TRANSFORM_PROPS:
        v = getattr(o, k)
        out[k] = v if isinstance(v, str) else tuple(v)
    return out


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
    transforms = [(o, _transform_props(o)) for o in collection.all_objects]
    pose = importer.restore_static_pose(collection, baseline)
    try:
        yield
    finally:
        importer.restore_pose_values(collection, pose)
        for o, props in transforms:
            try:
                for k, v in props.items():
                    setattr(o, k, v)
            except ReferenceError:
                pass
        for id_data, act, slot in saved:
            try:
                actions.assign(id_data, act, slot)
            except (ReferenceError, RuntimeError):
                pass
        scene.frame_set(frame)


_ROT_PATH = {"QUATERNION": ("rotation_quaternion", 4), "AXIS_ANGLE": ("rotation_axis_angle", 4)}
STATIC_FRAME = -100000  # far from every take, so no strip touches the static pose


def _key_static(bag, base, target, group):
    path, n = _ROT_PATH.get(target.rotation_mode, ("rotation_euler", 3))
    for prop, size in (("location", 3), (path, n), ("scale", 3)):
        values = getattr(target, prop)
        for i in range(size):
            fc = actions.new_fcurve(bag, base + prop, i, group)
            actions.write_samples(fc, [float(STATIC_FRAME)], [float(values[i])])


def _static_pose_action(arm):
    """One key per channel holding the current (default) pose, for the exporter's static evaluation."""
    act = bpy.data.actions.new("UB_STATIC_POSE")
    slot = actions.ensure_slot(act, "OBJECT", arm.name)
    bag = actions.ensure_channelbag(act, slot)
    _key_static(bag, "", arm, "Object Transforms")
    for pb in arm.pose.bones:
        _key_static(bag, 'pose.bones["%s"].' % bpy.utils.escape_identifier(pb.name), pb, pb.name)
    return act, slot


@contextmanager
def nla_takes(entries, scene=None):
    """Temporary NLA strips so every take is written under its exact name and timing.

    The exporter writes node defaults from the scene as evaluated after baking, with every strip
    active. Strips here extrapolate nothing and the scene sits on STATIC_FRAME, while a one-key
    action holds the default pose; so the file's default pose is the source's, not a take's frame.

    entries: [{'id_data', 'action', 'take', 'start', 'end'}]; adds 'exported_name' to each.
    """
    created, muted, statics = [], [], []
    ours = set()
    try:
        for id_data in {e["id_data"] for e in entries}:
            ad = id_data.animation_data or id_data.animation_data_create()
            act, slot = _static_pose_action(id_data)
            statics.append(act)
            actions.assign(id_data, act, slot)
        for e in entries:
            id_data = e["id_data"]
            ad = id_data.animation_data
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
            try:
                strip.frame_start_ui = e["start"]  # exact (fractional) take start
            except (AttributeError, TypeError):
                pass
            strip.extrapolation = "NOTHING"
            strip.name = e["take"]
            e["exported_name"] = strip.name
            created.append((ad, track))
        if scene is not None and entries:
            scene.frame_set(STATIC_FRAME)
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
        for act in statics:
            try:
                bpy.data.actions.remove(act)
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
    pose_source = baseline
    if f.kind == "CLIP" and profile.get("static_pose"):
        pose_source = dict(baseline, pose=profile["static_pose"])  # the clip file's own default pose
    with export_pose(scene, col, pose_source):
        if pose_source is not baseline:
            from mathutils import Matrix
            for name, vals in (profile["static_pose"].get("__object__") or {}).items():
                ob = bpy.data.objects.get(name)
                if ob is not None:
                    ob.matrix_world = Matrix((vals[0:4], vals[4:8], vals[8:12], vals[12:16]))
        if f.kind == "MODEL":
            entries = _entries_for(arm, clips) if arm is not None else []
            key_clip = clips[0] if len(clips) == 1 and clips[0].key_action is not None else None
            if key_clip is not None:
                actions.assign(bpy.data.objects[key_clip.key_target].data.shape_keys, key_clip.key_action)
            with nla_takes(entries, scene):
                export_fbx(tmp, profile, col, bool(entries), MODEL_TYPES, allowed_clusters(col, baseline))
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
                with nla_takes(entries, scene):
                    export_fbx(tmp, profile, tmp_col, True, types, allowed_clusters(col, baseline))

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
        prev_all = json.loads(st.baseline_json or "{}")
        fresh["clusters"] = prev_all.get("clusters", {})
        fresh["mirrored"] = prev_all.get("mirrored", [])
        fresh["dropped"] = prev_all.get("dropped", {})
        prev = prev_all.get("pose", {})
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
