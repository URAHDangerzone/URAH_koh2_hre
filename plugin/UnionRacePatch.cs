using HarmonyLib;
using Logic;

namespace URAH_HRE
{
    /// <summary>
    /// Several union quests can unlock the same kingdom (the HRE map's regional Germany quests). Vanilla
    /// UnionQuest.OnComplete only angers kingdoms that hold the same quest def; this extends the
    /// relations penalty (outcome.rel_change_with_eligible) to every kingdom holding another quest
    /// for the same union, so the rival claimants resent the winner too.
    /// </summary>
    [HarmonyPatch(typeof(Logic.UnionQuest), "OnComplete")]
    internal static class UnionRacePatch
    {
        private static void Postfix(Logic.UnionQuest __instance)
        {
            if (!(__instance.owner is Logic.Kingdom winner) || __instance.def?.field == null)
            {
                return;
            }
            string union = __instance.def.field.GetString("outcome.unlock_union");
            if (string.IsNullOrEmpty(union))
            {
                return;
            }
            Value penalty = __instance.def.field.GetValue("outcome.rel_change_with_eligible");
            foreach (Logic.Kingdom other in __instance.game.kingdoms)
            {
                if (other == null || other == winner || other.IsDefeated() || other.quests == null ||
                    other.quests.Find(__instance.def) != null)
                {
                    continue; // same quest: vanilla already applied the penalty
                }
                for (int i = 0; i < other.quests.Count; i++)
                {
                    Logic.Quest q = other.quests[i];
                    if (q != null && q.def?.field?.GetString("outcome.unlock_union") == union)
                    {
                        winner.AddRelationModifier(other, penalty, null);
                        Plugin.Log.LogInfo($"Union race: {winner.Name} became {union}; rival claimant {other.Name} ({q.def.field.key}) resents it");
                        break;
                    }
                }
            }
        }
    }
}
