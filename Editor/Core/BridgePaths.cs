using System;
using System.IO;
using System.Linq;
using UnityEngine;

namespace Farik.BlenderBridge
{
    /// <summary>Folder layout shared with the Blender add-on (Blender~/unity_bridge/paths.py).</summary>
    internal static class BridgePaths
    {
        public const string StagingFolder = "Assets/BlenderBridgeStaging";

        public static string ProjectRoot => Norm(Path.GetFullPath(Path.Combine(Application.dataPath, "..")));
        public static string ArtSource => Norm(Path.Combine(ProjectRoot, BridgeSettings.instance.artSourceFolder));
        public static string Cache => ArtSource + "/Cache";
        public static string Pinned => ArtSource + "/Pinned";
        public static string Links => ArtSource + "/links.json";
        public static string Bridge => ArtSource + "/.bridge";
        public static string Sessions => Bridge + "/sessions";
        public static string Outbox => Bridge + "/outbox";
        public static string Processed => Outbox + "/processed";
        public static string History => Bridge + "/history";
        public static string State => Bridge + "/state";
        public static string Locks => Bridge + "/locks";
        public static string Snapshots => Bridge + "/snapshots";
        public static string Reports => Bridge + "/reports.json";

        public static string Norm(string p) => string.IsNullOrEmpty(p) ? p : p.Replace('\\', '/');

        public static string Abs(string rel) => Norm(Path.GetFullPath(Path.Combine(ProjectRoot, rel)));

        public static string Rel(string abs)
        {
            var root = ProjectRoot.TrimEnd('/') + "/";
            var a = Norm(Path.GetFullPath(abs));
            return a.StartsWith(root, StringComparison.Ordinal) ? a.Substring(root.Length) : a;
        }

        static string WithoutAssets(string assetPath)
        {
            var p = Norm(assetPath);
            return p.StartsWith("Assets/", StringComparison.Ordinal) ? p.Substring("Assets/".Length) : p;
        }

        public static string CacheBlendFor(string assetPath) =>
            Cache + "/" + Path.ChangeExtension(WithoutAssets(assetPath), ".blend");

        public static string PinnedBlendFor(string assetPath) =>
            Pinned + "/" + Path.ChangeExtension(WithoutAssets(assetPath), ".blend");

        public static string StateFile(string guid) => State + "/" + guid + ".json";
        public static string LockFile(string guid) => Locks + "/" + guid + ".lock";
        public static string SnapshotFile(string guid) => Snapshots + "/" + guid + ".json";

        public static void EnsureFolders()
        {
            foreach (var d in new[] { Cache, Sessions, Outbox, Processed, History, State, Locks, Snapshots })
                Directory.CreateDirectory(d);
            // Keep the cache and tool state out of git; pinned .blend files go through LFS.
            WriteIfMissing(ArtSource + "/.gitignore", "Cache/\n.bridge/\n");
            WriteIfMissing(ArtSource + "/.gitattributes", "Pinned/**/*.blend filter=lfs diff=lfs merge=lfs -text\n");
        }

        static void WriteIfMissing(string path, string text)
        {
            if (!File.Exists(path)) File.WriteAllText(path, text);
        }

        static string _packageRoot;

        /// <summary>Where this package lives on disk (Packages/, Library/PackageCache or a copy in Assets).</summary>
        public static string PackageRoot
        {
            get
            {
                if (!string.IsNullOrEmpty(_packageRoot) && Directory.Exists(_packageRoot)) return _packageRoot;
                var info = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(BridgePaths).Assembly);
                if (info != null && Directory.Exists(info.resolvedPath))
                    return _packageRoot = Norm(info.resolvedPath);
                // Fallback: scripts copied into Assets. Unity skips "~" folders but the files are on disk.
                var boot = Directory.GetFiles(Application.dataPath, "bootstrap.py", SearchOption.AllDirectories)
                    .FirstOrDefault(p => Norm(p).Contains("/Blender~/"));
                if (boot != null) return _packageRoot = Norm(Path.GetFullPath(Path.Combine(Path.GetDirectoryName(boot), "..")));
                return null;
            }
        }

        public static string BlenderScripts => PackageRoot == null ? null : PackageRoot + "/Blender~";
        public static string BootstrapScript => BlenderScripts == null ? null : BlenderScripts + "/bootstrap.py";
    }
}
