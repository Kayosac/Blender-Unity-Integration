using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;

namespace Farik.BlenderBridge
{
    internal sealed class HistoryEntry
    {
        public string dir;
        public HistoryInfo info;
        public long size;
    }

    /// <summary>Copies Blender made before overwriting a file. Restoring brings back the file and its .meta.</summary>
    internal static class HistoryStore
    {
        public static List<HistoryEntry> Entries(string guidOrNull)
        {
            var list = new List<HistoryEntry>();
            if (!Directory.Exists(BridgePaths.History)) return list;
            var keys = string.IsNullOrEmpty(guidOrNull)
                ? Directory.GetDirectories(BridgePaths.History)
                : new[] { Path.Combine(BridgePaths.History, guidOrNull) }.Where(Directory.Exists).ToArray();
            foreach (var key in keys)
            foreach (var d in Directory.GetDirectories(key))
            {
                var info = BridgeIO.ReadJson<HistoryInfo>(Path.Combine(d, "info.json"));
                if (info == null) continue;
                list.Add(new HistoryEntry { dir = d, info = info, size = BridgeIO.DirSize(d) });
            }
            return list.OrderByDescending(e => e.info.createdUtc).ToList();
        }

        public static bool Restore(HistoryEntry e)
        {
            var assetPath = !string.IsNullOrEmpty(e.info.guid) ? AssetDatabase.GUIDToAssetPath(e.info.guid) : null;
            if (string.IsNullOrEmpty(assetPath)) assetPath = e.info.assetPath;
            if (string.IsNullOrEmpty(assetPath)) return false;
            var src = Path.Combine(e.dir, e.info.fileName);
            if (!File.Exists(src)) return false;
            var dst = BridgePaths.Abs(assetPath);
            File.Copy(src, dst, true);
            var meta = src + ".meta";
            if (File.Exists(meta)) File.Copy(meta, dst + ".meta", true);
            AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            ModelSnapshot.Save(assetPath);
            BridgeIO.Log($"Restored {assetPath} from {e.info.createdUtc}");
            return true;
        }
    }

    /// <summary>Daily housekeeping: old cache files, processed manifests, session files.</summary>
    [InitializeOnLoad]
    internal static class Cleanup
    {
        static Cleanup()
        {
            EditorApplication.delayCall += RunDaily;
        }

        static void RunDaily()
        {
            var today = DateTime.UtcNow.ToString("yyyy-MM-dd");
            if (BridgeUserPrefs.LastCleanup == today) return;
            if (!Directory.Exists(BridgePaths.Bridge)) return;
            BridgeUserPrefs.LastCleanup = today;
            try
            {
                var removed = CleanCache(BridgeSettings.instance.cacheDays);
                CleanFolder(BridgePaths.Processed, 7);
                CleanFolder(BridgePaths.Sessions, 7);
                if (removed > 0) BridgeIO.Log($"Removed {removed} unused cached .blend file(s).");
            }
            catch (Exception e)
            {
                BridgeIO.Warn("Cleanup failed: " + e.Message);
            }
        }

        /// <summary>Deletes synced, unlocked cache files not opened for <paramref name="days"/> days. 0 = all synced.</summary>
        public static int CleanCache(int days)
        {
            int removed = 0;
            var limit = DateTime.UtcNow.AddDays(-days);
            foreach (var s in StateStore.All().ToList())
            {
                if (s.pinned || s.dirty || string.IsNullOrEmpty(s.blendPath)) continue;
                if (LockStore.ReadAlive(s.guid) != null) continue;
                var last = BridgeIO.ParseIso(s.lastOpenedUtc ?? s.lastExportUtc);
                if (days > 0 && last > limit) continue;
                var blend = BridgePaths.Abs(s.blendPath);
                if (!blend.StartsWith(BridgePaths.Cache, StringComparison.Ordinal)) continue;
                foreach (var f in new[] { blend, blend + "1" })
                    if (File.Exists(f)) File.Delete(f);
                StateStore.Delete(s.guid);
                removed++;
            }
            DeleteEmptyDirs(BridgePaths.Cache);
            return removed;
        }

        static void CleanFolder(string dir, int days)
        {
            if (!Directory.Exists(dir)) return;
            var limit = DateTime.UtcNow.AddDays(-days);
            foreach (var f in Directory.GetFiles(dir))
                if (File.GetLastWriteTimeUtc(f) < limit) File.Delete(f);
        }

        static void DeleteEmptyDirs(string root)
        {
            if (!Directory.Exists(root)) return;
            foreach (var d in Directory.GetDirectories(root, "*", SearchOption.AllDirectories).OrderByDescending(d => d.Length))
                if (!Directory.EnumerateFileSystemEntries(d).Any()) Directory.Delete(d);
        }
    }
}
