using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    /// <summary>Project-wide settings, stored in ProjectSettings/ and committed with the project.</summary>
    [FilePath("ProjectSettings/BlenderBridgeSettings.asset", FilePathAttribute.Location.ProjectFolder)]
    internal sealed class BridgeSettings : ScriptableSingleton<BridgeSettings>
    {
        [Tooltip("Folder next to Assets that holds working .blend files.")]
        public string artSourceFolder = "ArtSource";

        [Tooltip("Your own content. Files outside these folders count as third-party (Asset Store, packages).")]
        public List<string> projectRoots = new List<string> { "Assets/Project" };

        [Tooltip("Synced cache files that were not opened for this many days are deleted.")]
        public int cacheDays = 14;

        [Tooltip("Exports kept per file for rollback.")]
        public int historySize = 10;

        [Tooltip("Total size limit of the export history, in megabytes.")]
        public int historyMaxMB = 2048;

        [Tooltip("Subfolder next to a model where new clips are written.")]
        public string clipsFolder = "Animations";

        [Tooltip("Pin the .blend automatically when a new clip is exported, so its original keys are kept.")]
        public bool autoPinClips = false;

        [Tooltip("Objects named Name_COL become MeshColliders without a renderer; Name_COLC become convex ones.")]
        public bool collidersEnabled = true;

        public string colliderSuffix = "_COL";

        [Tooltip("Copied for new material slots that have no material with the same name in the project.")]
        public Material templateMaterial;

        [Tooltip("Default folder for Create ▸ Blender Model when nothing is selected.")]
        public string newModelFolder = "Assets/Project/Content";

        public void Save() => Save(true);

        public bool IsThirdParty(string assetPath)
        {
            if (string.IsNullOrEmpty(assetPath) || projectRoots == null || projectRoots.Count == 0) return false;
            foreach (var root in projectRoots)
            {
                if (string.IsNullOrWhiteSpace(root)) continue;
                var r = root.Trim().TrimEnd('/') + "/";
                if (assetPath.StartsWith(r, System.StringComparison.Ordinal)) return false;
            }
            return true;
        }
    }

    /// <summary>Settings that belong to this computer, not the project.</summary>
    internal static class BridgeUserPrefs
    {
        const string KeyBlender = "Farik.BlenderBridge.BlenderPath";
        const string KeyCleanup = "Farik.BlenderBridge.LastCleanup";

        public static string BlenderPath
        {
            get => EditorPrefs.GetString(KeyBlender, "");
            set => EditorPrefs.SetString(KeyBlender, value ?? "");
        }

        public static string LastCleanup
        {
            get => EditorPrefs.GetString(KeyCleanup, "");
            set => EditorPrefs.SetString(KeyCleanup, value ?? "");
        }
    }
}
