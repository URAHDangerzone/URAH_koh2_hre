using System.Collections.Generic;
using HarmonyLib;
using Logic;

namespace URAH_HRE
{
    /// <summary>
    /// Holy sites that depend on the period or may be empty (URAH_HRE/Defs/hre_religion.def).
    /// Maps and mods without these settings behave like vanilla.
    /// </summary>
    internal static class HolySitesPatches
    {
        /// <summary>
        /// Catholic.Init reads holy_lands_realm once from the religion def, whatever the period. A
        /// Catholic def with a holy_lands_realm_per_period block (keys of Periods.&lt;map&gt;) replaces it
        /// with the current period's realm. Init also runs when a save is loaded; the period comes from
        /// the save, so the target stays the same.
        /// </summary>
        [HarmonyPatch(typeof(Catholic), nameof(Catholic.Init))]
        private static class CatholicInitPatch
        {
            private static void Postfix(Catholic __instance)
            {
                DT.Field table = __instance.def?.field?.FindChild("holy_lands_realm_per_period");
                if (table == null)
                {
                    return;
                }
                string period = __instance.game.map_period;
                string key = string.IsNullOrEmpty(period) ? null : table.GetString(period);
                if (string.IsNullOrEmpty(key))
                {
                    Plugin.Log.LogWarning($"Crusade target: no entry for period '{period}', keeping {__instance.holy_lands_realm?.name}");
                    return;
                }
                Logic.Realm realm = __instance.game.GetRealm(key);
                if (realm == null)
                {
                    Plugin.Log.LogWarning($"Crusade target: realm '{key}' of period '{period}' not found, keeping {__instance.holy_lands_realm?.name}");
                    return;
                }
                __instance.holy_lands_realm = realm;
                Plugin.Log.LogInfo($"Crusade target for period '{period}': {key}");
            }
        }

        /// <summary>Sunni.Init leaves holy_lands_realms null for an empty list, but callers use it unchecked.</summary>
        [HarmonyPatch(typeof(Sunni), nameof(Sunni.Init))]
        private static class SunniInitPatch
        {
            private static void Postfix(Sunni __instance)
            {
                if (__instance.holy_lands_realms == null)
                {
                    __instance.holy_lands_realms = new List<Logic.Realm>();
                }
            }
        }

        /// <summary>
        /// Realm.IsShiaHolyLand dereferences Shia.holy_lands_realm unchecked (political view figures,
        /// battles), which an empty def value leaves null.
        /// </summary>
        [HarmonyPatch(typeof(Logic.Realm), nameof(Logic.Realm.IsShiaHolyLand))]
        private static class IsShiaHolyLandPatch
        {
            private static bool Prefix(Logic.Realm __instance, ref bool __result)
            {
                if (__instance.game?.religions?.shia?.holy_lands_realm != null)
                {
                    return true;
                }
                __result = false;
                return false;
            }
        }
    }
}
