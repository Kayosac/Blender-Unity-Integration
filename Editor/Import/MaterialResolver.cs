using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Farik.BlenderBridge
{
    /// <summary>Gives new material slots a project material: same name first, else a copy of the template.</summary>
    internal static class MaterialResolver
    {
        public static void Resolve(string modelPath, ExportReport r)
        {
            var mi = AssetImporter.GetAtPath(modelPath) as ModelImporter;
            if (mi == null || mi.materialImportMode == ModelImporterMaterialImportMode.None) return;
            var before = ModelSnapshot.Load(AssetDatabase.AssetPathToGUID(modelPath));
            var known = new HashSet<string>(before?.materials ?? new List<string>());
            var remapped = new HashSet<string>(mi.GetExternalObjectMap().Keys
                .Where(k => k.type == typeof(Material)).Select(k => k.name));
            var embedded = AssetDatabase.LoadAllAssetsAtPath(modelPath).OfType<Material>()
                .Select(m => m.name).Where(n => !remapped.Contains(n) && !known.Contains(n)).Distinct().ToList();
            if (embedded.Count == 0) return;
            bool changed = false;
            var template = BridgeSettings.instance.templateMaterial;
            foreach (var name in embedded)
            {
                var mat = FindByName(name, modelPath);
                if (mat == null && template != null)
                {
                    var dir = Path.GetDirectoryName(modelPath)?.Replace('\\', '/') + "/Materials";
                    if (!AssetDatabase.IsValidFolder(dir))
                        AssetDatabase.CreateFolder(Path.GetDirectoryName(dir)?.Replace('\\', '/'), "Materials");
                    var dst = AssetDatabase.GenerateUniqueAssetPath(dir + "/" + name + ".mat");
                    if (AssetDatabase.CopyAsset(AssetDatabase.GetAssetPath(template), dst))
                    {
                        mat = AssetDatabase.LoadAssetAtPath<Material>(dst);
                        r.infos.Add($"Material slot '{name}': created {dst} from the template");
                    }
                }
                else if (mat != null)
                {
                    r.infos.Add($"Material slot '{name}': uses {AssetDatabase.GetAssetPath(mat)}");
                }
                if (mat == null)
                {
                    r.warnings.Add($"Material slot '{name}' has no material; set a template in Project Settings ▸ Blender Bridge");
                    continue;
                }
                mi.AddRemap(new AssetImporter.SourceAssetIdentifier(typeof(Material), name), mat);
                changed = true;
            }
            if (changed) mi.SaveAndReimport();
        }

        static Material FindByName(string name, string nearPath)
        {
            var best = AssetDatabase.FindAssets("t:Material " + name)
                .Select(AssetDatabase.GUIDToAssetPath)
                .Where(p => p.EndsWith(".mat") && Path.GetFileNameWithoutExtension(p) == name)
                .OrderByDescending(p => CommonPrefix(p, nearPath))
                .FirstOrDefault();
            return best == null ? null : AssetDatabase.LoadAssetAtPath<Material>(best);
        }

        static int CommonPrefix(string a, string b)
        {
            int n = Mathf.Min(a.Length, b.Length), i = 0;
            while (i < n && a[i] == b[i]) i++;
            return i;
        }
    }
}
