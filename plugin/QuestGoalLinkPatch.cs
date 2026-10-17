using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using HarmonyLib;
using Logic;

namespace URAH_HRE
{
    /// <summary>
    /// The union quest window (UIQuestsInfoWindow.PopulateConditions) lists the goal provinces of
    /// complete_conditions.own_realms as plain text ("tn_" + key), so unlike elsewhere in the game they
    /// can't be hovered or clicked. This rewrites each goal row as "Province (Town)" wrapped in the
    /// game's object link: hover shows the realm tooltip, click selects it, double-click looks at it.
    /// The "[union] must not exist" row links the kingdom of that name while it exists, so a player
    /// whose row stays unchecked can find whom to topple.
    /// </summary>
    [HarmonyPatch(typeof(UIQuestsInfoWindow), "PopulateConditions")]
    internal static class QuestGoalLinkPatch
    {
        private static readonly FieldInfo ConditionList = AccessTools.Field(typeof(UIQuestsInfoWindow), "m_conditionList");
        private static readonly Type RowType = AccessTools.Inner(typeof(UIQuestsInfoWindow), "QuestConditionRow");
        private static readonly MethodInfo SetDescription = AccessTools.Method(RowType, "SetDescription");

        private static void Postfix(UIQuestsInfoWindow __instance, Quest quest)
        {
            DT.Field goals = quest?.def?.field?.FindChild("complete_conditions.own_realms");
            Game game = quest?.game;
            if (goals == null || game == null || !(ConditionList?.GetValue(__instance) is IList rows) ||
                SetDescription == null)
            {
                return;
            }
            // m_conditionList is never cleared: this call's rows are its tail, in window order the
            // no_existing_kingdom row (if any), one row per goal, the religion row (if any)
            List<DT.Field> keys = goals.Children();
            DT.Field conditions = quest.def.field.FindChild("complete_conditions");
            int lead = conditions.FindChild("no_existing_kingdom") != null ? 1 : 0;
            int tail = conditions.FindChild("religion") != null ? 1 : 0;
            int first = rows.Count - tail - keys.Count;
            if (first - lead < 0)
            {
                return;
            }
            string union = quest.def.field.GetString("outcome.unlock_union");
            Logic.Kingdom existing = lead == 1 ? FindKingdom(game, union) : null;
            if (existing != null)
            {
                SetDescription.Invoke(rows[first - 1], new object[] {
                    $"{Defs.LocalizedObjName(existing)} {Defs.Localize("UnionQuest.NoExist")}" });
            }
            for (int i = 0; i < keys.Count; i++)
            {
                Logic.Realm realm = game.GetRealm(keys[i].key);
                if (realm == null)
                {
                    continue;
                }
                string province = Defs.Localize("tn_" + keys[i].key);
                string town = Defs.Localize(realm.GetTownNameKey());
                string label = string.IsNullOrEmpty(town) || town == province ? province : $"{province} ({town})";
                SetDescription.Invoke(rows[first + i], new object[] { $"<link=\"obj:{realm.uid}\">{label}</link>" });
            }
        }

        // the kingdom that blocks the union: same test as QuestConditionDef's no_existing_kingdom
        private static Logic.Kingdom FindKingdom(Game game, string name)
        {
            if (string.IsNullOrEmpty(name) || game.kingdoms == null)
            {
                return null;
            }
            foreach (Logic.Kingdom k in game.kingdoms)
            {
                if (k != null && !k.IsDefeated() && k.ActiveName == name)
                {
                    return k;
                }
            }
            return null;
        }
    }
}
