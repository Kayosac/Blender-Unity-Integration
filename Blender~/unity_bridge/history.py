"""Copies of files before they are overwritten. Unity's History tab restores them."""

import os
import shutil

from . import util


def _entries(root):
    try:
        return sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    except FileNotFoundError:
        return []


def backup(paths, guid, asset_rel, session_id, keep=10, max_mb=2048, reason="export"):
    src = paths.abs(asset_rel)
    if not os.path.isfile(src):
        return None
    key = guid or asset_rel.replace("/", "_")
    dst_dir = os.path.join(paths.history, key, util.timestamp_slug())
    os.makedirs(dst_dir, exist_ok=True)
    name = os.path.basename(src)
    shutil.copy2(src, os.path.join(dst_dir, name))
    if os.path.isfile(src + ".meta"):
        shutil.copy2(src + ".meta", os.path.join(dst_dir, name + ".meta"))
    util.write_json_atomic(os.path.join(dst_dir, "info.json"), {
        "guid": guid,
        "assetPath": asset_rel,
        "fileName": name,
        "createdUtc": util.now_utc_iso(),
        "sessionId": session_id,
        "reason": reason,
        "sha256": util.sha256_file(src),
    })
    prune(paths, key, keep, max_mb)
    return dst_dir


def prune(paths, key, keep, max_mb):
    root = os.path.join(paths.history, key)
    entries = _entries(root)
    for old in entries[: max(0, len(entries) - max(1, keep))]:
        shutil.rmtree(os.path.join(root, old), ignore_errors=True)
    limit = max_mb * 1024 * 1024
    all_dirs = []
    for k in _entries(paths.history):
        for e in _entries(os.path.join(paths.history, k)):
            p = os.path.join(paths.history, k, e)
            all_dirs.append((e, p, util.dir_size(p)))
    total = sum(s for _e, _p, s in all_dirs)
    for _e, p, s in sorted(all_dirs):
        if total <= limit:
            break
        shutil.rmtree(p, ignore_errors=True)
        total -= s
