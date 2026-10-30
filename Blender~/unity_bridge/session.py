"""Session lifecycle: start from a Unity manifest, save, lock, pin/unpin, state for Unity."""

import atexit
import json
import os
import time
import traceback

import bpy
from bpy.app.handlers import persistent

from . import clips, exporter, fbx_profile, fingerprint, importer, paths as bpaths, util

_LOCK = {"path": None}
_GRACE = {"until": 0.0}  # ignore depsgraph noise right after we change the scene ourselves


def quiet_period(seconds=2.0):
    _GRACE["until"] = time.time() + seconds
DEFAULT_SETTINGS = {"historySize": 10, "historyMaxMB": 2048, "clipsFolder": "Animations",
                    "colliderSuffix": "_COL", "autoPinClips": False, "humanBones": []}


# --------------------------------------------------------------------------- helpers


def bridge_paths(st):
    return bpaths.BridgePaths(st.project_root, st.art_source or "ArtSource")


def save_blend(filepath=None):
    if filepath:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        importer.call_op(bpy.ops.wm.save_as_mainfile, filepath=filepath, check_existing=False)
    elif bpy.data.filepath:
        importer.call_op(bpy.ops.wm.save_mainfile)


def write_lock(paths, guid):
    if not guid:
        return
    p = paths.lock_file(guid)
    util.write_json_atomic(p, {"pid": os.getpid(), "blend": bpy.data.filepath, "startedUtc": util.now_utc_iso()})
    _LOCK["path"] = p


def release_lock():
    p = _LOCK.get("path")
    if p and os.path.exists(p):
        try:
            data = util.read_json(p, {}) or {}
            if data.get("pid") == os.getpid():
                os.remove(p)
        except OSError:
            pass
    _LOCK["path"] = None


atexit.register(release_lock)


def _file_entry(st, info):
    f = st.files.add()
    f.guid = info.get("guid", "") or ""
    f.asset_path = info.get("assetPath", "")
    f.kind = info.get("kind") or "MODEL"
    f.has_mesh = bool(info.get("hasMesh", False))
    f.is_new = bool(info.get("isNew", False))
    f.rig_guid = info.get("rigGuid", "") or ""
    f.third_party = bool(info.get("thirdParty", False))
    f.path_prefix = info.get("pathPrefix", "") or ""
    f.sample_path = info.get("samplePath", "") or ""
    return f


def _clip_entries_for_file(st, fi, info):
    """One clip per take. Loop/root motion are managed only for one-take, one-clip files."""
    takes = info.get("takes", [])
    # Indices, not items: st.clips.add() re-allocates the collection and invalidates references.
    existing = {c.name: i for i, c in enumerate(st.clips) if c.file_index == fi}
    for t in takes:
        ci = existing.get(t["name"])
        if ci is None:
            clip = st.clips.add()
            clip.file_index = fi
            clip.name = t["name"]
            ci = existing[t["name"]] = len(st.clips) - 1
        clip = st.clips[ci]
        uclips = t.get("unityClips", [])
        clip.unity_clips = ", ".join(u["name"] for u in uclips)
        kind = info.get("kind")
        if kind == "CLIP" and len(takes) == 1 and len(uclips) == 1:
            clip.managed = True
            clip.loop = bool(uclips[0].get("loop", False))
            clip.root_motion = uclips[0].get("rootMotion", "CUSTOM") or "CUSTOM"
        clip.orig_start = float(t.get("startFrame", clip.orig_start))


def _apply_common(scene, m):
    st = scene.ub_state
    st.session_id = m.get("sessionId", "")
    st.project_root = m.get("projectRoot", st.project_root)
    st.art_source = m.get("artSource", "ArtSource")
    settings = dict(DEFAULT_SETTINGS)
    settings.update(m.get("settings", {}))
    st.settings_json = json.dumps(settings)
    st.pinned = bool(m.get("pinned", False))
    model = m.get("model", {})
    st.model_guid = model.get("guid", "") or st.model_guid
    st.model_path = model.get("assetPath", st.model_path)
    st.model_name = model.get("name", st.model_name)


# --------------------------------------------------------------------------- fresh / refresh


def fresh_session(m):
    scene = importer.clear_scene()
    _apply_common(scene, m)
    st = scene.ub_state
    paths = bridge_paths(st)
    paths.ensure()
    model = m["model"]
    name = model.get("name") or os.path.splitext(os.path.basename(model["assetPath"]))[0]
    if model.get("isNew"):
        profile = dict(fbx_profile.DEFAULT_PROFILE)
        importer.setup_scene(scene, profile)
        col = bpy.data.collections.new(name)
        scene.collection.children.link(col)
        takes = []
    else:
        abs_path = os.path.join(st.project_root, model["assetPath"])
        col, profile, takes = importer.fresh_model(scene, abs_path, name, m.get("materials"))
    importer.ensure_helpers_collection(scene)
    st.collection_name = col.name
    st.profile_json = json.dumps({k: profile[k] for k in ("axis_up", "axis_forward", "unit_scale_factor", "fps")})

    model_fi = None
    for info in m.get("files", []):
        f = _file_entry(st, info)
        fi = len(st.files) - 1
        if f.kind == "MODEL":
            model_fi = fi
            f.loaded = True
            f.sha_sync = info.get("sha256", "")
            f.profile_json = st.profile_json
        _clip_entries_for_file(st, fi, info)
    if model_fi is None:
        f = _file_entry(st, {"guid": st.model_guid, "assetPath": st.model_path, "kind": "MODEL",
                             "isNew": bool(model.get("isNew")), "hasMesh": True})
        model_fi = len(st.files) - 1
        f.loaded = True
        f.profile_json = st.profile_json

    by_name = {c.name: i for i, c in enumerate(st.clips) if c.file_index == model_fi}
    for t in takes:
        ci = by_name.get(t["take"])
        if ci is None:
            clip = st.clips.add()
            clip.file_index = model_fi
            clip.name = t["take"]
            ci = by_name[t["take"]] = len(st.clips) - 1
        clip = st.clips[ci]
        clip.action = t["action"]
        clip.key_action = t["key_action"]
        clip.key_target = t["key_target"]
        clip.orig_start = float(t.get("start", 0.0))
        clip.loaded = True

    baseline = importer.capture_baseline(col)
    baseline["clusters"] = profile.get("clusters", {})
    baseline["mirrored"] = profile.get("mirrored", [])
    baseline["dropped"] = {t["take"]: t["dropped"] for t in takes if t.get("dropped")}
    st.baseline_json = json.dumps(baseline)
    fps = fingerprint.model_fingerprints(col)
    st.model_fp_sync = fps["model"]
    st.skeleton_fp_sync = fps["skeleton"]
    for c in st.clips:
        if c.loaded:
            c.fp_sync = exporter.clip_fp(c)

    _focus(scene, m)
    blend_abs = paths.abs(m["blendPath"])
    save_blend(blend_abs)
    write_lock(paths, st.model_guid)
    bpaths.update_state(paths, st.model_guid, assetPath=st.model_path, blendPath=m["blendPath"],
                        pinned=st.pinned, fbxSha256AtSync=model.get("sha256", ""), dirty=False,
                        lastOpenedUtc=util.now_utc_iso())


def refresh_session(m):
    """The .blend was opened from disk; bring paths, settings and the clip list up to date."""
    scene = bpy.context.scene
    st = scene.ub_state
    if st.model_guid and m["model"].get("guid") and st.model_guid != m["model"]["guid"]:
        util.log("Warning: this .blend belongs to another model (%s)" % st.model_guid)
    _apply_common(scene, m)
    paths = bridge_paths(st)
    paths.ensure()
    for info in m.get("files", []):
        f = next((x for x in st.files if (info.get("guid") and x.guid == info["guid"])
                  or x.asset_path == info.get("assetPath")), None)
        if f is None:
            f = _file_entry(st, info)
        else:
            f.asset_path = info.get("assetPath", f.asset_path)
            f.third_party = bool(info.get("thirdParty", f.third_party))
            f.path_prefix = info.get("pathPrefix", f.path_prefix) or ""
            f.sample_path = info.get("samplePath", f.sample_path) or ""
            f.rig_guid = info.get("rigGuid", f.rig_guid) or ""
        fi = list(st.files).index(f)
        _clip_entries_for_file(st, fi, info)
        sha = info.get("sha256", "")
        if f.kind == "CLIP" and f.loaded and sha and f.sha_sync and sha != f.sha_sync:
            local_edits = any(c.loaded and exporter.clip_fp(c) != c.fp_sync for c in st.clips if c.file_index == fi)
            if local_edits:
                util.log("Warning:", f.asset_path, "changed in Unity and here; keeping your version")
            else:
                util.log("Reloading", f.asset_path, "(changed in Unity)")
                clips.load_file(scene, fi)
        if f.kind == "ANIM" and f.sample_path:
            clips.load_file(scene, fi) if not f.loaded else None
    _focus(scene, m)
    save_blend()
    write_lock(paths, st.model_guid)
    bpaths.update_state(paths, st.model_guid, blendPath=m.get("blendPath", ""), lastOpenedUtc=util.now_utc_iso())


def _focus(scene, m):
    st = scene.ub_state
    focus = m.get("focus") or {}
    fguid, fpath, take = focus.get("fileGuid"), focus.get("assetPath"), focus.get("take")
    if not (fguid or fpath):
        return
    for fi, f in enumerate(st.files):
        if (fguid and f.guid == fguid) or (fpath and f.asset_path == fpath):
            if f.kind in {"CLIP", "ANIM"} and not f.loaded:
                clips.load_file(scene, fi)
            idx = next((i for i, c in enumerate(st.clips) if c.file_index == fi and (not take or c.name == take)), -1)
            if idx >= 0:
                clips.activate(scene, idx)
            return


def start(manifest_path):
    """Entry point from bootstrap.py. Runs once Blender's UI is up."""
    def _run():
        try:
            m = util.read_json(manifest_path)
            if not m:
                raise RuntimeError("Session manifest not found: %s" % manifest_path)
            if m.get("fresh", True) or not bpy.data.filepath:
                fresh_session(m)
            else:
                refresh_session(m)
            _open_panel()
            bpy.context.scene.ub_state.dirty_hint = False
            quiet_period()
            util.log("Session ready:", m["model"].get("assetPath"))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            _report_error("Unity Bridge could not start: %s" % e)
        return None

    bpy.app.timers.register(_run, first_interval=0.3)


def _open_panel():
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "VIEW_3D":
                    space = area.spaces.active
                    space.show_region_ui = True
                    for region in area.regions:
                        if region.type == "UI":
                            try:
                                region.active_panel_category = "Unity"
                            except (AttributeError, TypeError):
                                pass
                    return
    except (AttributeError, ReferenceError):
        pass


def _report_error(message):
    util.log(message)

    def draw(self, _context):
        self.layout.label(text=message)

    try:
        bpy.context.window_manager.popup_menu(draw, title="Unity Bridge", icon="ERROR")
    except (AttributeError, RuntimeError):
        pass


# --------------------------------------------------------------------------- pin / unpin


def _links_path(st):
    return bridge_paths(st).links


def set_pinned(scene, pinned):
    st = scene.ub_state
    paths = bridge_paths(st)
    rel = st.model_path[len("Assets/"):] if st.model_path.startswith("Assets/") else st.model_path
    base = os.path.splitext(rel)[0] + ".blend"
    dst = os.path.join(paths.pinned if pinned else paths.cache, base)
    old = bpy.data.filepath
    st.pinned = pinned
    save_blend(dst)
    if old and os.path.normpath(old) != os.path.normpath(dst) and os.path.exists(old):
        try:
            os.remove(old)
            if os.path.exists(old + "1"):
                os.remove(old + "1")
        except OSError:
            pass
    links = util.read_json(paths.links, {"version": 1, "pins": [], "clipRigs": []}) or {}
    pins = [p for p in links.get("pins", []) if p.get("guid") != st.model_guid]
    if pinned:
        pins.append({"guid": st.model_guid, "assetPath": st.model_path, "blend": paths.rel(dst)})
    links["pins"] = sorted(pins, key=lambda p: p.get("assetPath", ""))
    links.setdefault("version", 1)
    links.setdefault("clipRigs", [])
    util.write_json_atomic(paths.links, links)
    bpaths.update_state(paths, st.model_guid, blendPath=paths.rel(dst), pinned=pinned)
    write_lock(paths, st.model_guid)


# --------------------------------------------------------------------------- handlers


@persistent
def on_save_post(_dummy):
    scene = bpy.context.scene
    st = getattr(scene, "ub_state", None)
    if st is None or not st.model_guid or not st.project_root:
        return
    try:
        # Fingerprinting toggles modifiers and re-evaluates; that must not raise the dirty hint.
        quiet_period()
        col = importer.model_collection(scene)
        dirty = False
        if col is not None:
            dirty = fingerprint.model_fingerprints(col)["model"] != st.model_fp_sync
        if not dirty:
            dirty = any(c.loaded and exporter.clip_fp(c) != c.fp_sync for c in st.clips) or \
                any(f.is_new for f in st.files)
        bpaths.update_state(bridge_paths(st), st.model_guid, dirty=bool(dirty), savedUtc=util.now_utc_iso())
    except Exception:  # noqa: BLE001
        traceback.print_exc()


@persistent
def on_depsgraph_update(scene, depsgraph):
    st = getattr(scene, "ub_state", None)
    if st is None or not st.collection_name or st.dirty_hint or time.time() < _GRACE["until"]:
        return
    for upd in depsgraph.updates:
        idd = upd.id
        if isinstance(idd, (bpy.types.Mesh, bpy.types.Armature, bpy.types.Action, bpy.types.Material)):
            st.dirty_hint = True
            return
        if isinstance(idd, bpy.types.Object) and (upd.is_updated_geometry or upd.is_updated_transform):
            st.dirty_hint = True
            return


@persistent
def on_load_pre(_dummy):
    release_lock()


@persistent
def on_load_post(_dummy):
    st = getattr(bpy.context.scene, "ub_state", None)
    if st is not None and st.model_guid and st.project_root and os.path.isdir(st.project_root):
        write_lock(bridge_paths(st), st.model_guid)


HANDLERS = (
    (bpy.app.handlers.save_post, on_save_post),
    (bpy.app.handlers.depsgraph_update_post, on_depsgraph_update),
    (bpy.app.handlers.load_pre, on_load_pre),
    (bpy.app.handlers.load_post, on_load_post),
)


def register():
    for lst, fn in HANDLERS:
        if fn not in lst:
            lst.append(fn)


def unregister():
    for lst, fn in HANDLERS:
        if fn in lst:
            lst.remove(fn)
