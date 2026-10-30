using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    internal static class BridgeMenus
    {
        const string Root = "Assets/Blender/";

        [MenuItem(Root + "Open in Blender", false, 1100)]
        static void OpenFromProject()
        {
            var targets = TargetResolver.FromProjectSelection(out var unsupported);
            SessionLauncher.OpenTargets(targets, unsupported);
        }

        [MenuItem(Root + "Open in Blender", true)]
        static bool OpenFromProjectValidate() =>
            Selection.objects.Any(o =>
            {
                var p = AssetDatabase.GetAssetPath(o);
                return ModelInfo.IsFbx(p) || ModelInfo.IsAnim(p) || o is GameObject;
            });

        [MenuItem(Root + "Create Model…", false, 1101)]
        static void CreateModel()
        {
            var folder = BridgeSettings.instance.newModelFolder;
            var sel = Selection.activeObject != null ? AssetDatabase.GetAssetPath(Selection.activeObject) : null;
            if (!string.IsNullOrEmpty(sel))
                folder = AssetDatabase.IsValidFolder(sel) ? sel : Path.GetDirectoryName(sel)?.Replace('\\', '/');
            if (string.IsNullOrEmpty(folder) || !AssetDatabase.IsValidFolder(folder)) folder = "Assets";
            NewModelWindow.Show(folder);
        }

        [MenuItem(Root + "Pin .blend", false, 1120)]
        static void Pin() => PinOps.SetPinned(SelectedModelPath(), true);

        [MenuItem(Root + "Pin .blend", true)]
        static bool PinValidate() => CanPin(true);

        [MenuItem(Root + "Unpin .blend", false, 1121)]
        static void Unpin() => PinOps.SetPinned(SelectedModelPath(), false);

        [MenuItem(Root + "Unpin .blend", true)]
        static bool UnpinValidate() => CanPin(false);

        [MenuItem(Root + "Show History", false, 1122)]
        static void History() => BridgeWindow.Open(BridgeWindow.Tab.History, SelectedModelPath());

        [MenuItem("Window/Blender Bridge", false, 2000)]
        static void Window() => BridgeWindow.Open(BridgeWindow.Tab.Sessions, null);

        // Hierarchy context menu (and GameObject menu). Called once per selected object, so run once.
        static double _lastCall = -1;

        [MenuItem("GameObject/Blender/Open in Blender", false, 30)]
        static void OpenFromHierarchy(MenuCommand cmd)
        {
            var now = EditorApplication.timeSinceStartup;
            if (now - _lastCall < 0.5) return;
            _lastCall = now;
            var targets = TargetResolver.FromGameObjects(Selection.gameObjects);
            EditorApplication.delayCall += () => SessionLauncher.OpenTargets(targets,
                "No model from an FBX file was found on the selected objects.");
        }

        [MenuItem("GameObject/Blender/Open in Blender", true)]
        static bool OpenFromHierarchyValidate() => Selection.gameObjects.Length > 0;

        static string SelectedModelPath()
        {
            var p = Selection.activeObject != null ? AssetDatabase.GetAssetPath(Selection.activeObject) : null;
            return ModelInfo.IsFbx(p) && ModelInfo.HasMesh(p) ? p : null;
        }

        static bool CanPin(bool pin)
        {
            var p = SelectedModelPath();
            if (p == null) return false;
            var pinned = LinksStore.FindPin(AssetDatabase.AssetPathToGUID(p)) != null;
            return pin != pinned;
        }
    }

    /// <summary>Pin/unpin from Unity, when Blender is not holding the file open.</summary>
    internal static class PinOps
    {
        public static void SetPinned(string modelPath, bool pin)
        {
            if (string.IsNullOrEmpty(modelPath)) return;
            var guid = AssetDatabase.AssetPathToGUID(modelPath);
            if (LockStore.ReadAlive(guid) != null)
            {
                EditorUtility.DisplayDialog("Blender Bridge",
                    "This model is open in Blender. Use the Pin button in Blender's Unity panel, or close Blender first.", "OK");
                return;
            }
            var cache = BridgePaths.CacheBlendFor(modelPath);
            var pinned = BridgePaths.PinnedBlendFor(modelPath);
            var from = pin ? cache : pinned;
            var to = pin ? pinned : cache;
            if (!File.Exists(from))
            {
                EditorUtility.DisplayDialog("Blender Bridge",
                    pin ? "There is no cached .blend for this model yet. Open it in Blender first."
                        : "The pinned .blend is missing.", "OK");
                return;
            }
            Directory.CreateDirectory(Path.GetDirectoryName(to) ?? ".");
            if (File.Exists(to)) File.Delete(to);
            File.Move(from, to);
            var rel = BridgePaths.Rel(to);
            LinksStore.SetPin(guid, modelPath, pin ? rel : null);
            StateStore.Update(guid, s =>
            {
                s.blendPath = rel;
                s.pinned = pin;
            });
            BridgeIO.Log((pin ? "Pinned " : "Unpinned ") + Path.GetFileName(modelPath) + " → " + rel);
            BridgeWindow.RepaintIfOpen();
        }
    }

    [InitializeOnLoad]
    internal static class InspectorButton
    {
        static InspectorButton()
        {
            Editor.finishedDefaultHeaderGUI += OnHeader;
        }

        static void OnHeader(Editor editor)
        {
            if (editor.targets.Length != 1) return;
            var target = editor.target;
            var path = AssetDatabase.GetAssetPath(target);
            if (target is ModelImporter mi) path = mi.assetPath;
            bool show = (target is ModelImporter && ModelInfo.IsFbx(path)) ||
                        (target is AnimationClip && (ModelInfo.IsAnim(path) || ModelInfo.IsFbx(path)));
            if (!show) return;
            using (new EditorGUILayout.HorizontalScope())
            {
                GUILayout.FlexibleSpace();
                if (GUILayout.Button("Open in Blender", GUILayout.Width(140)))
                {
                    var t = new BridgeTarget { assetPath = path };
                    if (target is AnimationClip clip)
                    {
                        t.clipName = clip.name;
                        t.kind = ModelInfo.IsAnim(path) ? TargetKind.Anim
                            : ModelInfo.HasMesh(path) ? TargetKind.EmbeddedClip : TargetKind.ClipFbx;
                    }
                    else
                    {
                        t.kind = ModelInfo.HasMesh(path) ? TargetKind.Model : TargetKind.ClipFbx;
                    }
                    EditorApplication.delayCall += () => SessionLauncher.Open(t);
                }
            }
        }
    }
}
