# HRE map: early starting periods 867 and 999

Status: **code groundwork done (branch `early-periods`), data not started, not tested in game**. Two
more periods before vanilla's 1110 / 1224 / 1360: **867** (Carolingian, before the Treaty of Meerssen)
and **999** (Ottonian, Otto III and Sylvester II). The data goes into the `C_867` / `O_999` columns of
`periods.csv`, `C` / `O` rows of `period_kingdoms.csv` and the `C` / `O` columns of `units.csv` and
`important_realms.csv` (see [PERIODS.md](PERIODS.md)). Facts marked **(verify)** are from memory and
need a source before they go into the tables; names marked **(invented)** fill gaps where sources have
no ruler (agreed: allowed, noted in the `note` column).

## Engine side

Built:

- Plugin `PeriodSuffixPatch.cs`: `Game.ResolvePeriodSuffix` hard-codes `E` / `M` / `L` and drops other
  rows ("Unknown period suffix"). The patch resolves other suffixes from `Periods.<map>`, read when the
  game loads realms / kingdoms / papacy csv (the csv fields have no DT). Not a `Type.GetType` method
- Suffixes are **one letter** (`LoadPapacyCSV` reads only the key's last character): `C` = `carolingian`
  (867), `O` = `ottonian` (999), in `hre_periods.ALL_PERIODS`. `C` / `E` / `I` as religion suffixes
  (Catholic.E = excommunicated) are a different column, no clash
- Tools switch a period on by its data: active once every province has an owner in its `periods.csv`
  column (`hre_periods.active_periods`); empty or partly filled = off, and the build is byte-identical
  to the three-period one (checked). `period_kingdoms.csv` rows of inactive periods wait
- When active, `import_hre.py` writes `Maps/europe/periods.def` (`Periods { europe { carolingian = "C" } }`)
  and `Defs/hre_periods.def` (options in `CampaignVars` and `SinglePlayerCampaignVars`, texts en / de in
  `Texts/languages.csv`). The options come after 1360 in the list (appended by the def merge; also keeps
  the index of the vanilla options); `mid` stays the default
- Cultures (`culture_rule`): `is_before(per, ...)` instead of `per == "E"`, so the Slavic east of the
  Elbe covers 867 / 999 too; Sorbs in Saxony (`DED`) `Pomeranian` in C / O; `Croatian` for Carantania /
  Pannonia in 867 (AT11, AT13, AT21, AT22) and Slovenia in 867 / 999
- Units (`hre_units.py`): an empty or `-` period cell falls back to the base row in the game, so it can't
  clear inherited LocalUnits. With an early period active every province's LocalUnits move from the base
  row into its period rows (E / M / L effective values unchanged, checked), and the C / O rows keep only
  `UNIT_FROM` units (Vikings, Highlanders, Saracens, Roman infantry from 867; horse archers, Norman
  cavalry from 999). Dry run with 867 = 1110 owners: 228 inherited units dropped, none kept (the Danish
  Vikings are only on vanilla's `.E` rows; add them in `units.csv`)
- `validate_hre.py` checks the active early periods too and wants an own `<kingdom>.C` / `.O` row for
  every owner (the dry run lists England, Champagne, Brittany, Scotland ... without one)

Still open:

- Every **edge kingdom** (England, France, Hungary, Denmark, Venice ...) needs `C` / `O` rows in
  `period_kingdoms.csv`; the papacy needs rows in a mod `papacy.csv` (vanilla's has E / M / L only; the
  importer does not write one yet)
- `union_quest.def` `active_period` lists (vanilla quests name only early / mid / late; the HRE quests
  with an empty `active_period` run in every period); `holy_lands_realm_per_period` entries for
  `carolingian` / `ottonian` (HolySitesPatches falls back to `holy_lands_realm` with a warning)
- `PAGAN` sets for C / O in `hre_periods.py`
- In game: options show up, csv rows load, save / load and multiplayer lobby with a new period
- Settlements are the same in every period: Lübeck, Munich, Berlin, Bern ... exist in 867 too.
  Accepted, vanilla does the same
- Kingdom names do **not** follow the period: `Kingdom.NameKey` is `"tn_" + Name`, no suffix. Only crest
  and colour are per period (`LoadKingdomCoAIndicesAndColors` gets the period suffix). The shown name
  is title set + `tn_<key>`, so `Germany` reads "Kingdom of Germany" in 867 too. Per-period names need
  either new keys (with the vanilla crest / colour copied in `CoA.csv`) or a plugin patch on `NameKey`.
  `Lotharingia` already exists as a landless union kingdom key

## Structure principle (decided)

Like vanilla's Germany: **a kingdom with small crown lands and many duchy-level vassals**, not one big
realm. Vanilla's `Germany` row (crown land + Saxony, Bavaria, Swabia, Carinthia, Meissen, Lorraine,
Luxembourg, Holland, Bohemia, Savoy ... as `VassalOf = Germany`, level Duchy) is the model. The
importer flattens chains, so a sub-king's own vassals become vassals of the top overlord.

- **867**: option A, sub-kingdoms of Louis the German's sons and the great margraves/counts as vassals
  of the Carolingian kings. No splitting into one-province county vassals beyond the marches and the
  few hereditary-looking counts
- **999**: the kingdom of Germany exists (exception to PERIODS.md decision 1, only for 999): Otto III
  with crown lands, dukes, margraves and the Ottonian imperial church as vassals

## 867

Louis the German (East Francia), Lothair II (Lotharingia), Emperor Louis II (Italy, Provence since 863),
Charles the Bald (West Francia). Lothair II's divorce fight with Pope Nicholas I, Viking Great Army in
England (York falls 867), Cyril and Methodius in Moravia (863) and in Rome (867), Photian schism (867).
Nicholas I dies in November 867, Adrian II follows.

### East Francia (key `Germany`?), Louis the German, b. ~806

Crown lands: Rhine-Main Franconia (Frankfurt, Worms) and the Regensburg palace region (verify which
reads better in game: Regensburg is in Bavaria proper). Vassals:

| Vassal | Ruler | Lands | Note |
|---|---|---|---|
| Bavaria (sub-kingdom) | Carloman, b. ~830 | Bavaria proper | 865 division; rebelled 861–863 |
| Carantania (march) | Gundakar (verify) | Carinthia, Styria | under Carloman, flattened |
| Eastern march / Pannonia | Wilhelm II & Engelschalk I? (verify) | Lower Austria, Traungau | Danube march |
| Lower Pannonia | Kocel, son of Pribina | Balaton region, W Hungary | Mosaburg; Methodius stays there 867 |
| Saxony (sub-kingdom) | Louis the Younger, b. ~835 | Franconia east of crown lands, Thuringia, Saxony crown parts | 865 division |
| Duchy of Saxony | Bruno (Liudolfing), Liudolf † 866 | Eastphalia, Harz | ancestors of the Ottonians |
| Thuringian / Sorbian march | Thachulf (verify, † 873) | Thuringia, Saale | "dux Sorabici limitis" |
| Alemannia (sub-kingdom) | Charles the Fat, b. 839 | Swabia, Raetia (Alsace is Lotharingian) | later emperor |
| Raetia Curiensis | bishop of Chur? | Graubünden | optional, ecclesiastical flavour |

Ecclesiastical princes as territories are anachronistic in 867 (no prince-bishoprics yet). Keep at most
Mainz (Liutbert) and Salzburg (Adalwin) as single-province vassals, or none. Decide in CSV phase.

### Lotharingia (key `Lorraine`?), Lothair II, b. ~835

Crown lands: Aachen, Metz, Meuse–Moselle. Vassals:

| Vassal | Ruler | Lands | Note |
|---|---|---|---|
| Frisia | Rorik of Dorestad (Danish Viking) | Frisia, Dorestad | Viking fief, pagan-turned-Christian (verify) |
| Alsace (duchy) | Hugh, son of Lothair II and Waldrada | Alsace | made duke 867 |
| Transjurane Burgundy | Conrad the Younger (verify) | W Switzerland, Jura | after Hubert † 864 |
| Lyon / Vienne | Gerard of Roussillon | Lyonnais, Viennois | Lothair II's since 863 |
| Hainaut / Maasgau | Giselbert? (verify) | Meuse valley | optional |

Cologne and Trier archbishops (Gunther, Theutgaud) were deposed 863: flavour text, not realms.

### Kingdom of Italy (key `Italy`?), Emperor Louis II, b. ~825

Crown lands: Pavia, Lombardy. Vassals: Friuli (Unruoch III, verify), Tuscany (Adalbert I; only the
northern part is in the frame), Provence (Louis II's since 863, governor verify), Spoleto out of frame.
Ravenna: archbishop vs papacy (out of the frame south?). **Venice**: doge Orso I Participazio,
independent, nominally Byzantine.

### West Francia (key `France`), Charles the Bald, b. 823

Crown lands: Paris basin, Soissons, Reims. Vassals: Aquitaine (Louis the Stammerer, king 867; mostly
out of frame), Neustrian march (Hugh the Abbot, after Robert the Strong † 866), Flanders (Baldwin I
Iron Arm), Burgundy (verify: Bernard Plantapilosa / Eudes?). **Brittany**: Salomon, gets the Cotentin by
the Treaty of Compiègne 867; as vassal king or independent (decide).

### East and north

- **Great Moravia**: Rastislav (Christian), vassal **Nitra**: his nephew Svatopluk
- **Bohemia**: Czech tribal dukes, pagan, under Moravian influence. One realm or two (Czechs, Lucko /
  Zličané)? Bořivoj I only from ~870 → (invented) or "Hostivít" (legendary, verify)
- **Polabian Slavs** (pagan): Obodrites (Tabomysl? verify), Wilzi / Veleti, Hevelli, Rani on Rügen,
  Sorbs (Milceni, Daleminzi), Pomeranians. Rulers mostly (invented)
- **Polish lands** (pagan): Polans (legendary Piast line: Siemowit, verify), Vistulans (Kraków),
  Silesians, Masovians. Rulers (invented) where needed
- **Prussians** (pagan): tribal, (invented)
- **Denmark**: Horik II (verify; sources thin), pagan
- **South Sweden**: Götar petty king (invented), pagan
- **Bulgaria** (edge, SE): Boris I, converted 864–866, reaches the Tisza / Banat
- **Croatia** (edge, S): Domagoj (Dalmatian Croatia); Pannonian Croatia (verify)
- **Hungarian plain**: no Magyars yet (895). Bulgarian / Slavic / Avar remnants; split between Bulgaria,
  Moravia and Kocel

### England, Scotland

Wessex (Æthelred I), Mercia (Burgred), East Anglia (Edmund, † 869), **Northumbria**: the Great Heathen
Army takes York (March 867); either a pagan Viking realm (Ivar / Halfdan) or the puppet Ecgberht I.
Proposal: Viking realm, it is the better start. Alba: Constantine I.

### Religion and culture 867

- Catholic: the Frankish kingdoms, Italy, Moravia, Pannonia, England (except York), Bulgaria, Croatia
- Pagan: Denmark, Sweden, York Vikings, Bohemia, all Polabian / Polish / Pomeranian / Prussian lands
- East of Elbe and Saale: Slavic (`Pomeranian` / `Polish` / `Bohemian` / `Moravian` as now);
  Carantania and the Eastern march: Slavic (no Slovene culture; `Croatian` or `SouthSlavic`, decide),
  Bavarian only in the Danube valley west of the Enns
- Lotharingia west of the language border: `French`; Frisia `Frisian`

## 999

Otto III (b. 980) in Rome, renovatio imperii Romanorum, Gerbert as Pope Sylvester II (April 999).
Archbishop Willigis of Mainz and abbess Matilda of Quedlinburg († Feb 999) run Germany. Congress of
Gniezno next year (March 1000), Stephen of Hungary crowned at Christmas 1000. Boleslaus II of Bohemia
† Feb 999. Arduin of Ivrea excommunicated 999 after the killing of the bishop of Vercelli.

### Kingdom of Germany (key `Germany`), Otto III, level Empire

Crown lands: Franconia (Frankfurt, Worms), the Harz (Quedlinburg, Goslar region, Magdeburg area
crown estates), Aachen. Vassals:

| Vassal | Ruler | Lands |
|---|---|---|
| Saxony | Bernhard I Billung | Saxony, Billung march |
| Bavaria | Henry IV (later Emperor Henry II), b. 973 | Bavaria |
| Swabia | Hermann II | Swabia, Alsace |
| Carinthia & March of Verona | Otto of Worms (Salian) | Carinthia, Carniola, Verona, Friuli (minus Aquileia) |
| Austria (Ostarrichi 996) | Henry I (Babenberg) | Lower Austria |
| Upper Lorraine | Theodoric I | Lorraine (Bar) |
| Lower Lorraine | Otto (Carolingian) | Brabant, Meuse |
| Meissen | Ekkehard I | Meissen, Thuringia (pretender 1002) |
| Lusatia / Saxon Eastern march | Gero II (verify) | Lusatia |
| Northern march | Lothair (verify) | Altmark only, rest lost to the Liutizi |
| Holland / West Frisia | Dirk III (child, verify) | Holland |
| Luxembourg | Henry I (verify; Siegfried † 998) | Luxembourg |
| Bohemia | Boleslaus III the Red | Bohemia (tributary since 950, vanilla `Bohemia.E` is a Germany vassal) |
| Kingdom of Italy? | see below | personal union, vassal or crown land (decide) |

Imperial church (Ottonian Reichskirche, fits 999 better than any later period). Candidates:
Mainz (Willigis), Cologne (Heribert, new 999), Trier (Liudolf), Magdeburg (Giselher), Hamburg-Bremen
(Liawizo I), Salzburg (Hartwig), Liège (Notger, prince-bishop), Utrecht (Ansfried), Würzburg (Heinrich I),
Hildesheim (Bernward), Paderborn, Halberstadt, Passau, Chur, Strasbourg, Basel; abbeys Fulda,
Quedlinburg, St. Gallen, Reichenau, Corvey. Not yet: Bamberg (1007), Trent as a county (1004/1027);
Merseburg dissolved 981–1004. Pick ~8–12 so the map stays readable.

### Kingdom of Italy

Otto III is king. Vassals: Tuscany (Hugh the Great), Ivrea (**Arduin**, excommunicated; as a vassal with
bad opinion or independent rebel, decide), Turin (Ulric Manfred II or Manfred I, verify), Montferrat
(Aleramici, verify), Obertenghi (eastern Liguria / Milan region, verify), archbishop of Milan (Arnulf II),
archbishop of Ravenna, patriarch of Aquileia (John IV). **Venice**: Pietro II Orseolo, independent,
Dalmatia expedition 1000.

### Kingdom of Burgundy (Arles), Rudolph III, independent

Vassals: Free County (Otto-William), Provence (sons of William the Liberator, verify), Geneva, Maurienne
(Humbert White-Hands, first Savoyard, verify date), archbishops of Lyon, Vienne, Besançon (optional).

### East and north

- **Poland**: Bolesław I Chrobry; Greater Poland, Silesia, Lesser Poland with Kraków (taken from Bohemia
  ~999, verify), Pomerania, Moravia (~999, verify)
- **Hungary**: Stephen (grand prince, Christian), **Transylvania**: Gyula (independent until 1003),
  **Banat**: Ajtony (until ~1008)
- **Liutizi** (pagan, free since 983), **Obodrites** (half pagan; Mstislav? verify), **Rani** / Rügen
  (pagan), **Prussians** (pagan; Adalbert of Prague killed there 997)
- **Wolin / Jomsborg**: Jomsvikings (legendary, optional Viking flavour in Pomerania)
- **Denmark**: Sweyn Forkbeard, **Sweden**: Olof Skötkonung (baptized ~1008: Pagan in 999)
- **Croatia** (edge): Svetoslav Suronja / Krešimir III (verify)

### West

- **France**: Robert II (weak Capetian, crown Île-de-France). Vassals: Normandy (Richard II), Flanders
  (Baldwin IV), Blois-Champagne (Odo II), Duchy of Burgundy (Henry I), Anjou (Fulk Nerra), Vermandois
  (verify), Brittany (Geoffrey I)
- **England**: Æthelred the Unready, Viking raids. **Scotland**: Kenneth III

### Religion and culture 999

- Catholic everywhere except: Liutizi, Rani, Prussians, Sweden (Pagan); Obodrites mixed (decide)
- Hungary `Hungarian`, Transylvania / Banat `Hungarian` (or Vlach / Slavic parts, verify)
- Austria: `Austrian` / `Bavarian` west, Slavic remnants east (decide); Slavic east of the Elbe as now

## Units (province LocalUnits and kingdom sets)

Vanilla units are mostly 12th–14th century. For 867 and 999 these don't fit:

| Unit | 867 | 999 | Why |
|---|---|---|---|
| `Crossbowman_Special` | no | no (maybe a few: Richer mentions crossbows ~990, verify) | crossbows spread after ~1050–1100 |
| `Pavise_Crossbowman_Special` | no | no | 14th c. |
| `Longbowman_Special`, `Hobelar_Special` | no | no | 13th–14th c. English |
| `Halberdier_Special`, `Flemish_Pikeman_Special` | no | no | 14th c. (Morgarten, Courtrai) |
| `Teutonic_*`, `Templar_*` | no | no | orders from 1119 / 1190 |
| `Man_At_Arms_Special` | no | no (check model) | late-medieval look |
| `Feudal_Knight_Special` | no | maybe (check model) | 999 milites loricati; model may be 12th c. |
| `Norman_Special` | no | yes: Normandy | Normandy founded 911 |
| `Viking_Special` | yes: Denmark, Sweden, Frisia (Rorik), York | yes: Denmark, Sweden, (Wolin) | |
| `Horse_Archer_CE` / `HungarianUnitSet` | no (no Magyars; Bulgarians?) | yes: Hungary, Transylvania, Banat | Magyars arrive 895 |
| `Heavy_Cavalry_Slav` (via `WestSlavicUnitSet`) | Moravia | Poland, Bohemia (drużyna) | |
| `Roman_Infantry_Special` | Venice? | Venice? | Byzantine-aligned; optional |
| `Highlander_Special` | Scotland | Scotland | as vanilla |

Unit sets: the `German` / `Saxon` / `Austrian` / `French` / `English` / `Flemish` / `Dutch` cultures
default to `WesternUnitSet` (Men-at-arms + Feudal Knights instead of the generic heavy swordsmen and
heavy cavalry). For C and O give every kingdom of these cultures `EuropeanUnitSet` in its period row
(`UnitsSet` column) so they recruit the generic `Heavy_Swordman_CE` / `Heavy_Cavalry_CE` (Frankish
scarae / Ottonian heavy horse). In 999 Normandy keeps `NormanUnitSet`.

`units.csv` needs `C` and `O` columns. The `from_kingdom Germany` row (crossbowmen) stays empty for C
and O; the Teutonic commanderies too.

Ideas for later (needs new unit defs reusing vanilla models): "Frankish Scarae" (Heavy_Cavalry_CE model,
better charge), "Saxon Levy", "Magyar Raiders" (Light_Horse_Archer_Steppe model). Siege: counterweight
trebuchets are 12th c.; check whether `Trebuchet` can be blocked per period (probably not without a
patch).

## Union quests

- 867: Germany does not exist. Candidate: "Restore Charlemagne's empire" (East Francia + Lotharingia +
  Italy, maybe West Francia). The four HRE crown quests (Saxon, Bavarian-Austrian, Swabian-Rhenish,
  Bohemian) could stay active: whoever unites the region founds Germany
- 999: Germany exists, so the four crown quests are off. Candidates: Polish crown (Gniezno; Poland +
  Silesia + Pomerania + Lesser Poland), Hungarian crown (Hungary + Transylvania + Banat), "Renovatio
  imperii" (Germany + Italy + Burgundy)

## Decisions taken

1. **Keys**: vanilla keys reused (`Germany` = East Francia 867 / Kingdom of Germany 999, `Lorraine` =
   Lotharingia, `France` = West Francia, `Italy` if vanilla has it), for colours and crests. Names do
   not follow the period (see Engine side): East Francia would read "Kingdom of Germany" in 867 unless
   it gets its own key or a `NameKey` patch (open)
3. **999 Italy**: no separate Italian kingdom. Otto III holds both crowns, and the game has one ruler
   per kingdom, so a vassal "Italy" would need an invented king. Pavia / Lombardy are crown lands of
   `Germany`, the Italian margraves and bishops are direct vassals of `Germany`
4. **Brittany 867** (Salomon, under Charles the Bald) and **Arduin 999** (under Otto III): vassals
5. **Culture of the Carantanian / Pannonian Slavs**: `Croatian` (South Slavic, the closest the game
   has; `Moravian` is West Slavic). 867: Carinthia, Styria, the Slovenian provinces, the Eastern march
   east of the Wienerwald and Kocel's Pannonia. 999: only the Slovenian provinces and Lower Styria,
   the rest `Bavarian` / `Austrian` (Bavarian settlement after 955). Today all of Slovenia is
   `Bavarian` in every period
6. **999 church states**: about 10

## Open decisions

2. 867 ecclesiastical vassals: none, or only Mainz / Salzburg, or the royal abbeys?
7. East Francia / Lotharingia names in 867: accept "Germany" / "Lorraine", new keys, or `NameKey` patch?
