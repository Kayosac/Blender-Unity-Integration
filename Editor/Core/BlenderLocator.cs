using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using UnityEngine;

namespace Farik.BlenderBridge
{
    /// <summary>Finds Blender on macOS, Windows and Linux and starts it with the bridge loaded.</summary>
    internal static class BlenderLocator
    {
        static readonly Regex VersionRe = new Regex(@"(\d+)\.(\d+)(?:\.(\d+))?");

        public static string Resolve()
        {
            var saved = BridgeUserPrefs.BlenderPath;
            if (IsValid(saved)) return saved;
            var found = Candidates().Where(IsValid)
                .Select(p => (path: p, ver: ParseVersion(GetVersion(p))))
                .OrderByDescending(x => x.ver)
                .FirstOrDefault();
            if (found.path != null)
            {
                BridgeUserPrefs.BlenderPath = found.path;
                return found.path;
            }
            return null;
        }

        public static bool IsValid(string exe) => !string.IsNullOrEmpty(exe) && File.Exists(exe);

        /// <summary>Accepts the .app bundle on macOS as well as the executable.</summary>
        public static string NormalizeSelection(string path)
        {
            if (string.IsNullOrEmpty(path)) return path;
            if (path.EndsWith(".app", StringComparison.OrdinalIgnoreCase) && Directory.Exists(path))
                return Path.Combine(path, "Contents/MacOS/Blender");
            return path;
        }

        static IEnumerable<string> Candidates()
        {
            var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            switch (Application.platform)
            {
                case RuntimePlatform.OSXEditor:
                    foreach (var dir in new[] { "/Applications", Path.Combine(home, "Applications") })
                    {
                        if (!Directory.Exists(dir)) continue;
                        foreach (var app in Directory.GetDirectories(dir, "Blender*.app"))
                            yield return Path.Combine(app, "Contents/MacOS/Blender");
                    }
                    yield return Path.Combine(home,
                        "Library/Application Support/Steam/steamapps/common/Blender/Blender.app/Contents/MacOS/Blender");
                    break;
                case RuntimePlatform.WindowsEditor:
                    var roots = new[]
                    {
                        Environment.GetEnvironmentVariable("ProgramFiles"),
                        Environment.GetEnvironmentVariable("ProgramFiles(x86)"),
                        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs"),
                    };
                    foreach (var root in roots.Where(r => !string.IsNullOrEmpty(r)))
                    {
                        var bf = Path.Combine(root, "Blender Foundation");
                        if (!Directory.Exists(bf)) continue;
                        foreach (var d in Directory.GetDirectories(bf, "Blender*"))
                            yield return Path.Combine(d, "blender.exe");
                    }
                    yield return @"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe";
                    yield return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                        @"Microsoft\WindowsApps\blender.exe");
                    break;
                default:
                    var pathVar = Environment.GetEnvironmentVariable("PATH") ?? "";
                    foreach (var d in pathVar.Split(':'))
                        if (!string.IsNullOrEmpty(d)) yield return Path.Combine(d, "blender");
                    yield return "/usr/bin/blender";
                    yield return "/usr/local/bin/blender";
                    yield return "/snap/bin/blender";
                    yield return "/var/lib/flatpak/exports/bin/org.blender.Blender";
                    if (Directory.Exists(home))
                        foreach (var d in Directory.GetDirectories(home, "blender*"))
                            yield return Path.Combine(d, "blender");
                    break;
            }
        }

        static readonly Dictionary<string, string> VersionCache = new Dictionary<string, string>();

        public static string GetVersion(string exe)
        {
            if (!IsValid(exe)) return "";
            if (VersionCache.TryGetValue(exe, out var v)) return v;
            v = "";
            // macOS: read the bundle's Info.plist instead of starting Blender.
            var plist = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(exe) ?? "", "../Info.plist"));
            if (File.Exists(plist))
            {
                var text = File.ReadAllText(plist);
                var m = Regex.Match(text, @"<key>CFBundleShortVersionString</key>\s*<string>([^<]+)</string>");
                if (m.Success) v = m.Groups[1].Value.Trim();
            }
            if (string.IsNullOrEmpty(v))
            {
                try
                {
                    var psi = new ProcessStartInfo(exe, "--version")
                    {
                        UseShellExecute = false, RedirectStandardOutput = true, CreateNoWindow = true,
                    };
                    using (var p = Process.Start(psi))
                    {
                        var output = p.StandardOutput.ReadToEnd();
                        p.WaitForExit(15000);
                        var m = Regex.Match(output, @"Blender\s+(\d+\.\d+(?:\.\d+)?)");
                        if (m.Success) v = m.Groups[1].Value;
                    }
                }
                catch (Exception e)
                {
                    BridgeIO.Warn("Could not read the Blender version: " + e.Message);
                }
            }
            VersionCache[exe] = v;
            return v;
        }

        public static Version ParseVersion(string s)
        {
            var m = VersionRe.Match(s ?? "");
            if (!m.Success) return new Version(0, 0);
            return new Version(int.Parse(m.Groups[1].Value), int.Parse(m.Groups[2].Value),
                m.Groups[3].Success ? int.Parse(m.Groups[3].Value) : 0);
        }

        public static bool IsSupported(string version) => ParseVersion(version) >= new Version(5, 0);

        static string Quote(string s) => "\"" + s.Replace("\"", "\\\"") + "\"";

        /// <summary>Starts a new Blender window. Opens the .blend when given, then runs the bootstrap.</summary>
        public static bool Launch(string exe, string blendAbs, string manifestAbs)
        {
            var boot = BridgePaths.BootstrapScript;
            if (boot == null || !File.Exists(boot))
            {
                BridgeIO.Error("bootstrap.py was not found in the package (Blender~ folder).");
                return false;
            }
            var args = new List<string>();
            if (!string.IsNullOrEmpty(blendAbs) && File.Exists(blendAbs)) args.Add(Quote(blendAbs));
            args.Add("--python");
            args.Add(Quote(boot));
            args.Add("--");
            args.Add("--ub-manifest");
            args.Add(Quote(manifestAbs));
            try
            {
                var psi = new ProcessStartInfo(exe, string.Join(" ", args))
                {
                    UseShellExecute = false,
                    CreateNoWindow = false,
                    WorkingDirectory = BridgePaths.ProjectRoot,
                };
                Process.Start(psi);
                return true;
            }
            catch (Exception e)
            {
                BridgeIO.Error("Could not start Blender: " + e.Message);
                return false;
            }
        }

        /// <summary>Runs Blender without UI for a short Python job (used to enable the installed add-on).</summary>
        public static bool RunBackground(string exe, string pythonExpr, int timeoutMs = 60000)
        {
            try
            {
                var psi = new ProcessStartInfo(exe, "-b --python-expr " + Quote(pythonExpr))
                {
                    UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true,
                    CreateNoWindow = true,
                };
                using (var p = Process.Start(psi))
                {
                    p.StandardOutput.ReadToEnd();
                    p.WaitForExit(timeoutMs);
                    return p.HasExited && p.ExitCode == 0;
                }
            }
            catch (Exception e)
            {
                BridgeIO.Warn("Blender background run failed: " + e.Message);
                return false;
            }
        }
    }
}
