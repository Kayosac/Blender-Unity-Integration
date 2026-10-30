using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    internal enum TargetKind
    {
        Model,
        ClipFbx,
        EmbeddedClip,
        Anim,
        NewModel,
    }

    internal sealed class BridgeTarget
    {
        public TargetKind kind;
        public string assetPath;
        public string clipName;
        public string rigPath;
        public string pathPrefix;

        public string Label
        {
            get
            {
                var file = Path.GetFileName(assetPath);
                switch (kind)
                {
                    case TargetKind.Model: return "Model  " + file;
                    case TargetKind.EmbeddedClip: return "Clip  " + clipName + "  (in " + file + ")";
                    case TargetKind.ClipFbx: return "Clip  " + (clipName ?? Path.GetFileNameWithoutExtension(file)) + "  (" + file + ")";
                    case TargetKind.Anim: return ".anim  " + Path.GetFileNameWithoutExtension(file);
                    default: return file;
                }
            }
        }

        public string Key => kind + "|" + assetPath + "|" + clipName;
    }

    /// <summary>Turns a selection in the Project window, Hierarchy or scene into things Blender can open.</summary>
    internal static class TargetResolver
    {
        public static List<BridgeTarget> FromProjectSelection(out string unsupported)
        {
            unsupported = null;
            var list = new List<BridgeTarget>();
            foreach (var obj in Selection.objects)
            {
                var path = AssetDatabase.GetAssetPath(obj);
                if (string.IsNullOrEmpty(path)) continue;
                if (ModelInfo.IsFbx(path))
                {
                    bool hasMesh = ModelInfo.HasMesh(path);
                    if (obj is AnimationClip clip)
                        list.Add(new BridgeTarget
                        {
                            kind = hasMesh ? TargetKind.EmbeddedClip : TargetKind.ClipFbx, assetPath = path,
                            clipName = clip.name,
                        });
                    else
                        list.Add(new BridgeTarget { kind = hasMesh ? TargetKind.Model : TargetKind.ClipFbx, assetPath = path });
                }
                else if (ModelInfo.IsAnim(path))
                {
                    list.Add(new BridgeTarget { kind = TargetKind.Anim, assetPath = path, clipName = obj.name });
                }
                else if (obj is GameObject go)
                {
                    list.AddRange(FromGameObject(go));
                }
                else if (obj is Mesh)
                {
                    unsupported = "Meshes stored outside FBX files (.asset, ProBuilder) are planned for v1.1.";
                }
            }
            return Dedupe(list);
        }

        public static List<BridgeTarget> FromGameObjects(IEnumerable<GameObject> gos) =>
            Dedupe(gos.Where(g => g != null).SelectMany(FromGameObject).ToList());

        static List<BridgeTarget> FromGameObject(GameObject go)
        {
            var list = new List<BridgeTarget>();
            var modelRoots = new Dictionary<string, Transform>();
            foreach (var r in go.GetComponentsInChildren<Renderer>(true))
            {
                Mesh mesh = null;
                if (r is SkinnedMeshRenderer smr) mesh = smr.sharedMesh;
                else if (r is MeshRenderer)
                {
                    var mf = r.GetComponent<MeshFilter>();
                    if (mf != null) mesh = mf.sharedMesh;
                }
                if (mesh == null) continue;
                var path = AssetDatabase.GetAssetPath(mesh);
                if (!ModelInfo.IsFbx(path)) continue;
                list.Add(new BridgeTarget { kind = TargetKind.Model, assetPath = path });
                if (!modelRoots.ContainsKey(path)) modelRoots[path] = FindModelRoot(r.transform, path);
            }

            var animator = go.GetComponentInParent<Animator>();
            if (animator == null) animator = go.GetComponentInChildren<Animator>(true);
            if (animator != null && animator.runtimeAnimatorController != null)
            {
                string rig = null;
                if (animator.avatar != null) rig = AssetDatabase.GetAssetPath(animator.avatar);
                if (string.IsNullOrEmpty(rig) || !ModelInfo.HasMesh(rig))
                    rig = modelRoots.Keys.FirstOrDefault(ModelInfo.HasSkin) ?? modelRoots.Keys.FirstOrDefault();
                string prefix = "";
                if (rig != null && modelRoots.TryGetValue(rig, out var root) && root != null && root != animator.transform)
                    prefix = AnimationUtility.CalculateTransformPath(root, animator.transform);
                foreach (var clip in animator.runtimeAnimatorController.animationClips.Distinct())
                {
                    var cp = AssetDatabase.GetAssetPath(clip);
                    if (ModelInfo.IsAnim(cp))
                        list.Add(new BridgeTarget { kind = TargetKind.Anim, assetPath = cp, clipName = clip.name, rigPath = rig, pathPrefix = prefix });
                    else if (ModelInfo.IsFbx(cp))
                        list.Add(new BridgeTarget
                        {
                            kind = ModelInfo.HasMesh(cp) ? TargetKind.EmbeddedClip : TargetKind.ClipFbx,
                            assetPath = cp, clipName = clip.name, rigPath = rig,
                        });
                }
            }
            return list;
        }

        /// <summary>The scene transform that corresponds to the model file's root object.</summary>
        static Transform FindModelRoot(Transform t, string modelPath)
        {
            var cur = t;
            while (cur != null)
            {
                if (PrefabUtility.IsAnyPrefabInstanceRoot(cur.gameObject) &&
                    PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(cur.gameObject) == modelPath)
                    return cur;
                cur = cur.parent;
            }
            return null;
        }

        static List<BridgeTarget> Dedupe(List<BridgeTarget> list)
        {
            var seen = new HashSet<string>();
            var result = new List<BridgeTarget>();
            foreach (var t in list)
                if (seen.Add(t.Key)) result.Add(t);
            // A model already listed makes its embedded clips redundant only when nothing else asks for them.
            return result;
        }

        /// <summary>Finds the model whose skeleton a clip plays on. May ask the user.</summary>
        public static void ResolveRig(BridgeTarget t, Action<BridgeTarget> done)
        {
            if (t.kind == TargetKind.Model || t.kind == TargetKind.EmbeddedClip || t.kind == TargetKind.NewModel)
            {
                t.rigPath = t.assetPath;
                done(t);
                return;
            }
            if (!string.IsNullOrEmpty(t.rigPath) && ModelInfo.HasMesh(t.rigPath))
            {
                done(t);
                return;
            }
            var clipGuid = AssetDatabase.AssetPathToGUID(t.assetPath);
            var link = LinksStore.FindClipRig(clipGuid);
            if (link != null)
            {
                var p = AssetDatabase.GUIDToAssetPath(link.rig);
                if (!string.IsNullOrEmpty(p) && ModelInfo.HasMesh(p))
                {
                    t.rigPath = p;
                    if (string.IsNullOrEmpty(t.pathPrefix)) t.pathPrefix = link.prefix;
                    done(t);
                    return;
                }
            }
            if (t.kind == TargetKind.ClipFbx)
            {
                var mi = (ModelImporter)AssetImporter.GetAtPath(t.assetPath);
                if (mi.sourceAvatar != null)
                {
                    var p = AssetDatabase.GetAssetPath(mi.sourceAvatar);
                    if (ModelInfo.HasMesh(p))
                    {
                        t.rigPath = p;
                        done(t);
                        return;
                    }
                }
            }
            var candidates = RigCandidates(t);
            if (candidates.Count == 1)
            {
                t.rigPath = candidates[0];
                LinksStore.SetClipRig(clipGuid, AssetDatabase.AssetPathToGUID(t.rigPath), t.pathPrefix);
                done(t);
                return;
            }
            var all = candidates.Count > 0 ? candidates : AllSkinnedModels();
            if (all.Count == 0)
            {
                EditorUtility.DisplayDialog("Blender Bridge",
                    "No skinned model found to play this clip on. Open the clip from an object with an Animator in the scene.",
                    "OK");
                return;
            }
            PickerWindow.Show("Which model does this clip play on?", all.Select(p => p).ToList(), all,
                p =>
                {
                    t.rigPath = p;
                    LinksStore.SetClipRig(clipGuid, AssetDatabase.AssetPathToGUID(p), t.pathPrefix);
                    done(t);
                });
        }

        static List<string> AllSkinnedModels()
        {
            var roots = BridgeSettings.instance.projectRoots.Where(r => AssetDatabase.IsValidFolder(r)).ToArray();
            var guids = roots.Length > 0 ? AssetDatabase.FindAssets("t:Model", roots) : AssetDatabase.FindAssets("t:Model");
            return guids.Select(AssetDatabase.GUIDToAssetPath).Where(ModelInfo.HasSkin).Distinct().OrderBy(p => p).ToList();
        }

        /// <summary>Models near the clip whose bones cover the clip's bones.</summary>
        static List<string> RigCandidates(BridgeTarget t)
        {
            HashSet<string> wanted;
            bool humanoid = false;
            if (t.kind == TargetKind.ClipFbx)
            {
                wanted = ModelInfo.TransformNames(t.assetPath);
            }
            else
            {
                var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(t.assetPath);
                humanoid = clip != null && clip.humanMotion;
                wanted = new HashSet<string>(AnimationUtility.GetCurveBindings(clip)
                    .Where(b => b.type == typeof(Transform))
                    .Select(b => b.path.Split('/').Last()));
            }
            var dir = Path.GetDirectoryName(t.assetPath)?.Replace('\\', '/');
            var search = new List<string>();
            for (int i = 0; i < 3 && !string.IsNullOrEmpty(dir) && dir != "Assets"; i++)
            {
                search.Add(dir);
                dir = Path.GetDirectoryName(dir)?.Replace('\\', '/');
            }
            var found = new List<(string path, float score)>();
            foreach (var guid in AssetDatabase.FindAssets("t:Model", search.Count > 0 ? search.ToArray() : null))
            {
                var p = AssetDatabase.GUIDToAssetPath(guid);
                if (p == t.assetPath || !ModelInfo.HasSkin(p)) continue;
                var mi = (ModelImporter)AssetImporter.GetAtPath(p);
                if (humanoid && mi.animationType != ModelImporterAnimationType.Human) continue;
                if (humanoid) { found.Add((p, 1f)); continue; }
                if (wanted.Count == 0) continue;
                var names = ModelInfo.TransformNames(p);
                float score = wanted.Count(names.Contains) / (float)wanted.Count;
                if (score >= 0.9f) found.Add((p, score));
            }
            return found.OrderByDescending(f => f.score).Select(f => f.path).Distinct().ToList();
        }
    }
}
