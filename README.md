# URAH HRE map

A mod for **Knights of Honor II: Sovereign** that replaces the campaign map with the Holy Roman Empire
at about 2.6x the detail: 302 provinces drawn from historical and modern administrative borders,
terrain, rivers and mountains from real elevation data, and three historical starting periods
(1110 / 1224 / 1360) with their kingdoms, rulers, vassals, union quests and holy sites.

It is a data mod (`URAH_HRE/`) plus a BepInEx 5 plugin (`plugin/`, URAH_HRE) that builds the
terrain, rivers, mountains, trees and pathfinding at runtime. It needs the
[URAH_MapKit](../URAH_koh2_mapkit) plugin, which makes the realm map and the political view moddable.

## Playing

Every player needs BepInEx 5, the plugins URAH_MapKit and URAH_HRE in `BepInEx/plugins/`, and the
built mod folder `URAH_HRE/` (with its generated `Maps/` and `Texts/`) in the game's Mods folder,
then activates **URAH HRE map** in the game's Mods menu. Release zips carry everything with
install steps. In multiplayer everyone needs exactly the same mod files: the game compares checksums.

## Releases

Built and signed releases are in [URAH_koh2_releases](https://github.com/URAHDangerzone/URAH_koh2_releases/releases): [`URAH_HRE-v<version>`](https://github.com/URAHDangerzone/URAH_koh2_releases/releases?q=URAH_HRE&expanded=true) carries
`URAH_HRE-plugins.zip`, the signed `plugins/` folder the mod ships for URAH_Loader. Players get the mod
itself from the Steam Workshop and need BepInEx 5 with URAH_Loader ([`URAH_Starter`](https://github.com/URAHDangerzone/URAH_koh2_releases/releases?q=URAH_Starter&expanded=true)).
Every release is signed with the URAH release key; [URAH_koh2_releases](https://github.com/URAHDangerzone/URAH_koh2_releases) explains how to check it.

## Building

The mod's map files are generated, not committed. `build.sh` regenerates them into `URAH_HRE/`,
copies the mod into the game's Mods folder and builds and deploys the plugin. It is yours to keep:
copy `build.sh.example` to `build.sh` (ignored by git) and set the paths of your machine at its top,
or pass them as environment variables. Inputs:

| Input | Where | |
|---|---|---|
| the game's data as shipped (`Maps/europe`, `Defs`, `Texts`) | `$KOH2_VANILLA` | extract from your own install |
| `BigMap_WV.png`, the political view's paper map | `$BIGMAP_WV` | `python external/URAH_shared/python/extract_bundle_assets.py "<game>/AssetBundles/ui" out BigMap_WV` |
| boundary, place and river data | `data/geo/` | downloads listed in `data/geo/README.txt` |
| the paper map's font | `data/fonts/` | download in `data/fonts/README.txt` |
| elevation | `data/dem/hre_etopo.nc` | committed (ETOPO1, public domain) |
| python 3 with numpy, scipy, Pillow, UnityPy | `$PYTHON` | |
| .NET SDK (`global.json`), the game with BepInEx 5 installed | `$GAME_DIR` | the plugin compiles against your install |

```sh
git clone --recursive <this repo>      # external/URAH_shared is a submodule
cp build.sh.example build.sh           # then edit the paths at its top
./build.sh
```

Builds are reproducible: the same commit gives the same map files, and with the SDK of `global.json`
the same plugin DLL bytes, so a release can be checked by rebuilding it and comparing checksums.

Plugin only: `cd plugin && dotnet build -c Release -p:Deploy=true` (builds against the installed
URAH_MapKit in `BepInEx/plugins/URAH_MapKit/`).

## Layout

- `URAH_HRE/`: the mod folder (defs, mod name, `bepinex_plugins.txt` naming the plugins it enables)
- `plugin/`: the URAH_HRE BepInEx plugin
- `tools/`: the generators `build.sh` runs (`make_hre_terrain.py`, `import_hre.py`, ...)
- `data/`: hand-made sources: the province drawing (`provinces.png`, `provinces.csv`), starting periods
  ([data/PERIODS.md](data/PERIODS.md)), union quests ([data/UNION_QUESTS.md](data/UNION_QUESTS.md)),
  units, arms, rivers, passes

## Credits

Elevation: ETOPO1 (NOAA NCEI, public domain). Boundaries: Eurostat GISCO NUTS 2021
(© EuroGeographics for the administrative boundaries), Natural Earth (public domain). Town names:
GeoNames (CC BY 4.0, geonames.org). Font: Pirata One (SIL Open Font License 1.1).
Knights of Honor II: Sovereign is a game by Black Sea Games / THQ Nordic. The built mod contains
edited game art: the coat of arms atlases (vanilla's with the HRE arms painted over, charges cut from
vanilla crests) and the paper map tiles (vanilla's paper, sea and mountain glyphs). That art stays
© Black Sea Games / THQ Nordic and is only for use with the game.

## License

The code and the hand-made data of this repository are under the MIT License (`LICENSE`). Data
derived from third-party sources keeps their terms: province borders and NUTS codes in `data/`
from Eurostat GISCO (© EuroGeographics for the administrative boundaries), town names from GeoNames
(CC BY 4.0). The game art the build edits is not covered by the MIT License.
