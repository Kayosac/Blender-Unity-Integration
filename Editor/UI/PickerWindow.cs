using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    /// <summary>A small list to pick one item from (several models in a prefab, a rig for a clip).</summary>
    internal sealed class PickerWindow : EditorWindow
    {
        List<string> _labels;
        Action<int> _onPick;
        string _title;
        Vector2 _scroll;
        string _filter = "";

        public static void Show<T>(string title, List<string> labels, List<T> values, Action<T> onPick)
        {
            var w = CreateInstance<PickerWindow>();
            w.titleContent = new GUIContent("Blender Bridge");
            w._title = title;
            w._labels = labels;
            w._onPick = i => onPick(values[i]);
            var h = Mathf.Clamp(90 + labels.Count * 22, 140, 520);
            var main = EditorGUIUtility.GetMainWindowPosition();
            w.position = new Rect(main.center.x - 230, main.center.y - h / 2f, 460, h);
            w.ShowUtility();
            w.Focus();
        }

        void OnGUI()
        {
            if (_labels == null)
            {
                Close();
                return;
            }
            EditorGUILayout.LabelField(_title, EditorStyles.boldLabel);
            if (_labels.Count > 8) _filter = EditorGUILayout.TextField("Filter", _filter);
            _scroll = EditorGUILayout.BeginScrollView(_scroll);
            for (int i = 0; i < _labels.Count; i++)
            {
                if (_filter.Length > 0 && _labels[i].IndexOf(_filter, StringComparison.OrdinalIgnoreCase) < 0) continue;
                if (GUILayout.Button(_labels[i], EditorStyles.miniButton, GUILayout.Height(20)))
                {
                    var pick = _onPick;
                    var index = i;
                    Close();
                    EditorApplication.delayCall += () => pick(index);
                    GUIUtility.ExitGUI();
                }
            }
            EditorGUILayout.EndScrollView();
            if (GUILayout.Button("Cancel")) Close();
        }
    }

    /// <summary>Asks for a new model's name.</summary>
    internal sealed class NewModelWindow : EditorWindow
    {
        string _folder;
        string _name = "NewModel";

        public static void Show(string folder)
        {
            var w = CreateInstance<NewModelWindow>();
            w.titleContent = new GUIContent("New Blender Model");
            w._folder = folder;
            var main = EditorGUIUtility.GetMainWindowPosition();
            w.position = new Rect(main.center.x - 210, main.center.y - 55, 420, 110);
            w.ShowUtility();
        }

        void OnGUI()
        {
            EditorGUILayout.LabelField("Folder", _folder);
            GUI.SetNextControlName("name");
            _name = EditorGUILayout.TextField("Name", _name);
            EditorGUI.FocusTextInControl("name");
            var path = _folder.TrimEnd('/') + "/" + _name.Trim() + ".fbx";
            bool exists = System.IO.File.Exists(BridgePaths.Abs(path));
            bool valid = _name.Trim().Length > 0 && _name.IndexOfAny(System.IO.Path.GetInvalidFileNameChars()) < 0 && !exists;
            if (exists) EditorGUILayout.HelpBox(path + " already exists.", MessageType.Warning);
            using (new EditorGUI.DisabledScope(!valid))
            {
                bool enter = Event.current.type == EventType.KeyDown && Event.current.keyCode == KeyCode.Return;
                if (GUILayout.Button("Create and open in Blender") || (enter && valid))
                {
                    Close();
                    EditorApplication.delayCall += () => SessionLauncher.CreateNewModel(path);
                    GUIUtility.ExitGUI();
                }
            }
        }
    }
}
