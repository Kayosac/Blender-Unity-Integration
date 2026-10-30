using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    /// <summary>
    /// Full round trips Unity → Blender → Unity on copies of real assets, without any window:
    /// the session manifest, background Blender with Tests~/blender/e2e.py, the import, the
    /// before/after comparison and, for .anim, the curve transfer. Results go to e2e.txt.
    /// </summary>
    internal static class EndToEnd
    {
        const string Folder = "Assets/BlenderBridgeSelfTest";
        public static string ResultFile => SelfTest.OutDir + "/e2e.txt";

        sealed class Step
        {
            public string name;
            public string scenario;
            public BridgeTarget target;
            public Func<string> verify; // returns null when fine, else the problem
            public Action before;
        }

        static readonly Queue<Step> Steps = new Queue<Step>();
        static Step _current;
        static Process _proc;
        static string _sessionManifest;
        static double _deadline;
        static StringBuilder _log;
        static int _failed;
        static readonly List<string> TouchedGuids = new List<string>();

        public static bool Running => _current != null || Steps.Count > 0;

        /// <summary>assets: model FBX files (static or skinned) and .anim clips, each followed by its rig on the same line after '|'.</summary>
        public static void Run(IEnumerable<string> lines)
        {
            if (Running) return;
            Directory.CreateDirectory(SelfTest.OutDir);
            _log = new StringBuilder("Unity Bridge end-to-end — " + DateTime.Now + "\n\n");
            _failed = 0;
            if (!AssetDatabase.IsValidFolder(Folder)) AssetDatabase.CreateFolder("Assets", "BlenderBridgeSelfTest");
            int n = 0;
            foreach (var raw in lines)
            {
                var parts = raw.Split('|').Select(x => x.Trim()).ToArray();
                var src = parts[0];
                if (!File.Exists(BridgePaths.Abs(src))) { _log.AppendLine("SKIP  " + src + ": not found"); continue; }
                n++;
                var dir = Folder + "/" + n.ToString("00");
                AssetDatabase.CreateFolder(Folder, n.ToString("00"));
                if (ModelInfo.IsAnim(src) && parts.Length > 1) AddAnim(src, parts[1], dir);
                else if (ModelInfo.IsFbx(src)) AddModel(src, dir);
            }
            EditorApplication.update -= Tick;
            EditorApplication.update += Tick;
        }

        static string Copy(string src, string dir)
        {
            var dst = dir + "/" + Path.GetFileName(src);
            if (!AssetDatabase.CopyAsset(src, dst)) throw new IOException("Could not copy " + src);
            AssetDatabase.ImportAsset(dst, ImportAssetOptions.ForceSynchronousImport);
            TouchedGuids.Add(AssetDatabase.AssetPathToGUID(dst));
            return dst;
        }

        static void AddModel(string src, string dir)
        {
            var path = Copy(src, dir);
            bool hasMesh = ModelInfo.HasMesh(path);
            bool skinned = ModelInfo.HasSkin(path);
            var mi = (ModelImporter)AssetImporter.GetAtPath(path);
            bool hasTakes = (mi.importedTakeInfos ?? Array.Empty<TakeInfo>()).Length > 0;
            if (hasMesh)
                Steps.Enqueue(new Step
                {
                    name = "edit_vertex " + Path.GetFileName(src), scenario = "edit_vertex",
                    target = new BridgeTarget { kind = TargetKind.Model, assetPath = path, rigPath = path },
                    verify = () => ReportProblems(path),
                });
            if (hasTakes || !hasMesh)
                Steps.Enqueue(new Step
                {
                    name = "rewrite takes " + Path.GetFileName(src), scenario = "noop",
                    target = new BridgeTarget { kind = TargetKind.Model, assetPath = path, rigPath = path },
                    verify = () => ReportProblems(path),
                });
            if (skinned)
            {
                var stem = Path.GetFileNameWithoutExtension(path);
                var clipPath = dir + "/" + BridgeSettings.instance.clipsFolder + "/" + stem + "@E2EWave.fbx";
                Steps.Enqueue(new Step
                {
                    name = "new clip on " + Path.GetFileName(src), scenario = "new_clip",
                    target = new BridgeTarget { kind = TargetKind.Model, assetPath = path, rigPath = path },
                    verify = () => VerifyNewClip(path, clipPath),
                });
            }
        }

        static void AddAnim(string src, string rigSrc, string dir)
        {
            var rig = Copy(rigSrc, dir);
            var anim = Copy(src, dir);
            AnimationClip before = null;
            Steps.Enqueue(new Step
            {
                name = ".anim " + Path.GetFileName(src) + " on " + Path.GetFileName(rigSrc), scenario = "anim",
                target = new BridgeTarget
                {
                    kind = TargetKind.Anim, assetPath = anim, rigPath = rig,
                    clipName = Path.GetFileNameWithoutExtension(anim),
                },
                before = () => before = UnityEngine.Object.Instantiate(AssetDatabase.LoadAssetAtPath<AnimationClip>(anim)),
                verify = () => VerifyAnim(anim, before),
            });
        }

        static void Tick()
        {
            try
            {
                TickInner();
            }
            catch (Exception e)
            {
                Fail("exception: " + e.Message);
                UnityEngine.Debug.LogException(e);
                _current = null;
                _proc = null;
            }
        }

        static void TickInner()
        {
            if (EditorApplication.isCompiling || EditorApplication.isUpdating) return;
            if (_current == null)
            {
                if (Steps.Count == 0)
                {
                    Finish();
                    return;
                }
                _current = Steps.Dequeue();
                _current.before?.Invoke();
                _sessionManifest = null;
                SessionLauncher.LaunchOverride = StartBlender;
                try
                {
                    SessionLauncher.OpenResolved(_current.target);
                }
                finally
                {
                    SessionLauncher.LaunchOverride = null;
                }
                if (_proc == null) Fail("Blender was not started");
                _deadline = EditorApplication.timeSinceStartup + 300;
                return;
            }
            if (_proc != null && !_proc.HasExited)
            {
                if (EditorApplication.timeSinceStartup > _deadline)
                {
                    try { _proc.Kill(); }
                    catch (InvalidOperationException) { }
                    Fail("Blender timed out");
                }
                return;
            }
            if (_proc != null)
            {
                if (_proc.ExitCode != 0) Fail("Blender exited with code " + _proc.ExitCode + " (see " + LogFile + ")");
                _proc = null;
                ExportProcessor.ProcessNow();
                return;
            }
            // Wait until Unity processed this session's export manifest.
            if (Directory.Exists(BridgePaths.Outbox) && Directory.GetFiles(BridgePaths.Outbox, "*.json").Length > 0)
            {
                ExportProcessor.ProcessNow();
                return;
            }
            if (_current != null)
            {
                string problem;
                try { problem = _current.verify(); }
                catch (Exception e) { problem = e.Message; }
                if (problem == null) _log.AppendLine("PASS  " + _current.name);
                else Fail(problem);
                _current = null;
            }
        }

        static string LogFile => SelfTest.OutDir + "_e2e_blender.log";

        static void StartBlender(string exe, string blend, string manifest)
        {
            _sessionManifest = manifest;
            var script = BridgePaths.PackageRoot + "/Tests~/blender/e2e.py";
            var args = (blend != null ? "\"" + blend + "\" " : "") + "-b --python \"" + BridgePaths.BootstrapScript +
                       "\" --python \"" + script + "\" -- --ub-manifest \"" + manifest + "\" --ub-e2e " + _current.scenario;
            var psi = new ProcessStartInfo(exe, args)
            {
                UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true,
            };
            _proc = new Process { StartInfo = psi };
            var writer = new StreamWriter(LogFile, true) { AutoFlush = true };
            writer.WriteLine("==== " + _current.name);
            _proc.EnableRaisingEvents = true;
            _proc.OutputDataReceived += (_, e) => { if (e.Data != null) lock (writer) writer.WriteLine(e.Data); };
            _proc.ErrorDataReceived += (_, e) => { if (e.Data != null) lock (writer) writer.WriteLine(e.Data); };
            _proc.Exited += (_, __) => { lock (writer) writer.Dispose(); };
            _proc.Start();
            _proc.BeginOutputReadLine();
            _proc.BeginErrorReadLine();
        }

        static void Fail(string problem)
        {
            _failed++;
            _log.AppendLine("FAIL  " + (_current != null ? _current.name : "?") + ": " + problem);
        }

        static ExportReport LatestReport(string assetPath) =>
            ReportsStore.Load().FirstOrDefault(r => r.assetPath == assetPath);

        static string ReportProblems(string path)
        {
            var r = LatestReport(path);
            if (r == null) return "no import report for " + path;
            var lines = new List<string>();
            lines.AddRange(r.errors.Select(e => "error: " + e));
            lines.AddRange(r.warnings.Select(w => "warning: " + w));
            if (r.errors.Count > 0) return string.Join("; ", lines);
            if (lines.Count > 0) _log.AppendLine("      " + string.Join("; ", lines));
            return null;
        }

        static string VerifyNewClip(string modelPath, string clipPath)
        {
            if (!File.Exists(BridgePaths.Abs(clipPath))) return "clip file was not created: " + clipPath;
            var problems = ReportProblems(clipPath);
            if (problems != null) return problems;
            var clips = ModelInfo.Clips(clipPath).ToList();
            if (clips.Count != 1 || clips[0].name != "E2EWave") return "expected one clip E2EWave, got " + string.Join(",", clips.Select(c => c.name));
            if (ModelInfo.HasMesh(clipPath)) return "clip file contains a mesh";
            var rig = (ModelImporter)AssetImporter.GetAtPath(modelPath);
            var mi = (ModelImporter)AssetImporter.GetAtPath(clipPath);
            if (rig.animationType == ModelImporterAnimationType.Human &&
                (mi.animationType != ModelImporterAnimationType.Human || mi.sourceAvatar == null))
                return "Humanoid clip did not get the model's avatar";
            var bindings = AnimationUtility.GetCurveBindings(clips[0]);
            if (bindings.Length == 0) return "clip has no curves";
            // Generic: curve paths must exist on the model.
            if (rig.animationType != ModelImporterAnimationType.Human)
            {
                var go = AssetDatabase.LoadAssetAtPath<GameObject>(modelPath);
                var missing = bindings.Where(b => b.type == typeof(Transform) && go.transform.Find(b.path) == null && b.path.Length > 0)
                    .Select(b => b.path).Distinct().Take(3).ToList();
                if (missing.Count > 0) return "clip paths not found on the model: " + string.Join(", ", missing);
            }
            return null;
        }

        static string VerifyAnim(string animPath, AnimationClip before)
        {
            var problems = ReportProblems(animPath);
            if (problems != null) return problems;
            var after = AssetDatabase.LoadAssetAtPath<AnimationClip>(animPath);
            if (after == null) return "clip lost";
            if (AnimationUtility.GetAnimationEvents(after).Length != AnimationUtility.GetAnimationEvents(before).Length)
                return "animation events changed";
            float worst = 0f;
            string worstPath = "";
            int compared = 0;
            foreach (var b in AnimationUtility.GetCurveBindings(before))
            {
                var ca = AnimationUtility.GetEditorCurve(before, b);
                var cb = AnimationUtility.GetEditorCurve(after, b);
                if (ca == null) continue;
                if (cb == null) return "curve lost: " + b.path + " " + b.propertyName;
                if (b.propertyName.StartsWith("m_LocalRotation", StringComparison.Ordinal))
                {
                    compared++;
                    continue; // compared as whole rotations below: components may flip sign together
                }
                bool position = b.propertyName.StartsWith("m_LocalPosition", StringComparison.Ordinal);
                float scale = 1f;
                if (position)
                {
                    for (int i = 0; i < ca.length; i++) scale = Mathf.Max(scale, Mathf.Abs(ca.keys[i].value));
                }
                for (int i = 0; i <= 20; i++)
                {
                    float t = before.length * i / 20f;
                    float d = Mathf.Abs(ca.Evaluate(t) - cb.Evaluate(t)) / scale;
                    if (d > worst) { worst = d; worstPath = b.path + "." + b.propertyName; }
                }
                compared++;
            }
            float angle = QuaternionWorst(before, after, out var anglePath);
            _log.AppendLine($"      {compared} curves compared; worst value difference {worst:0.#####} at {worstPath}; " +
                            $"worst rotation {angle:0.###}° at {anglePath}");
            if (worst > 2e-3f) return $"curves differ by {worst:0.####} at {worstPath}";
            if (angle > 0.5f) return $"rotation differs by {angle:0.##}° at {anglePath}";
            return null;
        }

        static float QuaternionWorst(AnimationClip a, AnimationClip b, out string worstPath)
        {
            float worst = 0f;
            worstPath = "";
            var paths = AnimationUtility.GetCurveBindings(a)
                .Where(x => x.type == typeof(Transform) && x.propertyName == "m_LocalRotation.x").Select(x => x.path);
            foreach (var p in paths)
            {
                for (int i = 0; i <= 20; i++)
                {
                    float t = a.length * i / 20f;
                    var qa = Q(a, p, t);
                    var qb = Q(b, p, t);
                    var d = Quaternion.Angle(qa, qb);
                    if (d > worst) { worst = d; worstPath = p; }
                }
            }
            return worst;
        }

        static Quaternion Q(AnimationClip c, string path, float t)
        {
            float V(string axis) => AnimationUtility.GetEditorCurve(c,
                EditorCurveBinding.FloatCurve(path, typeof(Transform), "m_LocalRotation." + axis))?.Evaluate(t) ?? 0f;
            return new Quaternion(V("x"), V("y"), V("z"), V("w")).normalized;
        }

        static void Finish()
        {
            EditorApplication.update -= Tick;
            _log.AppendLine();
            _log.AppendLine(_failed == 0 ? "All end-to-end steps passed." : _failed + " step(s) failed.");
            File.WriteAllText(ResultFile, _log.ToString());
            AssetDatabase.DeleteAsset(Folder);
            foreach (var g in TouchedGuids)
            {
                StateStore.Delete(g);
            }
            TouchedGuids.Clear();
            BridgeIO.Log("End-to-end self-test finished:\n" + _log);
            BridgeWindow.RepaintIfOpen();
        }
    }
}
