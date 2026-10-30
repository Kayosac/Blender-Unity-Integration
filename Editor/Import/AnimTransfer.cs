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
            int removed = 0;
            foreach (var b in AnimationUtility.GetCurveBindings(dst))
            {
                bool replace = human
                    ? b.type == typeof(Animator) && IsHumanCurve(b.propertyName)
                    : b.type == typeof(Transform) && IsBone(b.path, prefix, bonePaths);
                if (!replace) continue;
                AnimationUtility.SetEditorCurve(dst, b, null);
                removed++;
            }

            var bindings = new List<EditorCurveBinding>();
            var curves = new List<AnimationCurve>();
            foreach (var b in AnimationUtility.GetCurveBindings(src))
            {
                if (human && !(b.type == typeof(Animator) && IsHumanCurve(b.propertyName))) continue;
                if (!human && b.type != typeof(Transform)) continue;
                var nb = b;
                if (!human && prefix.Length > 0) nb.path = b.path.Length == 0 ? prefix : prefix + "/" + b.path;
                bindings.Add(nb);
                curves.Add(AnimationUtility.GetEditorCurve(src, b));
            }
            AnimationUtility.SetEditorCurves(dst, bindings.ToArray(), curves.ToArray());
            AnimationUtility.SetAnimationClipSettings(dst, settings);
            AnimationUtility.SetAnimationEvents(dst, events);
            EditorUtility.SetDirty(dst);
            AssetDatabase.SaveAssetIfDirty(dst);
            r.infos.Add($"{bindings.Count} curves written into {Path.GetFileName(animPath)} ({removed} replaced); other curves, events and settings kept");

            AssetDatabase.DeleteAsset(staging);
            DeleteEmptyFolders(Path.GetDirectoryName(staging)?.Replace('\\', '/'));
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
