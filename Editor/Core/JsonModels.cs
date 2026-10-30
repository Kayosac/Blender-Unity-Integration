using System;
using System.Collections.Generic;

// JSON contract with the Blender add-on. Field names are the JSON keys; keep them in sync with
// Blender~/unity_bridge/session.py (session manifest) and exporter.py (export manifest).
namespace Farik.BlenderBridge
{
    [Serializable]
    internal class SessionManifest
    {
        public int version = 1;
        public string sessionId;
        public string projectRoot;
        public string artSource;
        public string blendPath;
        public bool fresh;
        public bool pinned;
        public ModelRef model = new ModelRef();
        public List<FileEntry> files = new List<FileEntry>();
        public FocusRef focus = new FocusRef();
        public List<MaterialPreview> materials = new List<MaterialPreview>();
        public SessionSettings settings = new SessionSettings();
    }

    [Serializable]
    internal class ModelRef
    {
        public string guid;
        public string assetPath;
        public string name;
        public bool isNew;
        public bool thirdParty;
        public string sha256;
        public string animationType;
    }

    [Serializable]
    internal class FileEntry
    {
        public string guid;
        public string assetPath;
        public string kind; // MODEL | CLIP | ANIM
        public bool hasMesh;
        public bool isNew;
        public string rigGuid;
        public bool thirdParty;
        public string sha256;
        public string pathPrefix;
        public string samplePath;
        public List<TakeEntry> takes = new List<TakeEntry>();
    }

    [Serializable]
    internal class TakeEntry
    {
        public string name;
        public float startFrame;
        public List<UnityClipEntry> unityClips = new List<UnityClipEntry>();
    }

    [Serializable]
    internal class UnityClipEntry
    {
        public string name;
        public float firstFrame;
        public float lastFrame;
        public bool loop;
        public string rootMotion; // IN_PLACE | ROOT_MOTION | CUSTOM
    }

    [Serializable]
    internal class FocusRef
    {
        public string fileGuid = "";
        public string assetPath = "";
        public string take = "";
    }

    [Serializable]
    internal class MaterialPreview
    {
        public string name;
        public string texture;
    }

    [Serializable]
    internal class SessionSettings
    {
        public int historySize = 10;
        public int historyMaxMB = 2048;
        public string clipsFolder = "Animations";
        public string colliderSuffix = "_COL";
        public bool autoPinClips;
        public List<string> humanBones = new List<string>();
    }

    [Serializable]
    internal class ExportManifest
    {
        public int version;
        public string sessionId;
        public string createdUtc;
        public string blendPath;
        public string modelGuid;
        public bool modelChanged;
        public bool skeletonChanged;
        public List<ExportItem> items = new List<ExportItem>();
        public List<string> warnings = new List<string>();
    }

    [Serializable]
    internal class ExportItem
    {
        public string kind; // model | clip | anim
        public string assetPath;
        public string guid;
        public bool isNew;
        public bool hasMesh;
        public string rigGuid;
        public List<TakeMap> takes = new List<TakeMap>();
        public List<ManagedClip> clips = new List<ManagedClip>();
        public List<string> changedTakes = new List<string>();
        public string armatureName;
        public bool modelMultiRoot;
        public string animGuid;
        public string animPath;
        public string stagingPath;
        public string pathPrefix;
    }

    [Serializable]
    internal class TakeMap
    {
        public string originalName;
        public string exportedName;
        public float originalStart;
        public float exportedStart;
    }

    [Serializable]
    internal class ManagedClip
    {
        public string name;
        public string takeName;
        public float firstFrame;
        public float lastFrame;
        public bool loop;
        public string rootMotion;
    }

    [Serializable]
    internal class StateFile
    {
        public string guid;
        public string assetPath;
        public string blendPath;
        public bool pinned;
        public string fbxSha256AtSync;
        public bool dirty;
        public string lastOpenedUtc;
        public string lastExportUtc;
        public string savedUtc;
    }

    [Serializable]
    internal class LinksFile
    {
        public int version = 1;
        public List<PinEntry> pins = new List<PinEntry>();
        public List<ClipRigEntry> clipRigs = new List<ClipRigEntry>();
    }

    [Serializable]
    internal class PinEntry
    {
        public string guid;
        public string assetPath;
        public string blend;
    }

    [Serializable]
    internal class ClipRigEntry
    {
        public string clip;
        public string rig;
        public string prefix;
    }

    [Serializable]
    internal class LockFile
    {
        public int pid;
        public string blend;
        public string startedUtc;
    }

    [Serializable]
    internal class HistoryInfo
    {
        public string guid;
        public string assetPath;
        public string fileName;
        public string createdUtc;
        public string sessionId;
        public string reason;
        public string sha256;
    }

    [Serializable]
    internal class ExportReport
    {
        public string timeUtc;
        public string assetPath;
        public string summary;
        public List<string> errors = new List<string>();
        public List<string> warnings = new List<string>();
        public List<string> infos = new List<string>();
    }

    [Serializable]
    internal class ReportsFile
    {
        public List<ExportReport> reports = new List<ExportReport>();
    }
}
