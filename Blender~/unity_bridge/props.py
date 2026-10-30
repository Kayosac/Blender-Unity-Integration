"""Scene-level state saved inside the .blend."""

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)

FILE_KINDS = [
    ("MODEL", "Model", "FBX with meshes; may also hold takes"),
    ("CLIP", "Clip FBX", "FBX with bones and takes only"),
    ("ANIM", "Unity .anim", "Unity animation clip asset"),
]

ROOT_MOTION = [
    ("IN_PLACE", "In place", "Bake rotation, height and XZ into the pose"),
    ("ROOT_MOTION", "Root motion", "Rotation and XZ move the object; height stays in the pose"),
    ("CUSTOM", "Custom", "Keep whatever is set in Unity"),
]


def _on_clip_index(self, context):
    from . import clips, util

    try:
        clips.activate(context.scene, self.clip_index)
    except Exception as e:  # noqa: BLE001
        util.log("Could not activate clip:", e)


def _sync_loop(self, _context):
    act = self.action
    if act is not None:
        act.use_frame_range = True if self.loop else act.use_frame_range
        act.use_cyclic = self.loop


class UB_File(bpy.types.PropertyGroup):
    guid: StringProperty()
    asset_path: StringProperty()
    kind: EnumProperty(items=FILE_KINDS, default="MODEL")
    has_mesh: BoolProperty()
    is_new: BoolProperty()
    rig_guid: StringProperty()
    third_party: BoolProperty()
    path_prefix: StringProperty(description="Path from the Animator root to the model root (.anim)")
    sha_sync: StringProperty(description="SHA-256 of the file when it was loaded or last exported")
    profile_json: StringProperty()
    loaded: BoolProperty()
    sample_path: StringProperty(description="Unity samples for an .anim clip")


class UB_Clip(bpy.types.PropertyGroup):
    name: StringProperty(description="Take name in the FBX, clip name for new clips")
    file_index: IntProperty(default=-1)
    action: PointerProperty(type=bpy.types.Action)
    key_action: PointerProperty(type=bpy.types.Action, description="Shape key action of this take")
    key_target: StringProperty(description="Mesh object whose shape keys the key action drives")
    orig_start: FloatProperty()
    loaded: BoolProperty()
    managed: BoolProperty(description="Loop and root motion are written to Unity")
    unity_clips: StringProperty(description="Names of Unity clips cut from this take")
    loop: BoolProperty(name="Loop", update=_sync_loop)
    root_motion: EnumProperty(name="Root motion", items=ROOT_MOTION, default="CUSTOM")
    fp_sync: StringProperty()


class UB_Issue(bpy.types.PropertyGroup):
    level: StringProperty()
    code: StringProperty()
    message: StringProperty()
    target: StringProperty()
    bone: StringProperty()


class UB_State(bpy.types.PropertyGroup):
    version: IntProperty(default=1)
    session_id: StringProperty()
    project_root: StringProperty(subtype="DIR_PATH")
    art_source: StringProperty(default="ArtSource")
    model_guid: StringProperty()
    model_path: StringProperty()
    model_name: StringProperty()
    collection_name: StringProperty()
    profile_json: StringProperty()
    baseline_json: StringProperty()
    settings_json: StringProperty()
    model_fp_sync: StringProperty()
    skeleton_fp_sync: StringProperty()
    pinned: BoolProperty()
    dirty_hint: BoolProperty()
    status_text: StringProperty()
    files: CollectionProperty(type=UB_File)
    clips: CollectionProperty(type=UB_Clip)
    clip_index: IntProperty(default=-1, update=_on_clip_index)
    issues: CollectionProperty(type=UB_Issue)
    blocked: BoolProperty(description="Last export stopped on errors")
    simplify_tolerance: FloatProperty(name="Tolerance", default=0.001, min=0.0, soft_max=0.05,
                                      precision=4, description="Allowed error when removing keys")


CLASSES = (UB_File, UB_Clip, UB_Issue, UB_State)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.ub_state = PointerProperty(type=UB_State)


def unregister():
    del bpy.types.Scene.ub_state
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
