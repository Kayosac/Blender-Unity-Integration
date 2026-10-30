using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;

namespace Farik.BlenderBridge
{
    internal static class BridgeIO
    {
        public static void Log(string msg) => Debug.Log("[Blender Bridge] " + msg);
        public static void Warn(string msg) => Debug.LogWarning("[Blender Bridge] " + msg);
        public static void Error(string msg) => Debug.LogError("[Blender Bridge] " + msg);

        public static string Sha256(string path)
        {
            if (!File.Exists(path)) return "";
            using (var sha = SHA256.Create())
            using (var fs = File.OpenRead(path))
            {
                var hash = sha.ComputeHash(fs);
                var sb = new StringBuilder(hash.Length * 2);
                foreach (var b in hash) sb.Append(b.ToString("x2"));
                return sb.ToString();
            }
        }

        public static T ReadJson<T>(string path) where T : class
        {
            try
            {
                if (!File.Exists(path)) return null;
                var text = File.ReadAllText(path, Encoding.UTF8);
                return string.IsNullOrWhiteSpace(text) ? null : JsonUtility.FromJson<T>(text);
            }
            catch (Exception e)
            {
                Warn($"Could not read {path}: {e.Message}");
                return null;
            }
        }

        /// <summary>Writes beside the target and swaps it in, so the other side never reads half a file.</summary>
        public static void WriteJson(string path, object data)
        {
            WriteTextAtomic(path, JsonUtility.ToJson(data, true));
        }

        public static void WriteTextAtomic(string path, string text)
        {
            var dir = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            var tmp = Path.Combine(dir ?? ".", ".tmp_" + Guid.NewGuid().ToString("N") + ".json");
            File.WriteAllText(tmp, text, new UTF8Encoding(false));
            if (File.Exists(path)) File.Replace(tmp, path, null);
            else File.Move(tmp, path);
        }

        public static string NowIso() =>
            DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", System.Globalization.CultureInfo.InvariantCulture);

        public static DateTime ParseIso(string s)
        {
            return DateTime.TryParse(s, System.Globalization.CultureInfo.InvariantCulture,
                System.Globalization.DateTimeStyles.AdjustToUniversal |
                                              System.Globalization.DateTimeStyles.AssumeUniversal, out var d)
                ? d
                : DateTime.MinValue;
        }

        public static long DirSize(string dir)
        {
            if (!Directory.Exists(dir)) return 0;
            long total = 0;
            foreach (var f in Directory.GetFiles(dir, "*", SearchOption.AllDirectories))
            {
                try { total += new FileInfo(f).Length; }
                catch (IOException) { }
            }
            return total;
        }

        public static string HumanSize(long bytes)
        {
            if (bytes < 1024) return bytes + " B";
            if (bytes < 1024 * 1024) return (bytes / 1024f).ToString("0.#") + " KB";
            if (bytes < 1024L * 1024 * 1024) return (bytes / 1024f / 1024f).ToString("0.#") + " MB";
            return (bytes / 1024f / 1024f / 1024f).ToString("0.##") + " GB";
        }
    }
}
