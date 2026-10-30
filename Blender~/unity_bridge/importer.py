"""Builds the working scene from Unity's files: model, clip FBX files and sampled .anim clips."""

import json
import os

import bpy
from mathutils import Matrix

from . import actions, baking, fbx_profile, fingerprint, util

IMPORTER_LEGACY = "LEGACY"
IMPORTER_CPP = "CPP"


# --------------------------------------------------------------------------- operators


def _window_override():
    wm = bpy.context.window_manager
    if wm is not None and wm.windows:
        win = wm.windows[0]
        return {"window": win, "screen": win.screen}
    return {}


def call_op(op, **kwargs):
    """Calls an operator with only the arguments it knows (they differ between versions)."""
    known = set(op.get_rna_type().properties.keys())
    args = {k: v for k, v in kwargs.items() if k in known}
    ov = _window_override()
    if ov:
        with bpy.context.temp_override(**ov):
            return op(**args)
    return op(**args)


def _legacy_available():
    try:
        bpy.ops.import_scene.fbx.get_rna_type()
        return True
    except (AttributeError, KeyError, RuntimeError):
        return False


def import_fbx(path, use_anim, importer=None):
    """Imports into the active collection. Returns (objects, actions) created by the import."""
    importer = importer or (IMPORTER_LEGACY if _legacy_available() else IMPORTER_CPP)
    before_obj = set(bpy.data.objects)
    before_act = set(bpy.data.actions)
    if importer == IMPORTER_LEGACY:
        res = call_op(
            bpy.ops.import_scene.fbx, filepath=path, use_manual_orientation=False, global_scale=1.0,
            bake_space_transform=False, use_custom_normals=True, colors_type="SRGB",
            use_image_search=False, use_alpha_decals=False, use_anim=use_anim, anim_offset=0.0,
            use_subsurf=False, use_custom_props=True, use_custom_props_enum_as_string=True,
            ignore_leaf_bones=False, force_connect_children=False, automatic_bone_orientation=False,
            primary_bone_axis="Y", secondary_bone_axis="X", use_prepost_rot=True)
    else:
        res = call_op(
            bpy.ops.wm.fbx_import, filepath=path, global_scale=1.0, use_custom_normals=True,
            import_colors="SRGB", use_custom_props=True, use_custom_props_enum_as_string=True,
            ignore_leaf_bones=False, use_anim=use_anim, anim_offset=0.0, validate_meshes=False)
    if "FINISHED" not in res:
        raise RuntimeError("FBX import failed: %s" % path)
    new_obj = [o for o in bpy.data.objects if o not in before_obj]
    new_act = [a for a in bpy.data.actions if a not in before_act]
    return new_obj, new_act


# --------------------------------------------------------------------------- scene


def clear_scene():
    """Empties the startup scene without reloading preferences or add-ons."""
    scene = bpy.context.scene
    st = getattr(scene, "ub_state", None)
    if st is not None:
        # The scene survives, so does its state: drop the old file/clip lists (they hold actions).
        st.issues.clear()
        st.clips.clear()
        st.files.clear()
        st.clip_index = -1
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights,
                 bpy.data.armatures, bpy.data.actions, bpy.data.images, bpy.data.curves):
        for block in list(coll):
            if block.users - int(block.use_fake_user) <= 0:
                coll.remove(block)
    try:
        bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    except TypeError:
        bpy.data.orphans_purge()
    for other in list(bpy.data.scenes):
        if other != scene:
            bpy.data.scenes.remove(other)
    return scene


def setup_scene(scene, profile):
    us = scene.unit_settings
    us.system = "METRIC"
    u = float(profile.get("unit_scale_factor") or 100.0)
    us.scale_length = u / 100.0
    if abs(u - 1.0) < 1e-6:
        us.length_unit = "CENTIMETERS"
    elif abs(u - 0.1) < 1e-6:
        us.length_unit = "MILLIMETERS"
    else:
        us.length_unit = "METERS"
    fps = float(profile.get("fps") or 30.0)
    r = max(1, int(round(fps)))
    scene.render.fps = r
    scene.render.fps_base = r / fps if fps > 0 else 1.0


def find_layer_collection(layer_col, collection):
    if layer_col.collection == collection:
        return layer_col
    for child in layer_col.children:
        found = find_layer_collection(child, collection)
        if found:
            return found
    return None


class active_collection:
    """Context manager: imports and exports work on the view layer's active collection."""

    def __init__(self, collection):
        self.collection = collection
        self.prev = None

    def __enter__(self):
        vl = bpy.context.view_layer
        self.prev = vl.active_layer_collection
        lc = find_layer_collection(vl.layer_collection, self.collection)
        if lc is None:
            raise RuntimeError("Collection %s is not in the view layer" % self.collection.name)
        if lc.exclude:
            lc.exclude = False
        vl.active_layer_collection = lc
        return lc

    def __exit__(self, *exc):
        try:
            bpy.context.view_layer.active_layer_collection = self.prev
        except (ReferenceError, TypeError):
            pass


def model_collection(scene):
    st = scene.ub_state
    return bpy.data.collections.get(st.collection_name) if st.collection_name else None


def armatures(collection):
    return sorted((o for o in collection.all_objects if o.type == "ARMATURE"), key=lambda o: o.name)


def main_armature(collection):
    arms = armatures(collection)
    if not arms:
        return None
    return max(arms, key=lambda o: len(o.data.bones))


# --------------------------------------------------------------------------- baseline


def capture_baseline(collection):
    objects = {o.name: o.type for o in collection.all_objects}
    bones = {a.name: [b.name for b in a.data.bones] for a in armatures(collection)}
    materials = sorted({s.material.name for o in collection.all_objects for s in getattr(o, "material_slots", [])
                        if s.material})
    pose = {}
    for a in armatures(collection):
        pose[a.name] = {pb.name: [v for row in pb.matrix_basis for v in row] for pb in a.pose.bones}
    return {"objects": objects, "bones": bones, "materials": materials, "pose": pose}


def restore_static_pose(collection, baseline):
    """Puts every armature back into the default pose read from the FBX (node defaults)."""
    saved = {}
    for a in armatures(collection):
        bpose = baseline.get("pose", {}).get(a.name)
        if not bpose:
            continue
        saved[a.name] = {pb.name: pb.matrix_basis.copy() for pb in a.pose.bones}
        for pb in a.pose.bones:
            vals = bpose.get(pb.name)
            pb.matrix_basis = Matrix((vals[0:4], vals[4:8], vals[8:12], vals[12:16])) if vals else Matrix.Identity(4)
    return saved


def restore_pose_values(collection, saved):
    for a in armatures(collection):
        values = saved.get(a.name)
        if not values:
            continue
        for pb in a.pose.bones:
            m = values.get(pb.name)
            if m is not None:
                pb.matrix_basis = m


# --------------------------------------------------------------------------- viewport textures


def apply_preview_textures(materials):
    """materials: [{'name': 'Body', 'texture': '/abs/albedo.png'}] sent by Unity."""
    for info in materials or []:
        mat = bpy.data.materials.get(info.get("name", ""))
        tex = info.get("texture")
        if mat is None or not tex or not os.path.isfile(tex):
            continue
        try:
            mat.use_nodes = True
        except AttributeError:
            pass
        nt = mat.node_tree
        if nt is None:
            continue
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        if bsdf.inputs["Base Color"].is_linked:
            continue
        img = bpy.data.images.load(tex, check_existing=True)
        node = nt.nodes.new("ShaderNodeTexImage")
        node.image = img
        node.location = (bsdf.location.x - 320, bsdf.location.y)
        node.label = "Unity preview"
        nt.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
        mat["ub_preview"] = True


# --------------------------------------------------------------------------- takes


def _owner_and_take(action_name):
    if "|" in action_name:
        owner, take = action_name.split("|", 1)
        return owner, take
    return "", action_name


def transfer_takes(temp_objects, new_actions, target_collection, profile_takes):
    """Moves actions created by an animation import onto the model's own objects.

    Returns [{'take', 'action', 'key_action', 'key_target'}] in the file's take order.
    """
    targets = {}
    key_targets = {}
    ours = {o.name: o for o in target_collection.all_objects}
    for tmp in temp_objects:
        base, _ = util.strip_blender_suffix(tmp.name)
        dst = ours.get(base)
        if dst is None or dst.type != tmp.type:
            continue
        targets[tmp.name] = dst
        if tmp.type == "MESH" and tmp.data.shape_keys is not None:
            key_targets[tmp.data.shape_keys.name] = dst
            key_targets[tmp.name] = dst

    takes = {}
    order = [t["name"] for t in profile_takes]
    single_arm = [o for o in target_collection.all_objects if o.type == "ARMATURE"]
    for act in new_actions:
        owner, take = _owner_and_take(act.name)
        entry = takes.setdefault(take, {"take": take, "action": None, "key_action": None, "key_target": ""})
        dst = targets.get(owner)
        if dst is None and owner in key_targets:
            entry["key_action"] = act
            entry["key_target"] = key_targets[owner].name
            continue
        if dst is None and len(single_arm) == 1:
            dst = single_arm[0]
        if dst is None:
            util.log("Take", take, "animates", owner, "which is not part of the model; skipped")
            continue
        if dst.type == "ARMATURE":
            entry["action"] = act
        else:
            util.log("Object animation on", dst.name, "is not preserved (only armatures and shape keys)")
    result = []
    for name in order + [t for t in takes if t not in order]:
        e = takes.get(name)
        if e and (e["action"] or e["key_action"]):
            if e["action"] is not None:
                e["action"].name = name
                e["action"].use_fake_user = True
            if e["key_action"] is not None:
                e["key_action"].name = name + " (shape keys)"
                e["key_action"].use_fake_user = True
            result.append(e)
    return result


def remove_imported(temp_objects, keep_actions, new_actions=()):
    """Deletes temporary import results, keeping the listed actions."""
    data_blocks = set()
    for o in temp_objects:
        if o.data is not None:
            data_blocks.add(o.data)
            if getattr(o.data, "shape_keys", None) is not None:
                data_blocks.add(o.data.shape_keys)
        for slot in getattr(o, "material_slots", []):
            if slot.material is not None:
                data_blocks.add(slot.material)
    for o in temp_objects:
        bpy.data.objects.remove(o, do_unlink=True)
    removers = (
        (bpy.types.Mesh, bpy.data.meshes),
        (bpy.types.Armature, bpy.data.armatures),
        (bpy.types.Material, bpy.data.materials),
        (bpy.types.Curve, bpy.data.curves),
    )
    for block in sorted(data_blocks, key=lambda b: isinstance(b, bpy.types.Material)):
        try:
            if block.users != 0:
                continue
            for cls, coll in removers:
                if isinstance(block, cls):
                    coll.remove(block)
                    break
        except ReferenceError:
            pass
    for a in list(new_actions):
        # Imported actions carry a fake user, so the users == 0 sweep below misses them.
        try:
            if a not in keep_actions:
                bpy.data.actions.remove(a)
        except ReferenceError:
            pass
    for a in list(bpy.data.actions):
        if a.users == 0 and a not in keep_actions:
            bpy.data.actions.remove(a)


def _skeletons_match(arm_a, arm_b, tol=1e-4):
    if set(arm_a.data.bones.keys()) != set(arm_b.data.bones.keys()):
        return False
    sa = baking._uniform_scale(arm_a.matrix_world)
    sb = baking._uniform_scale(arm_b.matrix_world)
    if abs(sa - sb) > tol * max(sa, sb):
        return False
    wa, wb = arm_a.matrix_world, arm_b.matrix_world
    for b in arm_a.data.bones:
        ma = wa @ b.matrix_local
        mb = wb @ arm_b.data.bones[b.name].matrix_local
        for i in range(4):
            for j in range(4):
                if abs(ma[i][j] - mb[i][j]) > tol * (1.0 if j < 3 else max(1.0, sa)):
                    return False
    return True


def import_takes_from(path, target_arm, scene, temp_parent):
    """Imports an FBX with animation and returns its takes as actions on target_arm."""
    tmp_col = bpy.data.collections.new("UB_TMP_IMPORT")
    temp_parent.children.link(tmp_col)
    try:
        with active_collection(tmp_col):
            new_obj, new_act = import_fbx(path, use_anim=True)
        prof = fbx_profile.read(path)
        src_arms = [o for o in new_obj if o.type == "ARMATURE"]
        if not src_arms:
            raise RuntimeError("No armature in %s" % os.path.basename(path))
        src = max(src_arms, key=lambda o: len(o.data.bones))
        reuse = _skeletons_match(src, target_arm)
        results = []
        by_take = {}
        for act in new_act:
            owner, take = _owner_and_take(act.name)
            base, _ = util.strip_blender_suffix(owner)
            if owner and base != util.strip_blender_suffix(src.name)[0] and owner != src.name:
                continue
            by_take[take] = act
        order = [t["name"] for t in prof["takes"]] or list(by_take)
        keep = []
        for take in order:
            act = by_take.get(take)
            if act is None:
                continue
            if reuse:
                act.name = take
                act.use_fake_user = True
                keep.append(act)
                results.append({"take": take, "action": act, "start": actions.frame_range(act)[0]})
                continue
            actions.assign(src, act)
            lo, hi = actions.frame_range(act)
            frames = list(range(int(round(lo)), int(round(hi)) + 1))
            world = baking.sample_armature_world(scene, src, frames)
            poses = baking.world_to_target_pose(target_arm, src, world)
            baked = bpy.data.actions.new(take)
            baked.use_fake_user = True
            baking.bake_pose_matrices(target_arm, baked, frames, poses)
            keep.append(baked)
            results.append({"take": take, "action": baked, "start": lo})
        remove_imported(new_obj, keep, new_act)
        return results, prof
    finally:
        bpy.data.collections.remove(tmp_col)


# --------------------------------------------------------------------------- fresh model


def fresh_model(scene, model_abs_path, model_name, materials=None):
    """Imports the model into an empty scene. Returns (collection, profile, takes)."""
    profile = fbx_profile.read(model_abs_path)
    setup_scene(scene, profile)
    col = bpy.data.collections.new(model_name)
    scene.collection.children.link(col)
    with active_collection(col):
        import_fbx(model_abs_path, use_anim=False)
    takes = []
    if profile["takes"]:
        tmp = bpy.data.collections.new("UB_TMP_TAKES")
        scene.collection.children.link(tmp)
        try:
            with active_collection(tmp):
                tmp_obj, tmp_act = import_fbx(model_abs_path, use_anim=True)
            takes = transfer_takes(tmp_obj, tmp_act, col, profile["takes"])
            keep = [t["action"] for t in takes if t["action"]] + [t["key_action"] for t in takes if t["key_action"]]
            remove_imported(tmp_obj, keep, tmp_act)
        finally:
            bpy.data.collections.remove(tmp)
        starts = {t["name"]: t["start_frame"] for t in profile["takes"]}
        for t in takes:
            t["start"] = starts.get(t["take"], 0.0)
    apply_preview_textures(materials)
    return col, profile, takes


def ensure_helpers_collection(scene):
    col = bpy.data.collections.get("Helpers (not exported)")
    if col is None:
        col = bpy.data.collections.new("Helpers (not exported)")
        scene.collection.children.link(col)
    return col


# --------------------------------------------------------------------------- new clip


def new_clip_action(arm_obj, name, length=30):
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    actions.assign(arm_obj, act)
    act.use_frame_range = True
    act.frame_start = 0
    act.frame_end = length
    # One key at rest on frame 0 so the clip is never empty and every bone has a channel.
    slot = actions.slot_of(arm_obj)
    bag = actions.ensure_channelbag(act, slot)
    for pb in arm_obj.pose.bones:
        pb.rotation_mode = "QUATERNION"
        base = 'pose.bones["%s"]' % bpy.utils.escape_identifier(pb.name)
        for path, vals in ((".location", pb.location), (".rotation_quaternion", pb.rotation_quaternion),
                           (".scale", pb.scale)):
            for i, v in enumerate(vals):
                fc = actions.new_fcurve(bag, base + path, i, pb.name)
                actions.write_samples(fc, [0.0], [float(v)])
    return act


# --------------------------------------------------------------------------- Unity samples


def load_anim_samples(arm_obj, sample_path, clip_name):
    sample = util.read_json(sample_path)
    if not sample:
        raise RuntimeError("Unity samples are missing: %s" % sample_path)
    poses, resid = baking.unity_samples_to_pose(arm_obj, sample)
    frames = list(range(len(poses)))
    act = bpy.data.actions.new(clip_name)
    act.use_fake_user = True
    baking.bake_pose_matrices(arm_obj, act, frames, poses)
    act.use_frame_range = True
    act.frame_start = 0
    act.frame_end = max(0, len(frames) - 1)
    return act, resid


def baseline_json(collection):
    return json.dumps(capture_baseline(collection))


def model_fps(collection):
    return fingerprint.model_fingerprints(collection)
