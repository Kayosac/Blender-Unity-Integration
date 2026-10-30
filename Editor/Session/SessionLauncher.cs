using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    /// <summary>Builds the session manifest for Blender and starts it.</summary>
    internal static class SessionLauncher
    {
        /// <summary>Self-test hook: receives (exe, blend or null, manifest) instead of opening a window.</summary>
        internal static Action<string, string, string> LaunchOverride;

        /// <summary>Self-test entry: skips the pickers by taking a fully resolved target.</summary>
        internal static void OpenResolved(BridgeTarget t) => Launch(t);

        public static void OpenTargets(List<BridgeTarget> targets, string unsupported = null)
        {
            if (targets == null || targets.Count == 0)
            {
                EditorUtility.DisplayDialog("Blender Bridge", unsupported ?? "Select a model or an animation clip.", "OK");
                return;
            }
            if (targets.Count == 1)
            {
                Open(targets[0]);
                return;
            }
            PickerWindow.Show("Open in Blender", targets.Select(t => t.Label).ToList(), targets, Open);
        }

        public static void Open(BridgeTarget target) => TargetResolver.ResolveRig(target, Launch);

        public static void CreateNewModel(string assetPath)
        {
            Launch(new BridgeTarget { kind = TargetKind.NewModel, assetPath = assetPath, rigPath = assetPath });
        }

        static void Launch(BridgeTarget t)
        {
            try
            {
                LaunchInternal(t);
            }
            catch (Exception e)
            {
                BridgeIO.Error("Could not open in Blender: " + e);
                EditorUtility.DisplayDialog("Blender Bridge", "Could not open in Blender:\n" + e.Message, "OK");
            }
            finally
            {
                EditorUtility.ClearProgressBar();
            }
        }

        static void LaunchInternal(BridgeTarget t)
        {
            var exe = BlenderLocator.Resolve();
            if (exe == null)
            {
                if (EditorUtility.DisplayDialog("Blender Bridge", "Blender was not found. Set its location in Preferences ▸ Blender Bridge.",
                        "Open Preferences", "Cancel"))
                    SettingsService.OpenUserPreferences(BridgeSettingsProvider.PreferencesPath);
                return;
            }
            var version = BlenderLocator.GetVersion(exe);
            if (!string.IsNullOrEmpty(version) && !BlenderLocator.IsSupported(version))
            {
                if (!EditorUtility.DisplayDialog("Blender Bridge",
                        $"Blender {version} is too old: the bridge needs Blender 5.0 or newer.", "Try anyway", "Cancel"))
                    return;
            }
            BridgePaths.EnsureFolders();

            var settings = BridgeSettings.instance;
            var rigPath = t.rigPath;
            bool isNew = t.kind == TargetKind.NewModel;
            var modelGuid = isNew ? "" : AssetDatabase.AssetPathToGUID(rigPath);

            // Same model already open in another Blender window?
            if (!isNew)
            {
                var lk = LockStore.ReadAlive(modelGuid);
                if (lk != null && !EditorUtility.DisplayDialog("Blender Bridge",
                        Path.GetFileName(rigPath) + " is already open in Blender (process " + lk.pid +
                        "). A second window can overwrite the first one's work.", "Open another window", "Cancel"))
                    return;
            }

            // Which .blend: pinned, cached or new.
            var pin = isNew ? null : LinksStore.FindPin(modelGuid);
            string blendRel = pin != null && File.Exists(BridgePaths.Abs(pin.blend))
                ? pin.blend
                : BridgePaths.Rel(BridgePaths.CacheBlendFor(rigPath));
            bool pinned = pin != null && blendRel == pin.blend;
            var blendAbs = BridgePaths.Abs(blendRel);
            var modelAbs = BridgePaths.Abs(rigPath);
            var modelSha = isNew ? "" : BridgeIO.Sha256(modelAbs);
            var state = isNew ? null : StateStore.Read(modelGuid);

            bool fresh = isNew || !File.Exists(blendAbs) || state == null || state.fbxSha256AtSync != modelSha;
            if (fresh && File.Exists(blendAbs) && !isNew)
            {
                if (pinned)
                {
                    int choice = EditorUtility.DisplayDialogComplex("Blender Bridge",
                        Path.GetFileName(rigPath) + " changed outside Blender since the pinned .blend was saved.",
                        "Open pinned .blend", "Cancel", "Re-import from FBX");
                    if (choice == 1) return;
                    fresh = choice == 2;
                    if (fresh) KeepCopy(blendAbs);
                }
                else if (state != null && state.dirty)
                {
                    int choice = EditorUtility.DisplayDialogComplex("Blender Bridge",
                        "The cached .blend has edits that were never exported, and the FBX changed since.",
                        "Discard edits", "Cancel", "Keep a copy");
                    if (choice == 1) return;
                    if (choice == 2) KeepCopy(blendAbs);
                }
            }

            EditorUtility.DisplayProgressBar("Blender Bridge", "Preparing " + Path.GetFileName(t.assetPath), 0.3f);
            var m = new SessionManifest
            {
                sessionId = Guid.NewGuid().ToString("N"),
                projectRoot = BridgePaths.ProjectRoot,
                artSource = settings.artSourceFolder,
                blendPath = blendRel,
                fresh = fresh,
                pinned = pinned,
            };
            m.model.guid = modelGuid;
            m.model.assetPath = rigPath;
            m.model.name = Path.GetFileNameWithoutExtension(rigPath);
            m.model.isNew = isNew;
            m.model.thirdParty = settings.IsThirdParty(rigPath);
            m.model.sha256 = modelSha;

            m.settings.historySize = settings.historySize;
            m.settings.historyMaxMB = settings.historyMaxMB;
            m.settings.clipsFolder = settings.clipsFolder;
            m.settings.colliderSuffix = settings.colliderSuffix;
            m.settings.autoPinClips = settings.autoPinClips;

            if (isNew)
            {
                m.files.Add(new FileEntry { kind = "MODEL", assetPath = rigPath, isNew = true, hasMesh = true });
            }
            else
            {
                var mi = (ModelImporter)AssetImporter.GetAtPath(rigPath);
                m.model.animationType = mi.animationType.ToString();
                m.settings.humanBones = ModelInfo.HumanBones(mi);
                m.materials = ModelInfo.MaterialPreviews(mi);
                m.files.Add(new FileEntry
                {
                    guid = modelGuid, assetPath = rigPath, kind = "MODEL", hasMesh = ModelInfo.HasMesh(rigPath), sha256 = modelSha,
                    thirdParty = m.model.thirdParty, takes = ModelInfo.Takes(mi),
                });
                var clipFiles = ModelInfo.RelatedClipFiles(rigPath);
                clipFiles.Remove(rigPath);
                if (t.kind == TargetKind.ClipFbx && t.assetPath != rigPath && !clipFiles.Contains(t.assetPath))
                    clipFiles.Add(t.assetPath);
                foreach (var cf in clipFiles) m.files.Add(ClipEntry(cf, modelGuid, settings));
                if (t.kind == TargetKind.Anim)
                {
                    EditorUtility.DisplayProgressBar("Blender Bridge", "Sampling " + Path.GetFileName(t.assetPath), 0.6f);
                    m.files.Add(AnimEntry(t, modelGuid, m.sessionId, settings));
                }
                ModelSnapshot.Save(rigPath);
                if (t.kind == TargetKind.ClipFbx && t.assetPath != rigPath) ModelSnapshot.Save(t.assetPath);
            }

            m.focus = Focus(t);
            var manifestAbs = BridgePaths.Sessions + "/" + m.sessionId + ".json";
            BridgeIO.WriteJson(manifestAbs, m);
            if (!isNew)
            {
                StateStore.Update(modelGuid, s =>
                {
                    s.assetPath = rigPath;
                    s.blendPath = blendRel;
                    s.pinned = pinned;
                    s.lastOpenedUtc = BridgeIO.NowIso();
                });
            }
            EditorUtility.DisplayProgressBar("Blender Bridge", "Starting Blender", 0.9f);
            if (LaunchOverride != null)
            {
                LaunchOverride(exe, fresh ? null : blendAbs, manifestAbs);
                return;
            }
            if (BlenderLocator.Launch(exe, fresh ? null : blendAbs, manifestAbs))
                BridgeIO.Log($"Opening {t.Label} in Blender {version} ({(fresh ? "fresh import" : blendRel)})");
        }

        static void KeepCopy(string blendAbs)
        {
            var dir = Path.GetDirectoryName(blendAbs) ?? "";
            var copy = Path.Combine(dir, Path.GetFileNameWithoutExtension(blendAbs) + ".unexported-" +
                                         DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".blend");
            File.Copy(blendAbs, copy, true);
            BridgeIO.Log("Kept a copy of the old .blend: " + BridgePaths.Rel(copy));
        }

        static FileEntry ClipEntry(string path, string rigGuid, BridgeSettings settings)
        {
            var mi = (ModelImporter)AssetImporter.GetAtPath(path);
            return new FileEntry
            {
                guid = AssetDatabase.AssetPathToGUID(path),
                assetPath = path,
                kind = "CLIP",
                hasMesh = false,
                rigGuid = rigGuid,
                thirdParty = settings.IsThirdParty(path),
                sha256 = BridgeIO.Sha256(BridgePaths.Abs(path)),
                takes = ModelInfo.Takes(mi),
            };
        }

        static FileEntry AnimEntry(BridgeTarget t, string rigGuid, string sessionId, BridgeSettings settings)
        {
            var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(t.assetPath);
            var prefix = t.pathPrefix;
            if (string.IsNullOrEmpty(prefix)) prefix = AnimSampler.InferPrefix(clip, t.rigPath);
            var sampleAbs = BridgePaths.Sessions + "/" + sessionId + ".anim.json";
            AnimSampler.Sample(clip, t.rigPath, prefix, sampleAbs);
            LinksStore.SetClipRig(AssetDatabase.AssetPathToGUID(t.assetPath), rigGuid, prefix);
            var settingsClip = AnimationUtility.GetAnimationClipSettings(clip);
            return new FileEntry
            {
                guid = AssetDatabase.AssetPathToGUID(t.assetPath),
                assetPath = t.assetPath,
                kind = "ANIM",
                rigGuid = rigGuid,
                thirdParty = settings.IsThirdParty(t.assetPath),
                sha256 = BridgeIO.Sha256(BridgePaths.Abs(t.assetPath)),
                pathPrefix = prefix,
                samplePath = BridgePaths.Rel(sampleAbs),
                takes = new List<TakeEntry>
                {
                    new TakeEntry
                    {
                        name = clip.name,
                        unityClips = new List<UnityClipEntry>
                        {
                            new UnityClipEntry { name = clip.name, loop = settingsClip.loopTime, rootMotion = "CUSTOM" },
                        },
                    },
                },
            };
        }

        static FocusRef Focus(BridgeTarget t)
        {
            var f = new FocusRef();
            switch (t.kind)
            {
                case TargetKind.EmbeddedClip:
                {
                    var mi = (ModelImporter)AssetImporter.GetAtPath(t.assetPath);
                    var c = ModelInfo.EffectiveClips(mi).FirstOrDefault(x => x.name == t.clipName);
                    f.fileGuid = AssetDatabase.AssetPathToGUID(t.assetPath);
                    f.assetPath = t.assetPath;
                    f.take = c != null ? c.takeName : "";
                    break;
                }
                case TargetKind.ClipFbx:
                {
                    var mi = (ModelImporter)AssetImporter.GetAtPath(t.assetPath);
                    var c = string.IsNullOrEmpty(t.clipName)
                        ? null
                        : ModelInfo.EffectiveClips(mi).FirstOrDefault(x => x.name == t.clipName);
                    f.fileGuid = AssetDatabase.AssetPathToGUID(t.assetPath);
                    f.assetPath = t.assetPath;
                    f.take = c != null ? c.takeName : "";
                    break;
                }
                case TargetKind.Anim:
                    f.fileGuid = AssetDatabase.AssetPathToGUID(t.assetPath);
                    f.assetPath = t.assetPath;
                    f.take = t.clipName ?? "";
                    break;
            }
            return f;
        }
    }
}
