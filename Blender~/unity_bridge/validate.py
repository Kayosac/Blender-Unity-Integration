"""Checks run before every export. Errors block (with an explicit override), warnings inform."""

import json
import os
import re

import bpy

from . import actions, importer, util

ERROR = "ERROR"
WARNING = "WARNING"

_LOD_RE = re.compile(r"^(.*)_LOD(\d+)$")
_BAD_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TOPOLOGY_SAFE = {"ARMATURE"}


def _issue(level, code, message, target="", bone=""):
    return {"level": level, "code": code, "message": message, "target": target, "bone": bone}


def _short(names, n=6):
    names = sorted(names)
    extra = len(names) - n
    s = ", ".join(names[:n])
    return s + (" and %d more" % extra if extra > 0 else "")


def run(scene, files_to_write=None):
    st = scene.ub_state
    issues = []
    col = importer.model_collection(scene)
    if col is None:
        return [_issue(ERROR, "NO_MODEL", "The model collection is missing")]
    baseline = json.loads(st.baseline_json or "{}")
    settings = json.loads(st.settings_json or "{}")
    objects = list(col.all_objects)
    names_now = {o.name for o in objects}

    # Bones: Unity clips and avatars find bones by name.
    human = set(settings.get("humanBones", []))
    for arm_name, bone_names in baseline.get("bones", {}).items():
        arm = bpy.data.objects.get(arm_name)
        if arm is None or arm not in objects:
            issues.append(_issue(ERROR, "ARMATURE_MISSING",
                                 "Armature '%s' was renamed or removed; Unity clips and the avatar use it" % arm_name,
                                 arm_name))
            continue
        missing = set(bone_names) - set(arm.data.bones.keys())
        if missing:
            avatar = missing & human
            msg = "Bones renamed or removed: %s. Existing clips lose them" % _short(missing)
            if avatar:
                msg += "; the Humanoid avatar maps %s" % _short(avatar, 3)
            issues.append(_issue(ERROR, "BONE_MISSING", msg, arm_name, sorted(missing)[0]))
        for b in arm.data.bones:
            base, suffixed = util.strip_blender_suffix(b.name)
            if suffixed and b.name not in bone_names:
                issues.append(_issue(ERROR, "NAME_SUFFIX", "Bone '%s' got a duplicate-name suffix" % b.name,
                                     arm_name, b.name))

    # Objects: prefabs reference child objects by name.
    old_objects = set(baseline.get("objects", {}))
    gone = old_objects - names_now
    if gone:
        issues.append(_issue(WARNING, "OBJECT_MISSING",
                             "Objects renamed or removed: %s. Prefab overrides on them will be lost" % _short(gone)))
    for o in objects:
        base, suffixed = util.strip_blender_suffix(o.name)
        if suffixed and o.name not in old_objects:
            issues.append(_issue(ERROR, "NAME_SUFFIX",
                                 "'%s' got a duplicate-name suffix; rename it or Unity sees a new object" % o.name,
                                 o.name))

    # Material slots map to Unity materials by name.
    mats_now = {s.material.name for o in objects for s in getattr(o, "material_slots", []) if s.material}
    gone_mats = set(baseline.get("materials", [])) - mats_now
    if gone_mats:
        issues.append(_issue(WARNING, "MATERIAL_MISSING", "Material slots renamed or removed: %s" % _short(gone_mats)))
    for m in mats_now:
        base, suffixed = util.strip_blender_suffix(m)
        if suffixed and m not in baseline.get("materials", []):
            issues.append(_issue(WARNING, "MATERIAL_SUFFIX",
                                 "Material '%s' has a duplicate suffix; Unity will treat it as a new slot" % m))

    # Geometry checks.
    lods = {}
    for o in objects:
        if o.matrix_world.to_3x3().determinant() < 0:
            issues.append(_issue(WARNING, "NEGATIVE_SCALE", "'%s' has a negative scale" % o.name, o.name))
        m = _LOD_RE.match(o.name)
        if m:
            lods.setdefault(m.group(1), set()).add(int(m.group(2)))
        if o.type != "MESH":
            continue
        arm_mods = [md for md in o.modifiers if md.type == "ARMATURE" and md.object is not None]
        if arm_mods:
            deform = set()
            for md in arm_mods:
                deform |= {b.name for b in md.object.data.bones if b.use_deform}
            idx = {vg.index for vg in o.vertex_groups if vg.name in deform}
            over = sum(1 for v in o.data.vertices if sum(1 for g in v.groups if g.group in idx and g.weight > 0) > 4)
            if over:
                issues.append(_issue(WARNING, "WEIGHTS_OVER_4",
                                     "'%s': %d vertices use more than 4 bones; Unity keeps 4 by default" % (o.name, over),
                                     o.name))
        if o.data.shape_keys is not None:
            topo = [md.name for md in o.modifiers if md.type not in _TOPOLOGY_SAFE and md.show_viewport]
            if topo:
                issues.append(_issue(WARNING, "SHAPEKEYS_MODIFIERS",
                                     "'%s' has shape keys and modifiers (%s); the FBX exporter may drop the shape keys"
                                     % (o.name, ", ".join(topo)), o.name))
    for base, levels in lods.items():
        expected = set(range(max(levels) + 1))
        if levels != expected:
            issues.append(_issue(WARNING, "LOD_GAP", "LOD chain '%s' skips %s" % (
                base, ", ".join("LOD%d" % i for i in sorted(expected - levels)))))

    # Clips.
    for clip in st.clips:
        if not clip.loaded:
            continue
        f = st.files[clip.file_index] if 0 <= clip.file_index < len(st.files) else None
        if clip.action is None or actions.fcurve_count(clip.action) == 0:
            if clip.key_action is None:
                issues.append(_issue(ERROR, "CLIP_EMPTY", "Clip '%s' has no keys" % clip.name))
        if f is not None and f.is_new:
            if not clip.name or _BAD_FILE_CHARS.search(clip.name):
                issues.append(_issue(ERROR, "CLIP_NAME", "Clip name '%s' cannot be a file name" % clip.name))
            elif os.path.exists(os.path.join(st.project_root, f.asset_path)) and not f.guid:
                issues.append(_issue(ERROR, "CLIP_EXISTS", "%s already exists; pick another clip name" % f.asset_path))

    # Target files.
    for f in st.files:
        if files_to_write is not None and f.asset_path not in files_to_write:
            continue
        if f.third_party:
            issues.append(_issue(WARNING, "THIRD_PARTY",
                                 "%s is outside your project folders; a package update will overwrite this edit"
                                 % f.asset_path))
        if f.is_new or f.kind == "ANIM":
            continue
        abs_path = os.path.join(st.project_root, f.asset_path)
        if not os.path.exists(abs_path) and not util.find_asset_by_guid(st.project_root, f.guid):
            issues.append(_issue(ERROR, "TARGET_MISSING", "%s was not found by path or GUID" % f.asset_path))
    return issues


def store(scene, issues):
    st = scene.ub_state
    st.issues.clear()
    for i in issues:
        item = st.issues.add()
        item.level = i["level"]
        item.code = i["code"]
        item.message = i["message"]
        item.target = i.get("target", "")
        item.bone = i.get("bone", "")
