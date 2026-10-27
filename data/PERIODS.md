# HRE map: starting periods

Who owns each of the 302 provinces in the three starting periods, which kingdoms exist, who rules
them and under whom. Hand-researched tables, read by `tools/import_hre.py` (through
`tools/hre_periods.py`); `tools/paint_hre_coa.py` paints the new kingdoms' arms.
The early periods 867 and 999 (columns `C_867` / `O_999`, off until filled) are planned in
[PERIODS_EARLY.md](PERIODS_EARLY.md).

- `periods.csv`: one row per province (`color` and `key` as in `provinces.csv` and the generated
  realms) with `E_1110`, `M_1224` and `L_1360` (owner kingdom keys), a `suggested_town` where the
  biggest modern town is a poor medieval main town (Worms → Mainz, Conselve → Padua, Traunreut →
  Burghausen; the castle moves there if GeoNames lists it inside the province), and a short note
- `period_kingdoms.csv`: one row per kingdom and period with display name, `govern`, ruler
  (`Name.Number[.Age]`, vanilla notation), `vassal_of`, capital province, culture, `titles`
  (a `<titles>Nobility` def, empty = the culture's default), `level` (Duchy / Kingdom / Empire,
  `.H` hides the type), `king_class`, province count and notes. Edge kingdoms used unchanged from
  vanilla (England, France, Hungary, Denmark, Venice ...) are not listed
- `kingdom_arms.csv`: English name and adjective, German name and arms (layers, see
  `tools/paint_hre_coa.py`; `same:<key>` shares that kingdom's crest) of the kingdoms without a
  vanilla crest or name

What the importer does with them:

- `realms.csv`: owner rows for every period. Cultures of HRE core provinces follow region and period
  (`culture_rule`: Bavarian / Austrian / Saxon / German / Swiss / Bohemian / Moravian / Flemish /
  Dutch / Frisian / French / Occitan / Italian / Polish / Pomeranian; Slavic `Pomeranian` east of
  the Elbe in 1110 and in Mecklenburg until 1224). Provinces of pagan kingdoms get religion Pagan,
  Pagan provinces under Christian owners become Catholic
- `kingdoms.csv`: vanilla rows edited (capital, overlord, title set and level per period; vanilla
  rulers and families kept except Bavaria 1224), new kingdoms added with base and
  period rows. Vassal chains are flattened to the top overlord (the game does not expect vassals of
  vassals), an overlord without land in the period is dropped, every capital is a province the
  kingdom holds in that period
- `CoA.csv`: new kingdoms get atlas tiles freed by deleted realms and a map colour as far as
  possible from all neighbouring kingdoms of all three periods (palette index distance, like the
  game's own pick)
- Texts: kingdom names (`tn_<key>`, English forms with adjectives, German names), ruler names the
  game does not know (`<lang>_hre_character_names.csv`), German texts of the title sets in
  `URAH_HRE/Defs/hre_nobility.def` (Prince-Bishopric, Electorate, Landgraviate, Burgraviate,
  Principality, Prince-Abbey, Patriarchate, Lordship, Free City, Commune, League, Dauphiné)
- Units (`units.csv`, `tools/hre_units.py`): provinces keep vanilla's `LocalUnits` (own key or
  donor). Units vanilla gave the whole of a kingdom the HRE splits up move into the provinces it held
  (Germany's crossbowmen, all periods); Teutonic Knights at four commanderies of the Order in 1224
  and 1360 (Koblenz, Mergentheim, Nuremberg, Bolzano); Saxony 1110 gets the Saxon `WesternUnitSet`
  instead of vanilla's `EuropeanUnitSet`. A province whose key is a kingdom's key gives its units to
  that whole kingdom (the game's rule): the importer lists those
- `map.def`: the new-campaign screen starts on Munich (Bavarian in all three periods)

## Dates: keep vanilla's 1110 / 1224 / 1360

The game labels the periods "1110 AD", "1224 AD" and "1360 AD" (`CampaignVars.start_period`).
Vanilla's German rulers match those years (Heinrich V, Lothar of Saxony, Welf II / Friedrich II,
Ottokar I, Leopold VI / Rudolf IV, Karl IV of Bohemia). Each year is also a good HRE starting point:

| Period | Situation |
|---|---|
| **1110**, late Salian | Henry V goes to Rome to be crowned (1111). Investiture Controversy, before the Concordat of Worms (1122). Stem duchies still stand (Saxony under Lothar, Welf Bavaria, Staufer Swabia, Eppenstein Carinthia). Matilda of Canossa holds Tuscany and Emilia. East of the Elbe live pagan Wends (Liutizi, Hevelli, Rani on Rügen), with a Christian Obodrite prince at Old Lübeck. Adolf I of Schauenburg becomes count of Holstein, and Otto of Bamberg is about to convert Pomerania. |
| **1224**, late Staufer | Frederick II rules from Sicily, with his son Henry (VII) and regent Engelbert of Cologne in Germany. Valdemar II of Denmark is a prisoner of the count of Schwerin, and Danish rule over Holstein, Lübeck and Pomerania collapses (Bornhöved 1227). The Lombard League renews itself against the emperor (1226). Andechs-Merania is at its peak. Poland is split among Piast dukes, and the Teutonic Order is invited in 1226. |
| **1360**, after the Golden Bull | Emperor Charles IV rules from Prague and the seven electors are fixed. Habsburg Austria (Rudolf IV) is large, the Wittelsbachs are split (Upper and Lower Bavaria, the Palatinate, Brandenburg, Holland-Hainaut) and the Wettins hold Meissen and Thuringia. The Swiss Confederacy reaches 8 cantons. The Visconti and della Scala hold Lombardy, the Pope sits in Avignon, and Albornoz takes Bologna. The Hanse heads into war with Valdemar IV (1361). |

Alternatives considered: 1152 / 1273 / 1356 or 1438 would fit the HRE even better. They would mean
new rulers for every vanilla kingdom and new UI texts, and would break the match with vanilla's
edge kingdoms. Not worth it.

## Kingdoms per period

| | E 1110 | M 1224 | L 1360 |
|---|---|---|---|
| Kingdoms with land (whole frame) | 92 | 97 | 101 |
| of them with one province | 34 | 46 | 49 |
| Owners different from the earlier donor import | 158 | 174 | 167 |

Largest HRE powers (no kingdom of Germany, see Decisions taken):
**E** Bavaria 18, Swabia 11, Bohemia 9, Carinthia 8, Saxony 8, Tuscany 7. **M** Swabia 14 (the
Staufer), Austria 13, Bavaria 12, Lombard League 12, Bohemia 9, Silesia 8. **L** Austria 21,
Brandenburg 11, Milan 11, Meissen 10 (with Thuringia), Bohemia 9 (Charles IV), Bavaria 8.

New kingdom keys (not in vanilla): about 90. These are secular princes (Styria, Merania, Görz,
Zähringen, Habsburg, Kyburg, Baden, Württemberg, Palatinate, Lower Bavaria, Zollern, Hesse,
Thuringia, Nassau, Anhalt, Nordmark, Brunswick, Lüneburg, Holstein, Oldenburg, Lippe, Cleves, Berg,
Mark, Jülich, Guelders, Brabant, Hainaut, Namur, Bar, Geneva, Dauphiné, Valentinois, Piedmont,
Montferrat, Saluzzo, Celje, Montfort, the Silesian and Polish duchies, pagan Liutizi / Hevelli /
Rugia ...), **ecclesiastical states** (Cologne, Mainz, Trier, Magdeburg, Bremen, Salzburg,
Würzburg, Bamberg, Münster, Paderborn, Osnabrück, Hildesheim, Utrecht, Liège, Passau, Trent,
Aquileia, Chur, Sion, Basel, Lyon, Nysa, the abbeys of Fulda and St. Gallen) and **cities /
leagues** (Bern, Metz, Strasbourg, Lübeck, Asti, Florence, Frisian Freedom, Lombard League,
Milan, Verona, Padua, Mantua, Ferrara).

## Main fixes against the earlier donor import

- East of the Elbe in 1110: pagan Liutizi, Hevelli and Rani plus the Obodrites instead of
  "Germany", "Meissen" and "Pomerania". Brandenburg exists only from M. Lubusz is Polish (E) and
  Silesian (M), not Pomeranian
- Silesia is Polish in E. In M it is Henry the Bearded's Silesia and Opole. In L it is split into
  fiefs of the Bohemian crown (Legnica, Głogów, Opole, Cieszyn, Opava, Nysa), Wrocław held directly
  by the crown, and independent Świdnica
- Westphalia and the Rhineland were "Lorraine" (E) and "Saxony" (M). They are now Cologne, Berg,
  Mark, Jülich, Cleves, Münster, Paderborn, Osnabrück and Lippe
- Burgenland is Hungarian in every period. Bohemian Ústí and Karlovy Vary are no longer
  "Meissen". The Banat, Bačka and Vidin are no longer "Pechenegs" / "Wallachia". The Scottish
  Borders are Scottish (E, M). Poitou and Limousin follow the Treaty of Brétigny (English in L)
- Italy: Matilda's Tuscany (E), Lombard League against imperial Pavia / Cremona / Parma / Modena
  (M), the Visconti, della Scala, Carrara, Gonzaga, Este and Albornoz's papal Bologna (L)
- Tyrol: Bavarian Inn valley (E), Tyrol and Andechs-Merania (M), Ludwig V of Upper Bavaria as
  count of Tyrol by marriage (L). Vanilla's L row already has Bavaria's Louis V
- Swiss lands: Zähringen (E), Kyburg / Habsburg / Bern (M), Confederacy of 8 plus Habsburg Aargau and
  Fribourg (L), instead of "Germany" everywhere

## Decisions taken

1. **No kingdom of Germany**. The emperor's
   crown lands went to their neighbours in each period, and the princes are vassals of regional
   overlords (1110 the stem duchies, 1224 the territorial princes, 1360 the electors of the Golden
   Bull) or independent. In 1360 Charles IV is king of Bohemia (vanilla's `Bohemia.L` row, level
   Empire) with Upper Lusatia; Moravia and the Silesian dukes (Legnica with Wrocław) are his
   vassals. Germany comes back only through four regional union quests (Saxon, Bavarian-Austrian,
   Swabian-Rhenish, Bohemian crown) that race for the crown
2. **Ecclesiastical states** have `GovernType` Order, but the game never reads `GovernType`: they
   play as dynasties like everyone else. Their title sets (Archbishop, Prince-Bishop, Prince-Abbot,
   Patriarch, Elector) and the Cleric king class carry the flavour
3. **Nested vassals** are flattened by the importer: Holstein and Mecklenburg (E) under Saxony,
   Piedmont under Savoy and Cilli under Austria (L). Masovia under Poland, Ferrara under the
   Papal States, Moravia under Bohemia (M), Rügen / Mecklenburg / Pomerania under Denmark (M)
4. **Lombard League (M)** is one `Confederacy` kingdom (hidden type: "Lombard League") instead of a
   dozen one-city republics
5. **Scania 1360**: Swedish until Valdemar IV retook it in summer 1360. Kept Swedish (vanilla)
6. **Vanilla keys parked on far-away provinces**: only Latium (on Romagna), Kyiv and Lithuania are
   left; they keep their key and get the owner of the place they sit on. Papal States keep Romagna
   in every period because the papacy mechanic needs a capital. The holy sites moved to HRE places
   and the other parked keys became ordinary provinces (`URAH_HRE/Defs/hre_religion.def`)

## Open

- **One-province kingdoms** (34 / 46 / 49). Candidates to fold if the map feels too fragmented:
  Sion, Chur, Basel, St. Gallen, Fulda, Osnabrück, Paderborn, Hildesheim, Asti, Valentinois, Lyon
- In-game test: kingdom choice per period, titles, vassals, crests, map colours, ruler names
- Other languages than English and German show English kingdom names and title sets

## Confidence

Owners are good to the province level. The province is the unit, so mixed provinces carry the
owner of their main town, with the rest in the note. Rulers of major kingdoms are well
established. Low confidence (minor or uncertain rulers, `-` where unknown): St. Gallen E, Chur E/M,
Sion E, Metz E, Trent E/L, Fulda E, Nassau E, Görz E, Moravia E (Oldřich of Brno, one of three
appanage princes), Pomerelia E, Kyburg M, Geneva M, Oldenburg M, Lippe L, Anhalt L, Montfort L,
Celje L. Vanilla's `Brandenburg.L` ruler "Otto.7" does not match history (Louis the Roman / Otto V).
Compiled from general historical knowledge. No sources were checked online.

## Rebuild

After editing the tables (or `provinces.png`), run `./build.sh` (inputs in the [README](../README.md#building)).
It regenerates everything from the importer on (castles may move), deploys the mod and builds the
plugin. The steps, with `V` the game's vanilla data folder and `B` the extracted `BigMap_WV.png`:

```sh
M=URAH_HRE/Maps/europe
python tools/make_hre_terrain.py data/dem/hre_etopo.nc $M/terrain.pmt   # heights, rivers, massifs
python tools/import_hre.py $V/Maps/europe data URAH_HRE
python tools/make_hre_mountains.py $M && python tools/make_hre_trees.py $M
python tools/make_hre_roads.py $M && python tools/make_hre_terrain_types.py $M
python tools/paint_hre_coa.py $V/Maps/europe data URAH_HRE --preview out/kingdom_arms.png
python tools/paint_hre_bigmap.py $B $M
```
