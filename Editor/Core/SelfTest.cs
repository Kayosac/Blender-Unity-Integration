using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using UnityEditor;

namespace Farik.BlenderBridge
{
    /// <summary>Runs the phase 0 bench (Tests~/blender/phase0.py) in background Blender.</summary>
    internal static class SelfTest
    {
        static Process _proc;

        public static string OutDir => BridgePaths.ProjectRoot + "/Library/BlenderBridgeSelfTest";
        public static string InputDir => OutDir + "_input";
        public static string ReportText => OutDir + "/report.txt";
        public static bool Running => _proc != null && !_proc.HasExited;

        /// <summary>Starts the bench. Selected FBX files in the Project window are added to the synthetic corpus.</summary>
        public static string RequestFile => BridgePaths.Bridge + "/selftest.request";

        /// <summary>A request file (one asset path per line, optional) starts the bench without the menu.</summary>
        public static string E2ERequestFile => BridgePaths.Bridge + "/e2e.request";

        public static void CheckRequest()
        {
            if (!Running && !EndToEnd.Running && File.Exists(E2ERequestFile))
            {
                var e2e = File.ReadAllLines(E2ERequestFile).Select(l => l.Trim()).Where(l => l.Length > 0).ToList();
                File.Delete(E2ERequestFile);
                EndToEnd.Run(e2e);
                return;
            }
            if (Running || !File.Exists(RequestFile)) return;
            var lines = File.ReadAllLines(RequestFile).Select(l => l.Trim()).Where(l => l.Length > 0).ToList();
            File.Delete(RequestFile);
            if (lines.Count > 0 && lines[0] == "e2e")
            {
                if (!EndToEnd.Running) EndToEnd.Run(lines.Skip(1));
                return;
            }
            Run(3, lines.Where(ModelInfo.IsFbx).ToList());
        }

        public static void Run(int cycles = 3) =>
            Run(cycles, Selection.objects.Select(AssetDatabase.GetAssetPath).Where(ModelInfo.IsFbx).Distinct().ToList());

        static void Run(int cycles, System.Collections.Generic.List<string> picked)
        {
            if (Running) return;
            var exe = BlenderLocator.Resolve();
            var script = BridgePaths.PackageRoot + "/Tests~/blender/phase0.py";
            if (exe == null || !File.Exists(script))
            {
                BridgeIO.Error("Self-test needs Blender and Tests~/blender/phase0.py in the package.");
                return;
            }
            if (File.Exists(ReportText)) File.Delete(ReportText);
            if (Directory.Exists(InputDir)) Directory.Delete(InputDir, true);
            Directory.CreateDirectory(InputDir);
            foreach (var p in picked) File.Copy(BridgePaths.Abs(p), Path.Combine(InputDir, Path.GetFileName(p)), true);
            var args = $"-b --factory-startup --python \"{script}\" -- --ub-out \"{OutDir}\" --ub-cycles {cycles}" +
                       (picked.Count > 0 ? $" --ub-fbx-dir \"{InputDir}\"" : "");
            var psi = new ProcessStartInfo(exe, args)
            {
                UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true,
            };
            var log = OutDir + "_blender.log";
            _proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
            var writer = new StreamWriter(log, false) { AutoFlush = true };
            _proc.OutputDataReceived += (_, e) => { if (e.Data != null) lock (writer) writer.WriteLine(e.Data); };
            _proc.ErrorDataReceived += (_, e) => { if (e.Data != null) lock (writer) writer.WriteLine(e.Data); };
            _proc.Exited += (_, __) =>
            {
                lock (writer) writer.Dispose();
                EditorApplication.delayCall += () =>
                {
                    var text = File.Exists(ReportText) ? File.ReadAllText(ReportText) : "No report; see " + log;
                    BridgeIO.Log("Self-test finished:\n" + text);
                    BridgeWindow.RepaintIfOpen();
                };
            };
            _proc.Start();
            _proc.BeginOutputReadLine();
            _proc.BeginErrorReadLine();
            BridgeIO.Log($"Self-test started ({picked.Count} of your FBX files + synthetic corpus). Report: {ReportText}");
        }
    }
}
