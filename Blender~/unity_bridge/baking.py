"""Turns per-frame bone matrices into keyed actions on the model's armature.

Used in two places:
* loading a clip from another FBX: bone matrices come from a temporarily imported armature;
* loading a Unity .anim: Unity samples the clip on the model and sends root-space matrices.
"""

import bpy
import numpy as np
from mathutils import Matrix

from . import actions, util


def bones_parent_first(arm_data):
    out = []

    def walk(b):
        out.append(b)
        for c in b.children:
            walk(c)

    for b in arm_data.bones:
        if b.parent is None:
            walk(b)
    return out


def _uniform_scale(m):
    s = m.to_scale()
    return (abs(s.x) + abs(s.y) + abs(s.z)) / 3.0 or 1.0


def bake_pose_matrices(arm_obj, action, frames, poses_per_frame):
    """poses_per_frame[i] maps bone name -> armature-space pose Matrix for frames[i].

    Bones without a matrix keep their rest pose relative to the parent. Keys every frame on
    location, rotation_quaternion and scale, which is what an FBX take holds anyway.
    """
    arm = arm_obj.data
    order = bones_parent_first(arm)
    slot = actions.ensure_slot(action, "OBJECT", arm_obj.name)
    bag = actions.ensure_channelbag(action, slot)
    n = len(frames)
    data = {b.name: np.zeros((n, 10), dtype=np.float64) for b in order}
    prev_q = {}
    for fi, poses in enumerate(poses_per_frame):
        P = {}
        for b in order:
            parent = b.parent
            M = poses.get(b.name)
            if M is None:
                if parent is None:
                    M = b.matrix_local.copy()
                else:
                    M = P[parent.name] @ (parent.matrix_local.inverted_safe() @ b.matrix_local)
            P[b.name] = M
            if parent is None:
                basis = b.convert_local_to_pose(M, b.matrix_local, invert=True)
            else:
                basis = b.convert_local_to_pose(
                    M, b.matrix_local,
                    parent_matrix=P[parent.name], parent_matrix_local=parent.matrix_local,
                    invert=True)
            loc, rot, scl = basis.decompose()
            q_prev = prev_q.get(b.name)
            if q_prev is not None and q_prev.dot(rot) < 0.0:
                rot.negate()
            prev_q[b.name] = rot.copy()
            data[b.name][fi] = (loc.x, loc.y, loc.z, rot.w, rot.x, rot.y, rot.z, scl.x, scl.y, scl.z)

    frames_f = [float(f) for f in frames]
    for b in order:
        pb = arm_obj.pose.bones.get(b.name)
        if pb is None:
            continue
        pb.rotation_mode = "QUATERNION"
        base = 'pose.bones["%s"]' % bpy.utils.escape_identifier(b.name)
        cols = data[b.name]
        for i in range(3):
            fc = actions.new_fcurve(bag, base + ".location", i, b.name)
            actions.write_samples(fc, frames_f, cols[:, i].tolist())
        for i in range(4):
            fc = actions.new_fcurve(bag, base + ".rotation_quaternion", i, b.name)
            actions.write_samples(fc, frames_f, cols[:, 3 + i].tolist())
        for i in range(3):
            fc = actions.new_fcurve(bag, base + ".scale", i, b.name)
            actions.write_samples(fc, frames_f, cols[:, 7 + i].tolist())
    return slot


# --------------------------------------------------------------------------- clip from another armature


def sample_armature_world(scene, arm_src, frames):
    """World-space pose matrices of every bone of arm_src for each frame (source action active)."""
    out = []
    for f in frames:
        scene.frame_set(int(f), subframe=f - int(f))
        mw = arm_src.matrix_world
        out.append({pb.name: (mw @ pb.matrix).copy() for pb in arm_src.pose.bones})
    return out


def world_to_target_pose(arm_dst, arm_src, world_per_frame):
    """Keeps the source's local bone motion literally (what Unity plays from an FBX take).

    The source armature may live in other units: its object scale is normalised out so the
    target gets the same rotations and real-size translations.
    """
    inv = arm_dst.matrix_world.inverted_safe()
    k = _uniform_scale(arm_dst.matrix_world) / _uniform_scale(arm_src.matrix_world)
    fix = Matrix.Scale(k, 4)
    names = set(arm_dst.data.bones.keys())
    return [{n: inv @ m @ fix for n, m in frame.items() if n in names} for frame in world_per_frame]


# --------------------------------------------------------------------------- Unity samples (.anim)


def _matrix_rows(vals, i):
    o = i * 16
    return Matrix((vals[o:o + 4], vals[o + 4:o + 8], vals[o + 8:o + 12], vals[o + 12:o + 16]))


def fit_similarity(src, dst):
    """Umeyama fit dst ~ s*R*src + t, reflection allowed (Unity is left-handed)."""
    X = np.asarray(src, dtype=np.float64)
    Y = np.asarray(dst, dtype=np.float64)
    mx, my = X.mean(axis=0), Y.mean(axis=0)
    Xc, Yc = X - mx, Y - my
    n = len(X)
    C = Yc.T @ Xc / n
    U, S, Vt = np.linalg.svd(C)
    R = U @ Vt
    var_x = (Xc ** 2).sum() / n
    s = S.sum() / var_x if var_x > 1e-12 else 1.0
    t = my - s * R @ mx
    G = np.eye(4)
    G[:3, :3] = s * R
    G[:3, 3] = t
    resid = np.sqrt(((Y - (s * (R @ X.T).T + t)) ** 2).sum(axis=1).mean())
    size = np.sqrt((Yc ** 2).sum(axis=1).max()) if n else 1.0
    return Matrix(G.tolist()), float(resid), float(size)


def unity_samples_to_pose(arm_obj, sample):
    """sample: {'bones': [paths], 'rest': [16*n], 'frames': N, 'data': [16*n*N]} in Unity root space."""
    arm = arm_obj.data
    paths = sample["bones"]
    names = [p.rsplit("/", 1)[-1] for p in paths]
    idx = {nm: i for i, nm in enumerate(names) if nm in arm.bones}
    if len(idx) < 3:
        raise RuntimeError("Too few matching bones between the clip and the armature (%d)" % len(idx))
    rest_vals = sample["rest"]
    arm_w = arm_obj.matrix_world
    src_pts, dst_pts = [], []
    for nm, i in idx.items():
        src_pts.append(tuple((arm_w @ arm.bones[nm].matrix_local).translation))
        dst_pts.append(tuple(_matrix_rows(rest_vals, i).translation))
    G, resid, size = fit_similarity(src_pts, dst_pts)
    if size > 0 and resid / size > 0.01:
        util.log("Warning: rest skeletons differ (fit error %.4f of %.4f)" % (resid, size))
    G_inv = G.inverted_safe()
    arm_inv = arm_w.inverted_safe()
    K_inv = {}
    for nm, i in idx.items():
        K = (G @ arm_w @ arm.bones[nm].matrix_local).inverted_safe() @ _matrix_rows(rest_vals, i)
        K_inv[nm] = K.inverted_safe()
    data = sample["data"]
    nb = len(paths)
    out = []
    for f in range(int(sample["frames"])):
        frame_vals = data[f * nb * 16:(f + 1) * nb * 16]
        out.append({nm: arm_inv @ G_inv @ _matrix_rows(frame_vals, i) @ K_inv[nm] for nm, i in idx.items()})
    return out, resid
