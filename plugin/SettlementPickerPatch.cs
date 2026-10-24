using System.Collections.Generic;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace URAH_HRE
{
    /// <summary>
    /// Settlement.CreatePickerCollider makes the click collider a flat fan over the wall corners (or a
    /// circle of the def radius), each point on the ground beneath it. On vanilla's flat town sites that
    /// is the town's footprint. On the HRE terrain the wall corners stand on slopes below the town, so the
    /// fan sags under it and the camera's slanted rays meet it beside the town; walls and houses also rise
    /// well above it and houses stand outside the walls. This replaces the fan with a solid: the convex
    /// hull of the wall outline and the visible town, from the lowest ground under it to the top of the
    /// town's models, so every click on a visible part of the town hits it.
    /// </summary>
    [HarmonyPatch(typeof(global::Settlement), nameof(global::Settlement.CreatePickerCollider))]
    internal static class SettlementPickerPatch
    {
        private const string MeshName = "URAH_Picker";
        private const int CircleVertices = 24;

        private static readonly FieldInfo PickerCollider = AccessTools.Field(typeof(global::Settlement), "picker_collider");
        private static readonly FieldInfo Aabb = AccessTools.Field(typeof(global::Settlement), "aabb");

        private static readonly List<Vector2> points = new List<Vector2>();

        private static void Postfix(global::Settlement __instance)
        {
            var mc = PickerCollider.GetValue(__instance) as MeshCollider;
            if (mc == null || (mc.sharedMesh != null && mc.sharedMesh.name == MeshName))
            {
                return;
            }
            var aabb = (Bounds)Aabb.GetValue(__instance);
            Vector3 center = __instance.GetCenter();
            float bottom = float.MaxValue;
            points.Clear();
            foreach (Vector3 p in __instance.GetSelectionPoints())
            {
                points.Add(new Vector2(p.x, p.z));
                bottom = Mathf.Min(bottom, p.y);
            }
            float radius = Mathf.Max(__instance.CalcRadius(), __instance.GetRadius());
            for (int i = 0; i < CircleVertices; i++)
            {
                float a = i * 2f * Mathf.PI / CircleVertices;
                var p = new Vector2(center.x + Mathf.Cos(a) * radius, center.z + Mathf.Sin(a) * radius);
                points.Add(p);
                bottom = Mathf.Min(bottom, Common.GetHeight(new Vector3(p.x, 0f, p.y)));
            }
            List<Vector2> hull = ConvexHull(points);
            if (hull.Count < 3)
            {
                return;
            }
            bottom -= 0.5f;
            float top = Mathf.Max(aabb.max.y, bottom + 2f);

            Transform t = __instance.transform;
            int n = hull.Count;
            var vertices = new Vector3[2 * n];
            for (int i = 0; i < n; i++)
            {
                vertices[i] = t.InverseTransformPoint(new Vector3(hull[i].x, bottom, hull[i].y));
                vertices[n + i] = t.InverseTransformPoint(new Vector3(hull[i].x, top, hull[i].y));
            }
            var triangles = new List<int>(12 * n);
            for (int i = 0; i < n; i++)
            {
                int j = (i + 1) % n;
                triangles.AddRange(new[] { i, j, n + j, i, n + j, n + i });
                if (i > 0 && j > 0)
                {
                    triangles.AddRange(new[] { 0, j, i, n, n + i, n + j });
                }
            }
            var mesh = new Mesh { name = MeshName, vertices = vertices, triangles = triangles.ToArray() };
            mesh.RecalculateBounds();
            Mesh old = mc.sharedMesh;
            mc.sharedMesh = null;
            mc.convex = true;
            mc.sharedMesh = mesh;
            if (old != null)
            {
                Object.Destroy(old);
            }
        }

        /// <summary>Andrew's monotone chain, counter-clockwise, no collinear points.</summary>
        private static List<Vector2> ConvexHull(List<Vector2> pts)
        {
            pts.Sort((a, b) => a.x != b.x ? a.x.CompareTo(b.x) : a.y.CompareTo(b.y));
            var hull = new List<Vector2>(pts.Count + 1);
            for (int pass = 0; pass < 2; pass++)
            {
                int start = hull.Count;
                for (int k = 0; k < pts.Count; k++)
                {
                    Vector2 p = pts[pass == 0 ? k : pts.Count - 1 - k];
                    while (hull.Count >= start + 2 && Cross(hull[hull.Count - 2], hull[hull.Count - 1], p) <= 0f)
                    {
                        hull.RemoveAt(hull.Count - 1);
                    }
                    hull.Add(p);
                }
                hull.RemoveAt(hull.Count - 1);
            }
            return hull;
        }

        private static float Cross(Vector2 o, Vector2 a, Vector2 b)
        {
            return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
        }
    }
}
