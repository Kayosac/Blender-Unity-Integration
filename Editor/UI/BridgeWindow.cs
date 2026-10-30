using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    /// <summary>Window ▸ Blender Bridge: open sessions, unfinished work, history and rollback, cache.</summary>
    internal sealed class BridgeWindow : EditorWindow
    {
        public enum Tab { Sessions, History, Reports, Cache }

        static readonly string[] TabNames = { "Sessions", "History", "Reports", "Cache" };
        Tab _tab;
        string _historyFor;
        Vector2 _scroll;
        List<StateFile> _states;
        List<HistoryEntry> _history;
        List<ExportReport> _reports;
        double _nextRefresh;

        public static void Open(Tab tab, string assetPath)
        {
            var w = GetWindow<BridgeWindow>("Blender Bridge");
            w._tab = tab;
            w._historyFor = assetPath;
            w.Refresh();
        }

        public static void RepaintIfOpen()
        {
            foreach (var w in Resources.FindObjectsOfTypeAll<BridgeWindow>())
            {
                w.Refresh();
                w.Repaint();
            }
        }

        void OnEnable() => Refresh();

        void Refresh()
        {
            _states = StateStore.All().OrderByDescending(s => s.lastOpenedUtc).ToList();
            var guid = string.IsNullOrEmpty(_historyFor) ? null : AssetDatabase.AssetPathToGUID(_historyFor);
            _history = HistoryStore.Entries(guid);
            _reports = ReportsStore.Load();
            _nextRefresh = EditorApplication.timeSinceStartup + 3;
        }

        void OnInspectorUpdate()
        {
            if (EditorApplication.timeSinceStartup > _nextRefresh)
            {
                Refresh();
                Repaint();
            }
        }

        void OnGUI()
        {
            _tab = (Tab)GUILayout.Toolbar((int)_tab, TabNames);
            EditorGUILayout.Space(4);
            _scroll = EditorGUILayout.BeginScrollView(_scroll);
            switch (_tab)
            {
                case Tab.Sessions: DrawSessions(); break;
                case Tab.History: DrawHistory(); break;
                case Tab.Reports: DrawReports(); break;
                case Tab.Cache: DrawCache(); break;
            }
            EditorGUILayout.EndScrollView();
        }

        void DrawSessions()
        {
            if (_states.Count == 0)
            {
                EditorGUILayout.HelpBox("Nothing opened in Blender yet. Right-click a model ▸ Blender ▸ Open in Blender.", MessageType.Info);
                return;
            }
            var unfinished = _states.Where(s => s.dirty).ToList();
            if (unfinished.Count > 0)
                EditorGUILayout.HelpBox($"{unfinished.Count} .blend file(s) have edits that were never exported. " +
                                        "They are kept until you export or discard them.", MessageType.Warning);
            foreach (var s in _states)
            {
                var path = AssetDatabase.GUIDToAssetPath(s.guid);
                if (string.IsNullOrEmpty(path)) path = s.assetPath;
                using (new EditorGUILayout.HorizontalScope(EditorStyles.helpBox))
                {
                    var open = LockStore.ReadAlive(s.guid) != null;
                    var status = (open ? "open in Blender · " : "") + (s.pinned ? "pinned" : "cache") +
                                 (s.dirty ? " · unexported edits" : "");
                    using (new EditorGUILayout.VerticalScope())
                    {
                        EditorGUILayout.LabelField(Path.GetFileName(path), EditorStyles.boldLabel);
                        EditorGUILayout.LabelField(status + " · " + Ago(s.lastOpenedUtc), EditorStyles.miniLabel);
                    }
                    if (GUILayout.Button("Open", GUILayout.Width(60)) && !string.IsNullOrEmpty(path))
                        SessionLauncher.Open(new BridgeTarget { kind = TargetKind.Model, assetPath = path });
                    using (new EditorGUI.DisabledScope(open))
                    {
                        if (GUILayout.Button(s.pinned ? "Unpin" : "Pin", GUILayout.Width(60)))
                            PinOps.SetPinned(path, !s.pinned);
                    }
                    if (GUILayout.Button("History", GUILayout.Width(64)))
                    {
                        _historyFor = path;
                        _tab = Tab.History;
                        Refresh();
                    }
                    if (GUILayout.Button("Reveal", GUILayout.Width(60)))
                        EditorUtility.RevealInFinder(BridgePaths.Abs(s.blendPath ?? ""));
                }
            }
        }

        void DrawHistory()
        {
            using (new EditorGUILayout.HorizontalScope())
            {
                EditorGUILayout.LabelField(string.IsNullOrEmpty(_historyFor) ? "All files" : _historyFor);
                if (!string.IsNullOrEmpty(_historyFor) && GUILayout.Button("Show all", GUILayout.Width(80)))
                {
                    _historyFor = null;
                    Refresh();
                }
            }
            if (_history.Count == 0)
            {
                EditorGUILayout.HelpBox("No exports recorded yet.", MessageType.Info);
                return;
            }
            foreach (var e in _history)
            {
                using (new EditorGUILayout.HorizontalScope(EditorStyles.helpBox))
                {
                    using (new EditorGUILayout.VerticalScope())
                    {
                        EditorGUILayout.LabelField(e.info.assetPath, EditorStyles.boldLabel);
                        EditorGUILayout.LabelField($"before export · {Ago(e.info.createdUtc)} · {BridgeIO.HumanSize(e.size)}",
                            EditorStyles.miniLabel);
                    }
                    if (GUILayout.Button("Restore", GUILayout.Width(70)) && EditorUtility.DisplayDialog("Blender Bridge",
                            $"Put {Path.GetFileName(e.info.assetPath)} back to how it was on {e.info.createdUtc}?\n" +
                            "Its .meta (import settings) is restored too.", "Restore", "Cancel"))
                    {
                        HistoryStore.Restore(e);
                        Refresh();
                    }
                }
            }
        }

        void DrawReports()
        {
            if (_reports.Count == 0)
            {
                EditorGUILayout.HelpBox("No imports from Blender yet.", MessageType.Info);
                return;
            }
            foreach (var r in _reports)
            {
                var type = r.errors.Count > 0 ? MessageType.Error : r.warnings.Count > 0 ? MessageType.Warning : MessageType.Info;
                var lines = new List<string> { Ago(r.timeUtc) + " · " + r.summary };
                lines.AddRange(r.errors.Select(x => "✖ " + x));
                lines.AddRange(r.warnings.Select(x => "! " + x));
                lines.AddRange(r.infos.Select(x => "· " + x));
                EditorGUILayout.HelpBox(string.Join("\n", lines), type);
            }
        }

        void DrawCache()
        {
            var size = BridgeIO.DirSize(BridgePaths.Cache);
            var historySize = BridgeIO.DirSize(BridgePaths.History);
            EditorGUILayout.LabelField("Cache", BridgeIO.HumanSize(size) + " in " + BridgePaths.Rel(BridgePaths.Cache));
            EditorGUILayout.LabelField("History", BridgeIO.HumanSize(historySize));
            EditorGUILayout.Space();
            EditorGUILayout.HelpBox($"Synced cache files are removed automatically after {BridgeSettings.instance.cacheDays} days " +
                                    "without opening. Files with unexported edits and pinned files are never removed.", MessageType.None);
            if (GUILayout.Button("Clear cache now (synced files only)"))
            {
                var n = Cleanup.CleanCache(0);
                BridgeIO.Log($"Removed {n} cached .blend file(s).");
                Refresh();
            }
            if (GUILayout.Button("Process pending exports now")) ExportProcessor.ProcessNow();
            EditorGUILayout.Space();
            EditorGUILayout.LabelField("Self-test", EditorStyles.boldLabel);
            EditorGUILayout.HelpBox("Runs FBX → Blender → FBX cycles on a synthetic set plus any FBX selected in the Project " +
                                    "window, and compares the files the way Unity reads them.", MessageType.None);
            using (new EditorGUI.DisabledScope(SelfTest.Running))
            {
                if (GUILayout.Button(SelfTest.Running ? "Self-test running…" : "Run self-test")) SelfTest.Run();
            }
            if (System.IO.File.Exists(SelfTest.ReportText))
                EditorGUILayout.HelpBox(System.IO.File.ReadAllText(SelfTest.ReportText), MessageType.None);
            if (GUILayout.Button("Open settings")) SettingsService.OpenProjectSettings(BridgeSettingsProvider.ProjectPath);
        }

        static string Ago(string iso)
        {
            var t = BridgeIO.ParseIso(iso);
            if (t == System.DateTime.MinValue) return "—";
            var d = System.DateTime.UtcNow - t;
            if (d.TotalMinutes < 1) return "just now";
            if (d.TotalHours < 1) return $"{(int)d.TotalMinutes} min ago";
            if (d.TotalDays < 1) return $"{(int)d.TotalHours} h ago";
            return $"{(int)d.TotalDays} d ago";
        }
    }
}
