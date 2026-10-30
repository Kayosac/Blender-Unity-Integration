using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    /// <summary>Export manifests Blender left in the outbox that Unity has not processed yet.</summary>
    internal static class PendingItems
    {
        internal sealed class Entry
        {
            public string manifestFile;
            public ExportManifest manifest;
            public ExportItem item;
            public string Key => "FBB.applied." + Path.GetFileName(manifestFile) + "|" + item.assetPath;
        }

        static string _signature;
        static Dictionary<string, Entry> _byPath = new Dictionary<string, Entry>();

        public static void Invalidate() => _signature = null;

        public static Entry Find(string assetPath)
        {
            Refresh();
            return _byPath.TryGetValue(assetPath, out var e) ? e : null;
        }

        static void Refresh()
        {
            var dir = BridgePaths.Outbox;
            if (!Directory.Exists(dir))
            {
                _byPath.Clear();
                return;
            }
            var files = Directory.GetFiles(dir, "*.json").OrderBy(f => f).ToArray();
            var sig = string.Join("|", files.Select(f => f + File.GetLastWriteTimeUtc(f).Ticks));
            if (sig == _signature) return;
            _signature = sig;
            _byPath = new Dictionary<string, Entry>();
            foreach (var f in files)
            {
                var m = BridgeIO.ReadJson<ExportManifest>(f);
                if (m?.items == null) continue;
                foreach (var it in m.items)
                {
                    if (!string.IsNullOrEmpty(it.assetPath))
                        _byPath[it.assetPath] = new Entry { manifestFile = f, manifest = m, item = it };
                }
            }
        }
    }

    /// <summary>Applies the settings an export needs, during the import itself.</summary>
    internal sealed class BridgePostprocessor : AssetPostprocessor
    {
        public override int GetPostprocessOrder() => 100;

        void OnPreprocessModel()
        {
            var entry = PendingItems.Find(assetPath);
            if (entry == null) return;
            if (SessionState.GetBool(entry.Key, false)) return;
            try
            {
                ImportSettingsWriter.Apply((ModelImporter)assetImporter, entry.item);
                SessionState.SetBool(entry.Key, true);
            }
            catch (Exception e)
            {
                BridgeIO.Error("Could not apply clip settings to " + assetPath + ": " + e.Message);
            }
        }

        void OnPostprocessModel(GameObject root)
        {
            Colliders.Apply(root);
        }

        static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            ExportProcessor.NotifyImported(imported);
        }
    }

    internal static class ImportSettingsWriter
    {
        public static void Apply(ModelImporter mi, ExportItem item)
        {
            var rigPath = string.IsNullOrEmpty(item.rigGuid) ? null : AssetDatabase.GUIDToAssetPath(item.rigGuid);
            var rigMi = string.IsNullOrEmpty(rigPath) ? null : AssetImporter.GetAtPath(rigPath) as ModelImporter;
            bool clipFile = item.kind == "clip" || item.kind == "anim";

            if (clipFile && (item.isNew || item.kind == "anim") && rigMi != null && rigMi != mi)
            {
                // A clip file must import exactly like its model so bone transforms and paths match.
                mi.globalScale = rigMi.globalScale;
                mi.useFileScale = rigMi.useFileScale;
                mi.bakeAxisConversion = rigMi.bakeAxisConversion;
                // Unity turns a single top-level node into the prefab root. When the model has other top-level
                // objects its armature node survives, so the clip file (armature only) must keep it too.
                mi.preserveHierarchy = item.modelMultiRoot || rigMi.preserveHierarchy;  // refined after import
                mi.materialImportMode = ModelImporterMaterialImportMode.None;
                mi.importCameras = false;
                mi.importLights = false;
                mi.importVisibility = false;
                mi.importBlendShapes = item.hasMesh;
                mi.importAnimation = true;
                // Humanoid needs the model's avatar; ExportProcessor sets it right after this import.
                mi.animationType = ModelImporterAnimationType.Generic;
                mi.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
                mi.motionNodeName = rigMi.animationType == ModelImporterAnimationType.Generic ? rigMi.motionNodeName : "";
                if (item.kind == "anim")
                {
                    mi.animationCompression = ModelImporterAnimationCompression.Off;
                    mi.resampleCurves = true;
                }
            }

            var clips = (mi.clipAnimations ?? Array.Empty<ModelImporterClipAnimation>()).ToList();
            bool changed = false;

            // Takes that came back under another name or start time: keep Unity's clip cuts on them.
            var moved = item.takes.Where(t => t.originalName != t.exportedName ||
                                              Mathf.Abs(t.exportedStart - t.originalStart) > 1e-4f).ToList();
            if (moved.Count > 0)
            {
                if (clips.Count == 0)
                {
                    var snap = ModelSnapshot.Load(AssetDatabase.AssetPathToGUID(mi.assetPath));
                    if (snap != null)
                        clips = snap.clipSettings.Select(c => new ModelImporterClipAnimation
                        {
                            name = c.name, takeName = c.takeName, firstFrame = c.firstFrame, lastFrame = c.lastFrame,
                            loopTime = c.loop,
                        }).ToList();
                }
                foreach (var t in moved)
                {
                    float delta = t.exportedStart - t.originalStart;
                    foreach (var c in clips.Where(c => c.takeName == t.originalName))
                    {
                        c.takeName = t.exportedName;
                        c.firstFrame += delta;
                        c.lastFrame += delta;
                        changed = true;
                    }
                }
            }

            // Clips whose loop and root motion Blender manages.
            foreach (var mc in item.clips)
            {
                var c = clips.FirstOrDefault(x => x.name == mc.name);
                if (c == null)
                {
                    c = new ModelImporterClipAnimation { name = mc.name };
                    clips.Add(c);
                }
                c.takeName = mc.takeName;
                c.firstFrame = mc.firstFrame;
                c.lastFrame = mc.lastFrame;
                c.loopTime = mc.loop;
                ModelInfo.ApplyRootMotion(c, mc.rootMotion);
                changed = true;
            }
            if (changed) mi.clipAnimations = clips.ToArray();
        }
    }

    internal static class Colliders
    {
        public static void Apply(GameObject root)
        {
            var s = BridgeSettings.instance;
            if (!s.collidersEnabled || string.IsNullOrEmpty(s.colliderSuffix)) return;
            var re = new Regex("^(.*)" + Regex.Escape(s.colliderSuffix) + "(C)?(_\\d+)?$");
            foreach (var t in root.GetComponentsInChildren<Transform>(true))
            {
                var m = re.Match(t.name);
                if (!m.Success) continue;
                var mf = t.GetComponent<MeshFilter>();
                if (mf == null || mf.sharedMesh == null) continue;
                var mc = t.gameObject.AddComponent<MeshCollider>();
                mc.sharedMesh = mf.sharedMesh;
                mc.convex = m.Groups[2].Success;
                var mr = t.GetComponent<MeshRenderer>();
                if (mr != null) UnityEngine.Object.DestroyImmediate(mr);
                UnityEngine.Object.DestroyImmediate(mf);
            }
        }
    }
}
