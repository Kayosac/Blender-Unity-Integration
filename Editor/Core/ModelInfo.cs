using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    internal static class ModelInfo
    {
        public static bool IsFbx(string path) =>
            !string.IsNullOrEmpty(path) && path.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase) &&
            AssetImporter.GetAtPath(path) is ModelImporter;

        public static bool IsAnim(string path) =>
            !string.IsNullOrEmpty(path) && path.EndsWith(".anim", StringComparison.OrdinalIgnoreCase);

        public static bool HasMesh(string path) =>
            AssetDatabase.LoadAllAssetsAtPath(path).OfType<Mesh>().Any();

        public static bool HasSkin(string path)
        {
            var go = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            return go != null && go.GetComponentsInChildren<SkinnedMeshRenderer>(true).Length > 0;
        }

        /// <summary>
        /// Unity turns a single top-level FBX node into the prefab root. A clip file holds only the
        /// armature, so its armature node collapses even when the model (which has meshes beside it)
        /// keeps it, and curve paths stop matching. Sets preserveHierarchy to match the model.
        /// Returns true when the importer changed and needs a reimport.
        /// </summary>
        public static bool MatchRootCollapse(ModelImporter clip, string rigPath, string armatureName)
        {
            if (clip == null || string.IsNullOrEmpty(armatureName) || string.IsNullOrEmpty(rigPath)) return false;
            var rig = AssetDatabase.LoadAssetAtPath<GameObject>(rigPath);
            if (rig == null) return false;
            bool keep = rig.transform.Find(armatureName) != null;
            if (clip.preserveHierarchy == keep) return false;
            clip.preserveHierarchy = keep;
            return true;
        }

        public static Avatar GetAvatar(string path) =>
            AssetDatabase.LoadAllAssetsAtPath(path).OfType<Avatar>().FirstOrDefault();

        public static IEnumerable<AnimationClip> Clips(string path) =>
            AssetDatabase.LoadAllAssetsAtPath(path).OfType<AnimationClip>()
                .Where(c => !c.name.StartsWith("__preview__", StringComparison.Ordinal));

        public static ModelImporterClipAnimation[] EffectiveClips(ModelImporter mi) =>
            mi.clipAnimations != null && mi.clipAnimations.Length > 0 ? mi.clipAnimations : mi.defaultClipAnimations;

        public static string RootMotionMode(ModelImporterClipAnimation c)
        {
            if (c.lockRootRotation && c.lockRootHeightY && c.lockRootPositionXZ) return "IN_PLACE";
            if (!c.lockRootRotation && c.lockRootHeightY && !c.lockRootPositionXZ) return "ROOT_MOTION";
            return "CUSTOM";
        }

        /// <summary>IN_PLACE bakes everything into the pose; ROOT_MOTION lets rotation and XZ move the object.</summary>
        public static void ApplyRootMotion(ModelImporterClipAnimation c, string mode)
        {
            switch (mode)
            {
                case "IN_PLACE":
                    c.lockRootRotation = true;
                    c.keepOriginalOrientation = true;
                    c.lockRootHeightY = true;
                    c.keepOriginalPositionY = true;
                    c.lockRootPositionXZ = true;
                    c.keepOriginalPositionXZ = true;
                    break;
                case "ROOT_MOTION":
                    c.lockRootRotation = false;
                    c.lockRootHeightY = true;
                    c.keepOriginalPositionY = true;
                    c.lockRootPositionXZ = false;
                    break;
            }
        }

        public static List<TakeEntry> Takes(ModelImporter mi)
        {
            var result = new List<TakeEntry>();
            var clips = EffectiveClips(mi) ?? Array.Empty<ModelImporterClipAnimation>();
            foreach (var t in mi.importedTakeInfos ?? Array.Empty<TakeInfo>())
            {
                var entry = new TakeEntry { name = t.name, startFrame = t.startTime * t.sampleRate };
                foreach (var c in clips.Where(c => c.takeName == t.name))
                {
                    entry.unityClips.Add(new UnityClipEntry
                    {
                        name = c.name, firstFrame = c.firstFrame, lastFrame = c.lastFrame, loop = c.loopTime,
                        rootMotion = RootMotionMode(c),
                    });
                }
                result.Add(entry);
            }
            return result;
        }

        public static List<string> HumanBones(ModelImporter mi)
        {
            if (mi == null || mi.animationType != ModelImporterAnimationType.Human) return new List<string>();
            return mi.humanDescription.human?.Select(h => h.boneName).Where(n => !string.IsNullOrEmpty(n)).ToList()
                   ?? new List<string>();
        }

        /// <summary>Bone names: every transform under the model's root, as Blender sees them.</summary>
        public static HashSet<string> TransformNames(string path)
        {
            var go = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            var set = new HashSet<string>();
            if (go == null) return set;
            foreach (var t in go.GetComponentsInChildren<Transform>(true))
                if (t != go.transform) set.Add(t.name);
            return set;
        }

        /// <summary>Base texture per FBX material name, so Blender can show it in the viewport.</summary>
        public static List<MaterialPreview> MaterialPreviews(ModelImporter mi)
        {
            var list = new List<MaterialPreview>();
            var byName = new Dictionary<string, Material>();
            foreach (var kv in mi.GetExternalObjectMap())
                if (kv.Key.type == typeof(Material) && kv.Value is Material m) byName[kv.Key.name] = m;
            foreach (var m in AssetDatabase.LoadAllAssetsAtPath(mi.assetPath).OfType<Material>())
                if (!byName.ContainsKey(m.name)) byName[m.name] = m;
            foreach (var kv in byName)
            {
                var tex = MainTexture(kv.Value);
                var texPath = tex != null ? AssetDatabase.GetAssetPath(tex) : null;
                if (string.IsNullOrEmpty(texPath)) continue;
                list.Add(new MaterialPreview { name = kv.Key, texture = BridgePaths.Abs(texPath) });
            }
            return list;
        }

        static Texture MainTexture(Material m)
        {
            if (m == null) return null;
            try
            {
                if (m.mainTexture != null) return m.mainTexture;
            }
            catch (Exception) { /* shaders without a main texture property */ }
            foreach (var prop in new[] { "_BaseMap", "_MainTex", "_BaseColorMap", "_Albedo" })
                if (m.HasProperty(prop) && m.GetTexture(prop) != null) return m.GetTexture(prop);
            return null;
        }

        /// <summary>Clip FBX files that belong to a model: Model@*.fbx near it, or files sharing its avatar.</summary>
        public static List<string> RelatedClipFiles(string modelPath)
        {
            var result = new List<string>();
            var dir = Path.GetDirectoryName(modelPath)?.Replace('\\', '/');
            if (string.IsNullOrEmpty(dir)) return result;
            var stem = Path.GetFileNameWithoutExtension(modelPath);
            var avatar = GetAvatar(modelPath);
            foreach (var guid in AssetDatabase.FindAssets("t:Model", new[] { dir }))
            {
                var p = AssetDatabase.GUIDToAssetPath(guid);
                if (p == modelPath || !IsFbx(p)) continue;
                var name = Path.GetFileNameWithoutExtension(p);
                var mi = (ModelImporter)AssetImporter.GetAtPath(p);
                bool sameAvatar = avatar != null && mi.sourceAvatar == avatar;
                if ((name.StartsWith(stem + "@", StringComparison.Ordinal) || sameAvatar) && !HasMesh(p))
                    result.Add(p);
            }
            return result.Distinct().OrderBy(p => p).ToList();
        }
    }
}
