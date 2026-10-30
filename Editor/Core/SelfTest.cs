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
        public static void Run(int cycles = 3)
        {
            if (Running) return;
            var exe = BlenderLocator.Resolve();
            var script = BridgePaths.PackageRoot + "/Tests~/blender/phase0.py";
            if (exe == null || !File.Exists(script))
            {
                BridgeIO.Error("Self-test needs Blender and Tests~/blender/phase0.py in the package.");
                return;
            }
            if (Directory.Exists(InputDir)) Directory.Delete(InputDir, true);
            Directory.CreateDirectory(InputDir);
            var picked = Selection.objects.Select(AssetDatabase.GetAssetPath).Where(ModelInfo.IsFbx).Distinct().ToList();
            foreach (var p in picked) File.Copy(BridgePaths.Abs(p), Path.Combine(InputDir, Path.GetFileName(p)), true);
            var args = $"-b --factory-startup --python \"{script}\" -- --out \"{OutDir}\" --cycles {cycles}" +
                       (picked.Count > 0 ? $" --fbx-dir \"{InputDir}\"" : "");
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
