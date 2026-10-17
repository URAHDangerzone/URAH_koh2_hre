using System;
using System.Collections.Generic;
using System.Diagnostics;
using Logic;
using URAH_MapKit;
using UnityEngine;

namespace URAH_HRE
{
    /// <summary>
    /// Regenerates the whole campaign pathfinding for a whole-map terrain, mirroring the game's editor-only
    /// PathDataBuilder (BuildTerrain, buildCoast, BuildLSA) on the low level grid and the high grid
    /// generation of RuntimePathDataBuilder.GenerateHighPFData. Rivers, lakes, roads, walls and mountain
    /// meshes are not stamped yet; towns stamp their own bits when settlements load.
    /// </summary>
    internal static class FullPathfinding
    {
        private const float WaterLevel = 10f;
        private const byte OutOfBoundsSlope = 102; // TerrainInfo.out_of_bounds_slope
        private const byte Unset = byte.MaxValue;

        /// <summary>
        /// Land areas smaller than this (nodes) get LSA 0 (impassable). The game only keeps areas with a
        /// town; until the HRE map has its own settlements every area big enough to matter gets an id.
        /// </summary>
        private const int MinLandArea = 400;

        /// <summary>
        /// Road bit (1) on the nodes under the mod's RoadsTrapezoidData.bin (tools/make_hre_roads.py), like
        /// the game's BuildRoads: cheaper travel, walkable on any slope. Sea, lake and coast nodes keep their
        /// bits. A road over a river makes a bridge: the river bit goes, like the gaps vanilla's rivers
        /// have under roads, so armies on the road walk across instead of the river jump (a 10 s tent
        /// stop, Army.HandleRiverCrossing).
        /// </summary>
        private static int StampRoads(string path, int w, int h, byte[] bits, out int bridges)
        {
            bridges = 0;
            if (path == null || !System.IO.File.Exists(path))
            {
                return 0;
            }
            byte[] raw = System.IO.File.ReadAllBytes(path);
            int stamped = 0;
            for (int o = 0; o + 12 <= raw.Length; o += 12)
            {
                float x = BitConverter.ToSingle(raw, o);
                float z = BitConverter.ToSingle(raw, o + 8);
                if (x < 0f || z < 0f)
                {
                    continue; // road terminator
                }
                int i = (int)z * w + (int)x;
                if ((int)x >= w || (int)z >= h || (bits[i] & 0x1C) != 0 || (bits[i] & 1) != 0)
                {
                    continue;
                }
                if ((bits[i] & 2) != 0)
                {
                    bits[i] &= unchecked((byte)~2);
                    bridges++;
                }
                bits[i] |= 1;
                stamped++;
            }
            return stamped;
        }

        /// <summary>
        /// Rivers from rivers.bin like vanilla's: nodes under a river get the river bit (2, impassable on
        /// foot, not "water"), and every CrossingSpacing units a pair of bank nodes on either side gets a
        /// mutual river_offset (a jump of up to 7 x 3 nodes per axis, PathFinding.GetRiverOffset) that the
        /// pathfinder crosses at river_avoidance cost.
        /// </summary>
        private static int StampRivers(List<Vector4[]> rivers, int w, int h, byte[] bits, byte[] offsets, out int crossings)
        {
            crossings = 0;
            if (rivers == null)
            {
                return 0;
            }
            int stamped = 0;
            foreach (Vector4[] course in rivers)
            {
                foreach (Vector4 p in course)
                {
                    float half = Math.Max(p.w * RiverBuilder.VisibleShare / 2f, 0.6f); // rivers widen downstream
                    int r = (int)Math.Ceiling(half);
                    int cx = (int)p.x, cz = (int)p.z;
                    for (int dz = -r; dz <= r; dz++)
                    {
                        for (int dx = -r; dx <= r; dx++)
                        {
                            int x = cx + dx, z = cz + dz;
                            if (x < 1 || z < 1 || x >= w - 1 || z >= h - 1)
                            {
                                continue;
                            }
                            float ddx = x + 0.5f - p.x, ddz = z + 0.5f - p.z;
                            int i = z * w + x;
                            if (ddx * ddx + ddz * ddz <= (half + 0.3f) * (half + 0.3f) && (bits[i] & 0x0E) == 0)
                            {
                                bits[i] |= 2;
                                stamped++;
                            }
                        }
                    }
                }
            }
            foreach (Vector4[] course in rivers)
            {
                int step = Math.Max((int)(CrossingSpacing / 0.5f), 1);
                for (int k = step / 2; k + 1 < course.Length; k += step)
                {
                    Vector4 a = course[Math.Max(k - 1, 0)], b = course[k + 1], c = course[k];
                    float tx = b.x - a.x, tz = b.z - a.z;
                    float len = (float)Math.Sqrt(tx * tx + tz * tz);
                    if (len < 1e-3f)
                    {
                        continue;
                    }
                    float nx = -tz / len, nz = tx / len;
                    float d = Math.Max(c.w * RiverBuilder.VisibleShare / 2f, 0.6f) + 2f;
                    int ax = (int)(c.x - nx * d), az = (int)(c.z - nz * d);
                    for (float reach = 2f * d; reach <= 2f * d + 6f; reach += 3f)
                    {
                        int ox = (int)Math.Round(nx * reach / 3f) * 3, oz = (int)Math.Round(nz * reach / 3f) * 3;
                        if ((ox == 0 && oz == 0) || Math.Abs(ox) > 21 || Math.Abs(oz) > 21)
                        {
                            continue;
                        }
                        int bx = ax + ox, bz = az + oz;
                        if (ax < 1 || az < 1 || bx < 1 || bz < 1 || ax >= w - 1 || bx >= w - 1 || az >= h - 1 || bz >= h - 1)
                        {
                            break;
                        }
                        int ia = az * w + ax, ib = bz * w + bx;
                        if ((bits[ia] & 0x0E) != 0 || (bits[ib] & 0x0E) != 0 || offsets[ia] != 0 || offsets[ib] != 0)
                        {
                            continue;
                        }
                        offsets[ia] = EncodeOffset(ox, oz);
                        offsets[ib] = EncodeOffset(-ox, -oz);
                        crossings++;
                        break;
                    }
                }
            }
            return stamped;
        }

        private const float CrossingSpacing = 3f;
        private const byte MountainSlope = 100; // TerrainInfo.mountain_slope: impassable, like PathDataBuilder.BuildMountains

        /// <summary>Impassable cores of the mountain models (tools/make_hre_mountains.py) on dry land; meshes
        /// with core 0 (massif edges, lower ranges) are walkable.</summary>
        private static int StampMountains(List<MountainBuilder.Mountain> mountains, int w, int h, byte[] slope, byte[] bits)
        {
            if (mountains == null)
            {
                return 0;
            }
            int stamped = 0;
            foreach (MountainBuilder.Mountain m in mountains)
            {
                if (m.Core <= 0f)
                {
                    continue;
                }
                int r = (int)Math.Ceiling(m.Core);
                for (int dz = -r; dz <= r; dz++)
                {
                    for (int dx = -r; dx <= r; dx++)
                    {
                        int x = (int)m.X + dx, z = (int)m.Z + dz;
                        if (x < 1 || z < 1 || x >= w - 1 || z >= h - 1 || dx * dx + dz * dz > m.Core * m.Core)
                        {
                            continue;
                        }
                        int i = z * w + x;
                        if ((bits[i] & 0x0E) == 0 && slope[i] != MountainSlope)
                        {
                            slope[i] = MountainSlope;
                            stamped++;
                        }
                    }
                }
            }
            return stamped;
        }

        /// <summary>Inverse of PathFinding.GetRiverOffset: sign x (bit 7), |x| / 3 (bits 4-6), sign z (bit 3), |z| / 3 (bits 0-2).</summary>
        private static byte EncodeOffset(int dx, int dz)
        {
            return (byte)(((dx > 0 ? 1 : 0) << 7) | ((Math.Abs(dx) / 3) << 4) | ((dz > 0 ? 1 : 0) << 3) | (Math.Abs(dz) / 3));
        }

        public static void Apply(Logic.PathFinding pf, MapTerrain terrain, string roadsPath = null)
        {
            PathData data = pf?.data;
            if (data?.nodes == null || data.nodes.Length == 0)
            {
                Plugin.Log.LogWarning("No pathfinding data loaded, map pathfinding not generated");
                return;
            }
            var watch = Stopwatch.StartNew();
            int w = data.width;
            int h = data.height;
            byte[] slope = new byte[w * h];
            byte[] bits = new byte[w * h];
            BuildTerrain(terrain, w, h, slope, bits);
            int coast = BuildCoast(w, h, bits);
            int mountainNodes = StampMountains(terrain.Mountains, w, h, slope, bits);
            byte[] riverOffsets = new byte[w * h];
            int riverNodes = StampRivers(terrain.Rivers, w, h, bits, riverOffsets, out int crossings);
            int roads = StampRoads(roadsPath, w, h, bits, out int bridges);
            byte[] lsa = BuildLsa(w, h, slope, bits, (int)pf.settings.max_slope, out int areas, out int dropped);
            long lowMs = watch.ElapsedMilliseconds;

            int land = 0;
            for (int y = 0; y < h; y++)
            {
                for (int x = 0; x < w; x++)
                {
                    int i = y * w + x;
                    var node = new PathData.Node { slope = slope[i], bits = bits[i], lsa = lsa[i], river_offset = riverOffsets[i], pa_id = 0 };
                    if (y < 1 || y + 1 >= h || x < 1 || x + 1 >= w)
                    {
                        node.weight = 0;
                    }
                    else
                    {
                        data.CalcWeight(ref node);
                    }
                    data.nodes[i] = node;
                    if ((bits[i] & 8) == 0)
                    {
                        land++;
                    }
                }
            }
            Plugin.Log.LogInfo($"Map pathfinding: {w}x{h} nodes, {land} land, {coast} coast, {areas} land areas " +
                               $"({dropped} smaller than {MinLandArea} nodes left impassable), {riverNodes} river nodes, {crossings} river crossings, {roads} road nodes ({bridges} bridges), {mountainNodes} mountain nodes, {lowMs} ms");

            ResetHighGrid(data);
            HighGrid.Patch(pf, 0, w - 1, 0, h - 1, setOcean: true);
            Plugin.Log.LogInfo($"Map pathfinding generated in {watch.ElapsedMilliseconds} ms");
        }

        /// <summary>
        /// PathDataBuilder.BuildTerrain: every heightmap sample falls into one node; a node keeps the max slope
        /// of its samples and is ocean if any sample is at or below the water level.
        /// </summary>
        private static void BuildTerrain(MapTerrain t, int w, int h, byte[] slope, byte[] bits)
        {
            int res = t.HeightRes;
            float scale = t.MaxHeight / 65535f;
            float cellX = CampaignTerrain.SizeX / (res - 1);
            float cellZ = CampaignTerrain.SizeZ / (res - 1);
            for (int i = 0; i < res; i++)
            {
                int y = (int)((long)i * h / res);
                int row = i * res;
                for (int j = 0; j < res; j++)
                {
                    int x = (int)((long)j * w / res);
                    int idx = y * w + x;
                    float height = t.Heights[row + j] * scale;
                    byte s;
                    if (i == 0 || i == res - 1 || j == 0 || j == res - 1)
                    {
                        s = OutOfBoundsSlope;
                    }
                    else
                    {
                        // TerrainData.GetSteepness at a sample: angle of the central-difference normal
                        float gx = (t.Heights[row + j + 1] - t.Heights[row + j - 1]) * scale / (2f * cellX);
                        float gz = (t.Heights[row + res + j] - t.Heights[row - res + j]) * scale / (2f * cellZ);
                        s = (byte)(Math.Atan(Math.Sqrt(gx * gx + gz * gz)) * 180.0 / Math.PI);
                    }
                    if (s > slope[idx])
                    {
                        slope[idx] = s;
                    }
                    if (height <= WaterLevel)
                    {
                        bits[idx] |= 8;
                    }
                }
            }
        }

        /// <summary>PathDataBuilder.buildCoast: ocean nodes next to dry land become coast (bit 4 instead of 8).</summary>
        private static int BuildCoast(int w, int h, byte[] bits)
        {
            int coast = 0;
            for (int x = 0; x < w; x++)
            {
                for (int y = 0; y < h; y++)
                {
                    int idx = y * w + x;
                    if ((bits[idx] & 8) == 0)
                    {
                        continue;
                    }
                    bool dry = false;
                    for (int nx = x - 1; nx <= x + 1 && !dry; nx++)
                    {
                        for (int ny = y - 1; ny <= y + 1; ny++)
                        {
                            if (nx >= 0 && nx < w && ny >= 0 && ny < h && (nx != x || ny != y) && (bits[ny * w + nx] & 0xE) == 0)
                            {
                                dry = true;
                                break;
                            }
                        }
                    }
                    if (dry)
                    {
                        bits[idx] = (byte)((bits[idx] & ~8) | 4);
                        coast++;
                    }
                }
            }
            return coast;
        }

        /// <summary>
        /// PathDataBuilder.BuildLSA: 8-connected waves over walkable nodes (slope within the limit, or road /
        /// coast) of the same kind; all ocean is area 1, land areas count up from 2. Unlike the game, land
        /// areas need no town but a minimum size.
        /// </summary>
        private static byte[] BuildLsa(int w, int h, byte[] slope, byte[] bits, int maxSlope, out int areas, out int dropped)
        {
            byte[] lsa = new byte[w * h];
            for (int i = 0; i < lsa.Length; i++)
            {
                lsa[i] = Unset;
            }
            bool Check(int idx, int oceanMask) =>
                lsa[idx] == Unset && (slope[idx] <= maxSlope || (bits[idx] & 5) != 0) && (bits[idx] & 8) == oceanMask;

            int next = 2;
            areas = 0;
            dropped = 0;
            var queue = new Queue<int>();
            var members = new List<int>();
            for (int start = 0; start < lsa.Length; start++)
            {
                if (lsa[start] != Unset)
                {
                    continue;
                }
                int oceanMask = bits[start] & 8;
                if (!Check(start, oceanMask))
                {
                    lsa[start] = 0;
                    continue;
                }
                // mark with a placeholder while collecting, then assign the id once the size is known
                const byte collecting = 254;
                members.Clear();
                queue.Enqueue(start);
                lsa[start] = collecting;
                while (queue.Count > 0)
                {
                    int idx = queue.Dequeue();
                    members.Add(idx);
                    int cx = idx % w, cy = idx / w;
                    for (int dy = -1; dy <= 1; dy++)
                    {
                        for (int dx = -1; dx <= 1; dx++)
                        {
                            int nx = cx + dx, ny = cy + dy;
                            if ((dx == 0 && dy == 0) || nx < 0 || ny < 0 || nx >= w || ny >= h)
                            {
                                continue;
                            }
                            int n = ny * w + nx;
                            if (Check(n, oceanMask))
                            {
                                lsa[n] = collecting;
                                queue.Enqueue(n);
                            }
                        }
                    }
                }
                byte id;
                if (oceanMask != 0)
                {
                    id = 1;
                }
                else if (members.Count >= MinLandArea && next < collecting)
                {
                    id = (byte)next++;
                    areas++;
                }
                else
                {
                    id = 0;
                    dropped++;
                }
                foreach (int idx in members)
                {
                    lsa[idx] = id;
                }
            }
            if (next >= 254)
            {
                Plugin.Log.LogWarning("Ran out of LSA ids, later land areas are impassable");
            }
            return lsa;
        }

        /// <summary>Fresh high grid (all edges unset) and no additional (sea graph) nodes.</summary>
        private static void ResetHighGrid(PathData data)
        {
            int count = data.highPFGrid_width * data.highPFGrid_height;
            if (data.highPFGrid == null || data.highPFGrid.Length != count)
            {
                throw new InvalidOperationException("High level grid not loaded");
            }
            for (int i = 0; i < count; i++)
            {
                data.highPFGrid[i] = PathData.HighGridNode.Create();
            }
            for (int i = 0; i < data.highPFGridWeights.Length; i++)
            {
                data.highPFGridWeights[i] = ushort.MaxValue;
            }
            if (data.highPFAdditionalGrid.IsCreated)
            {
                data.highPFAdditionalGrid.Clear();
            }
            data.highPFAdditionalNodes = new PathData.HighAdditionalNode[0];
        }
    }
}
