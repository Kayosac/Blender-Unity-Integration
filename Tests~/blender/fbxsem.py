"""Reads an FBX into plain data close to what Unity's importer sees, and diffs two such reads.

Works on raw FBX nodes (no Blender import), so the comparison does not inherit importer bias.
Transforms are composed with the FBX formula (pre/post rotation, rotation order) before comparing,
because different exporters spread the same local matrix over different properties.
"""

import math

import numpy as np

KTIME = 46186158000  # FBX_KTIME_V7; files may opt into V8 (see _ktime)
KTIME_V8 = 141120000


def _parse(path):
    from io_scene_fbx import parse_fbx

    root, version = parse_fbx.parse(path)
    return root, version


def _name(prop):
    if isinstance(prop, bytes):
        return prop.split(b"\x00\x01")[0].decode("utf-8", "replace")
    return str(prop)


def _p70(elem):
    out = {}
    for sub in elem.elems:
        if sub.id == b"Properties70":
            for p in sub.elems:
                if p.id == b"P":
                    out[p.props[0].decode("utf-8", "replace")] = list(p.props[4:])
    return out


def _child(elem, name):
    for sub in elem.elems:
        if sub.id == name:
            return sub
    return None


def _children(elem, name):
    return [s for s in elem.elems if s.id == name]


def _arr(elem, name, dtype=np.float64):
    c = _child(elem, name)
    if c is None or not c.props:
        return None
    return np.asarray(c.props[0], dtype=dtype)


def _str(elem, name, default=""):
    c = _child(elem, name)
    if c is None or not c.props:
        return default
    v = c.props[0]
    return v.decode() if isinstance(v, bytes) else str(v)


# --------------------------------------------------------------------------- transforms


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


def euler_matrix(deg, order=0):
    """FBX eEulerXYZ means X applied first, so the matrix is Rz @ Ry @ Rx."""
    rx, ry, rz = (math.radians(v) for v in deg)
    m = {"X": _rx(rx), "Y": _ry(ry), "Z": _rz(rz)}
    seq = _ORDERS.get(int(order), "XYZ")
    out = np.eye(3)
    for axis in seq:
        out = m[axis] @ out
    return out


def compose(props, t=None, r=None, s=None):
    def vec(key, default):
        v = props.get(key)
        return np.array(v[:3], dtype=np.float64) if v else np.array(default, dtype=np.float64)

    T = vec("Lcl Translation", (0, 0, 0)) if t is None else np.asarray(t, dtype=np.float64)
    R = vec("Lcl Rotation", (0, 0, 0)) if r is None else np.asarray(r, dtype=np.float64)
    S = vec("Lcl Scaling", (1, 1, 1)) if s is None else np.asarray(s, dtype=np.float64)
    pre = vec("PreRotation", (0, 0, 0))
    post = vec("PostRotation", (0, 0, 0))
    roff = vec("RotationOffset", (0, 0, 0))
    rpiv = vec("RotationPivot", (0, 0, 0))
    soff = vec("ScalingOffset", (0, 0, 0))
    spiv = vec("ScalingPivot", (0, 0, 0))
    order = int(props.get("RotationOrder", [0])[0]) if props.get("RotationOrder") else 0
    active = int(props.get("RotationActive", [0])[0]) if props.get("RotationActive") else 0
    if not active:
        pre = post = np.zeros(3)
        order = 0

    def h(m3=None, tr=None):
        m = np.eye(4)
        if m3 is not None:
            m[:3, :3] = m3
        if tr is not None:
            m[:3, 3] = tr
        return m

    M = (h(tr=T) @ h(tr=roff) @ h(tr=rpiv) @ h(euler_matrix(pre)) @ h(euler_matrix(R, order))
         @ np.linalg.inv(h(euler_matrix(post))) @ h(tr=-rpiv) @ h(tr=soff) @ h(tr=spiv)
         @ h(np.diag(S)) @ h(tr=-spiv))
    return M


# --------------------------------------------------------------------------- read


def _expand(layer, values_name, index_name, width, poly_vi, n_ctrl):
    vals = _arr(layer, values_name)
    if vals is None:
        return None
    vals = vals.reshape(-1, width)
    mapping = _str(layer, b"MappingInformationType")
    ref = _str(layer, b"ReferenceInformationType")
    idx = _arr(layer, index_name, np.int64) if ref == "IndexToDirect" else None
    n_pv = len(poly_vi)
    if mapping in ("ByPolygonVertex",):
        if idx is not None:
            return vals[idx]
        return vals[:n_pv]
    if mapping in ("ByVertice", "ByVertex"):
        src = vals[idx] if idx is not None else vals
        return src[poly_vi]
    if mapping == "AllSame":
        return np.repeat(vals[:1], n_pv, axis=0)
    return vals


def _ktime(root, version):
    """KTime units per second, chosen the way io_scene_fbx's importer does (TCDefinition)."""
    ktime = KTIME_V8 if version >= 8000 else KTIME
    header = _child(root, b"FBXHeaderExtension")
    hv = _child(header, b"FBXHeaderVersion") if header is not None else None
    if hv is not None and hv.props and hv.props[0] >= 1004:
        flags = _child(header, b"OtherFlags")
        tc = _child(flags, b"TCDefinition") if flags is not None else None
        if tc is not None and tc.props:
            ktime = {0: KTIME_V8, 127: KTIME}.get(tc.props[0], KTIME_V8)
    return ktime


def read(path):
    root, version = _parse(path)
    ktime = _ktime(root, version)
    out = {"global": {}, "models": {}, "geometry": {}, "skins": {}, "shapes": {}, "takes": {}}
    gs = _child(root, b"GlobalSettings")
    if gs is not None:
        p = _p70(gs)
        for k in ("UpAxis", "UpAxisSign", "FrontAxis", "FrontAxisSign", "CoordAxis", "CoordAxisSign",
                  "UnitScaleFactor", "TimeMode", "CustomFrameRate"):
            if k in p:
                out["global"][k] = p[k][0]
    objs = {}
    objects = _child(root, b"Objects")
    for e in objects.elems if objects is not None else []:
        if not e.props:
            continue
        uid = e.props[0]
        objs[uid] = e
    conns = []
    cel = _child(root, b"Connections")
    for c in cel.elems if cel is not None else []:
        if c.id == b"C":
            kind = c.props[0].decode()
            conns.append((kind, c.props[1], c.props[2], c.props[3].decode() if len(c.props) > 3 else ""))
    parent_of = {}
    children_of = {}
    for kind, child, parent, prop in conns:
        children_of.setdefault(parent, []).append((child, kind, prop))
        if kind == "OO":
            parent_of.setdefault(child, []).append(parent)

    def typ(uid):
        e = objs.get(uid)
        return e.id.decode() if e is not None else ("root" if uid == 0 else "?")

    fps = 30.0
    tm = out["global"].get("TimeMode")
    try:
        from io_scene_fbx import fbx_utils

        table = {eid: val for val, eid in fbx_utils.FBX_FRAMERATES[1:]}
        fps = float(table.get(tm) or out["global"].get("CustomFrameRate") or 30.0)
    except Exception:  # noqa: BLE001
        pass
    out["fps"] = fps

    model_names = {}
    for uid, e in objs.items():
        if e.id == b"Model":
            nm = _name(e.props[1])
            model_names[uid] = nm
            props = _p70(e)
            parent = next((p for p in parent_of.get(uid, []) if typ(p) in ("Model", "root")), 0)
            out["models"][nm] = {
                "type": e.props[2].decode() if len(e.props) > 2 else "",
                "parent": model_names.get(parent, "") if parent else "",
                "_parent_uid": parent,
                "local": compose(props),
                "props": props,
            }
    for m in out["models"].values():
        if m["_parent_uid"]:
            m["parent"] = model_names.get(m["_parent_uid"], "?")
        del m["_parent_uid"]

    # Geometry attached to models.
    for uid, e in objs.items():
        if e.id != b"Geometry":
            continue
        kind = e.props[2] if len(e.props) > 2 else b""
        owners = [model_names[p] for p in parent_of.get(uid, []) if p in model_names]
        if kind == b"Mesh" and owners:
            verts = _arr(e, b"Vertices")
            pvi_raw = _arr(e, b"PolygonVertexIndex", np.int64)
            if verts is None or pvi_raw is None:
                continue
            pvi = np.where(pvi_raw < 0, ~pvi_raw, pvi_raw)
            g = {"vertices": verts.reshape(-1, 3), "polys": pvi_raw, "normals": None, "uvs": {}, "colors": {},
                 "materials": None}
            n_ctrl = len(g["vertices"])
            for layer in _children(e, b"LayerElementNormal"):
                g["normals"] = _expand(layer, b"Normals", b"NormalsIndex", 3, pvi, n_ctrl)
                break
            for i, layer in enumerate(_children(e, b"LayerElementUV")):
                g["uvs"][_str(layer, b"Name") or str(i)] = _expand(layer, b"UV", b"UVIndex", 2, pvi, n_ctrl)
            for i, layer in enumerate(_children(e, b"LayerElementColor")):
                g["colors"][_str(layer, b"Name") or str(i)] = _expand(layer, b"Colors", b"ColorIndex", 4, pvi, n_ctrl)
            for layer in _children(e, b"LayerElementMaterial"):
                mats = _arr(layer, b"Materials", np.int64)
                g["materials"] = mats
                break
            out["geometry"][owners[0]] = g
            # Skin and blend shapes hang off the geometry.
            for child, kind2, _prop in children_of.get(uid, []):
                ce = objs.get(child)
                if ce is None or ce.id != b"Deformer":
                    continue
                dkind = ce.props[2] if len(ce.props) > 2 else b""
                if dkind == b"Skin":
                    clusters = {}
                    for cl, _k, _p in children_of.get(child, []):
                        cle = objs.get(cl)
                        if cle is None:
                            continue
                        bone = next((model_names[x] for x, _k2, _p2 in children_of.get(cl, []) if x in model_names), None)
                        idx = _arr(cle, b"Indexes", np.int64)
                        w = _arr(cle, b"Weights")
                        clusters[bone or "?"] = {
                            "weights": dict(zip(idx.tolist(), w.tolist())) if idx is not None else {},
                            "link": _arr(cle, b"TransformLink"),
                        }
                    out["skins"][owners[0]] = clusters
                elif dkind == b"BlendShape":
                    for ch, _k, _p in children_of.get(child, []):
                        che = objs.get(ch)
                        if che is None:
                            continue
                        ch_name = _name(che.props[1])
                        for sh, _k2, _p2 in children_of.get(ch, []):
                            she = objs.get(sh)
                            if she is None or she.id != b"Geometry":
                                continue
                            idx = _arr(she, b"Indexes", np.int64)
                            dv = _arr(she, b"Vertices")
                            out["shapes"][(owners[0], ch_name)] = {
                                "indexes": idx, "deltas": dv.reshape(-1, 3) if dv is not None else None,
                                "normals": (_arr(she, b"Normals").reshape(-1, 3)
                                            if _arr(she, b"Normals") is not None else None),
                            }

    # Animation: stack -> layer -> curve node -> (model, property) -> curves.
    for uid, e in objs.items():
        if e.id != b"AnimationStack":
            continue
        p = _p70(e)
        take = {"start": p.get("LocalStart", [0])[0] / ktime * fps,
                "stop": p.get("LocalStop", [0])[0] / ktime * fps, "channels": {}}
        for layer, _k, _p in children_of.get(uid, []):
            if typ(layer) != "AnimationLayer":
                continue
            for node, _k2, _p2 in children_of.get(layer, []):
                if typ(node) != "AnimationCurveNode":
                    continue
                target = None
                for kind, child, parent, prop in conns:
                    if child == node and kind == "OP" and parent in model_names:
                        target = (model_names[parent], prop)
                        break
                if target is None:
                    continue
                curves = {}
                for cv, kind3, prop3 in children_of.get(node, []):
                    ce = objs.get(cv)
                    if ce is None or ce.id != b"AnimationCurve":
                        continue
                    times = _arr(ce, b"KeyTime", np.int64)
                    vals = _arr(ce, b"KeyValueFloat")
                    curves[prop3] = (times / ktime * fps if times is not None else np.zeros(0),
                                     vals if vals is not None else np.zeros(0))
                take["channels"][target] = curves
        out["takes"][_name(e.props[1])] = take
    return out


# --------------------------------------------------------------------------- diff


def _eval(curve, t, default):
    if curve is None:
        return default
    times, vals = curve
    if len(times) == 0:
        return default
    return float(np.interp(t, times, vals))


def _pose_at(model, take, t):
    ch = take["channels"]
    props = model["props"]

    def channel(prop, default):
        curves = ch.get((model["name"], prop))
        base = props.get(prop)
        base = list(base[:3]) if base else list(default)
        if not curves:
            return base
        return [_eval(curves.get("d|" + a), t, base[i]) for i, a in enumerate("XYZ")]

    return compose(props, channel("Lcl Translation", (0, 0, 0)), channel("Lcl Rotation", (0, 0, 0)),
                   channel("Lcl Scaling", (1, 1, 1)))


def _mat_err(a, b, unit):
    """Errors relative to the values' own size: float32 files keep ~7 significant digits."""
    rot = float(np.abs(a[:3, :3] - b[:3, :3]).max()) / max(1.0, float(np.abs(a[:3, :3]).max()))
    tr = float(np.abs(a[:3, 3] - b[:3, 3]).max()) / max(unit, 1e-9, float(np.abs(a[:3, 3]).max()))
    return rot, tr


def _unit_rows(n):
    length = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.where(length > 1e-12, length, 1.0)


def diff(a, b, take_offset=None):
    """Returns a dict of problems and maximum errors. a = before, b = after."""
    rep = {"structure": [], "max": {}}

    def bump(key, v):
        rep["max"][key] = max(rep["max"].get(key, 0.0), float(v))

    for k in ("UpAxis", "UpAxisSign", "FrontAxis", "FrontAxisSign", "CoordAxis", "CoordAxisSign",
              "UnitScaleFactor", "TimeMode"):
        if a["global"].get(k) != b["global"].get(k):
            rep["structure"].append("GlobalSettings.%s: %s -> %s" % (k, a["global"].get(k), b["global"].get(k)))
    unit = 1.0
    an, bn = set(a["models"]), set(b["models"])
    for n in sorted(an - bn):
        rep["structure"].append("node missing: " + n)
    for n in sorted(bn - an):
        rep["structure"].append("node added: " + n)
    for n in sorted(an & bn):
        ma, mb = a["models"][n], b["models"][n]
        if ma["parent"] != mb["parent"]:
            rep["structure"].append("parent of %s: %s -> %s" % (n, ma["parent"], mb["parent"]))
        r, t = _mat_err(ma["local"], mb["local"], unit)
        bump("node_rot", r)
        bump("node_pos", t)
    for n in sorted(set(a["geometry"]) & set(b["geometry"])):
        ga, gb = a["geometry"][n], b["geometry"][n]
        if ga["vertices"].shape != gb["vertices"].shape or not np.array_equal(ga["polys"], gb["polys"]):
            rep["structure"].append("topology of %s: %s/%d -> %s/%d" % (
                n, ga["vertices"].shape, len(ga["polys"]), gb["vertices"].shape, len(gb["polys"])))
            continue
        if len(ga["vertices"]):
            size = max(1.0, float(np.abs(ga["vertices"]).max()))
            bump("vertex", np.abs(ga["vertices"] - gb["vertices"]).max() / size)
        if ga["normals"] is not None and gb["normals"] is not None and ga["normals"].shape == gb["normals"].shape:
            # Unity normalises normals on import; some files store unnormalised ones.
            # Loops whose source normal is not unit length come from degenerate faces; Blender
            # recomputes those, so they are counted but not compared.
            ok = np.abs(np.linalg.norm(ga["normals"], axis=1) - 1.0) < 0.05
            rep.setdefault("info", []).append("%s: %d degenerate normals skipped" % (n, int((~ok).sum()))) if (~ok).any() else None
            if ok.any():
                bump("normal", np.abs(_unit_rows(ga["normals"][ok]) - _unit_rows(gb["normals"][ok])).max())
        elif (ga["normals"] is None) != (gb["normals"] is None):
            rep["structure"].append("normals of %s present/absent changed" % n)
        for key in ("uvs", "colors"):
            # Unity reads layers by position; their names do not matter.
            la, lb = list(ga[key].values()), list(gb[key].values())
            if len(la) != len(lb):
                rep["structure"].append("%s layers of %s: %d -> %d" % (key, n, len(la), len(lb)))
            for lname, (xa, xb) in enumerate(zip(la, lb)):
                if xa is None or xb is None or xa.shape != xb.shape:
                    rep["structure"].append("%s %s of %s changed shape" % (key, lname, n))
                    continue
                bump("uv" if key == "uvs" else "color", np.abs(xa - xb).max() if len(xa) else 0)
        if ga["materials"] is not None and gb["materials"] is not None:
            if len(ga["materials"]) == len(gb["materials"]) and not np.array_equal(ga["materials"], gb["materials"]):
                rep["structure"].append("material indices of %s changed" % n)
    for mesh in sorted(set(a["skins"]) & set(b["skins"])):
        sa, sb = a["skins"][mesh], b["skins"][mesh]
        if set(sa) != set(sb):
            rep["structure"].append("skin bones of %s: -%s +%s" % (mesh, sorted(set(sa) - set(sb)),
                                                                  sorted(set(sb) - set(sa))))
        for bone in set(sa) & set(sb):
            wa, wb = sa[bone]["weights"], sb[bone]["weights"]
            keys = set(wa) | set(wb)
            if keys:
                bump("weight", max(abs(wa.get(k, 0.0) - wb.get(k, 0.0)) for k in keys))
            if sa[bone]["link"] is not None and sb[bone]["link"] is not None:
                la, lb = sa[bone]["link"], sb[bone]["link"]
                bump("bind", np.abs(la - lb).max() / max(1.0, float(np.abs(la).max())))
    for key in sorted(set(a["shapes"]) | set(b["shapes"])):
        if key not in a["shapes"] or key not in b["shapes"]:
            rep["structure"].append("blend shape %s/%s present/absent changed" % key)
            continue
        xa, xb = a["shapes"][key], b["shapes"][key]
        da = dict(zip(xa["indexes"].tolist(), map(tuple, xa["deltas"]))) if xa["indexes"] is not None else {}
        db = dict(zip(xb["indexes"].tolist(), map(tuple, xb["deltas"]))) if xb["indexes"] is not None else {}
        for i in set(da) | set(db):
            va, vb = np.array(da.get(i, (0, 0, 0))), np.array(db.get(i, (0, 0, 0)))
            bump("shape", np.abs(va - vb).max())
    # Takes: compare composed per-frame local matrices at the source's key times.
    for take in sorted(set(a["takes"]) | set(b["takes"])):
        if take not in a["takes"] or take not in b["takes"]:
            rep["structure"].append("take %s present/absent changed" % take)
            continue
        ta, tb = a["takes"][take], b["takes"][take]
        shift = (tb["start"] - ta["start"]) if take_offset is None else take_offset
        bump("take_length", abs((ta["stop"] - ta["start"]) - (tb["stop"] - tb["start"])))
        nodes = {n for (n, _p) in ta["channels"]} | {n for (n, _p) in tb["channels"]}
        for n in sorted(nodes):
            if n not in a["models"] or n not in b["models"]:
                continue
            ma = dict(a["models"][n], name=n)
            mb = dict(b["models"][n], name=n)
            times = set()
            for (nn, _p), curves in ta["channels"].items():
                if nn == n:
                    for tt, _v in curves.values():
                        times |= set(np.round(tt, 4).tolist())
            for t in sorted(times):
                r, tr = _mat_err(_pose_at(ma, ta, t), _pose_at(mb, tb, t + shift), unit)
                bump("anim_rot", r)
                bump("anim_pos", tr)
    return rep


TOLERANCES = {
    "node_rot": 5e-4, "node_pos": 1e-4, "vertex": 1e-6, "normal": 2e-2, "uv": 1e-5, "color": 1.0 / 255 + 1e-6,
    "weight": 1e-4, "bind": 1e-4, "shape": 1e-4, "take_length": 1e-3, "anim_rot": 1e-4, "anim_pos": 1e-3,
}


def verdict(rep):
    bad = [k for k, v in rep["max"].items() if v > TOLERANCES.get(k, 1e-4)]
    return (not rep["structure"]) and not bad, bad
