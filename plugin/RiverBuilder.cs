using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;

namespace URAH_HRE
{
    /// <summary>
    /// Water meshes for the rivers of a whole-map terrain. Vanilla rivers are RamSpline objects (river
    /// meshes generated at runtime from control points: position + width) under the europe scene roots
    /// "BigRivers" / "Small Rivers", which MapTerrain hides. Each course from rivers.bin
    /// (tools/hre_rivers.py) becomes a clone of a vanilla river with new control points, so it uses the
    /// game's own water material.
    /// </summary>
    internal static class RiverBuilder
    {
        public const string FileName = "rivers.bin";
        private const float ControlSpacing = 6f; // world units between control points (Catmull-Rom in between)
        private const float SurfaceLift = 0.04f;

        /// <summary>Share of a course's width that shows: the mesh edges lie inside the banks (tools/hre_rivers.py).</summary>
        public const float VisibleShare = 0.7f;

        /// <summary>A course starting at least this share of its widest point continues another one: no spring.</summary>
        private const float JoinedStart = 0.3f;

        /// <summary>rivers.bin: "PMRV", int32 rivers, per river int32 points, points x (x, surface y, z, width).</summary>
        public static List<Vector4[]> Load(string path)
        {
            var rivers = new List<Vector4[]>();
            using (var r = new BinaryReader(File.OpenRead(path)))
            {
                string magic = Encoding.ASCII.GetString(r.ReadBytes(4));
                if (magic != "PMRV")
                {
                    throw new InvalidDataException($"{path}: bad magic '{magic}'");
                }
                int n = r.ReadInt32();
                for (int i = 0; i < n; i++)
                {
                    int k = r.ReadInt32();
                    var pts = new Vector4[k];
                    for (int j = 0; j < k; j++)
                    {
                        pts[j] = new Vector4(r.ReadSingle(), r.ReadSingle(), r.ReadSingle(), r.ReadSingle());
                    }
                    rivers.Add(pts);
                }
            }
            return rivers;
        }

        public static int Build(UnityEngine.SceneManagement.Scene scene, List<Vector4[]> rivers)
        {
            RamSpline template = FindTemplate(scene);
            if (template == null)
            {
                Plugin.Log.LogWarning("No vanilla RamSpline river found, rivers not built");
                return 0;
            }
            var root = new GameObject("URAH_Rivers");
            UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(root, scene);
            int built = 0;
            foreach (Vector4[] course in rivers)
            {
                List<Vector4> points = Thin(course);
                if (points.Count < 2)
                {
                    continue;
                }
                GameObject go = UnityEngine.Object.Instantiate(template.gameObject, root.transform);
                go.name = "River" + built;
                go.transform.position = Vector3.zero;
                go.transform.rotation = Quaternion.identity;
                go.transform.localScale = Vector3.one;
                foreach (Transform child in go.transform)
                {
                    UnityEngine.Object.Destroy(child.gameObject); // waypoints of the vanilla river
                }
                RamSpline spline = go.GetComponent<RamSpline>();
                spline.beginningSpline = null;
                spline.endingSpline = null;
                spline.beginnigChildSplines.Clear();
                spline.endingChildSplines.Clear();
                spline.snapToTerrain = false;
                spline.normalFromRaycast = false;
                spline.controlPoints = points;
                spline.controlPointsRotations = new List<Quaternion>();
                spline.controlPointsSnap = new List<float>();
                spline.controlPointsMeshCurves = new List<AnimationCurve>();
                foreach (Vector4 _ in points)
                {
                    spline.controlPointsRotations.Add(Quaternion.identity);
                    spline.controlPointsSnap.Add(0f);
                    spline.controlPointsMeshCurves.Add(new AnimationCurve(spline.meshCurve.keys));
                }
                spline.GenerateSpline();
                built++;
            }
            int springs = PlaceSprings(scene, root.transform, rivers);
            Plugin.Log.LogInfo($"Rivers: {built} courses built from template '{template.name}', {springs} springs");
            return built;
        }

        /// <summary>Vanilla's boulders (the europe scene's ROCKS group uses these prefabs).</summary>
        private static readonly string[] SpringRocks =
        {
            "assets/3rd party/art/nature/hq rocks/prefabs/rock_1_v1.prefab",
            "assets/3rd party/art/nature/hq rocks/prefabs/rock_8_v1.prefab",
        };

        /// <summary>
        /// A boulder at every river source inside the map, so a river emerges from somewhere instead of
        /// starting on open ground. Courses entering across the map edge or leaving another river get none (tools/hre_rivers.py
        /// only tapers real sources). The prefabs are loaded through the game's asset loader: the scene's
        /// own rocks are static-batched (world-space combined mesh), so clones of them would still be
        /// drawn at their vanilla positions.
        /// </summary>
        private static int PlaceSprings(UnityEngine.SceneManagement.Scene scene, Transform parent, List<Vector4[]> rivers)
        {
            var rocks = new List<GameObject>();
            foreach (string path in SpringRocks)
            {
                if (Assets.GetObject(path) is GameObject prefab)
                {
                    rocks.Add(prefab);
                }
            }
            int layer = LayerMask.NameToLayer("Rocks");
            Terrain terrain = Terrain.activeTerrain;
            if (rocks.Count == 0 || terrain == null)
            {
                return 0;
            }
            Vector3 size = terrain.terrainData.size;
            var rng = new System.Random(7);
            int placed = 0;
            foreach (Vector4[] course in rivers)
            {
                Vector4 s = course[0];
                float widest = 0f;
                foreach (Vector4 p in course)
                {
                    widest = Math.Max(widest, p.w);
                }
                if (Math.Min(Math.Min(s.x, s.z), Math.Min(size.x - s.x, size.z - s.z)) <= 8f || s.w >= JoinedStart * widest)
                {
                    continue;
                }
                GameObject rock = UnityEngine.Object.Instantiate(rocks[rng.Next(rocks.Count)], parent);
                rock.name = "Spring" + placed;
                var pos = new Vector3(s.x, 0f, s.z);
                pos.y = terrain.SampleHeight(pos) + terrain.transform.position.y;
                rock.transform.position = pos;
                rock.transform.rotation = Quaternion.Euler(0f, (float)rng.NextDouble() * 360f, 0f);
                rock.transform.localScale = Vector3.one * (0.6f + 0.3f * (float)rng.NextDouble());
                rock.SetActive(true);
                if (layer >= 0)
                {
                    foreach (Transform t in rock.GetComponentsInChildren<Transform>(true))
                    {
                        t.gameObject.layer = layer;
                    }
                }
                // the clone's pivot is not its base: sink the mesh so its lowest point is a bit below the
                // lowest ground under it
                Bounds b = default(Bounds);
                bool any = false;
                foreach (Renderer r in rock.GetComponentsInChildren<Renderer>())
                {
                    if (any)
                    {
                        b.Encapsulate(r.bounds);
                    }
                    else
                    {
                        b = r.bounds;
                        any = true;
                    }
                }
                if (any)
                {
                    float ground = float.MaxValue;
                    foreach (Vector3 c in new[] { b.min, b.max, new Vector3(b.min.x, 0f, b.max.z), new Vector3(b.max.x, 0f, b.min.z), b.center })
                    {
                        ground = Math.Min(ground, terrain.SampleHeight(new Vector3(c.x, 0f, c.z)) + terrain.transform.position.y);
                    }
                    rock.transform.position += Vector3.up * (ground - b.min.y - 0.25f * b.size.y);
                }
                placed++;
            }
            return placed;
        }

        private static RamSpline FindTemplate(UnityEngine.SceneManagement.Scene scene)
        {
            foreach (GameObject root in scene.GetRootGameObjects())
            {
                if (root.name != "Small Rivers" && root.name != "BigRivers")
                {
                    continue;
                }
                foreach (RamSpline s in root.GetComponentsInChildren<RamSpline>(true))
                {
                    if (s.controlPoints != null && s.controlPoints.Count >= 2 && s.GetComponent<MeshRenderer>() != null)
                    {
                        return s;
                    }
                }
            }
            return null;
        }

        /// <summary>Every ControlSpacing units plus the last point, lifted to sit on the water surface.</summary>
        private static List<Vector4> Thin(Vector4[] course)
        {
            var points = new List<Vector4>();
            float run = ControlSpacing;
            for (int i = 0; i < course.Length; i++)
            {
                if (i > 0)
                {
                    run += Vector2.Distance(new Vector2(course[i].x, course[i].z), new Vector2(course[i - 1].x, course[i - 1].z));
                }
                if (run >= ControlSpacing || i == course.Length - 1)
                {
                    Vector4 p = course[i];
                    p.y += SurfaceLift;
                    points.Add(p);
                    run = 0f;
                }
            }
            return points;
        }
    }
}
