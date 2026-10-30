using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;

namespace Farik.BlenderBridge
{
    /// <summary>Per-model state shared with Blender: which .blend, synced FBX hash, unexported edits.</summary>
    internal static class StateStore
    {
        public static StateFile Read(string guid) =>
            string.IsNullOrEmpty(guid) ? null : BridgeIO.ReadJson<StateFile>(BridgePaths.StateFile(guid));

        public static void Update(string guid, Action<StateFile> change)
        {
            if (string.IsNullOrEmpty(guid)) return;
            var s = Read(guid) ?? new StateFile { guid = guid };
            change(s);
            s.guid = guid;
            BridgeIO.WriteJson(BridgePaths.StateFile(guid), s);
        }

        public static IEnumerable<StateFile> All()
        {
            if (!Directory.Exists(BridgePaths.State)) yield break;
            foreach (var f in Directory.GetFiles(BridgePaths.State, "*.json"))
            {
                var s = BridgeIO.ReadJson<StateFile>(f);
                if (s != null && !string.IsNullOrEmpty(s.guid)) yield return s;
            }
        }

        public static void Delete(string guid)
        {
            var p = BridgePaths.StateFile(guid);
            if (File.Exists(p)) File.Delete(p);
        }
    }

    /// <summary>ArtSource/links.json, committed to git: pinned .blend files and clip-to-rig pairs.</summary>
    internal static class LinksStore
    {
        public static LinksFile Read() => BridgeIO.ReadJson<LinksFile>(BridgePaths.Links) ?? new LinksFile();

        public static void Write(LinksFile links)
        {
            links.pins = links.pins.OrderBy(p => p.assetPath).ToList();
            links.clipRigs = links.clipRigs.OrderBy(c => c.clip).ToList();
            Directory.CreateDirectory(BridgePaths.ArtSource);
            BridgeIO.WriteJson(BridgePaths.Links, links);
        }

        public static PinEntry FindPin(string guid) => Read().pins.FirstOrDefault(p => p.guid == guid);

        public static ClipRigEntry FindClipRig(string clipGuid) => Read().clipRigs.FirstOrDefault(c => c.clip == clipGuid);

        public static void SetClipRig(string clipGuid, string rigGuid, string prefix)
        {
            if (string.IsNullOrEmpty(clipGuid) || string.IsNullOrEmpty(rigGuid)) return;
            var links = Read();
            links.clipRigs.RemoveAll(c => c.clip == clipGuid);
            links.clipRigs.Add(new ClipRigEntry { clip = clipGuid, rig = rigGuid, prefix = prefix ?? "" });
            Write(links);
        }

        public static void SetPin(string guid, string assetPath, string blendRel)
        {
            var links = Read();
            links.pins.RemoveAll(p => p.guid == guid);
            if (!string.IsNullOrEmpty(blendRel))
                links.pins.Add(new PinEntry { guid = guid, assetPath = assetPath, blend = blendRel });
            Write(links);
        }
    }

    /// <summary>Lock files Blender writes while a model is open.</summary>
    internal static class LockStore
    {
        public static LockFile ReadAlive(string guid)
        {
            var path = BridgePaths.LockFile(guid);
            var l = BridgeIO.ReadJson<LockFile>(path);
            if (l == null) return null;
            if (IsAlive(l.pid)) return l;
            try { File.Delete(path); }
            catch (IOException) { }
            return null;
        }

        static bool IsAlive(int pid)
        {
            if (pid <= 0) return false;
            try
            {
                var p = Process.GetProcessById(pid);
                return !p.HasExited;
            }
            catch (ArgumentException) { return false; }
            catch (InvalidOperationException) { return false; }
        }
    }
}
