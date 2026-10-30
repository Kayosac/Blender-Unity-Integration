"""Folder layout shared with the Unity side. Keep in sync with Editor/Core/BridgePaths.cs."""

import os

from . import util


class BridgePaths:
    def __init__(self, project_root, art_source="ArtSource"):
        self.project_root = os.path.normpath(project_root)
        self.art_source = os.path.join(self.project_root, art_source)
        self.cache = os.path.join(self.art_source, "Cache")
        self.pinned = os.path.join(self.art_source, "Pinned")
        self.links = os.path.join(self.art_source, "links.json")
        self.bridge = os.path.join(self.art_source, ".bridge")
        self.sessions = os.path.join(self.bridge, "sessions")
        self.outbox = os.path.join(self.bridge, "outbox")
        self.history = os.path.join(self.bridge, "history")
        self.state = os.path.join(self.bridge, "state")
        self.locks = os.path.join(self.bridge, "locks")
        self.tmp = os.path.join(self.bridge, "tmp")
        self.staging_assets_rel = "Assets/BlenderBridgeStaging"

    def ensure(self):
        for d in (self.cache, self.sessions, self.outbox, self.history, self.state, self.locks, self.tmp):
            os.makedirs(d, exist_ok=True)

    def abs(self, rel):
        return util.to_abs(self.project_root, rel)

    def rel(self, abs_path):
        return util.to_rel(self.project_root, abs_path)

    def state_file(self, guid):
        return os.path.join(self.state, guid + ".json")

    def lock_file(self, guid):
        return os.path.join(self.locks, guid + ".lock")

    def cache_blend_for(self, asset_rel_path):
        """Assets/Project/Content/Hero/Hero.fbx -> ArtSource/Cache/Project/Content/Hero/Hero.blend"""
        rel = asset_rel_path.replace("\\", "/")
        if rel.startswith("Assets/"):
            rel = rel[len("Assets/"):]
        base, _ext = os.path.splitext(rel)
        return os.path.join(self.cache, base + ".blend")


def read_state(paths, guid):
    return util.read_json(paths.state_file(guid), default={}) or {}


def update_state(paths, guid, **fields):
    """Read-modify-write so fields written by Unity are kept."""
    if not guid:
        return
    data = read_state(paths, guid)
    data.update(fields)
    data["guid"] = guid
    util.write_json_atomic(paths.state_file(guid), data)
