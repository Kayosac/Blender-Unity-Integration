using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    /// <summary>What Unity sees in an imported model, captured before Blender writes and compared after.</summary>
    [Serializable]
    internal class ModelSnapshot
    {
        public string guid;
        public string assetPath;
        public List<SubAsset> subAssets = new List<SubAsset>();
        public List<NodeTx> nodes = new List<NodeTx>();
        public List<string> bonePaths = new List<string>();
        public List<string> materials = new List<string>();
        public List<ClipSample> clips = new List<ClipSample>();
        public List<string> importer = new List<string>();
        public List<ClipSetting> clipSettings = new List<ClipSetting>();

        [Serializable]
        public class SubAsset
        {
            public string type;
            public string name;
            public long fileId;
        }

        [Serializable]
        public class NodeTx
        {
            public string path;
            public Vector3 pos;
            public Quaternion rot;
            public Vector3 scale;
        }

        [Serializable]
        public class ClipSample
        {
            public string name;
            public float length;
            public List<string> bindings = new List<string>();
            public List<float> values = new List<float>(); // bindings.Count * SampleCount
        }

        [Serializable]
        public class ClipSetting
        {
            public string name;
            public string takeName;
            public float firstFrame;
            public float lastFrame;
            public bool loop;
        }

        const int SampleCount = 24;

        public static ModelSnapshot Capture(string path)
        {
            var s = new ModelSnapshot { assetPath = path, guid = AssetDatabase.AssetPathToGUID(path) };
            foreach (var o in AssetDatabase.LoadAllAssetsAtPath(path))
            {
                if (o == null) continue;
                if (AssetDatabase.TryGetGUIDAndLocalFileIdentifier(o, out string _, out long id))
                    s.subAssets.Add(new SubAsset { type = o.GetType().Name, name = o.name, fileId = id });
                if (o is Material) s.materials.Add(o.name);
            }
            var go = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            if (go != null)
            {
                foreach (var t in go.GetComponentsInChildren<Transform>(true))
                {
                    if (t == go.transform) continue;
                    s.nodes.Add(new NodeTx
                    {
                        path = AnimationUtility.CalculateTransformPath(t, go.transform),
                        pos = t.localPosition, rot = t.localRotation, scale = t.localScale,
                    });
                }
                var bones = new HashSet<Transform>();
                foreach (var smr in go.GetComponentsInChildren<SkinnedMeshRenderer>(true))
                    foreach (var b in smr.bones) if (b != null) bones.Add(b);
                s.bonePaths = bones.Select(b => AnimationUtility.CalculateTransformPath(b, go.transform)).OrderBy(p => p).ToList();
            }
            if (AssetImporter.GetAtPath(path) is ModelImporter mi)
            {
                foreach (var kv in mi.GetExternalObjectMap())
                    if (kv.Key.type == typeof(Material) && !s.materials.Contains(kv.Key.name)) s.materials.Add(kv.Key.name);
                s.importer = ImporterFields(mi);
                foreach (var c in ModelInfo.EffectiveClips(mi) ?? Array.Empty<ModelImporterClipAnimation>())
                    s.clipSettings.Add(new ClipSetting
                    {
                        name = c.name, takeName = c.takeName, firstFrame = c.firstFrame, lastFrame = c.lastFrame,
                        loop = c.loopTime,
                    });
            }
            foreach (var clip in ModelInfo.Clips(path))
            {
                var cs = new ClipSample { name = clip.name, length = clip.length };
                foreach (var b in AnimationUtility.GetCurveBindings(clip))
                {
                    var curve = AnimationUtility.GetEditorCurve(clip, b);
                    if (curve == null) continue;
                    cs.bindings.Add(b.path + "|" + b.type.Name + "|" + b.propertyName);
                    for (int i = 0; i < SampleCount; i++)
                        cs.values.Add(curve.Evaluate(clip.length * i / (SampleCount - 1)));
                }
                s.clips.Add(cs);
            }
            s.materials.Sort();
            return s;
        }

        static List<string> ImporterFields(ModelImporter mi) => new List<string>
        {
            "globalScale=" + mi.globalScale,
            "useFileScale=" + mi.useFileScale,
            "bakeAxisConversion=" + mi.bakeAxisConversion,
            "animationType=" + mi.animationType,
            "avatarSetup=" + mi.avatarSetup,
            "motionNodeName=" + mi.motionNodeName,
            "importBlendShapes=" + mi.importBlendShapes,
            "importNormals=" + mi.importNormals,
            "importTangents=" + mi.importTangents,
            "materialImportMode=" + mi.materialImportMode,
            "meshCompression=" + mi.meshCompression,
            "isReadable=" + mi.isReadable,
            "generateSecondaryUV=" + mi.generateSecondaryUV,
            "optimizeGameObjects=" + mi.optimizeGameObjects,
            "importAnimation=" + mi.importAnimation,
            "preserveHierarchy=" + mi.preserveHierarchy,
        };

        public static void Save(string path)
        {
            var guid = AssetDatabase.AssetPathToGUID(path);
            if (string.IsNullOrEmpty(guid)) return;
            BridgeIO.WriteJson(BridgePaths.SnapshotFile(guid), Capture(path));
        }

        public static ModelSnapshot Load(string guid) => BridgeIO.ReadJson<ModelSnapshot>(BridgePaths.SnapshotFile(guid));

        /// <summary>Compares before/after. Untouched parts must match; intended edits are reported as info.</summary>
        public static void Compare(ModelSnapshot before, ModelSnapshot after, bool modelChanged, bool skeletonChanged,
            ICollection<string> changedTakes, bool managedSettings, ExportReport report)
        {
            if (before == null || after == null) return;
            if (before.guid != after.guid) report.errors.Add("GUID changed: " + before.guid + " -> " + after.guid);

            var afterSubs = after.subAssets.GroupBy(x => x.type + "|" + x.name).ToDictionary(g => g.Key, g => g.First().fileId);
            foreach (var sub in before.subAssets)
            {
                if (sub.type == "GameObject" || sub.type == "Transform") continue; // covered by node checks
                if (!afterSubs.TryGetValue(sub.type + "|" + sub.name, out long id))
                {
                    var msg = $"{sub.type} '{sub.name}' is gone: references to it are broken";
                    if (sub.type == "AnimationClip" || sub.type == "Avatar" || !modelChanged) report.errors.Add(msg);
                    else report.warnings.Add(msg);
                }
                else if (id != sub.fileId)
                {
                    report.errors.Add($"{sub.type} '{sub.name}' got a new file ID: references to it are broken");
                }
            }

            var afterNodes = after.nodes.GroupBy(n => n.path).ToDictionary(g => g.Key, g => g.First());
            var bones = new HashSet<string>(before.bonePaths);
            int moved = 0;
            foreach (var n in before.nodes)
            {
                if (!afterNodes.TryGetValue(n.path, out var a))
                {
                    var msg = "Object '" + n.path + "' is gone (renamed or removed)";
                    if (!modelChanged) report.errors.Add(msg);
                    else report.warnings.Add(msg);
                    continue;
                }
                bool same = (n.pos - a.pos).magnitude <= 1e-4f && Quaternion.Angle(n.rot, a.rot) <= 0.01f &&
                            (n.scale - a.scale).magnitude <= 1e-4f;
                if (same) continue;
                bool isBone = bones.Contains(n.path);
                if (!modelChanged || (isBone && !skeletonChanged))
                    report.errors.Add("'" + n.path + "' moved although it was not edited");
                else
                    moved++;
            }
            if (moved > 0) report.infos.Add(moved + " objects moved as edited");
            var added = after.nodes.Count(n => before.nodes.All(b => b.path != n.path));
            if (added > 0) report.infos.Add(added + " objects added");

            var newMats = after.materials.Except(before.materials).ToList();
            if (newMats.Count > 0) report.infos.Add("New material slots: " + string.Join(", ", newMats));

            var beforeImp = new HashSet<string>(before.importer);
            foreach (var line in after.importer.Where(l => !beforeImp.Contains(l)))
                report.warnings.Add("Import setting changed: " + line);

            if (!managedSettings)
            {
                var afterSettings = after.clipSettings.GroupBy(c => c.name).ToDictionary(g => g.Key, g => g.First());
                foreach (var c in before.clipSettings)
                {
                    if (!afterSettings.TryGetValue(c.name, out var a))
                    {
                        report.errors.Add("Clip setting '" + c.name + "' was lost");
                        continue;
                    }
                    if (a.loop != c.loop || Math.Abs((a.lastFrame - a.firstFrame) - (c.lastFrame - c.firstFrame)) > 0.01f)
                        report.warnings.Add("Clip '" + c.name + "' range or loop changed");
                }
            }

            var takeOfClip = before.clipSettings.GroupBy(c => c.name).ToDictionary(g => g.Key, g => g.First().takeName);
            var afterClips = after.clips.GroupBy(c => c.name).ToDictionary(g => g.Key, g => g.First());
            foreach (var c in before.clips)
            {
                if (!afterClips.TryGetValue(c.name, out var a))
                {
                    report.errors.Add("Clip '" + c.name + "' is gone");
                    continue;
                }
                takeOfClip.TryGetValue(c.name, out var take);
                if (changedTakes != null && (changedTakes.Contains(c.name) || (take != null && changedTakes.Contains(take))))
                    continue;
                float err = CurveError(c, a);
                if (err > 1e-3f) report.errors.Add($"Clip '{c.name}' changed (max {err:0.####}) although it was not edited");
            }
        }

        static float CurveError(ClipSample a, ClipSample b)
        {
            if (Math.Abs(a.length - b.length) > 1e-3f) return float.PositiveInfinity;
            var idx = new Dictionary<string, int>();
            for (int i = 0; i < b.bindings.Count; i++) idx[b.bindings[i]] = i;
            float err = 0f;
            for (int i = 0; i < a.bindings.Count; i++)
            {
                if (!idx.TryGetValue(a.bindings[i], out int j)) continue;
                for (int k = 0; k < SampleCount; k++)
                    err = Mathf.Max(err, Mathf.Abs(a.values[i * SampleCount + k] - b.values[j * SampleCount + k]));
            }
            return err;
        }
    }
}
