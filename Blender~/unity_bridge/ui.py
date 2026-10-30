"""Sidebar panel (View3D ▸ N ▸ Unity) and its operators."""

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import actions, clips, exporter, importer, session, util

EXPORT_MODES = [
    ("SMART", "Changed files", "Write only what changed"),
    ("MODEL", "Model", "Write the model FBX even if nothing changed"),
    ("ACTIVE_CLIP", "Active clip", "Write the file of the active clip"),
    ("ALL", "Everything loaded", "Write the model and every loaded clip"),
]


def _st(context):
    return getattr(context.scene, "ub_state", None)


def _has_session(context):
    st = _st(context)
    return st is not None and bool(st.collection_name)


# --------------------------------------------------------------------------- operators


class UB_OT_export(bpy.types.Operator):
    bl_idname = "unity_bridge.export"
    bl_label = "Export to Unity"
    bl_description = "Write changed files back into the Unity project"
    bl_options = {"REGISTER"}

    mode: EnumProperty(items=EXPORT_MODES, default="SMART")
    allow_errors: BoolProperty(default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _has_session(context)

    def execute(self, context):
        if context.object is not None and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        try:
            res = exporter.run(context.scene, self.mode, self.allow_errors)
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            self.report({"ERROR"}, "Export failed: %s" % e)
            return {"CANCELLED"}
        st = _st(context)
        if res["status"] == "up_to_date":
            st.status_text = "Up to date"
            self.report({"INFO"}, "Nothing changed")
        elif res["status"] == "blocked":
            st.status_text = "Blocked by errors"
            self.report({"ERROR"}, "Export blocked: see the list in the Unity panel")
        else:
            names = ", ".join(os.path.basename(i["assetPath"]) for i in res["items"])
            st.status_text = "Exported: " + names
            self.report({"INFO"}, "Exported %s. Switch to Unity to import." % names)
        return {"FINISHED"}


class UB_OT_check(bpy.types.Operator):
    bl_idname = "unity_bridge.check"
    bl_label = "Check Changes"
    bl_description = "Compare the scene with the files in Unity"

    @classmethod
    def poll(cls, context):
        return _has_session(context)

    def execute(self, context):
        st = _st(context)
        work = exporter.plan(context.scene)
        parts = []
        if work["model_changed"]:
            parts.append("skeleton" if work["skeleton_changed"] else "mesh")
        n = len(work["changed_clips"])
        if n:
            parts.append("%d clip%s" % (n, "" if n == 1 else "s"))
        st.status_text = ("Changed: " + ", ".join(parts)) if parts else "Up to date"
        st.dirty_hint = bool(parts)
        session.quiet_period(0.5)
        return {"FINISHED"}


class UB_OT_new_clip(bpy.types.Operator):
    bl_idname = "unity_bridge.new_clip"
    bl_label = "New Clip"
    bl_description = "Create an animation that exports as Model@Clip.fbx"

    clip_name: StringProperty(name="Name", default="NewClip")
    length: IntProperty(name="Frames", default=30, min=1)

    @classmethod
    def poll(cls, context):
        if not _has_session(context):
            return False
        col = importer.model_collection(context.scene)
        return col is not None and importer.main_armature(col) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            clips.new_clip(context.scene, self.clip_name.strip(), self.length)
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class UB_OT_load_clip(bpy.types.Operator):
    bl_idname = "unity_bridge.load_clip"
    bl_label = "Load"
    bl_description = "Bring this clip's file into the scene"

    index: IntProperty(default=-1)

    def execute(self, context):
        st = _st(context)
        idx = self.index if self.index >= 0 else st.clip_index
        if not (0 <= idx < len(st.clips)):
            return {"CANCELLED"}
        try:
            clips.load_file(context.scene, st.clips[idx].file_index)
            clips.activate(context.scene, idx)
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        session.quiet_period()
        return {"FINISHED"}


class UB_OT_simplify(bpy.types.Operator):
    bl_idname = "unity_bridge.simplify_keys"
    bl_label = "Simplify Keys"
    bl_description = "Remove keys while the motion stays within the tolerance"

    @classmethod
    def poll(cls, context):
        st = _st(context)
        return st is not None and 0 <= st.clip_index < len(st.clips) and st.clips[st.clip_index].action is not None

    def execute(self, context):
        st = _st(context)
        removed = clips.simplify_action(st.clips[st.clip_index].action, st.simplify_tolerance)
        self.report({"INFO"}, "Removed %d keys" % removed)
        return {"FINISHED"}


class UB_OT_pin(bpy.types.Operator):
    bl_idname = "unity_bridge.pin"
    bl_label = "Pin"
    bl_description = "Keep this .blend in ArtSource/Pinned (committed to git) instead of the cache"

    pin: BoolProperty(default=True)

    @classmethod
    def poll(cls, context):
        return _has_session(context) and bool(bpy.data.filepath)

    def execute(self, context):
        try:
            session.set_pinned(context.scene, self.pin)
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, "Pinned" if self.pin else "Moved back to the cache")
        return {"FINISHED"}


class UB_OT_select_issue(bpy.types.Operator):
    bl_idname = "unity_bridge.select_issue"
    bl_label = "Select"
    bl_description = "Select the object or bone this message is about"

    index: IntProperty()

    def execute(self, context):
        st = _st(context)
        issue = st.issues[self.index]
        ob = bpy.data.objects.get(issue.target)
        if ob is None:
            return {"CANCELLED"}
        if context.object is not None and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for o in context.view_layer.objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        if ob.type == "ARMATURE" and issue.bone and issue.bone in ob.data.bones:
            bpy.ops.object.mode_set(mode="POSE")
            # Blender 5.0 stores bone selection on pose bones (Bone.select is gone).
            for pb in ob.pose.bones:
                pb.select = pb.name == issue.bone
            ob.data.bones.active = ob.data.bones[issue.bone]
        return {"FINISHED"}


class UB_MT_export_only(bpy.types.Menu):
    bl_idname = "UB_MT_export_only"
    bl_label = "Export only"

    def draw(self, _context):
        layout = self.layout
        for mode, label, _desc in EXPORT_MODES[1:]:
            layout.operator(UB_OT_export.bl_idname, text=label).mode = mode


# --------------------------------------------------------------------------- panels


class UB_UL_clips(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        st = data
        f = st.files[item.file_index] if 0 <= item.file_index < len(st.files) else None
        row = layout.row(align=True)
        ic = "ACTION" if item.loaded else "IMPORT"
        if f is not None and f.is_new:
            ic = "ADD"
        row.label(text=item.name, icon=ic)
        if f is not None:
            row.label(text={"MODEL": "in model", "CLIP": "FBX", "ANIM": ".anim"}[f.kind])
        if not item.loaded:
            op = row.operator(UB_OT_load_clip.bl_idname, text="", icon="IMPORT", emboss=False)
            op.index = index


class UB_PT_main(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Unity"
    bl_label = "Unity Bridge"

    def draw(self, context):
        layout = self.layout
        st = _st(context)
        if st is None or not st.collection_name:
            layout.label(text="Open a model from Unity:", icon="INFO")
            layout.label(text="Blender ▸ Open in Blender")
            return
        box = layout.box()
        col = box.column(align=True)
        col.label(text=st.model_name or os.path.basename(st.model_path), icon="MESH_DATA")
        sub = col.row()
        sub.scale_y = 0.8
        sub.label(text=st.model_path)
        status = st.status_text or ("May have changes" if st.dirty_hint else "Up to date")
        row = box.row()
        row.label(text=status, icon="FILE_REFRESH" if st.dirty_hint else "CHECKMARK")
        row.operator(UB_OT_check.bl_idname, text="", icon="VIEWZOOM")

        row = layout.row(align=True)
        row.scale_y = 1.6
        row.operator(UB_OT_export.bl_idname, text="Export to Unity", icon="EXPORT").mode = "SMART"
        row.menu(UB_MT_export_only.bl_idname, text="", icon="DOWNARROW_HLT")

        if len(st.issues):
            box = layout.box()
            for i, issue in enumerate(st.issues[:10]):
                r = box.row()
                r.alert = issue.level == "ERROR"
                r.label(text=issue.message, icon="ERROR" if issue.level == "ERROR" else "INFO")
                if issue.target:
                    r.operator(UB_OT_select_issue.bl_idname, text="", icon="RESTRICT_SELECT_OFF").index = i
            if st.blocked:
                r = box.row()
                r.alert = True
                op = r.operator(UB_OT_export.bl_idname, text="Export anyway", icon="ERROR")
                op.mode = "SMART"
                op.allow_errors = True

        row = layout.row()
        if st.pinned:
            row.operator(UB_OT_pin.bl_idname, text="Pinned · Unpin", icon="PINNED").pin = False
        else:
            row.operator(UB_OT_pin.bl_idname, text="Pin this .blend", icon="UNPINNED").pin = True


class UB_PT_clips(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Unity"
    bl_label = "Clips"
    bl_parent_id = "UB_PT_main"

    @classmethod
    def poll(cls, context):
        if not _has_session(context):
            return False
        col = importer.model_collection(context.scene)
        return col is not None and importer.main_armature(col) is not None

    def draw(self, context):
        layout = self.layout
        st = _st(context)
        layout.template_list("UB_UL_clips", "", st, "clips", st, "clip_index", rows=4)
        row = layout.row(align=True)
        row.operator(UB_OT_new_clip.bl_idname, icon="ADD")
        if not (0 <= st.clip_index < len(st.clips)):
            return
        clip = st.clips[st.clip_index]
        box = layout.box()
        if not clip.loaded:
            box.operator(UB_OT_load_clip.bl_idname, text="Load clip", icon="IMPORT").index = st.clip_index
            return
        act = clip.action
        if act is not None:
            r = box.row(align=True)
            r.prop(act, "use_frame_range", text="Manual range")
            sub = r.row(align=True)
            sub.enabled = act.use_frame_range
            sub.prop(act, "frame_start", text="")
            sub.prop(act, "frame_end", text="")
        if clip.managed:
            box.prop(clip, "loop")
            box.prop(clip, "root_motion")
        else:
            note = "Clip settings stay in Unity"
            if clip.unity_clips:
                note += " (%s)" % clip.unity_clips
            box.label(text=note, icon="INFO")
        r = box.row(align=True)
        r.prop(st, "simplify_tolerance")
        r.operator(UB_OT_simplify.bl_idname, text="Simplify keys")


CLASSES = (UB_OT_export, UB_OT_check, UB_OT_new_clip, UB_OT_load_clip, UB_OT_simplify, UB_OT_pin,
           UB_OT_select_issue, UB_MT_export_only, UB_UL_clips, UB_PT_main, UB_PT_clips)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
