using System.Collections.Generic;
using System.Reflection;
using HarmonyLib;
using Logic;

namespace URAH_HRE
{
    /// <summary>
    /// Starting periods beyond vanilla's three. Game.ResolvePeriodSuffix maps the suffix of a realms.csv /
    /// kingdoms.csv / papacy.csv key to its period with a hard-coded switch (E early, M mid, L late) and
    /// drops every other row with "Unknown period suffix". This resolves the other suffixes from
    /// Periods.&lt;map&gt; (Maps/&lt;map&gt;/periods.def, e.g. carolingian = "C"), so a mod adds a period with
    /// data only: the suffix there, a start_period option, and the csv rows. Vanilla's three suffixes
    /// keep the game's own switch.
    /// </summary>
    internal static class PeriodSuffixPatch
    {
        /// <summary>suffix -> period key of the map whose csv files are loading (or were last).</summary>
        private static readonly Dictionary<string, string> Extra = new Dictionary<string, string>();

        /// <summary>
        /// ResolvePeriodSuffix is static and the csv fields have no DT (ReadMapsCsv parses them without
        /// one), so the suffix table is read from the game whenever it loads a map's csv files.
        /// Kingdom.ChangeNameAndCulture resolves later, in game, with the table of the loaded map.
        /// </summary>
        [HarmonyPatch]
        private static class LoadCsvPatch
        {
            private static IEnumerable<MethodBase> TargetMethods()
            {
                yield return AccessTools.Method(typeof(Game), nameof(Game.LoadRealmsCSV));
                yield return AccessTools.Method(typeof(Game), nameof(Game.LoadKingdomsCSV));
                yield return AccessTools.Method(typeof(Game), nameof(Game.LoadPapacyCSV));
            }

            private static void Prefix(Game __instance, string map_name)
            {
                Extra.Clear();
                DT.Field table = __instance.dt?.Find("Periods." + map_name);
                if (table?.children == null)
                {
                    return;
                }
                foreach (DT.Field period in table.children)
                {
                    string suffix = period.String();
                    if (string.IsNullOrEmpty(period.key) || string.IsNullOrEmpty(suffix) ||
                        suffix == "E" || suffix == "M" || suffix == "L")
                    {
                        continue;
                    }
                    Extra[suffix] = period.key;
                }
            }
        }

        [HarmonyPatch(typeof(Game), nameof(Game.ResolvePeriodSuffix))]
        private static class ResolvePeriodSuffixPatch
        {
            private static bool Prefix(string suffix, ref string __result)
            {
                if (suffix == null || !Extra.TryGetValue(suffix, out string period))
                {
                    return true;
                }
                __result = period;
                return false;
            }
        }
    }
}
