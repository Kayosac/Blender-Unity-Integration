"""Node transforms and skin clusters straight from an FBX file (no import).

Used to correct what Blender's importer gets wrong and to write back exactly the skin
clusters the source had.
"""

import math

import numpy as np

from . import util


def _parse(path):
    from io_scene_fbx import parse_fbx

    root, _version = parse_fbx.parse(path)
    return root


def _name(prop):
    if isinstance(prop, bytes):
        return prop.split(b"\x00\x01")[0].decode("utf-8", "replace")
    return str(prop)


def _p70(elem):
    out = {}
    for sub in elem.elems:
        if sub.id == b"Properties70":
            for p in sub.elems:
                if p.id == b"P" and p.props:
                    out[p.props[0].decode("utf-8", "replace")] = list(p.props[4:])
    return out


def _child(elem, name):
    for sub in elem.elems:
        if sub.id == name:
            return sub
    return None


def _rx(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _ry(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


_ORDERS = {0: "XYZ", 1: "XZY", 2: "YZX", 3: "YXZ", 4: "ZXY", 5: "ZYX"}


def _euler(deg, order=0):
    rot = {"X": _rx(math.radians(deg[0])), "Y": _ry(math.radians(deg[1])), "Z": _rz(math.radians(deg[2]))}
    out = np.eye(3)
    for axis in _ORDERS.get(int(order), "XYZ"):
        out = rot[axis] @ out
    return out


def _h(m3=None, tr=None):
    m = np.eye(4)
    if m3 is not None:
        m[:3, :3] = m3
    if tr is not None:
        m[:3, 3] = tr
    return m


def compose(props):
    def vec(key, default):
        v = props.get(key)
        return np.array(v[:3], dtype=np.float64) if v else np.array(default, dtype=np.float64)

    t = vec("Lcl Translation", (0, 0, 0))
    r = vec("Lcl Rotation", (0, 0, 0))
    s = vec("Lcl Scaling", (1, 1, 1))
    pre, post = vec("PreRotation", (0, 0, 0)), vec("PostRotation", (0, 0, 0))
    roff, rpiv = vec("RotationOffset", (0, 0, 0)), vec("RotationPivot", (0, 0, 0))
    soff, spiv = vec("ScalingOffset", (0, 0, 0)), vec("ScalingPivot", (0, 0, 0))
    order = int(props["RotationOrder"][0]) if props.get("RotationOrder") else 0
    active = int(props["RotationActive"][0]) if props.get("RotationActive") else 0
    if not active:
        pre, post, order = np.zeros(3), np.zeros(3), 0
    return (_h(tr=t) @ _h(tr=roff) @ _h(tr=rpiv) @ _h(_euler(pre)) @ _h(_euler(r, order))
            @ np.linalg.inv(_h(_euler(post))) @ _h(tr=-rpiv) @ _h(tr=soff) @ _h(tr=spiv)
            @ _h(np.diag(s)) @ _h(tr=-spiv))


def read(path):
    """{'nodes': {name: {'type', 'parent', 'world'}}, 'clusters': {mesh model name: [bone names]}}.

    World matrices are in file axes and units. Never raises: returns empty dicts on failure.
    """
    out = {"nodes": {}, "clusters": {}}
    try:
        root = _parse(path)
    except Exception as e:  # noqa: BLE001
        util.log("Could not read nodes from", path, "-", e)
        return out
    objects = _child(root, b"Objects")
    conns = _child(root, b"Connections")
    if objects is None or conns is None:
        return out
    objs = {e.props[0]: e for e in objects.elems if e.props}
    parent_of, children_of = {}, {}
    for c in conns.elems:
        if c.id != b"C" or len(c.props) < 3:
            continue
        kind, child, parent = c.props[0], c.props[1], c.props[2]
        children_of.setdefault(parent, []).append(child)
        if kind == b"OO":
            parent_of.setdefault(child, []).append(parent)

    models = {uid: e for uid, e in objs.items() if e.id == b"Model"}
    local = {uid: compose(_p70(e)) for uid, e in models.items()}
    names = {uid: _name(e.props[1]) for uid, e in models.items()}

    def model_parent(uid):
        return next((p for p in parent_of.get(uid, []) if p in models), None)

    world = {}

    def get_world(uid, depth=0):
        if uid in world:
            return world[uid]
        p = model_parent(uid)
        m = local[uid] if p is None or depth > 512 else get_world(p, depth + 1) @ local[uid]
        world[uid] = m
        return m

    for uid, e in models.items():
        p = model_parent(uid)
        out["nodes"][names[uid]] = {
            "type": e.props[2].decode() if len(e.props) > 2 and isinstance(e.props[2], bytes) else "",
            "parent": names.get(p, "") if p else "",
            "world": get_world(uid),
            "mirrored": bool(np.linalg.det(local[uid][:3, :3]) < 0),
        }

    for uid, e in objs.items():
        if e.id != b"Geometry":
            continue
        owners = [names[p] for p in parent_of.get(uid, []) if p in names]
        if not owners:
            continue
        for child in children_of.get(uid, []):
            skin = objs.get(child)
            if skin is None or skin.id != b"Deformer" or len(skin.props) < 3 or skin.props[2] != b"Skin":
                continue
            bones = []
            for cl in children_of.get(child, []):
                for b in children_of.get(cl, []):
                    if b in names:
                        bones.append(names[b])
            for owner in owners:
                out["clusters"][owner] = sorted(set(bones))
    return out
