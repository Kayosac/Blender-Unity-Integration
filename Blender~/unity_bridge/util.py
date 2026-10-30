"""Small helpers shared by the whole add-on: logging, paths, JSON, hashing, GUID lookup."""

import datetime
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid

LOG_PREFIX = "[UnityBridge]"


def log(*parts):
    print(LOG_PREFIX, *parts, flush=True)


def now_utc_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def timestamp_slug():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


def new_id():
    return uuid.uuid4().hex


# --------------------------------------------------------------------------- JSON


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        log("Could not read JSON", path, e)
        return default


def write_json_atomic(path, data):
    """Writes JSON next to the target and swaps it in, so readers never see half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- Files


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def replace_file_atomic(src_tmp, dst):
    """Moves a finished temp file over the destination. Both must be on the same volume."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src_tmp, dst)


def copy_file(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


# --------------------------------------------------------------------------- Unity project paths

_GUID_RE = re.compile(r"^guid:\s*([0-9a-fA-F]{32})\s*$", re.MULTILINE)


def read_meta_guid(asset_abs_path):
    meta = asset_abs_path + ".meta"
    try:
        with open(meta, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return None
    m = _GUID_RE.search(head)
    return m.group(1).lower() if m else None


def find_asset_by_guid(project_root, guid):
    """Scans Assets/**/*.meta for a GUID. Used when a file moved while Blender was open."""
    if not guid:
        return None
    guid = guid.lower()
    needle = ("guid: " + guid).encode()
    assets = os.path.join(project_root, "Assets")
    for root, dirs, files in os.walk(assets):
        # Unity ignores these folders, so can we.
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.endswith("~")]
        for name in files:
            if not name.endswith(".meta"):
                continue
            p = os.path.join(root, name)
            try:
                with open(p, "rb") as f:
                    if needle in f.read(2048):
                        asset = p[: -len(".meta")]
                        return os.path.relpath(asset, project_root).replace(os.sep, "/")
            except OSError:
                continue
    return None


def to_abs(project_root, rel_path):
    return os.path.normpath(os.path.join(project_root, rel_path))


def to_rel(project_root, abs_path):
    return os.path.relpath(abs_path, project_root).replace(os.sep, "/")


def strip_blender_suffix(name):
    """'Body.001' -> ('Body', True). Blender adds these when names collide."""
    m = re.match(r"^(.*)\.(\d{3,})$", name)
    if m:
        return m.group(1), True
    return name, False
