"""Content fingerprints. Export writes a file only when its fingerprint changed.

The model fingerprint covers what ends up in the FBX: evaluated geometry at rest (basis shape,
no armature deformation, other modifiers applied), weights, shape keys, material slots, names,
hierarchy, transforms and the rest skeleton. Viewport visibility, selection and the current
animation frame do not count.
"""

import hashlib
from contextlib import contextmanager

import bpy
import numpy as np

from . import actions


def _s(h, value):
    h.update(str(value).encode("utf-8"))
    h.update(b"\x00")


def _arr(h, arr):
    h.update(np.ascontiguousarray(arr).tobytes())
    h.update(b"\x01")


def _mat(h, m):
    _arr(h, np.array([v for row in m for v in row], dtype=np.float64).round(6))


@contextmanager
def rest_evaluation(objects):
    """Evaluate meshes at rest: armature modifiers off, basis shape only."""
    saved = []
    try:
        for ob in objects:
            if ob.type != "MESH":
                continue
            for mod in ob.modifiers:
                if mod.type == "ARMATURE" and mod.show_viewport:
                    saved.append((mod, "show_viewport", True))
                    mod.show_viewport = False
            if ob.data.shape_keys is not None:
                saved.append((ob, "show_only_shape_key", ob.show_only_shape_key))
                saved.append((ob, "active_shape_key_index", ob.active_shape_key_index))
                ob.active_shape_key_index = 0
                ob.show_only_shape_key = True
        bpy.context.view_layer.update()
        yield
    finally:
        for target, attr, value in reversed(saved):
            try:
                setattr(target, attr, value)
            except (ReferenceError, AttributeError):
                pass
        bpy.context.view_layer.update()


def _mesh_geometry(h, ob, depsgraph):
    ob_eval = ob.evaluated_get(depsgraph)
    me = ob_eval.to_mesh()
    try:
        nv, np_, nl = len(me.vertices), len(me.polygons), len(me.loops)
        _s(h, (nv, np_, nl))
        co = np.empty(nv * 3, np.float32)
        me.vertices.foreach_get("co", co)
        _arr(h, co)
        lt = np.empty(np_, np.int32)
        me.polygons.foreach_get("loop_total", lt)
        _arr(h, lt)
        mi = np.empty(np_, np.int32)
        me.polygons.foreach_get("material_index", mi)
        _arr(h, mi)
        vi = np.empty(nl, np.int32)
        me.loops.foreach_get("vertex_index", vi)
        _arr(h, vi)
        nrm = np.empty(nl * 3, np.float32)
        me.corner_normals.foreach_get("vector", nrm)
        _arr(h, nrm.round(5))
        for uv in me.uv_layers:
            _s(h, ("uv", uv.name))
            a = np.empty(nl * 2, np.float32)
            uv.data.foreach_get("uv", a)
            _arr(h, a)
        for ca in me.color_attributes:
            _s(h, ("col", ca.name, ca.domain, ca.data_type))
            a = np.empty(len(ca.data) * 4, np.float32)
            ca.data.foreach_get("color", a)
            _arr(h, a.round(5))
    finally:
        ob_eval.to_mesh_clear()


def _mesh_data(h, ob):
    me = ob.data
    _s(h, [vg.name for vg in ob.vertex_groups])
    weights = []
    for v in me.vertices:
        for g in v.groups:
            if g.weight > 0.0:
                weights.append((v.index, g.group, round(g.weight, 5)))
    _arr(h, np.array(weights, dtype=np.float64))
    keys = me.shape_keys
    if keys is not None:
        for kb in keys.key_blocks:
            _s(h, ("sk", kb.name, kb.relative_key.name if kb.relative_key else "", kb.mute))
            a = np.empty(len(kb.data) * 3, np.float32)
            kb.data.foreach_get("co", a)
            _arr(h, a)
    _s(h, [(s.material.name if s.material else "", s.link) for s in ob.material_slots])


def _idprop_value(v):
    """Plain Python value of a custom property (group/array reprs hold addresses or no values)."""
    if hasattr(v, "to_dict"):
        return v.to_dict()
    if hasattr(v, "to_list"):
        return v.to_list()
    return v


def _object_header(h, ob):
    _s(h, (ob.name, ob.type))
    _s(h, (ob.parent.name if ob.parent else "", ob.parent_type, ob.parent_bone))
    _mat(h, ob.matrix_local)
    props = sorted((k, repr(_idprop_value(ob[k]))) for k in ob.keys() if not k.startswith(("_", "ub_", "cycles")))
    _s(h, props)


def _skeleton(h, ob):
    _s(h, ("arm", ob.name, ob.data.name))
    for b in ob.data.bones:
        _s(h, (b.name, b.parent.name if b.parent else "", b.use_deform, b.use_connect,
               b.inherit_scale, b.use_inherit_rotation, b.use_local_location))
        _mat(h, b.matrix_local)
        _arr(h, np.array(list(b.head_local) + list(b.tail_local), np.float64).round(6))


def model_fingerprints(collection):
    """Returns {'model': hex, 'skeleton': hex, 'objects': {name: hex}}."""
    objects = sorted(set(collection.all_objects), key=lambda o: o.name)
    per_object = {}
    skel = hashlib.sha256()
    with rest_evaluation(objects):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for ob in objects:
            h = hashlib.sha256()
            _object_header(h, ob)
            if ob.type == "MESH":
                _mesh_geometry(h, ob, depsgraph)
                _mesh_data(h, ob)
            elif ob.type == "ARMATURE":
                _skeleton(h, ob)
                _object_header(skel, ob)
                _skeleton(skel, ob)
            elif ob.type in {"CURVE", "SURFACE", "FONT", "META"}:
                _mesh_geometry(h, ob, depsgraph)
            per_object[ob.name] = h.hexdigest()
    total = hashlib.sha256()
    for name in sorted(per_object):
        _s(total, (name, per_object[name]))
    return {"model": total.hexdigest(), "skeleton": skel.hexdigest(), "objects": per_object}


def action_fingerprint(action, settings=None):
    h = hashlib.sha256()
    if action is None:
        return ""
    rows = []
    for slot, bag in actions.iter_channelbags(action):
        for fc in bag.fcurves:
            rows.append((slot.identifier if slot else "", fc.data_path, fc.array_index, fc))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    for slot_id, path, idx, fc in rows:
        _s(h, (slot_id, path, idx, fc.mute, fc.extrapolation))
        n = len(fc.keyframe_points)
        if n:
            for attr in ("co", "handle_left", "handle_right"):
                a = np.empty(n * 2, np.float32)
                fc.keyframe_points.foreach_get(attr, a)
                _arr(h, a)
            _s(h, [(kp.interpolation, kp.easing) for kp in fc.keyframe_points])
        _s(h, [(m.type, m.mute) for m in fc.modifiers])
    _s(h, (getattr(action, "use_frame_range", False), round(action.frame_start, 4),
           round(action.frame_end, 4), getattr(action, "use_cyclic", False)))
    if settings:
        _s(h, sorted(settings.items()))
    return h.hexdigest()
