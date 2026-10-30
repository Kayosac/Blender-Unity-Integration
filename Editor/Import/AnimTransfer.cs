using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    /// <summary>Moves bone curves from the temporary FBX Blender wrote into the original .anim (GUID kept).</summary>
    internal static class AnimTransfer
    {
        static HashSet<string> _humanCurves;

        static bool IsHumanCurve(string property)
        {
            if (_humanCurves == null)
            {
                _humanCurves = new HashSet<string>(HumanTrait.MuscleName);
                foreach (var goal in new[] { "Root", "Motion", "LeftFoot", "RightFoot", "LeftHand", "RightHand" })
                {
                    foreach (var c in new[] { "T.x", "T.y", "T.z", "Q.x", "Q.y", "Q.z", "Q.w" })
                        _humanCurves.Add(goal + c);
                }
            }
            return _humanCurves.Contains(property);
        }

        public static void Run(ExportItem it, ExportReport r)
        {
            var staging = it.stagingPath;
            var animPath = !string.IsNullOrEmpty(it.animGuid) ? AssetDatabase.GUIDToAssetPath(it.animGuid) : it.animPath;
            if (string.IsNullOrEmpty(animPath)) animPath = it.animPath;
            if (!File.Exists(BridgePaths.Abs(staging)))
            {
                r.errors.Add("Blender's temporary clip is missing: " + staging);
                return;
            }
            AssetDatabase.ImportAsset(staging, ImportAssetOptions.ForceSynchronousImport);
            var mi = AssetImporter.GetAtPath(staging) as ModelImporter;
            var rigPath = AssetDatabase.GUIDToAssetPath(it.rigGuid);
            var rigMi = AssetImporter.GetAtPath(rigPath) as ModelImporter;
            bool human = rigMi != null && rigMi.animationType == ModelImporterAnimationType.Human;
            bool reimport = mi != null && ModelInfo.MatchRootCollapse(mi, rigPath, it.armatureName);
            if (mi != null && human)
            {
                mi.animationType = ModelImporterAnimationType.Human;
                mi.avatarSetup = ModelImporterAvatarSetup.CopyFromOther;
                mi.sourceAvatar = ModelInfo.GetAvatar(rigPath);
                reimport = true;
            }
            if (reimport) mi.SaveAndReimport();
            var src = ModelInfo.Clips(staging).FirstOrDefault();
            var dst = AssetDatabase.LoadAssetAtPath<AnimationClip>(animPath);
            if (src == null || dst == null)
            {
                r.errors.Add("Could not read the clip back (" + (src == null ? "temporary FBX" : animPath) + ")");
                return;
            }

            var settings = AnimationUtility.GetAnimationClipSettings(dst);
            var events = AnimationUtility.GetAnimationEvents(dst);
            var prefix = it.pathPrefix ?? "";
            var rigGo = AssetDatabase.LoadAssetAtPath<GameObject>(rigPath);
            var bonePaths = new HashSet<string>();
            if (rigGo != null)
                foreach (var t in rigGo.GetComponentsInChildren<Transform>(true))
                    if (t != rigGo.transform) bonePaths.Add(AnimationUtility.CalculateTransformPath(t, rigGo.transform));

            Undo.RegisterCompleteObjectUndo(dst, "Blender Bridge: update clip");
            var before = new HashSet<string>(AnimationUtility.GetCurveBindings(dst).Select(b => b.path + "|" + Group(b.propertyName)));
            var oldCurves = new Dictionary<string, AnimationCurve>();
            foreach (var b in AnimationUtility.GetCurveBindings(dst))
                oldCurves[b.path + "|" + b.propertyName] = AnimationUtility.GetEditorCurve(dst, b);

            // What Blender sends: bone curves (Generic) or muscle curves (Humanoid). Blender bakes every
            // bone, so a channel the clip never animated is written only if it now moves or differs from
            // the model's default; otherwise layered clips and avatar masks would start pinning it.
            var bindings = new List<EditorCurveBinding>();
            var curves = new List<AnimationCurve>();
            int skipped = 0;
            foreach (var b in AnimationUtility.GetCurveBindings(src))
            {
                if (human && !(b.type == typeof(Animator) && IsHumanCurve(b.propertyName))) continue;
                if (!human && b.type != typeof(Transform)) continue;
                var nb = b;
                if (!human && prefix.Length > 0) nb.path = b.path.Length == 0 ? prefix : prefix + "/" + b.path;
                var curve = AnimationUtility.GetEditorCurve(src, b);
                if (!before.Contains(nb.path + "|" + Group(nb.propertyName)) &&
                    IsConstant(curve, out var value) && MatchesDefault(rigGo, b.path, b.propertyName, value, human))
                {
                    skipped++;
                    continue;
                }
                bindings.Add(nb);
                curves.Add(KeepKeyTiming(curve, oldCurves.TryGetValue(nb.path + "|" + nb.propertyName, out var old) ? old : null));
            }
            if (bindings.Count == 0 && skipped == 0)
            {
                r.errors.Add("The clip Blender wrote has no curves; " + Path.GetFileName(animPath) + " was left unchanged");
                return;
            }
            var written = new HashSet<string>(bindings.Select(b => b.path + "|" + Group(b.propertyName)));

            // Replace exactly the channels written (rotation in any of Unity's encodings); keep the rest.
            int removed = 0;
            foreach (var b in AnimationUtility.GetCurveBindings(dst))
            {
                if (!written.Contains(b.path + "|" + Group(b.propertyName))) continue;
                if (human ? b.type != typeof(Animator) : b.type != typeof(Transform)) continue;
                AnimationUtility.SetEditorCurve(dst, b, null);
                removed++;
            }
            AnimationUtility.SetEditorCurves(dst, bindings.ToArray(), curves.ToArray());
            AnimationUtility.SetAnimationClipSettings(dst, settings);
            AnimationUtility.SetAnimationEvents(dst, events);
            EditorUtility.SetDirty(dst);
            AssetDatabase.SaveAssetIfDirty(dst);
            r.infos.Add($"{bindings.Count} curves written into {Path.GetFileName(animPath)} ({removed} replaced, " +
                        $"{skipped} unchanged channels left out); other curves, events and settings kept");

            AssetDatabase.DeleteAsset(staging);
            DeleteEmptyFolders(Path.GetDirectoryName(staging)?.Replace('\\', '/'));
        }

        /// <summary>
        /// A stepped original (constant tangents, e.g. 12 fps "on twos" animation) keeps its key times and
        /// steps: the new curve is read at the old key times. Smooth curves are taken as Blender baked them.
        /// </summary>
        static AnimationCurve KeepKeyTiming(AnimationCurve baked, AnimationCurve old)
        {
            if (old == null || old.length < 2) return baked;
            for (int i = 0; i < old.length - 1; i++)
                if (AnimationUtility.GetKeyRightTangentMode(old, i) != AnimationUtility.TangentMode.Constant &&
                    !float.IsInfinity(old.keys[i].outTangent))
                    return baked;
            var keys = new Keyframe[old.length];
            for (int i = 0; i < old.length; i++)
            {
                // Read just after the key: Blender holds the value from the key onwards too.
                float t = old.keys[i].time;
                keys[i] = new Keyframe(t, baked.Evaluate(t + 1e-4f), float.PositiveInfinity, float.PositiveInfinity);
            }
            var c = new AnimationCurve(keys);
            for (int i = 0; i < c.length; i++)
            {
                AnimationUtility.SetKeyLeftTangentMode(c, i, AnimationUtility.TangentMode.Constant);
                AnimationUtility.SetKeyRightTangentMode(c, i, AnimationUtility.TangentMode.Constant);
            }
            return c;
        }

        static string Group(string property)
        {
            if (property.StartsWith("m_LocalPosition", System.StringComparison.Ordinal)) return "pos";
            if (property.StartsWith("m_LocalRotation", System.StringComparison.Ordinal) ||
                property.StartsWith("localEulerAngles", System.StringComparison.Ordinal)) return "rot";
            if (property.StartsWith("m_LocalScale", System.StringComparison.Ordinal)) return "scale";
            return property;
        }

        static bool IsConstant(AnimationCurve c, out float value)
        {
            value = c != null && c.length > 0 ? c.keys[0].value : 0f;
            if (c == null) return false;
            foreach (var k in c.keys)
                if (Mathf.Abs(k.value - value) > 1e-5f) return false;
            return true;
        }

        /// <summary>Does a constant channel equal the model's default local value?</summary>
        static bool MatchesDefault(GameObject rig, string path, string property, float value, bool human)
        {
            if (human) return Mathf.Abs(value) < 1e-4f; // muscles rest at 0
            if (rig == null) return false;
            var t = path.Length == 0 ? rig.transform : rig.transform.Find(path);
            if (t == null) return false;
            float d;
            switch (property)
            {
                case "m_LocalPosition.x": d = t.localPosition.x; break;
                case "m_LocalPosition.y": d = t.localPosition.y; break;
                case "m_LocalPosition.z": d = t.localPosition.z; break;
                case "m_LocalScale.x": d = t.localScale.x; break;
                case "m_LocalScale.y": d = t.localScale.y; break;
                case "m_LocalScale.z": d = t.localScale.z; break;
                case "m_LocalRotation.x": return Mathf.Abs(Mathf.Abs(value) - Mathf.Abs(t.localRotation.x)) < 1e-4f;
                case "m_LocalRotation.y": return Mathf.Abs(Mathf.Abs(value) - Mathf.Abs(t.localRotation.y)) < 1e-4f;
                case "m_LocalRotation.z": return Mathf.Abs(Mathf.Abs(value) - Mathf.Abs(t.localRotation.z)) < 1e-4f;
                case "m_LocalRotation.w": return Mathf.Abs(Mathf.Abs(value) - Mathf.Abs(t.localRotation.w)) < 1e-4f;
                default: return false;
            }
            return Mathf.Abs(value - d) <= 1e-4f * Mathf.Max(1f, Mathf.Abs(d));
        }

        static bool IsBone(string path, string prefix, HashSet<string> bonePaths)
        {
            if (prefix.Length > 0)
            {
                if (path == prefix) return false;
                if (!path.StartsWith(prefix + "/", System.StringComparison.Ordinal)) return false;
                path = path.Substring(prefix.Length + 1);
            }
            return bonePaths.Contains(path);
        }

        static void DeleteEmptyFolders(string folder)
        {
            while (!string.IsNullOrEmpty(folder) && folder.StartsWith(BridgePaths.StagingFolder, System.StringComparison.Ordinal))
            {
                if (!AssetDatabase.IsValidFolder(folder)) break;
                if (AssetDatabase.FindAssets("", new[] { folder }).Length > 0) break;
                AssetDatabase.DeleteAsset(folder);
                folder = Path.GetDirectoryName(folder)?.Replace('\\', '/');
            }
        }
    }
}
