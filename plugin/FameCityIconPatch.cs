using HarmonyLib;

namespace URAH_HRE
{
    /// <summary>
    /// The political view marks religious centres (papacy, patriarch, holy lands) with the holy-city
    /// castle icon (UIPVFigureSettlement.RefreshSettlementType). This gives the same icon to castles of
    /// realms with at least MinFame fame (realms.csv Fame column; on the HRE map data/important_realms.csv,
    /// four cities per period: 1110 Cologne, Mainz, Regensburg, Milan; 1224 Cologne, Regensburg,
    /// Strasbourg, Milan; 1360 Prague, Cologne, Lübeck, Nuremberg). Vanilla's only realms with that much
    /// fame are its holy cities, which have the icon anyway.
    /// </summary>
    [HarmonyPatch(typeof(UIPVFigureSettlement), nameof(UIPVFigureSettlement.RefreshSettlementType))]
    internal static class FameCityIconPatch
    {
        private const float MinFame = 100f;

        private static void Postfix(UIPVFigureSettlement __instance)
        {
            if (__instance.settlementType != UIPVFigureSettlement.SettlementType.Castle)
            {
                return;
            }
            Logic.Realm realm = __instance.settlement?.logic?.GetRealm();
            if (realm != null && realm.fame >= MinFame)
            {
                __instance.settlementType = UIPVFigureSettlement.SettlementType.Castle_Religion_HQ;
            }
        }
    }
}
