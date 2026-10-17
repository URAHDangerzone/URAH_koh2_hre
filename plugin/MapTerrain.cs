using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;
using UnityEngine;

namespace URAH_HRE
{
    /// <summary>
    /// A whole-map terrain replacement produced by tools/make_hre_terrain.py (gzip-compressed "PMT1",
    /// see the tool's docstring): full heightmap and the top two splat layers per alphamap texel.
    /// </summary>
    internal sealed class MapTerrain
    {
        public const string FileName = "terrain.pmt";

        public string Path;
        public int HeightRes, AlphaRes, Layers;
        public float MaxHeight;
        public ushort[] Heights; // [z * HeightRes + x], world height = v / 65535 * MaxHeight
        public byte[] LayerA, LayerB, WeightA; // [z * AlphaRes + x]

        public const string TreesFileName = "trees.bin";

        /// <summary>River courses from tools/hre_rivers.py (x, water surface y, z, width); null = none.</summary>
        public List<Vector4[]> Rivers;

        /// <summary>Mountain models from tools/make_hre_mountains.py; null = none.</summary>
        public List<MountainBuilder.Mountain> Mountains;

        /// <summary>Forests from tools/make_hre_trees.py; null = no trees.</summary>
        public TreeInstance[] Trees;

        /// <summary>trees.bin: "PMTR", int32 count, per tree float32 x, y, z (normalized), width, height, rotation, uint8 prototype.</summary>
        public void LoadTrees(string path)
        {
            using (var r = new BinaryReader(File.OpenRead(path)))
            {
                string magic = Encoding.ASCII.GetString(r.ReadBytes(4));
                if (magic != "PMTR")
                {
                    throw new InvalidDataException($"{path}: bad magic '{magic}'");
                }
                int n = r.ReadInt32();
                var trees = new TreeInstance[n];
                var white = new Color32(255, 255, 255, 255);
                for (int i = 0; i < n; i++)
                {
                    trees[i] = new TreeInstance
                    {
                        position = new Vector3(r.ReadSingle(), r.ReadSingle(), r.ReadSingle()),
                        widthScale = r.ReadSingle(),
                        heightScale = r.ReadSingle(),
                        rotation = r.ReadSingle(),
                        prototypeIndex = r.ReadByte(),
                        color = white,
                        lightmapColor = white,
                    };
                }
                Trees = trees;
            }
        }

        public static MapTerrain Load(string path)
        {
            using (var r = new BinaryReader(new GZipStream(File.OpenRead(path), CompressionMode.Decompress)))
            {
                string magic = Encoding.ASCII.GetString(r.ReadBytes(4));
                if (magic != "PMT1")
                {
                    throw new InvalidDataException($"{path}: bad magic '{magic}'");
                }
                var t = new MapTerrain { Path = path };
                t.HeightRes = r.ReadInt32();
                t.AlphaRes = r.ReadInt32();
                t.Layers = r.ReadInt32();
                t.MaxHeight = r.ReadSingle();
                t.Heights = new ushort[t.HeightRes * t.HeightRes];
                byte[] hb = ReadExactly(r, t.Heights.Length * 2);
                Buffer.BlockCopy(hb, 0, t.Heights, 0, hb.Length);
                int n = t.AlphaRes * t.AlphaRes;
                t.LayerA = ReadExactly(r, n);
                t.LayerB = ReadExactly(r, n);
                t.WeightA = ReadExactly(r, n);
                return t;
            }
        }

        private static byte[] ReadExactly(BinaryReader r, int count)
        {
            byte[] b = r.ReadBytes(count);
            if (b.Length != count)
            {
                throw new InvalidDataException("truncated terrain file");
            }
            return b;
        }

        /// <summary>Replaces heights and splat of the campaign terrain and removes all trees.</summary>
        public void Apply(Terrain terrain)
        {
            TerrainData data = terrain.terrainData;
            if (data.heightmapResolution != HeightRes || data.alphamapResolution != AlphaRes || data.alphamapLayers != Layers)
            {
                Plugin.Log.LogError($"Terrain is {data.heightmapResolution}/{data.alphamapResolution}/{data.alphamapLayers}, " +
                                    $"{System.IO.Path.GetFileName(Path)} is {HeightRes}/{AlphaRes}/{Layers}; not applied");
                return;
            }
            var watch = System.Diagnostics.Stopwatch.StartNew();

            // trees first: TreesBatching rebuilds its GPU buffers from the new instance list (the vanilla
            // trees would float or sink on the new terrain)
            int trees = data.treeInstanceCount;
            data.treeInstances = Trees ?? new TreeInstance[0];
            foreach (TreesBatching batching in UnityEngine.Object.FindObjectsOfType<TreesBatching>())
            {
                batching.Rebuild();
            }

            // GetHeights/SetHeights are indexed [z, x] and normalized to size.y
            float scale = MaxHeight / 65535f / data.size.y;
            var heights = new float[HeightRes, HeightRes];
            for (int z = 0; z < HeightRes; z++)
            {
                int row = z * HeightRes;
                for (int x = 0; x < HeightRes; x++)
                {
                    heights[z, x] = Mathf.Clamp01(Heights[row + x] * scale);
                }
            }
            data.SetHeights(0, 0, heights);
            long heightsMs = watch.ElapsedMilliseconds;

            // alphamap in row bands: the full float[2048, 2048, 24] would be 400 MB
            const int band = 128;
            var alphas = new float[band, AlphaRes, Layers];
            for (int z0 = 0; z0 < AlphaRes; z0 += band)
            {
                int rows = Math.Min(band, AlphaRes - z0);
                if (rows != band)
                {
                    alphas = new float[rows, AlphaRes, Layers];
                }
                else
                {
                    Array.Clear(alphas, 0, alphas.Length);
                }
                for (int j = 0; j < rows; j++)
                {
                    int row = (z0 + j) * AlphaRes;
                    for (int x = 0; x < AlphaRes; x++)
                    {
                        float a = WeightA[row + x] / 255f;
                        alphas[j, x, LayerA[row + x]] += a;
                        alphas[j, x, LayerB[row + x]] += 1f - a;
                    }
                }
                data.SetAlphamaps(0, z0, alphas);
            }
            int hidden = HideSceneDecorations(terrain.gameObject.scene);
            if (Rivers != null)
            {
                RiverBuilder.Build(terrain.gameObject.scene, Rivers);
            }
            if (Mountains != null)
            {
                MountainBuilder.Build(terrain.gameObject.scene, terrain, Mountains);
            }
            Plugin.Log.LogInfo($"Map terrain applied from {Path}: {trees} trees replaced by {Trees?.Length ?? 0}, {hidden} scene groups hidden, " +
                               $"heights {heightsMs} ms, total {watch.ElapsedMilliseconds} ms");
        }

        /// <summary>
        /// Scene roots of the europe map (bundle maps_europe) placed for the vanilla terrain: mountain and
        /// rock meshes, river and lake meshes, docks, boats, birds and fish, weather, mills and towers.
        /// Deactivated rather than destroyed, so nothing referencing them breaks.
        /// </summary>
        private static readonly string[] Decorations =
        {
            "MOUNTAINS", "ROCKS", "Small Rivers", "BigRivers", "LAKES", "WATER PROPS", "SHIPS", "ANIMALS",
            "EFFECTS", "WORLD_PG", "SEA NAMES",
        };

        /// <summary>Layers whose renderers belong to the vanilla landscape (FarPlanePerLayer names them).</summary>
        private static readonly string[] DecorationLayers = { "Mountains", "Rocks", "Lakes" };

        /// <summary>Roots this plugin builds; their renderers stay on.</summary>
        internal static readonly string[] OwnRoots = { "URAH_Rivers", "URAH_Mountains" };

        private static int HideSceneDecorations(UnityEngine.SceneManagement.Scene scene)
        {
            int hidden = 0;
            foreach (GameObject root in scene.GetRootGameObjects())
            {
                if (Array.IndexOf(Decorations, root.name) >= 0 && root.activeSelf)
                {
                    root.SetActive(false);
                    hidden++;
                }
            }
            // stray vanilla rocks / mountains outside those roots (e.g. Africa's), in any loaded scene
            int mask = 0;
            foreach (string layer in DecorationLayers)
            {
                int l = LayerMask.NameToLayer(layer);
                if (l >= 0)
                {
                    mask |= 1 << l;
                }
            }
            int renderers = 0;
            foreach (Renderer r in UnityEngine.Object.FindObjectsOfType<Renderer>())
            {
                if (r.enabled && (mask & (1 << r.gameObject.layer)) != 0 && !UnderOwnRoot(r.transform))
                {
                    r.enabled = false;
                    renderers++;
                }
            }
            if (renderers > 0)
            {
                Plugin.Log.LogInfo($"Hid {renderers} more vanilla renderers on the {string.Join(" / ", DecorationLayers)} layers");
            }
            return hidden;
        }

        internal static bool UnderOwnRoot(Transform t)
        {
            return Array.IndexOf(OwnRoots, t.root.name) >= 0;
        }
    }
}
