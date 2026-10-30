"""Synthetic test files that exercise what the bridge must keep intact."""

import math
import os

import bpy
from mathutils import Euler, Quaternion, Vector

from unity_bridge import actions, importer


def _link(ob, col=None):
    (col or bpy.context.scene.collection).objects.link(ob)
    return ob


def _mesh(name, verts, faces):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    return _link(bpy.data.objects.new(name, me))


def _grid(name, nx, ny, size):
    verts, faces = [], []
    for j in range(ny + 1):
        for i in range(nx + 1):
            x = (i / nx - 0.5) * size
            y = (j / ny - 0.5) * size
            verts.append((x, y, 0.15 * math.sin(i * 0.9) * math.cos(j * 0.7)))
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            faces.append((a, a + 1, a + nx + 2, a + nx + 1))
    return _mesh(name, verts, faces)


def _box(name, sx, sy, sz):
    v = [(x * sx, y * sy, z * sz) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    f = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return _mesh(name, v, f)


def _decorate(ob, mats):
    me = ob.data
    for m in mats:
        mat = bpy.data.materials.get(m) or bpy.data.materials.new(m)
        me.materials.append(mat)
    for p in me.polygons:
        p.material_index = p.index % len(mats)
    for k in range(2):
        uv = me.uv_layers.new(name="UVMap" if k == 0 else "Lightmap")
        for li, loop in enumerate(me.loops):
            co = me.vertices[loop.vertex_index].co
            uv.data[li].uv = (co.x * (0.5 + k) + 0.25, co.y * (0.5 + k) + 0.31 * k)
    col = me.color_attributes.new(name="Color", type="BYTE_COLOR", domain="CORNER")
    for li, loop in enumerate(me.loops):
        v = me.vertices[loop.vertex_index].co
        col.data[li].color = ((v.x + 1) % 1.0, (v.y + 1) % 1.0, (li % 7) / 7.0, 1.0)
    normals = []
    for loop in me.loops:
        n = Vector(me.vertices[loop.vertex_index].normal) + Vector((0.2 * math.sin(loop.index), 0.1, 0.0))
        normals.append(n.normalized())
    me.normals_split_custom_set(normals)


def build_static(path, scale_option):
    importer.clear_scene()
    root = _link(bpy.data.objects.new("Root", None))
    lod0 = _grid("House_LOD0", 6, 5, 2.0)
    _decorate(lod0, ["Wall", "Roof"])
    lod1 = _grid("House_LOD1", 3, 2, 2.0)
    _decorate(lod1, ["Wall", "Roof"])
    col = _box("House_COL", 1.0, 1.0, 0.5)
    door = _box("Door", 0.2, 0.05, 0.4)
    door.location = (0.4, -1.0, 0.4)
    door.rotation_euler = Euler((0.1, 0.0, 0.35))
    door.scale = (1.0, 1.3, 0.9)
    for ob in (lod0, lod1, col):
        ob.parent = root
    door.parent = lod0
    root.location = (1.0, 2.0, 0.0)
    _export(path, scale_option, "Y", "-Z", anim=False)


def _armature():
    arm_data = bpy.data.armatures.new("Armature")
    arm = _link(bpy.data.objects.new("Armature", arm_data))
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones

    def bone(name, head, tail, parent=None, roll=0.0, connect=False):
        b = eb.new(name)
        b.head, b.tail, b.roll = head, tail, roll
        if parent:
            b.parent = eb[parent]
            b.use_connect = connect
        return b

    bone("Root", (0, 0, 0), (0, 0.2, 0))
    bone("Hips", (0, 0, 1.0), (0, 0, 1.2), "Root", roll=0.3)
    bone("Spine", (0, 0, 1.2), (0, 0.02, 1.5), "Hips", connect=True, roll=-0.2)
    bone("Chest", (0, 0.02, 1.5), (0, 0, 1.75), "Spine", connect=True)
    bone("Head", (0, 0, 1.75), (0, 0, 1.95), "Chest", connect=True, roll=0.5)
    bone("HeadTop_End", (0, 0, 1.95), (0, 0, 2.05), "Head", connect=True)
    for side, x in (("L", 0.15), ("R", -0.15)):
        bone("Thigh." + side, (x, 0, 1.0), (x, 0.03, 0.55), "Hips", roll=0.1 if side == "L" else -0.1)
        bone("Shin." + side, (x, 0.03, 0.55), (x, 0, 0.1), "Thigh." + side, connect=True)
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def _body(arm):
    verts, faces, rings, seg = [], [], 12, 8
    for r in range(rings + 1):
        z = 0.1 + 1.9 * r / rings
        rad = 0.25 + 0.05 * math.sin(r)
        for s in range(seg):
            a = 2 * math.pi * s / seg
            verts.append((rad * math.cos(a), rad * math.sin(a), z))
    for r in range(rings):
        for s in range(seg):
            a, b = r * seg + s, r * seg + (s + 1) % seg
            faces.append((a, b, b + seg, a + seg))
    body = _mesh("Body", verts, faces)
    _decorate(body, ["Skin", "Cloth"])
    groups = {n: body.vertex_groups.new(name=n) for n in ("Hips", "Spine", "Chest", "Head", "Thigh.L", "Thigh.R",
                                                         "Shin.L", "Shin.R")}
    for v in body.data.vertices:
        z, x = v.co.z, v.co.x
        if z < 0.55:
            side = "L" if x >= 0 else "R"
            groups["Shin." + side].add([v.index], 0.7, "REPLACE")
            groups["Thigh." + side].add([v.index], 0.3, "REPLACE")
        elif z < 1.0:
            side = "L" if x >= 0 else "R"
            groups["Thigh." + side].add([v.index], 0.6, "REPLACE")
            groups["Hips"].add([v.index], 0.4, "REPLACE")
        elif z < 1.5:
            groups["Spine"].add([v.index], 0.5, "REPLACE")
            groups["Hips"].add([v.index], 0.3, "REPLACE")
            groups["Chest"].add([v.index], 0.2, "REPLACE")
        elif z < 1.75:
            groups["Chest"].add([v.index], 1.0, "REPLACE")
        else:
            groups["Head"].add([v.index], 0.8, "REPLACE")
            groups["Chest"].add([v.index], 0.2, "REPLACE")
    body.parent = arm
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    body.shape_key_add(name="Basis")
    for name, dz in (("Smile", 0.03), ("Blink", -0.02)):
        kb = body.shape_key_add(name=name)
        for i, p in enumerate(kb.data):
            if body.data.vertices[i].co.z > 1.75:
                p.co.z += dz
                p.co.x *= 1.05
    return body


def _key_action(arm, name, length, fn):
    act = bpy.data.actions.new(name)
    actions.assign(arm, act)
    for f in range(0, length + 1, 2):
        for pb in arm.pose.bones:
            pb.rotation_mode = "QUATERNION"
            loc, rot = fn(pb.name, f)
            pb.location = loc
            pb.rotation_quaternion = rot
            pb.keyframe_insert("location", frame=f)
            pb.keyframe_insert("rotation_quaternion", frame=f)
            pb.keyframe_insert("scale", frame=f)
    return act


def _idle(name, f):
    a = 0.1 * math.sin(f * 0.3)
    if name in ("Spine", "Chest"):
        return Vector(), Quaternion((1, 0, 0), a)
    return Vector(), Quaternion()


def _run(name, f):
    a = 0.6 * math.sin(f * 0.4)
    if name == "Hips":
        return Vector((0, 0.05 * math.sin(f * 0.8), 0.0)), Quaternion((0, 0, 1), 0.1 * a)
    if name.startswith("Thigh"):
        return Vector(), Quaternion((1, 0, 0), a if name.endswith("L") else -a)
    if name.startswith("Shin"):
        return Vector(), Quaternion((1, 0, 0), abs(a))
    return Vector(), Quaternion()


def _wave(name, f):
    if name == "Head":
        return Vector(), Quaternion((0, 1, 0), 0.4 * math.sin(f * 0.5))
    return Vector(), Quaternion()


def _stash(arm, acts):
    ad = arm.animation_data
    for act in acts:
        tr = ad.nla_tracks.new()
        lo, _hi = actions.frame_range(act)
        st = tr.strips.new(act.name, int(lo), act)
        try:
            st.action_slot = actions.pick_slot(act, arm)
        except (AttributeError, TypeError):
            pass
    ad.action = None


def build_skinned(path, scale_option, axis_up, axis_forward):
    importer.clear_scene()
    arm = _armature()
    _body(arm)
    idle = _key_action(arm, "Idle", 20, _idle)
    run = _key_action(arm, "Run", 16, _run)
    _stash(arm, [idle, run])
    _export(path, scale_option, axis_up, axis_forward, anim=True)


def build_clip(path, scale_option):
    importer.clear_scene()
    arm = _armature()
    wave = _key_action(arm, "Wave", 24, _wave)
    _stash(arm, [wave])
    _export(path, scale_option, "Y", "-Z", anim=True, types={"ARMATURE"})


def _export(path, scale_option, axis_up, axis_forward, anim, types=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=path, use_selection=False, apply_unit_scale=True, apply_scale_options=scale_option,
        object_types=types or {"EMPTY", "MESH", "ARMATURE"}, mesh_smooth_type="OFF", add_leaf_bones=False,
        bake_anim=anim, bake_anim_use_nla_strips=anim, bake_anim_use_all_actions=False,
        bake_anim_simplify_factor=0.0, axis_up=axis_up, axis_forward=axis_forward, colors_type="SRGB")


def build_all(folder):
    files = {
        "static_m.fbx": lambda p: build_static(p, "FBX_SCALE_UNITS"),
        "static_cm.fbx": lambda p: build_static(p, "FBX_SCALE_NONE"),
        "skinned_cm_zup.fbx": lambda p: build_skinned(p, "FBX_SCALE_NONE", "Z", "Y"),
        "skinned_m.fbx": lambda p: build_skinned(p, "FBX_SCALE_UNITS", "Y", "-Z"),
        "clip_wave_m.fbx": lambda p: build_clip(p, "FBX_SCALE_UNITS"),
    }
    out = {}
    for name, fn in files.items():
        p = os.path.join(folder, name)
        fn(p)
        out[name] = p
    return out
