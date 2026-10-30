using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge.Tests
{
    public class BridgeContractTests
    {
        [Test]
        public void ExportManifest_FromBlenderJson_ParsesEveryField()
        {
            // Shape written by Blender~/unity_bridge/exporter.py
            const string json = @"{
              ""version"": 1, ""sessionId"": ""abc"", ""createdUtc"": ""2026-10-30T10:00:00.000000Z"",
              ""blendPath"": ""ArtSource/Cache/Hero.blend"", ""modelGuid"": ""0123"", ""modelChanged"": true,
              ""skeletonChanged"": false,
              ""items"": [{
                ""kind"": ""clip"", ""assetPath"": ""Assets/Hero/Animations/Hero@Wave.fbx"", ""guid"": """",
                ""isNew"": true, ""hasMesh"": false, ""rigGuid"": ""0123"",
                ""takes"": [{""originalName"": ""Wave"", ""exportedName"": ""Wave"", ""originalStart"": 0.0, ""exportedStart"": 0.0}],
                ""clips"": [{""name"": ""Wave"", ""takeName"": ""Wave"", ""firstFrame"": 0.0, ""lastFrame"": 20.0,
                             ""loop"": true, ""rootMotion"": ""IN_PLACE""}],
                ""changedTakes"": [""Wave""], ""armatureName"": ""Armature"", ""modelMultiRoot"": true
              }],
              ""warnings"": [""w1""]
            }";
            var m = JsonUtility.FromJson<ExportManifest>(json);
            Assert.That(m.modelChanged, Is.True);
            Assert.That(m.items, Has.Count.EqualTo(1));
            var it = m.items[0];
            Assert.That(it.kind, Is.EqualTo("clip"));
            Assert.That(it.isNew, Is.True);
            Assert.That(it.clips[0].lastFrame, Is.EqualTo(20f));
            Assert.That(it.clips[0].rootMotion, Is.EqualTo("IN_PLACE"));
            Assert.That(it.changedTakes, Does.Contain("Wave"));
            Assert.That(it.modelMultiRoot, Is.True);
            Assert.That(m.warnings, Has.Count.EqualTo(1));
        }

        [Test]
        public void SessionManifest_HasKeysBlenderReads()
        {
            var m = new SessionManifest { sessionId = "s", blendPath = "ArtSource/Cache/A.blend", fresh = true };
            m.model.guid = "g";
            m.model.assetPath = "Assets/A.fbx";
            m.files.Add(new FileEntry { kind = "MODEL", assetPath = "Assets/A.fbx" });
            var json = JsonUtility.ToJson(m);
            foreach (var key in new[] { "\"sessionId\"", "\"projectRoot\"", "\"blendPath\"", "\"fresh\"", "\"model\"",
                         "\"files\"", "\"focus\"", "\"materials\"", "\"settings\"", "\"humanBones\"", "\"takes\"" })
                StringAssert.Contains(key, json);
        }

        [TestCase("IN_PLACE")]
        [TestCase("ROOT_MOTION")]
        public void RootMotion_ApplyThenRead_RoundTrips(string mode)
        {
            var c = new ModelImporterClipAnimation();
            ModelInfo.ApplyRootMotion(c, mode);
            Assert.That(ModelInfo.RootMotionMode(c), Is.EqualTo(mode));
        }

        [Test]
        public void RootMotion_Custom_LeavesFlagsAlone()
        {
            var c = new ModelImporterClipAnimation { lockRootRotation = true, lockRootHeightY = false, lockRootPositionXZ = true };
            ModelInfo.ApplyRootMotion(c, "CUSTOM");
            Assert.That(c.lockRootRotation, Is.True);
            Assert.That(c.lockRootHeightY, Is.False);
            Assert.That(ModelInfo.RootMotionMode(c), Is.EqualTo("CUSTOM"));
        }

        [Test]
        public void CacheBlendPath_MirrorsAssetsFolder()
        {
            var p = BridgePaths.CacheBlendFor("Assets/Project/Content/Hero/Hero.fbx");
            StringAssert.EndsWith("/Cache/Project/Content/Hero/Hero.blend", p);
            var q = BridgePaths.PinnedBlendFor("Assets/Project/Content/Hero/Hero.fbx");
            StringAssert.EndsWith("/Pinned/Project/Content/Hero/Hero.blend", q);
        }

        [Test]
        public void ThirdParty_DependsOnProjectRoots()
        {
            var s = BridgeSettings.instance;
            var saved = new System.Collections.Generic.List<string>(s.projectRoots);
            try
            {
                s.projectRoots = new System.Collections.Generic.List<string> { "Assets/Project" };
                Assert.That(s.IsThirdParty("Assets/Project/Content/A.fbx"), Is.False);
                Assert.That(s.IsThirdParty("Assets/ProjectX/A.fbx"), Is.True);
                Assert.That(s.IsThirdParty("Assets/Plugins/Store/A.fbx"), Is.True);
            }
            finally
            {
                s.projectRoots = saved;
            }
        }

        [Test]
        public void BlenderVersion_ParsesAndGates()
        {
            Assert.That(BlenderLocator.IsSupported("5.2.1"), Is.True);
            Assert.That(BlenderLocator.IsSupported("5.0"), Is.True);
            Assert.That(BlenderLocator.IsSupported("4.5.3"), Is.False);
            Assert.That(BlenderLocator.ParseVersion("Blender 5.3.0 Alpha").Minor, Is.EqualTo(3));
        }
    }
}
