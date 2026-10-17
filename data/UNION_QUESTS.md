# Union quests on the HRE map

Kingdoms get union quests once, at the start of a campaign (`Game.InitQuests`). A kingdom gets a
quest when **every** activate condition holds (`QuestConditionDef.Validate`): the period, its
culture in `culture`, not in `excluded_kingdoms`, its name in `eligable_kingdoms` (if listed), and
one of its realms in `eligable_realms` (if listed). The `UnionQuest` template completes only while
no kingdom named like `unlock_union` exists, so quests for the same union race each other, and a
quest whose union already exists ("Conquer France") means toppling it. Completing renames the
kingdom and takes culture, titles and arms from the union kingdom's `kingdoms.csv` row.

## Tables

- `union_quests.csv`: hand-made quests. A row with a **new key** becomes a `def … : UnionQuest`
  (needs `unlock_union`, `own_realms`, eligible realms or kingdoms); a row with a **vanilla key**
  replaces that quest's lists (and `culture`) where its columns are filled and keeps vanilla's
  texts. Eligible realms = the provinces whose NUTS code in `provinces.csv` starts with one of
  `regions`, plus `eligible_realms`. Texts en / de go to `Texts/languages.csv`
- `union_quests_deleted.csv`: vanilla quests deleted, with the reason
- `union_kingdoms.csv`: landless kingdoms only a union creates (base row in `kingdoms.csv`); their
  names and arms are in `kingdom_arms.csv`
- Every other vanilla quest keeps its lists, without the realms the map lacks
  (`rewrite_union_quests` in `tools/import_hre.py`)

`tools/validate_hre.py` hands the quests out like the game and reports goal realms or union
kingdoms that don't exist, and any kingdom that starts with a quest it can finish with fewer than
4 conquests; it also lists the kingdoms without a quest. The plugin's `QuestReport` logs the real
hand-out at campaign start.

## Quests (2026-10-14)

| Quest | Unlocks | Who gets it | Goal |
|---|---|---|---|
| 4 regional Germany quests | Germany | the princes of each region | 8 realms each |
| `UniteLotharingiaQuest` (vanilla, new lists) | Lotharingia | Low Countries, Lower Rhine, Lorraine (+ French culture) | Bruges, Antwerp, Leuven, Liège, Holland, Luxembourg, Aachen, Nancy |
| `UniteArlesQuest` | Arles | Burgundy, Franche-Comté, Lyon, Dauphiné, Provence, Savoy, Geneva, Valais (also France, Toulouse) | Besançon, Dole, Lyon, Grenoble, Valence, Geneva, Chambéry, Sisteron |
| `UniteFranciaQuest` | Francia | France | Paris, Rheims, Rouen, Angers, Dijon, Nancy, Bruges, Bordeaux |
| `UniteAngevinQuest` | Angevin_Empire | England | London, Rouen, Le Mans, Angers, Tours, Poitiers, Bordeaux, Limoges |
| `UniteStStephenQuest` | Hungary_Croatia | Hungary, Croatia | Buda, Esztergom, Nitra, Sibiu, Osijek, Zagreb, Zadar, Banja Luka, Požarevac |
| `ConquerCroatiaQuest` (vanilla, new lists) | Croatia | Bosnia | Zagreb, Zadar, Banja Luka, Osijek, Koper |
| `UniteTeutonicStateQuest` (1360) | Teutonic_State | Teutonic Order | Königsberg, Marienburg, Danzig, Thorn, Elk, Klaipėda, Kaunas, Grodno, Białystok, Landsberg |
| `UniteItalyQuest` (vanilla, new goal) | Italy | Italian princes | Milan, Genoa, Venice, Bologna, Turin, Massa, Udine, Trento |
| `ConquerLithuaniaQuest` (vanilla, new goal) | Lithuania | Yotvingia | Klaipėda, Kaunas, Grodno, Białystok, Brest, Elk |

France and England race for Normandy, Anjou and Aquitaine; France also holds land that the Arles
quest wants. Kept as vanilla (lists trimmed): `ConquerFranceQuest` (France's neighbours and
vassals), `UnitePolishLithuaniaQuest`, `ConquerPolandQuest`, `ConquerHungaryQuest`.
