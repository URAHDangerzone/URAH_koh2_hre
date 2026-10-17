using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;

namespace URAH_HRE
{
    /// <summary>
    /// Vanilla mountain models on the HRE map's highest ranges (tools/make_hre_mountains.py). The scene's
    /// own mountains are static-batched into combined meshes with world-space vertices and cannot be
    /// moved, so the original prefabs are loaded through the game's asset loader. Their cores are
    /// stamped impassable by FullPathfinding.
    /// </summary>
    internal static class MountainBuilder
    {
        public const string FileName = "mountains.bin";
        private const string PrefabPath = "assets/europe/mountains/background mountains pack/prefabs/mountain{0:D2}.prefab";
        private const float HeightScale = 1.3f; // vanilla instances are ~1.3x as tall as wide

        public struct Mountain
        {
            public float X, Z, Scale, Rotation, Core;
            /// <summary>Height of the mesh's base (world units, terrain-relative); NaN in PMMT files.</summary>
            public float Base;
            public int Prefab;
        }

        /// <summary>
        /// mountains.bin: "PMM2", int32 count, per mountain float32 x, z, scale, rotation, core radius (0 =
        /// walkable), base height, int32 prefab. The older "PMMT" has no base height.
        /// </summary>
        public static List<Mountain> Load(string path)
        {
            var list = new List<Mountain>();
            using (var r = new BinaryReader(File.OpenRead(path)))
            {
                string magic = Encoding.ASCII.GetString(r.ReadBytes(4));
                if (magic != "PMMT" && magic != "PMM2")
                {
                    throw new InvalidDataException($"{path}: bad magic '{magic}'");
                }
                bool withBase = magic == "PMM2";
                int n = r.ReadInt32();
                for (int i = 0; i < n; i++)
                {
                    var m = new Mountain
                    {
                        X = r.ReadSingle(), Z = r.ReadSingle(), Scale = r.ReadSingle(),
                        Rotation = r.ReadSingle(), Core = r.ReadSingle(), Base = float.NaN,
                    };
                    if (withBase)
                    {
                        m.Base = r.ReadSingle();
                    }
                    m.Prefab = r.ReadInt32();
                    list.Add(m);
                }
            }
            return list;
        }

        public static int Build(UnityEngine.SceneManagement.Scene scene, Terrain terrain, List<Mountain> mountains)
        {
            var prefabs = new Dictionary<int, GameObject>();
            var root = new GameObject("URAH_Mountains");
            UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(root, scene);
            int layer = LayerMask.NameToLayer("Mountains");
            int built = 0, missing = 0;
            foreach (Mountain m in mountains)
            {
                if (!prefabs.TryGetValue(m.Prefab, out GameObject prefab))
                {
                    prefab = Assets.GetObject(string.Format(PrefabPath, m.Prefab)) as GameObject;
                    prefabs[m.Prefab] = prefab;
                }
                if (prefab == null)
                {
                    missing++;
                    continue;
                }
                GameObject go = Object.Instantiate(prefab, root.transform);
                go.name = $"Mountain{m.Prefab:D2}_{built}";
                // PMM2: sunk to the stored base like vanilla's (tools/make_hre_mountains.py); PMMT: sit on the
                // lowest ground of the core so no edge floats
                float y = m.Base;
                if (float.IsNaN(y))
                {
                    y = float.MaxValue;
                    for (int i = 0; i < 9; i++)
                    {
                        float a = i * Mathf.PI * 2f / 8f;
                        float d = i == 8 ? 0f : m.Core;
                        var p = new Vector3(m.X + Mathf.Cos(a) * d, 0f, m.Z + Mathf.Sin(a) * d);
                        y = Mathf.Min(y, terrain.SampleHeight(p));
                    }
                    y -= 0.3f;
                }
                go.transform.position = new Vector3(m.X, y + terrain.transform.position.y, m.Z);
                go.transform.rotation = Quaternion.Euler(0f, m.Rotation, 0f);
                go.transform.localScale = new Vector3(m.Scale, m.Scale * HeightScale, m.Scale);
                if (layer >= 0)
                {
                    foreach (Transform t in go.GetComponentsInChildren<Transform>(true))
                    {
                        t.gameObject.layer = layer;
                    }
                }
                built++;
            }
            if (built > 0)
            {
                StaticBatchingUtility.Combine(root);
            }
            Plugin.Log.LogInfo($"Mountains: {built} built{(missing > 0 ? $", {missing} without a loadable prefab" : "")}");
            return built;
        }
    }
}
