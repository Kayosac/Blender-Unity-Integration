using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    internal static class BridgeSettingsProvider
    {
        public const string ProjectPath = "Project/Blender Bridge";
        public const string PreferencesPath = "Preferences/Blender Bridge";

        [SettingsProvider]
        static SettingsProvider Project() => new SettingsProvider(ProjectPath, SettingsScope.Project)
        {
            label = "Blender Bridge",
            keywords = new HashSet<string> { "blender", "fbx", "bridge", "clip", "cache" },
            guiHandler = _ =>
            {
                var s = BridgeSettings.instance;
                // ScriptableSingleton instances carry HideAndDontSave, which includes NotEditable:
                // SerializedObject would show the fields read-only and not apply edits.
                s.hideFlags &= ~HideFlags.NotEditable;
                var so = new SerializedObject(s);
                EditorGUI.BeginChangeCheck();
                EditorGUILayout.PropertyField(so.FindProperty("artSourceFolder"), new GUIContent("Working .blend folder"));
                EditorGUILayout.PropertyField(so.FindProperty("projectRoots"), new GUIContent("Your content folders"), true);
                EditorGUILayout.PropertyField(so.FindProperty("newModelFolder"), new GUIContent("New models folder"));
                EditorGUILayout.Space();
                EditorGUILayout.PropertyField(so.FindProperty("cacheDays"), new GUIContent("Remove unused cache after (days)"));
                EditorGUILayout.PropertyField(so.FindProperty("historySize"), new GUIContent("Exports kept per file"));
                EditorGUILayout.PropertyField(so.FindProperty("historyMaxMB"), new GUIContent("History size limit (MB)"));
                EditorGUILayout.Space();
                EditorGUILayout.PropertyField(so.FindProperty("clipsFolder"), new GUIContent("New clips subfolder"));
                EditorGUILayout.PropertyField(so.FindProperty("autoPinClips"), new GUIContent("Auto-pin clips"));
                EditorGUILayout.Space();
                EditorGUILayout.PropertyField(so.FindProperty("collidersEnabled"), new GUIContent("Mesh colliders by suffix"));
                using (new EditorGUI.DisabledScope(!s.collidersEnabled))
                    EditorGUILayout.PropertyField(so.FindProperty("colliderSuffix"), new GUIContent("Collider suffix"));
                EditorGUILayout.PropertyField(so.FindProperty("templateMaterial"), new GUIContent("Template material"));
                if (EditorGUI.EndChangeCheck())
                {
                    so.ApplyModifiedPropertiesWithoutUndo();
                    s.cacheDays = Mathf.Max(0, s.cacheDays);
                    s.historySize = Mathf.Max(1, s.historySize);
                    s.Save();
                }
                EditorGUILayout.Space();
                EditorGUILayout.HelpBox("Name_COL becomes a MeshCollider without a renderer, Name_COLC a convex one, " +
                                        "Name_COL_1 and so on for several. Name_LOD0, _LOD1… get a LODGroup from Unity itself.",
                    MessageType.None);
            },
        };

        [SettingsProvider]
        static SettingsProvider Preferences() => new SettingsProvider(PreferencesPath, SettingsScope.User)
        {
            label = "Blender Bridge",
            keywords = new HashSet<string> { "blender", "path", "addon" },
            guiHandler = _ =>
            {
                var path = BridgeUserPrefs.BlenderPath;
                using (new EditorGUILayout.HorizontalScope())
                {
                    var next = EditorGUILayout.TextField("Blender", path);
                    if (next != path) BridgeUserPrefs.BlenderPath = BlenderLocator.NormalizeSelection(next);
                    if (GUILayout.Button("Browse", GUILayout.Width(70)))
                    {
                        var picked = Application.platform == RuntimePlatform.OSXEditor
                            ? EditorUtility.OpenFilePanel("Blender.app", "/Applications", "app")
                            : EditorUtility.OpenFilePanel("Blender executable", "", "");
                        if (!string.IsNullOrEmpty(picked)) BridgeUserPrefs.BlenderPath = BlenderLocator.NormalizeSelection(picked);
                    }
                    if (GUILayout.Button("Detect", GUILayout.Width(70)))
                    {
                        BridgeUserPrefs.BlenderPath = "";
                        BlenderLocator.Resolve();
                    }
                }
                var exe = BridgeUserPrefs.BlenderPath;
                if (BlenderLocator.IsValid(exe))
                {
                    var v = BlenderLocator.GetVersion(exe);
                    var ok = BlenderLocator.IsSupported(v);
                    EditorGUILayout.HelpBox($"Blender {v}" + (ok ? "" : " — the bridge needs 5.0 or newer"),
                        ok ? MessageType.Info : MessageType.Warning);
                }
                else
                {
                    EditorGUILayout.HelpBox("Blender not found. Press Detect or Browse.", MessageType.Warning);
                }
                EditorGUILayout.Space();
                EditorGUILayout.LabelField("Add-on", EditorStyles.boldLabel);
                EditorGUILayout.HelpBox("Not needed when Blender is opened from Unity: the add-on loads from this package. " +
                                        "Install it only to see the Unity panel in .blend files you open yourself.", MessageType.None);
                using (new EditorGUI.DisabledScope(!BlenderLocator.IsValid(exe)))
                {
                    if (GUILayout.Button("Install add-on into Blender")) AddonInstaller.Install(exe);
                }
            },
        };
    }

    internal static class AddonInstaller
    {
        public static void Install(string exe)
        {
            var src = BridgePaths.BlenderScripts + "/unity_bridge";
            var version = BlenderLocator.ParseVersion(BlenderLocator.GetVersion(exe));
            var dst = Path.Combine(UserScriptsDir(version.Major + "." + version.Minor), "extensions", "user_default", "unity_bridge");
            try
            {
                if (Directory.Exists(dst)) Directory.Delete(dst, true);
                CopyDir(src, dst);
                var ok = BlenderLocator.RunBackground(exe,
                    "import bpy; bpy.ops.preferences.addon_enable(module='bl_ext.user_default.unity_bridge'); bpy.ops.wm.save_userpref()");
                EditorUtility.DisplayDialog("Blender Bridge",
                    ok ? "Add-on installed and enabled." : "Add-on copied to\n" + dst + "\nEnable it in Blender ▸ Preferences ▸ Add-ons.",
                    "OK");
            }
            catch (Exception e)
            {
                EditorUtility.DisplayDialog("Blender Bridge", "Could not install the add-on: " + e.Message, "OK");
            }
        }

        static string UserScriptsDir(string ver)
        {
            var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            switch (Application.platform)
            {
                case RuntimePlatform.OSXEditor:
                    return Path.Combine(home, "Library/Application Support/Blender", ver);
                case RuntimePlatform.WindowsEditor:
                    return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                        "Blender Foundation", "Blender", ver);
                default:
                    return Path.Combine(home, ".config", "blender", ver);
            }
        }

        static void CopyDir(string src, string dst)
        {
            Directory.CreateDirectory(dst);
            foreach (var f in Directory.GetFiles(src))
            {
                if (f.EndsWith(".pyc")) continue;
                File.Copy(f, Path.Combine(dst, Path.GetFileName(f)), true);
            }
            foreach (var d in Directory.GetDirectories(src))
            {
                if (Path.GetFileName(d) == "__pycache__") continue;
                CopyDir(d, Path.Combine(dst, Path.GetFileName(d)));
            }
        }
    }
}
