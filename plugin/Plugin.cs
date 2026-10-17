using System;
using BepInEx;
using BepInEx.Logging;
using HarmonyLib;
using Logic;
using URAH;
using URAH_MapKit;
using UnityEngine;

namespace URAH_HRE
{
    /// <summary>
    /// The HRE map's runtime part: whole-map terrain, rivers, mountains, trees and pathfinding from the
    /// mod's map files, plus the map's gameplay patches (holy sites, fame icons, union races, quest links).
    /// Realm map, paper map and borders come from URAH_MapKit.
    /// </summary>
    [BepInPlugin(Guid, Name, Version)]
    [BepInDependency(URAH_MapKit.Plugin.Guid)]
    public class Plugin : BaseUnityPlugin
    {
        public const string Guid = "urah.koh2.hre";
        public const string Name = "URAH_HRE";
        public const string Version = "0.26.0";

        internal static ManualLogSource Log;

        /// <summary>Whole-map terrain replacement of the campaign currently loaded, if an active mod provides one.</summary>
        internal static MapTerrain FullTerrain;

        // the Terrain component, not its TerrainData: a second campaign reloads the scene (vanilla
        // decorations active again) but Unity keeps the same TerrainData object
        private Terrain patchedTerrain;
        private ModLink link;

        private void Awake()
        {
            Log = Logger;
            // patches only while the URAH_HRE mod is active
            link = new ModLink(Guid, typeof(Plugin).Assembly, Log);
            link.Changed += active =>
            {
                if (!active)
                {
                    FullTerrain = null; // set by the LoadPFData patch, which is gone now
                }
            };
            Log.LogInfo($"{Name} {Version} loaded");
        }

        private void Update()
        {
            if (FullTerrain == null)
            {
                return;
            }
            Terrain terrain = CampaignTerrain.Find();
            if (terrain != null && terrain != patchedTerrain)
            {
                patchedTerrain = terrain;
                try
                {
                    FullTerrain.Apply(terrain);
                }
                catch (Exception e)
                {
                    Log.LogError($"Applying terrain failed: {e}");
                }
            }
        }

        /// <summary>
        /// Runs right after the campaign pathfinding data is loaded and before settlements stamp
        /// their town bits into it, so the new terrain is part of the map from the start.
        /// </summary>
        [HarmonyPatch(typeof(Game), nameof(Game.LoadPFData), typeof(string))]
        private static class LoadPFDataPatch
        {
            private static void Postfix(Game __instance, string map_name)
            {
                FullTerrain = null;
                if (__instance.type != "world_view")
                {
                    return;
                }
                try
                {
                    string terrainPath = ModManager.GetModdedAssetPath(Game.maps_path + map_name + "/" + MapTerrain.FileName);
                    if (terrainPath == null)
                    {
                        Log.LogInfo($"No map terrain for map '{map_name}' in active mods");
                        return;
                    }
                    FullTerrain = MapTerrain.Load(terrainPath);
                    Log.LogInfo($"Loaded map terrain {terrainPath}");
                    string riversPath = ModManager.GetModdedAssetPath(Game.maps_path + map_name + "/" + RiverBuilder.FileName);
                    if (riversPath != null)
                    {
                        FullTerrain.Rivers = RiverBuilder.Load(riversPath);
                        Log.LogInfo($"Loaded {FullTerrain.Rivers.Count} river courses from {riversPath}");
                    }
                    string mountainsPath = ModManager.GetModdedAssetPath(Game.maps_path + map_name + "/" + MountainBuilder.FileName);
                    if (mountainsPath != null)
                    {
                        FullTerrain.Mountains = MountainBuilder.Load(mountainsPath);
                        Log.LogInfo($"Loaded {FullTerrain.Mountains.Count} mountains from {mountainsPath}");
                    }
                    string treesPath = ModManager.GetModdedAssetPath(Game.maps_path + map_name + "/" + MapTerrain.TreesFileName);
                    if (treesPath != null)
                    {
                        FullTerrain.LoadTrees(treesPath);
                        Log.LogInfo($"Loaded {FullTerrain.Trees.Length} trees from {treesPath}");
                    }
                    FullPathfinding.Apply(__instance.path_finding, FullTerrain,
                        ModManager.GetModdedAssetPath(Game.maps_path + map_name + "/RoadsTrapezoidData.bin"));
                }
                catch (Exception e)
                {
                    Log.LogError($"Map terrain failed: {e}");
                }
            }
        }
    }
}
