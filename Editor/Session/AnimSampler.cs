using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Farik.BlenderBridge
{
    /// <summary>Plays a .anim on its model in a hidden scene and writes every bone's matrix per frame.</summary>
    internal static class AnimSampler
    {
        /// <summary>Path from the Animator root to the model root, guessed from the clip's curve paths.</summary>
        public static string InferPrefix(AnimationClip clip, string rigPath)
        {
            if (clip == null || clip.humanMotion) return "";
            var go = AssetDatabase.LoadAssetAtPath<GameObject>(rigPath);
            if (go == null) return "";
            var modelPaths = new HashSet<string>(go.GetComponentsInChildren<Transform>(true)
                .Where(t => t != go.transform)
                .Select(t => AnimationUtility.CalculateTransformPath(t, go.transform)));
            var votes = new Dictionary<string, int>();
            foreach (var b in AnimationUtility.GetCurveBindings(clip).Where(b => b.type == typeof(Transform)))
            {
                if (modelPaths.Contains(b.path))
                {
                    Vote(votes, "");
                    continue;
                }
                var parts = b.path.Split('/');
                for (int i = 1; i < parts.Length; i++)
                {
                    var suffix = string.Join("/", parts.Skip(i));
                    if (modelPaths.Contains(suffix))
                    {
                        Vote(votes, string.Join("/", parts.Take(i)));
                        break;
                    }
                }
            }
            return votes.Count == 0 ? "" : votes.OrderByDescending(kv => kv.Value).First().Key;
        }

        static void Vote(Dictionary<string, int> votes, string key) =>
            votes[key] = votes.TryGetValue(key, out var n) ? n + 1 : 1;

        public static void Sample(AnimationClip clip, string rigPath, string prefix, string outAbs)
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(rigPath);
            if (prefab == null) throw new System.InvalidOperationException("Model not found: " + rigPath);
            var avatar = ModelInfo.GetAvatar(rigPath);
            var scene = EditorSceneManager.NewPreviewScene();
            try
            {
                var instance = UnityEngine.Object.Instantiate(prefab);
                instance.name = prefab.name;
                SceneManager.MoveGameObjectToScene(instance, scene);
                GameObject animRoot = instance;
                if (!string.IsNullOrEmpty(prefix))
                {
                    // prefix = path from the Animator root to the model root, model root included:
                    // "Visual/Hero" -> AnimatorRoot/Visual/Hero(model instance).
                    var segs = prefix.Split('/');
                    animRoot = new GameObject("UB_AnimatorRoot");
                    SceneManager.MoveGameObjectToScene(animRoot, scene);
                    var parent = animRoot.transform;
                    for (int i = 0; i < segs.Length - 1; i++)
                    {
                        var g = new GameObject(segs[i]);
                        g.transform.SetParent(parent, false);
                        parent = g.transform;
                    }
                    instance.name = segs[segs.Length - 1];
                    instance.transform.SetParent(parent, false);
                }

                // No ?? here: in the Editor a missing GetComponent result is a "fake null" that ?? treats as non-null.
                var animator = animRoot.GetComponent<Animator>();
                if (animator == null) animator = animRoot.AddComponent<Animator>();
                if (avatar != null) animator.avatar = avatar;
                animator.applyRootMotion = true;

                var bones = instance.GetComponentsInChildren<Transform>(true).Where(t => t != instance.transform).ToList();
                var paths = bones.Select(b => AnimationUtility.CalculateTransformPath(b, instance.transform)).ToList();
                var root = instance.transform;
                var rest = bones.Select(b => root.worldToLocalMatrix * b.localToWorldMatrix).ToList();

                float fps = clip.frameRate > 0 ? clip.frameRate : 30f;
                int frames = Mathf.Max(1, Mathf.RoundToInt(clip.length * fps) + 1);
                var data = new List<Matrix4x4>(frames * bones.Count);
                bool useAnimMode = clip.humanMotion;
                if (useAnimMode) AnimationMode.StartAnimationMode();
                try
                {
                    for (int f = 0; f < frames; f++)
                    {
                        float time = Mathf.Min(f / fps, clip.length);
                        if (useAnimMode)
                        {
                            AnimationMode.BeginSampling();
                            AnimationMode.SampleAnimationClip(animRoot, clip, time);
                            AnimationMode.EndSampling();
                        }
                        else
                        {
                            clip.SampleAnimation(animRoot, time);
                        }
                        foreach (var b in bones) data.Add(root.worldToLocalMatrix * b.localToWorldMatrix);
                    }
                }
                finally
                {
                    if (useAnimMode) AnimationMode.StopAnimationMode();
                }
                Write(outAbs, paths, rest, frames, fps, data, IsStepped(clip));
            }
            finally
            {
                EditorSceneManager.ClosePreviewScene(scene);
            }
        }

        /// <summary>True when the clip's curves hold their values between keys (constant tangents).</summary>
        public static bool IsStepped(AnimationClip clip)
        {
            int keys = 0, constant = 0;
            foreach (var b in AnimationUtility.GetCurveBindings(clip))
            {
                var c = AnimationUtility.GetEditorCurve(clip, b);
                if (c == null || c.length < 2) continue;
                for (int i = 0; i < c.length - 1; i++)
                {
                    keys++;
                    if (AnimationUtility.GetKeyRightTangentMode(c, i) == AnimationUtility.TangentMode.Constant ||
                        float.IsInfinity(c.keys[i].outTangent)) constant++;
                }
            }
            return keys > 0 && constant == keys;
        }

        static void Write(string path, List<string> bones, List<Matrix4x4> rest, int frames, float fps, List<Matrix4x4> data,
            bool stepped)
        {
            var sb = new StringBuilder(64 * 1024);
            sb.Append("{\"fps\":").Append(fps.ToString(CultureInfo.InvariantCulture));
            sb.Append(",\"stepped\":").Append(stepped ? "true" : "false");
            sb.Append(",\"frames\":").Append(frames);
            sb.Append(",\"bones\":[");
            for (int i = 0; i < bones.Count; i++)
            {
                if (i > 0) sb.Append(',');
                sb.Append('"').Append(bones[i].Replace("\\", "\\\\").Replace("\"", "\\\"")).Append('"');
            }
            sb.Append("],\"rest\":[");
            AppendMatrices(sb, rest);
            sb.Append("],\"data\":[");
            AppendMatrices(sb, data);
            sb.Append("]}");
            Directory.CreateDirectory(Path.GetDirectoryName(path) ?? ".");
            File.WriteAllText(path, sb.ToString());
        }

        static void AppendMatrices(StringBuilder sb, List<Matrix4x4> list)
        {
            bool first = true;
            foreach (var m in list)
            {
                for (int r = 0; r < 4; r++)
                for (int c = 0; c < 4; c++)
                {
                    if (!first) sb.Append(',');
                    first = false;
                    sb.Append(m[r, c].ToString("R", CultureInfo.InvariantCulture));
                }
            }
        }
    }
}
