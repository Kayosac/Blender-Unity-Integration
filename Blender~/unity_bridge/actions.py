"""Helpers for Blender 5 slotted actions (the legacy Action.fcurves API is gone in 5.0)."""

import bpy


def iter_channelbags(action):
    """Yields (slot, channelbag) for every keyframe strip of every layer."""
    if action is None:
        return
    for layer in action.layers:
        for strip in layer.strips:
            if getattr(strip, "type", "KEYFRAME") != "KEYFRAME":
                continue
            for bag in strip.channelbags:
                yield bag.slot, bag


def iter_fcurves(action):
    for _slot, bag in iter_channelbags(action):
        for fc in bag.fcurves:
            yield fc


def fcurve_count(action):
    return sum(1 for _ in iter_fcurves(action))


def ensure_slot(action, id_type, name):
    for slot in action.slots:
        if slot.target_id_type == id_type and slot.name_display == name:
            return slot
    return action.slots.new(id_type=id_type, name=name)


def ensure_channelbag(action, slot):
    from bpy_extras import anim_utils

    return anim_utils.action_ensure_channelbag_for_slot(action, slot)


def pick_slot(action, id_data):
    """The slot an action should use on this ID: same name, else the only one of that type."""
    id_type = "OBJECT" if isinstance(id_data, bpy.types.Object) else "KEY"
    same_type = [s for s in action.slots if s.target_id_type == id_type]
    named = [s for s in same_type if s.name_display == id_data.name]
    if named:
        return named[0]
    if len(same_type) == 1:
        return same_type[0]
    return ensure_slot(action, id_type, id_data.name)


def assign(id_data, action, slot=None):
    """Assigns an action (and a slot) to an ID. Creates the slot when missing."""
    ad = id_data.animation_data or id_data.animation_data_create()
    ad.action = action
    if action is None:
        return None
    if slot is None:
        # Imported actions carry the slot of the temporary object; pick_slot reuses it.
        slot = pick_slot(action, id_data)
    try:
        ad.action_slot = slot
    except (AttributeError, TypeError, RuntimeError):
        pass
    return slot


def slot_of(id_data):
    ad = id_data.animation_data
    if not ad:
        return None
    return getattr(ad, "action_slot", None)


def frame_range(action):
    """The range the clip covers: the manual range when set, otherwise the keys."""
    if action is None:
        return (0.0, 0.0)
    if getattr(action, "use_frame_range", False):
        return (float(action.frame_start), float(action.frame_end))
    lo, hi = None, None
    for fc in iter_fcurves(action):
        if len(fc.keyframe_points) == 0:
            continue
        a, b = fc.range()
        lo = a if lo is None else min(lo, a)
        hi = b if hi is None else max(hi, b)
    if lo is None:
        return (0.0, 0.0)
    return (float(lo), float(hi))


def new_fcurve(bag, data_path, index, group_name):
    fc = bag.fcurves.find(data_path, index=index)
    if fc is None:
        fc = bag.fcurves.new(data_path, index=index)
    if group_name:
        try:
            grp = bag.groups.get(group_name) if hasattr(bag.groups, "get") else None
            if grp is None:
                grp = bag.groups.new(group_name)
            fc.group = grp
        except (AttributeError, RuntimeError, TypeError):
            pass
    return fc


def write_samples(fc, frames, values):
    """Replaces all keys of an F-curve with one key per sample (fast path)."""
    kps = fc.keyframe_points
    if len(kps):
        try:
            kps.clear()
        except AttributeError:
            for i in range(len(kps) - 1, -1, -1):
                kps.remove(kps[i], fast=True)
    kps.add(len(frames))
    flat = [0.0] * (2 * len(frames))
    flat[0::2] = frames
    flat[1::2] = values
    kps.foreach_set("co", flat)
    fc.update()


def is_animated(action, eps=1e-6):
    """True when any curve of the action actually changes value."""
    import numpy as np

    for fc in iter_fcurves(action):
        n = len(fc.keyframe_points)
        if n < 2:
            continue
        co = np.empty(n * 2, np.float32)
        fc.keyframe_points.foreach_get("co", co)
        vals = co[1::2]
        if float(vals.max() - vals.min()) > eps:
            return True
    return False
