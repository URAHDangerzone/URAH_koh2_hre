"""Match vanilla realm keys to the HRE map's provinces and seas.

Usage: python tools/match_vanilla_realms.py <vanilla_map_dir> <hre_dir>

Writes <hre_dir>/vanilla_keys.csv (edit it to change an assignment), one row per vanilla realm:
    key, kind (land / sea), action (province / required / sea / delete), lat, lon, source, province color

* province  the vanilla castle lies in the frame: the province under it takes over the vanilla key,
            so kingdoms.csv, CoA, union quests and the period history keep working
* required  outside the frame but named in the game rules (Defs/: papacy, holy sites, fame, AI) and
            not in OVERRIDDEN; parked on the nearest province without a key so those rules still
            find a realm
* sea       vanilla sea realm with at least 4% of its area on the frame's sea; the mean position of
            that part seeds the new sea partition
* delete    everything else

Vanilla castle positions are turned into lat/lon by name (Natural Earth populated places, name
variants), a short table of known places, or a cubic fit of 48 cities (median error ~40 km).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import urah_paths  # noqa: F401,E402  (puts URAH_shared on sys.path)
import koh2map  # noqa: E402
import propose_provinces as pp  # noqa: E402

GEO = Path(__file__).resolve().parent.parent / "data" / "geo"
# seas the share rule misses: only a sliver of them is in the frame, but it is their own water body
EXTRA_SEAS = {"Gulf_Of_Liguria": (43.95, 8.6)}
SEA_SHARE = 0.04  # share of a vanilla sea realm's area that must map onto the frame's sea
# keys named in Defs/ that need no realm on the HRE map: holy sites URAH_HRE/Defs/hre_religion.def
# moves elsewhere, fame.def (read nowhere), kingdom names (AI.def), achievement and opinion rules that
# only compare names. Latium (papacy), Kyiv and Lithuania stay
OVERRIDDEN = {"Palestine", "Constantinopolis", "Al_Maqqah", "Yathrib", "Baghdad", "Cairo", "Alexandria", "Nicaea",
              "Karvuna", "Marrakesh", "Salamanca", "Andalucia", "Leinster", "Muscovy", "Novgorod", "Sicily"}

# vanilla castle (x, z) -> lat, lon fitted on 48 cities: cubic polynomial in (x / 1000, z / 1000)
FIT_LAT = [19.86709, 7.4581, 13.5727, -3.19374, -1.33078, 2.9693, 0.3496, 0.59147, -0.32191, -0.77595]
FIT_LON = [-25.96959, 22.08228, 2.85258, 1.37836, -6.77397, 2.5464, -0.53441, 1.21109, 0.70935, -0.90158]

# castles Natural Earth does not know by their vanilla name
KNOWN = {
    "Plotsk": (52.55, 19.70), "Konigsberg": (54.71, 20.51), "Volodymyr": (50.85, 24.32), "Wismar": (53.89, 11.47),
    "Berestye": (52.10, 23.69), "Halych": (49.12, 24.72), "Malbork": (54.04, 19.03), "Torun": (53.01, 18.60),
    "Severin": (44.63, 22.66), "Gradets": (45.81, 15.97), "Hedeby": (54.49, 9.57), "Eztergom": (47.79, 18.74),
    "Clermont": (45.78, 3.08), "Canterbury": (51.28, 1.08), "Warsawa": (52.23, 21.01), "Meissen": (51.16, 13.47),
    "Buda": (47.50, 19.04), "Sieradz": (51.60, 18.73), "Konstanz": (47.66, 9.18), "Winchester": (51.06, -1.31),
    "Rheims": (49.26, 4.03), "Bacs": (45.39, 19.23), "Nitra": (48.31, 18.09), "Warwick": (52.28, -1.58),
    "Visoko": (43.99, 18.18), "Sczieczin": (53.43, 14.55), "Worms": (49.63, 8.36),
}


def fit(x, z, coef):
    x, z = x / 1000, z / 1000
    return float(np.dot(coef, [1, x, z, x * x, x * z, z * z, x ** 3, x * x * z, x * z * z, z ** 3]))


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.replace("_", " ").lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def load_places():
    keys = ("NAME", "NAMEASCII", "NAMEALT", "NAME_EN", "NAME_DE", "NAME_FR", "NAME_IT", "NAME_PL", "NAME_HU",
            "NAME_NL", "NAMEPAR")
    out = []
    for f in json.load(open(GEO / "ne_places.geojson", encoding="utf-8"))["features"]:
        p = f["properties"]
        names = set()
        for k in keys:
            if p.get(k):
                names |= {norm(n) for n in str(p[k]).split("|")}
        out.append((names, p["LATITUDE"], p["LONGITUDE"]))
    return out


def castle_latlon(name, x, z, places):
    lat, lon = fit(x, z, FIT_LAT), fit(x, z, FIT_LON)
    if name in KNOWN:
        return (*KNOWN[name], "known")
    best = None
    for names, pl, po in places:
        if norm(name) in names:
            d = np.hypot((pl - lat) * 111.2, (po - lon) * 111.32 * np.cos(np.radians(lat)))
            if d < 250 and (best is None or d < best[0]):
                best = (d, pl, po)
    if best:
        return best[1], best[2], "name"
    return lat, lon, "fit"


def required_keys(sovereign: Path, keys) -> set[str]:
    """Vanilla realm keys named in the game rules (Defs/), not counting character name lists and the
    keys in OVERRIDDEN."""
    pat = re.compile(r"\b(" + "|".join(map(re.escape, keys)) + r")\b")
    found = set()
    for p in list((sovereign / "Defs").rglob("*.def")) + list((sovereign / "PGDefs").rglob("*.def")):
        if p.name.startswith("names."):
            continue
        found |= set(pat.findall(p.read_text(encoding="utf-8-sig", errors="replace")))
    return found - OVERRIDDEN


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("vanilla", type=Path, help="vanilla Maps/europe")
    ap.add_argument("hre", type=Path, help="folder with provinces.png / provinces.csv")
    args = ap.parse_args()

    realms = koh2map.realm_ids(args.vanilla)
    by_name = {r.name: r for r in realms.values()}
    rmap = koh2map.read_realms_bin(args.vanilla / "realms.bin")
    text = (args.vanilla / "settlements.def").read_text(encoding="utf-8-sig")
    castles = {}
    for name, x, z in re.findall(r'def \d+ : Castle\s*\{\s*name = "([^"]+)"\s*position = ([\d.\-]+), ([\d.\-]+)', text):
        x, z = float(x), float(z)
        rid = int(rmap[int(x * 2048 / 3750), int(z * 2048 / 2625)])
        castles[realms[rid].name] = (name, x, z)

    img = np.array(Image.open(args.hre / "provinces.png").convert("RGB")).astype(np.int32)
    color = img[..., 0] << 16 | img[..., 1] << 8 | img[..., 2]
    rows = {int(r["color"][1:], 16): r for r in csv.DictReader(open(args.hre / "provinces.csv", encoding="utf-8"))}
    sea = color == 0
    land_y, land_x = np.nonzero(~sea)

    places = load_places()
    out = []
    taken = {}
    for key, (cname, x, z) in sorted(castles.items()):
        lat, lon, src = castle_latlon(cname, x, z, places)
        px, py = pp.lonlat_to_px(lon, lat)
        px, py = int(round(float(px))), int(round(float(py)))
        row = dict(key=key, kind="land", action="delete", castle=cname, lat=round(lat, 3), lon=round(lon, 3),
                   source=src, color="")
        if 0 <= px < pp.W and 0 <= py < pp.H:
            if sea[py, px]:  # castle just off the new coastline: nearest land
                i = np.argmin((land_x - px) ** 2 + (land_y - py) ** 2)
                px, py = int(land_x[i]), int(land_y[i])
            c = int(color[py, px])
            row.update(action="province", color="#%06x" % c, _d=0.0, _px=(px, py))
        out.append(row)

    # one vanilla key per province: the castle nearest the province centre wins, the others move to
    # the nearest province without a key
    centres = {}
    for c in rows:
        ys, xs = np.nonzero(color == c)
        centres[c] = (xs.mean(), ys.mean())
    claims = {}
    for r in out:
        if r["action"] == "province":
            c = int(r["color"][1:], 16)
            d = np.hypot(r["_px"][0] - centres[c][0], r["_px"][1] - centres[c][1])
            claims.setdefault(c, []).append((d, r))
    for c, lst in claims.items():
        lst.sort(key=lambda t: t[0])
        taken[c] = lst[0][1]["key"]
    for c, lst in claims.items():
        for d, r in lst[1:]:
            free = [k for k in rows if k not in taken]
            best = min(free, key=lambda k: np.hypot(centres[k][0] - r["_px"][0], centres[k][1] - r["_px"][1]))
            taken[best] = r["key"]
            r["color"] = "#%06x" % best
            r["source"] += ", moved"

    # keys the game rules need: park them on the nearest free edge province
    req = required_keys(args.vanilla.parent.parent, [r["key"] for r in out if r["action"] == "delete"])
    for r in sorted((r for r in out if r["key"] in req and r["action"] == "delete"), key=lambda r: r["key"]):
        px, py = pp.lonlat_to_px(r["lon"], r["lat"])
        free = [k for k in rows if k not in taken]
        best = min(free, key=lambda k: np.hypot(centres[k][0] - px, centres[k][1] - py))
        taken[best] = r["key"]
        r.update(action="required", color="#%06x" % best)

    # sea realms: kept when a good share of their area maps onto the frame's sea
    for rid, info in sorted(realms.items()):
        if not info.is_sea:
            continue
        xs, zs = np.nonzero(rmap == rid)
        if len(xs) == 0:
            continue
        step = max(1, len(xs) // 4000)
        x, z = (xs[::step] + 0.5) * 3750 / 2048, (zs[::step] + 0.5) * 2625 / 2048
        lat = np.array([fit(a, b, FIT_LAT) for a, b in zip(x, z)])
        lon = np.array([fit(a, b, FIT_LON) for a, b in zip(x, z)])
        px, py = pp.lonlat_to_px(lon, lat)
        px, py = np.round(px).astype(int), np.round(py).astype(int)
        inside = (px >= 0) & (px < pp.W) & (py >= 0) & (py < pp.H)
        on_sea = np.zeros_like(inside)
        on_sea[inside] = sea[py[inside], px[inside]]
        share = on_sea.mean()
        row = dict(key=info.name, kind="sea", action="delete", castle="", lat=round(float(lat.mean()), 3),
                   lon=round(float(lon.mean()), 3), source=f"fit, {share:.0%} in frame", color="")
        if info.name in EXTRA_SEAS:
            row.update(action="sea", lat=EXTRA_SEAS[info.name][0], lon=EXTRA_SEAS[info.name][1], source="manual")
        elif share >= SEA_SHARE:
            row.update(action="sea", lat=round(float(lat[on_sea].mean()), 3), lon=round(float(lon[on_sea].mean()), 3))
        out.append(row)

    with open(args.hre / "vanilla_keys.csv", "w", newline="", encoding="utf-8") as f:
        cols = ["key", "kind", "action", "castle", "lat", "lon", "source", "color"]
        wr = csv.DictWriter(f, cols, extrasaction="ignore", lineterminator="\n")
        wr.writeheader()
        for r in sorted(out, key=lambda r: (r["kind"], r["action"], r["key"])):
            wr.writerow(r)
    count = lambda a: sum(r["action"] == a for r in out)
    print(f"{count('province')} vanilla keys on provinces, {count('required')} required keys parked on edge "
          f"provinces, {count('sea')} sea realms kept, {count('delete')} deleted; "
          f"{len(rows) - len(taken)} provinces get new keys")
    for r in out:
        if r["action"] == "required" or "moved" in r["source"]:
            print(f"  {r['key']:18} -> {rows[int(r['color'][1:], 16)]['name']} ({r['action']}, {r['source']})")


if __name__ == "__main__":
    main()
