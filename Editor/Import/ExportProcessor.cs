using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    /// <summary>Picks up what Blender exported: imports it, finishes clip and material setup, checks the result.</summary>
    [InitializeOnLoad]
    internal static class ExportProcessor
    {
        static double _next;
        static bool _busy;

        static ExportProcessor()
        {
            EditorApplication.update += Tick;
            EditorApplication.focusChanged += focused =>
            {
                if (focused) _next = 0;
            };
        }

        public static void NotifyImported(string[] paths)
        {
            if (paths != null && paths.Length > 0) _next = 0;
        }

        static void Tick()
        {
            if (_busy || EditorApplication.isCompiling || EditorApplication.isUpdating ||
                EditorApplication.isPlayingOrWillChangePlaymode)
                return;
            if (EditorApplication.timeSinceStartup < _next) return;
            _next = EditorApplication.timeSinceStartup + 1.5;
            SelfTest.CheckRequest();
            string[] files;
            try
            {
                if (!Directory.Exists(BridgePaths.Outbox)) return;
                files = Directory.GetFiles(BridgePaths.Outbox, "*.json").OrderBy(f => f).ToArray();
            }
            catch (IOException)
            {
                return;
            }
            if (files.Length == 0) return;
            _busy = true;
            try
            {
                foreach (var f in files) Process(f);
            }
            finally
            {
                _busy = false;
                PendingItems.Invalidate();
            }
        }

        public static void ProcessNow()
        {
            _next = 0;
            Tick();
        }

        static void Process(string file)
        {
            var m = BridgeIO.ReadJson<ExportManifest>(file);
            var reports = new List<ExportReport>();
            if (m == null || m.items == null)
            {
                BridgeIO.Warn("Skipped an unreadable export manifest: " + Path.GetFileName(file));
                MoveToProcessed(file);
                return;
            }
            try
            {
                var models = m.items.Where(i => i.kind != "anim").ToList();

                // 1. Bring the written files in. The postprocessor applies take remaps and clip settings.
                foreach (var it in models)
                    if (File.Exists(BridgePaths.Abs(it.assetPath)))
                        AssetDatabase.ImportAsset(it.assetPath, ImportAssetOptions.ForceSynchronousImport);

                foreach (var it in m.items)
                {
                    var r = new ExportReport { timeUtc = BridgeIO.NowIso(), assetPath = it.kind == "anim" ? it.animPath : it.assetPath };
                    try
                    {
                        if (it.kind == "clip" && it.isNew) FinishNewClip(it, r);
                        else if (it.kind == "anim") AnimTransfer.Run(it, r);
                        if (it.kind == "model") MaterialResolver.Resolve(it.assetPath, r);
                        if (it.kind != "anim" && !it.isNew) Verify(m, it, r);
                        else if (it.kind == "clip" && it.isNew) r.infos.Add("New clip created");
                        ModelSnapshot.Save(it.kind == "anim" ? it.animPath : it.assetPath);
                    }
                    catch (Exception e)
                    {
                        r.errors.Add(e.Message);
                        Debug.LogException(e);
                    }
                    r.summary = Summary(it, r);
                    reports.Add(r);
                }
                foreach (var w in m.warnings ?? new List<string>()) BridgeIO.Warn("Blender: " + w);
            }
            finally
            {
                foreach (var r in reports) LogReport(r);
                ReportsStore.Append(reports);
                MoveToProcessed(file);
                BridgeWindow.RepaintIfOpen();
            }
        }

        static void FinishNewClip(ExportItem it, ExportReport r)
        {
            var mi = AssetImporter.GetAtPath(it.assetPath) as ModelImporter;
            var rigPath = AssetDatabase.GUIDToAssetPath(it.rigGuid);
            var rigMi = AssetImporter.GetAtPath(rigPath) as ModelImporter;
            if (mi == null || rigMi == null)
            {
                r.warnings.Add("Clip imported, but its model was not found to copy the rig from");
                return;
            }
            bool reimport = ModelInfo.MatchRootCollapse(mi, rigPath, it.armatureName);
            if (rigMi.animationType == ModelImporterAnimationType.Human)
            {
                mi.animationType = ModelImporterAnimationType.Human;
                mi.avatarSetup = ModelImporterAvatarSetup.CopyFromOther;
                mi.sourceAvatar = ModelInfo.GetAvatar(rigPath);
                reimport = true;
            }
            if (reimport) mi.SaveAndReimport();
            LinksStore.SetClipRig(AssetDatabase.AssetPathToGUID(it.assetPath), it.rigGuid, "");
        }

        static void Verify(ExportManifest m, ExportItem it, ExportReport r)
        {
            var guid = AssetDatabase.AssetPathToGUID(it.assetPath);
            var before = ModelSnapshot.Load(guid);
            if (before == null)
            {
                r.infos.Add("No snapshot from before the edit; skipped the comparison");
                return;
            }
            var after = ModelSnapshot.Capture(it.assetPath);
            bool isModel = it.kind == "model";
            ModelSnapshot.Compare(before, after,
                modelChanged: isModel && m.modelChanged,
                skeletonChanged: isModel && m.skeletonChanged,
                changedTakes: it.changedTakes,
                managedSettings: it.clips != null && it.clips.Count > 0,
                report: r);
        }

        static string Summary(ExportItem it, ExportReport r)
        {
            var name = Path.GetFileName(it.kind == "anim" ? it.animPath : it.assetPath);
            if (r.errors.Count > 0) return $"{name}: {r.errors.Count} problem(s) — see details, you can roll back in Window ▸ Blender Bridge";
            if (r.warnings.Count > 0) return $"{name}: imported with {r.warnings.Count} warning(s)";
            return $"{name}: imported, everything untouched stayed the same";
        }

        static void LogReport(ExportReport r)
        {
            var lines = new List<string> { r.summary };
            lines.AddRange(r.errors.Select(e => "  ✖ " + e));
            lines.AddRange(r.warnings.Select(w => "  ! " + w));
            lines.AddRange(r.infos.Select(i => "  · " + i));
            var text = string.Join("\n", lines);
            if (r.errors.Count > 0) BridgeIO.Error(text);
            else if (r.warnings.Count > 0) BridgeIO.Warn(text);
            else BridgeIO.Log(text);
        }

        static void MoveToProcessed(string file)
        {
            try
            {
                Directory.CreateDirectory(BridgePaths.Processed);
                var dst = Path.Combine(BridgePaths.Processed, Path.GetFileName(file));
                if (File.Exists(dst)) File.Delete(dst);
                File.Move(file, dst);
            }
            catch (IOException e)
            {
                BridgeIO.Warn("Could not move " + file + ": " + e.Message);
            }
        }
    }

    internal static class ReportsStore
    {
        public static List<ExportReport> Load() =>
            BridgeIO.ReadJson<ReportsFile>(BridgePaths.Reports)?.reports ?? new List<ExportReport>();

        public static void Append(List<ExportReport> reports)
        {
            if (reports.Count == 0) return;
            var all = Load();
            all.InsertRange(0, reports);
            if (all.Count > 100) all.RemoveRange(100, all.Count - 100);
            BridgeIO.WriteJson(BridgePaths.Reports, new ReportsFile { reports = all });
        }
    }
}
