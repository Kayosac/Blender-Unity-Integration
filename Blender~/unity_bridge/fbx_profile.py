"""Reads what we need from an FBX file without importing it.

The "profile" (axes, units, frame rate) is reproduced on export so Unity sees the same
transforms as before. Take (AnimationStack) names and start times are needed to keep
Unity's clip splits pointing at the right data.
"""

from . import util

KTIME_PER_SECOND = 46186158000  # FBX_KTIME_V7; Blender's exporter writes this
KTIME_V8 = 141120000  # newer FBX SDKs may opt into this time base (see _ktime)

# (axis_up, axis_forward) for Blender's exporter, keyed by FBX GlobalSettings axes.
# Filled lazily from io_scene_fbx so we always match the exporter that is installed.
_AXES_FROM_FBX = None

DEFAULT_PROFILE = {
    "axis_up": "Y",
    "axis_forward": "-Z",
    "unit_scale_factor": 100.0,  # FBX UnitScaleFactor: centimetres per file unit (100 = metres)
    "fps": 30.0,
    "is_binary": True,
    "source": "default",
}


def _fbx_modules():
    from io_scene_fbx import parse_fbx, fbx_utils  # bundled with Blender

    return parse_fbx, fbx_utils


def _axes_table():
    global _AXES_FROM_FBX
    if _AXES_FROM_FBX is None:
        _parse, fbx_utils = _fbx_modules()
        _AXES_FROM_FBX = {v: k for k, v in fbx_utils.RIGHT_HAND_AXES.items()}
    return _AXES_FROM_FBX


def _fps_from_time_mode(time_mode, custom_fps):
    try:
        _parse, fbx_utils = _fbx_modules()
        table = {eid: val for val, eid in fbx_utils.FBX_FRAMERATES[1:]}
        fps = table.get(time_mode)
        if fps and fps > 0:
            return float(fps)
    except Exception:  # noqa: BLE001 - table layout may change between versions
        pass
    return float(custom_fps or 30.0)


def _decode(b):
    if isinstance(b, bytes):
        return b.decode("utf-8", "replace")
    return str(b)


def _object_name(prop):
    """FBX stores 'Name\\x00\\x01Class' in binary files."""
    if isinstance(prop, bytes):
        return prop.split(b"\x00\x01")[0].decode("utf-8", "replace")
    return str(prop)


def _props70(elem):
    out = {}
    if elem is None:
        return out
    for sub in elem.elems:
        if sub.id == b"Properties70":
            for p in sub.elems:
                if p.id == b"P" and p.props:
                    out[_decode(p.props[0])] = list(p.props[4:])
    return out


def _find(elem, name):
    for sub in elem.elems:
        if sub.id == name:
            return sub
    return None


def _ktime(root, version):
    """KTime units per second, chosen the way io_scene_fbx's importer does (TCDefinition)."""
    ktime = KTIME_V8 if version >= 8000 else KTIME_PER_SECOND
    header = _find(root, b"FBXHeaderExtension")
    hv = _find(header, b"FBXHeaderVersion") if header is not None else None
    if hv is not None and hv.props and hv.props[0] >= 1004:
        flags = _find(header, b"OtherFlags")
        tc = _find(flags, b"TCDefinition") if flags is not None else None
        if tc is not None and tc.props:
            ktime = {0: KTIME_V8, 127: KTIME_PER_SECOND}.get(tc.props[0], KTIME_V8)
    return ktime


def read(path):
    """Returns a dict: profile + takes + structure summary. Never raises for a readable file."""
    result = dict(DEFAULT_PROFILE)
    result.update({"takes": [], "has_mesh": False, "has_skin": False, "bone_count": 0, "source": "fbx"})
    try:
        parse_fbx, _ = _fbx_modules()
        root, version = parse_fbx.parse(path)
    except Exception as e:  # noqa: BLE001
        # ASCII FBX or a damaged file: the C++ importer may still read it.
        util.log("Profile: could not parse", path, "-", e)
        result["is_binary"] = False
        result["source"] = "default"
        return result

    gs = _props70(_find(root, b"GlobalSettings"))

    def num(key, default):
        v = gs.get(key)
        return v[0] if v else default

    up = (int(num("UpAxis", 1)), int(num("UpAxisSign", 1)))
    front = (int(num("FrontAxis", 2)), int(num("FrontAxisSign", 1)))
    coord = (int(num("CoordAxis", 0)), int(num("CoordAxisSign", 1)))
    try:
        axis_up, axis_forward = _axes_table().get((up, front, coord), ("Y", "-Z"))
    except Exception:  # noqa: BLE001
        axis_up, axis_forward = "Y", "-Z"
    result["axis_up"] = axis_up
    result["axis_forward"] = axis_forward
    result["unit_scale_factor"] = float(num("UnitScaleFactor", 1.0))
    result["fps"] = _fps_from_time_mode(int(num("TimeMode", 0)), num("CustomFrameRate", 30.0))

    ktime = _ktime(root, version)
    objects = _find(root, b"Objects")
    if objects is not None:
        for e in objects.elems:
            if e.id == b"Geometry" and len(e.props) > 2 and e.props[2] == b"Mesh":
                result["has_mesh"] = True
            elif e.id == b"Deformer" and len(e.props) > 2 and e.props[2] == b"Skin":
                result["has_skin"] = True
            elif e.id == b"Model" and len(e.props) > 2 and e.props[2] == b"LimbNode":
                result["bone_count"] += 1
            elif e.id == b"AnimationStack":
                name = _object_name(e.props[1]) if len(e.props) > 1 else "Take"
                p = _props70(e)
                start = p.get("LocalStart", [0])[0]
                stop = p.get("LocalStop", [0])[0]
                fps = result["fps"]
                result["takes"].append({
                    "name": name,
                    "start_frame": round(start / ktime * fps, 4),
                    "end_frame": round(stop / ktime * fps, 4),
                })
    return result
