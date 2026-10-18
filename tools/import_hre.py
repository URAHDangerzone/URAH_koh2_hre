"""Import the HRE map's provinces into the URAH_HRE mod.

Usage: python tools/import_hre.py <vanilla_map_dir> <hre_dir> <mod_dir> [--preview <dir>]

Reads <hre_dir>/provinces.png + provinces.csv (the province drawing), <hre_dir>/vanilla_keys.csv
(tools/match_vanilla_realms.py) and <mod_dir>/Maps/europe/terrain.pmt, and replaces the whole
europe realm and settlement set:

  Maps/europe/realms.def        `Realms { delete europe { ... } }`: every land and sea realm with
                                map colour, neighbours, neighbours through sea and geo features
  Maps/europe/realms.csv        town, owner, culture, religion per period (complete table)
  Maps/europe/kingdoms.csv      kingdoms that own land in some period (complete table): vanilla rows
                                edited, new HRE kingdoms added
  Maps/europe/replace.txt       tells the URAH_HRE plugin to use the two tables above instead of
                                merging them with vanilla (vanilla rows can't be removed otherwise)
  Maps/europe/settlements.def   `Settlements { delete europe { ... } }`: a castle and villages per
                                province on flat dry land
  Maps/europe/realms.bin, RealmBounds.bin, RealmBordersTrapezoidData.bin,
                KingdomBordersTrapezoidData.bin
  Maps/europe/realmsdata_patch.bin  relief (blue channel) of the political view for all land
  Maps/europe/sdf_r.bin         realm border distance field for the world view (_SDF red channel)
  Maps/europe/union_quest.def, feature_presets.def, map.def
                                delete what refers to realms that no longer exist; union_quest.def
                                also gets the quests of <hre_dir>/union_quests.csv (new ones, and
                                vanilla ones with hand-made lists) and deletes those of
                                <hre_dir>/union_quests_deleted.csv; feature_presets.def switches the
                                vanilla presets off and fixes the settlements of
                                <hre_dir>/fixed_settlements.csv (church, trade, castle provinces)
  Texts/languages.csv           texts of those quests (en, de)
  Maps/europe/CoA.csv           crests and map colours of new provinces and kingdoms
  Texts/<lang>/<lang>_teritories.csv  names of new provinces, towns and kingdoms
  Texts/<lang>/<lang>_hre_character_names.csv  ruler names the game does not know
  Texts/de/de_hre_kingdomtypes.csv, de_hre_titles.csv  German texts of Defs/hre_nobility.def

Provinces with a vanilla key keep its realms.csv rows and castle layout. New provinces copy the
rows (town, population, units) of the nearest vanilla-key province. Owners per period, cultures,
religions and the HRE kingdoms (rows, rulers, overlords, title sets, names, map colours, atlas tiles
for their arms) come from <hre_dir>/periods.csv, period_kingdoms.csv and kingdom_arms.csv
(tools/hre_periods.py, data/PERIODS.md); tools/paint_hre_coa.py paints the arms afterwards. Landless
kingdoms that only a union quest creates come from <hre_dir>/union_kingdoms.csv.
Units vanilla gave a whole kingdom that the HRE splits up, and other recruitable units, come from
<hre_dir>/units.csv (tools/hre_units.py).
Keys the game rules need (papacy) are parked on provinces by match_vanilla_realms.py and renamed
after them. Fame comes only from <hre_dir>/important_realms.csv.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import re
import struct
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).parent))
import urah_paths  # noqa: F401,E402  (puts URAH_shared on sys.path)
import koh2map  # noqa: E402
import propose_provinces as pp  # noqa: E402
from match_vanilla_realms import GEO, norm  # noqa: E402
import hre_periods  # noqa: E402
import hre_units  # noqa: E402

W, H = pp.W, pp.H
PERIODS = ("E", "M", "L")
THROUGH_SEA_DIST = 300.0  # world units between castles for neighbors_through_sea
BORDER_SPACING = 1.1  # world units between border points (vanilla median)
FIRST_NEW_SETTLEMENT_ID = 5000
GREAT_RIVER_WIDTH = 5.5    # mouth water width (world units) of a great river (Large Rivers feature)
GREAT_RIVER_MIN_LOCAL = 4.0  # ... counted where it is at least this wide (not its headwaters)
GREAT_RIVER_BAND = 4       # world units either side: a river on the border counts for both provinces
GREAT_RIVER_MIN_LENGTH = 12.0  # world units of great river through / along a province
# great rivers too narrow by Natural Earth's rank, from where they begin (lon, lat; the lines run
# downstream): Natural Earth's Weser starts at the Werra's source, the Weser at Hann. Münden;
# the Thames (rank 6) from its source, England's only one
GREAT_RIVERS_BY_NAME = {"Weser": (9.65, 51.42), "Thames": (-2.05, 51.84)}
MIN_RIVER_LENGTH = 15.0    # river course (world units) inside a province for has_rivers
COASTAL_MIN_SHORE = 25    # coastline pixels (world units) a province needs for a coastal castle
COAST_PER_VILLAGE = 30    # coastline (world units) per possible coastal village ...
COASTAL_VILLAGES_MAX = 4  # ... at most this many (vanilla 1-4); the number is drawn from 1 to that
COASTAL_VILLAGE_DIST = 10  # a village this close to the sea is coastal
COASTAL_VILLAGE_SPACING = 0.5  # a shore village keeps this share of the province's village spacing
                               # (sqrt(area / settlements)) from the others; else it goes inland
SHORE_SLOPE = 20.0  # shore sites may be this steep (the beach ramp is ~25 deg within 3 units of the water)
STEEP_SHORE_SLOPE = 30.0  # ... or this steep on a cliff coast without such sites (Liguria)
STEEP_SHORE_MIN = 50  # shore site pixels a province needs before the steep ones are left out
COASTAL_TOWN_DIST = 15    # a town this close to the sea counts as coastal
COASTAL_CASTLE_SHIFT = 100  # a coastal castle moves at most this far from its town to reach the shore
INLAND_CASTLES = {"London"}  # keys whose castle stays at its town although vanilla made it coastal
CASTLE_BORDER_SHARE = 0.5  # a castle keeps this share of the province's inradius from its land borders ...
CASTLE_BORDER_MIN, CASTLE_BORDER_MAX = 5, 35  # ... clipped to this (vanilla: castle at 0.71 of the inradius, p10 0.33)
CASTLE_MAX_SHIFT = 40  # ... moving at most this far from its town (vanilla castles are ~30 units off the real place)
FRAME_MARGIN_CASTLE = 25  # world units from the map frame (vanilla: no settlement closer than 25)
FRAME_MARGIN_VILLAGE = 15
VILLAGE_BORDER_WEIGHT = 2.0  # village spacing score: min(distance to other sites, this * distance to border)
# villages per province, where there is room: provinces ranked by village_score() get these counts,
# from the lowest score up (min 4, median 6; vanilla 2-10, median 5)
VILLAGE_SPREAD = ((4, 0.10), (5, 0.20), (6, 0.26), (7, 0.20), (8, 0.13), (9, 0.07), (10, 0.04))
VILLAGE_AREA_WEIGHT = 0.5  # score: this * log(area) ...
VILLAGE_CORE_BONUS = 0.3   # ... + this for the core tier (the HRE heartland; the outer provinces are bigger) ...
VILLAGE_FAME_BONUS = ((100, 0.5), (50, 0.25))  # ... + this for fame (any period, data/important_realms.csv) ...
VILLAGE_JITTER = 0.12      # ... + gaussian noise of this sd
MIN_VILLAGES = 4  # fewer on flat land (mountain provinces): fill up on steeper ground ...
FALLBACK_SLOPES = (18, 22, 26, 30)  # ... under these slope limits (degrees, 5 px window), in turn
FALLBACK_SPACING = 6  # ... at least this far apart (flat land: 8)
VILLAGE_MIN_SPACING = 0.3  # share of the province's village spacing (sqrt(area / settlements)) that every
                           # village keeps from the others, if more than the above (narrow valleys: Valais)
FLAT_SLOPE = 6.7   # median relief slope (degrees) below which a province counts as flat
HILL_SLOPE = 10.4  # ... and above which (not mountainous) as hilly
MOUNTAIN_STEEP = 0.10  # share of the relief steeper than 30 deg above which it counts as mountainous
# (FLAT_SLOPE, HILL_SLOPE and MOUNTAIN_STEEP refitted for the massif terrain of tools/make_hre_terrain.py:
# 281 of 302 provinces keep their class)
KEEP_VANILLA_ROWS = {"Latium"}  # required keys that keep their own history (the papacy)

# modern administrative names (districts, counties, subregions); such provinces are named after their
# main town instead. Historical region names (Emsland, Harz, French départements, Hungarian
# counties) are kept.
ADMIN_NAME = re.compile(r"[Kk]reis\b|[Kk]reis$|^Region |Umland|Landkreis|\bkraj\b|županija|apskritis|oblast|"
                        r"\bCounty\b|Seenplatte|Stadt\b|(ski|cki|zki|ńskie|skie)$|-[a-z]+-|\bStadtverband\b|^Kraj |"
                        r"\b[a-z]\. [a-z]\. |[A-ZÄÖÜ][\w.]+-[A-ZÄÖÜ]")  # Name-Name: merged modern districts

# German district name noise that reads badly as a province name
NAME_CLEANUP = [(r" — .*$", ""), (r"^(Universitäts- und )?Hansestadt ", ""), (r"^Stadt ", ""), (r",? Landkreis$", ""), (r",? Kreisfreie Stadt$", ""), (r" CC$", ""), (r"^Arr\. ", ""),
                (r", Stadtkreis$", "")]


# ---------------------------------------------------------------- inputs

def load_provinces(hre: Path):
    img = np.array(Image.open(hre / "provinces.png").convert("RGB")).astype(np.int32)
    color = img[..., 0] << 16 | img[..., 1] << 8 | img[..., 2]
    rows = list(csv.DictReader(open(hre / "provinces.csv", encoding="utf-8")))
    known = {int(r["color"][1:], 16) for r in rows}
    unknown = set(np.unique(color).tolist()) - known - {0}
    if unknown:
        raise SystemExit(f"provinces.png has colours without a provinces.csv row: "
                         f"{', '.join('#%06x' % c for c in sorted(unknown)[:10])}")
    return color, rows


def load_heights(pmt: Path):
    """Terrain height (world units) at pixel centres, row 0 = north."""
    raw = gzip.open(pmt).read()
    res = struct.unpack("<i", raw[4:8])[0]
    max_h = struct.unpack("<f", raw[16:20])[0]
    hm = np.frombuffer(raw, "<u2", res * res, 20).reshape(res, res).astype(np.float32) * max_h / 65535
    zs = (np.arange(H)[::-1] + 0.5) * (res - 1) / pp.SIZE_Z
    xs = (np.arange(W) + 0.5) * (res - 1) / pp.SIZE_X
    return ndimage.map_coordinates(hm, np.meshgrid(zs, xs, indexing="ij"), order=1)


# letters NFKD does not split into a base letter and a mark
TRANSLIT = str.maketrans({"ß": "ss", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O", "æ": "ae",
                          "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ı": "i"})


def ascii_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.translate(TRANSLIT))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s if s and s[0].isalpha() else "P_" + s


def clean_name(name: str) -> str:
    for pat, rep in NAME_CLEANUP:
        name = re.sub(pat, rep, name)
    return name


def world_xz(col, row):
    return float(col) + 0.5, float(H - 1 - row) + 0.5


# ---------------------------------------------------------------- seas

def partition_sea(sea, seeds):
    """Label sea pixels by the nearest seed through water (BFS on a 4x coarser grid, then refined by
    nearest coarse label); water bodies no seed reaches go to the nearest seed in a straight line."""
    f = 4
    hc, wc = (H + f - 1) // f, (W + f - 1) // f
    coarse = np.zeros((hc, wc), bool)
    pad = np.zeros((hc * f, wc * f), bool)
    pad[:H, :W] = sea
    coarse = pad.reshape(hc, f, wc, f).any(axis=(1, 3))
    lab = np.full((hc, wc), -1, np.int32)
    frontier = []
    for i, (x, y) in enumerate(seeds):
        cy, cx = int(y) // f, int(x) // f
        ys, xs = np.nonzero(coarse)
        j = np.argmin((ys - cy) ** 2 + (xs - cx) ** 2)
        lab[ys[j], xs[j]] = i
        frontier.append((ys[j], xs[j]))
    front = np.zeros_like(coarse)
    for y, x in frontier:
        front[y, x] = True
    while front.any():
        new = np.zeros_like(front)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            src = np.roll(np.where(front, lab, -1), (dy, dx), axis=(0, 1))
            take = coarse & (lab < 0) & (src >= 0)
            lab[take] = src[take]
            new |= take
        front = new
    if (coarse & (lab < 0)).any():
        _, (iy, ix) = ndimage.distance_transform_edt(lab < 0, return_indices=True)
        lab = lab[iy, ix]
    fine = np.repeat(np.repeat(lab, f, 0), f, 1)[:H, :W]
    return np.where(sea, fine, -1)


# ---------------------------------------------------------------- borders

def boundary_segments(rid):
    """Pixel edges between different realms, grouped by realm pair; vertices are pixel corners (col, row)."""
    segs = defaultdict(list)
    a, b = rid[:, :-1], rid[:, 1:]
    rr, cc = np.nonzero(a != b)
    for r, c in zip(rr.tolist(), cc.tolist()):
        p, q = int(a[r, c]), int(b[r, c])
        segs[(min(p, q), max(p, q))].append(((c + 1, r), (c + 1, r + 1)))
    a, b = rid[:-1, :], rid[1:, :]
    rr, cc = np.nonzero(a != b)
    for r, c in zip(rr.tolist(), cc.tolist()):
        p, q = int(a[r, c]), int(b[r, c])
        segs[(min(p, q), max(p, q))].append(((c, r + 1), (c + 1, r + 1)))
    return segs


def chain(edges):
    """Join pixel edges into polylines, breaking at vertices of degree != 2."""
    adj = defaultdict(list)
    for u, v in edges:
        adj[u].append(v)
        adj[v].append(u)
    used = set()
    lines = []

    def walk(start, nxt):
        line = [start]
        prev, cur = start, nxt
        used.add(frozenset((start, nxt)))
        while True:
            line.append(cur)
            if len(adj[cur]) != 2:
                break
            n = adj[cur][0] if adj[cur][0] != prev else adj[cur][1]
            e = frozenset((cur, n))
            if e in used:
                break
            used.add(e)
            prev, cur = cur, n
        return line

    for v in list(adj):
        if len(adj[v]) != 2:
            for n in adj[v]:
                if frozenset((v, n)) not in used:
                    lines.append(walk(v, n))
    for v in list(adj):  # closed loops
        for n in adj[v]:
            if frozenset((v, n)) not in used:
                lines.append(walk(v, n))
    return lines


def smooth_resample(line, spacing=BORDER_SPACING):
    pts = np.array(line, np.float64)
    closed = len(pts) > 3 and (pts[0] == pts[-1]).all()
    for _ in range(3):  # moving average, ends fixed (closed loops wrap)
        if closed:
            pts[:-1] = (np.roll(pts[:-1], 1, 0) + 2 * pts[:-1] + np.roll(pts[:-1], -1, 0)) / 4
            pts[-1] = pts[0]
        elif len(pts) > 2:
            pts[1:-1] = (pts[:-2] + 2 * pts[1:-1] + pts[2:]) / 4
    d = np.hypot(*np.diff(pts, axis=0).T)
    length = d.sum()
    if length < 2.0:
        return None
    t = np.concatenate([[0], np.cumsum(d)])
    s = np.linspace(0, length, max(int(length / spacing), 1) + 1)
    x = np.interp(s, t, pts[:, 0])
    y = np.interp(s, t, pts[:, 1])
    wx, wz = x, H - y  # pixel corners -> world units
    mid_x, mid_z = (wx[:-1] + wx[1:]) / 2, (wz[:-1] + wz[1:]) / 2  # WorldMap.GenerateTrapezoidData
    return np.column_stack([mid_x, np.zeros(len(mid_x)), mid_z]).astype(np.float32)


# ---------------------------------------------------------------- settlements

def vanilla_castles(text: str):
    """key: (id, name, body lines) for every vanilla castle; body = def contents minus name/position."""
    out = {}
    for m in re.finditer(r"\t\tdef (\d+) : Castle\s*\{", text):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        block = text[m.end():i - 1]
        name = re.search(r'name = "([^"]+)"', block).group(1)
        pos = re.search(r"position = ([\d.\-]+), ([\d.\-]+)", block)
        body = "\n".join(l for l in block.strip("\n").split("\n")
                         if not re.match(r"\s*(name|position|coastal) =", l))
        out[name] = (int(m.group(1)), (float(pos.group(1)), float(pos.group(2))), body)
    return out


def village_targets(provinces, hre: Path) -> dict[str, int]:
    """Villages per province key: provinces ranked by area, heartland and fame get the counts of
    VILLAGE_SPREAD in turn."""
    fame = {r["key"]: max(int(r[per] or 0) for per in PERIODS)
            for r in csv.DictReader(open(hre / "important_realms.csv", encoding="utf-8"))}
    rng = np.random.default_rng(7)
    score = np.array([VILLAGE_AREA_WEIGHT * np.log(p["area"]) + VILLAGE_CORE_BONUS * (p["row"]["tier"] == "core")
                      + next((b for f, b in VILLAGE_FAME_BONUS if fame.get(p["key"], 0) >= f), 0.0)
                      for p in provinces]) + rng.normal(0, VILLAGE_JITTER, len(provinces))
    order = np.argsort(score, kind="stable")
    bounds = np.round(np.cumsum([share for _, share in VILLAGE_SPREAD]) / sum(s for _, s in VILLAGE_SPREAD)
                      * len(provinces)).astype(int)
    out, lo = {}, 0
    for (n, _), hi in zip(VILLAGE_SPREAD, bounds):
        for j in order[lo:hi]:
            out[provinces[j]["key"]] = n
        lo = hi
    return out


def pick_site(mask_ok, target, fallback):
    ys, xs = np.nonzero(mask_ok)
    if len(ys) == 0:
        ys, xs = np.nonzero(fallback)
    j = np.argmin((xs - target[0]) ** 2 + (ys - target[1]) ** 2)
    return int(xs[j]), int(ys[j])


# ---------------------------------------------------------------- def text editing

def def_paths(text: str):
    """(path, key, line number) for every field in a def file; path = keys of the enclosing blocks."""
    stack, last, out = [], None, []
    for n, raw in enumerate(text.split("\n")):
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        for tok in re.findall(r"\{|\}|[^{}]+", line):
            tok = tok.strip()
            if tok == "{":
                stack.append(last)
            elif tok == "}":
                stack.pop()
            elif tok:
                words = tok.split()
                key = words[1] if words[0] in ("def", "extend") and len(words) > 1 else words[0]
                key = key.split("=")[0].strip().rstrip(";")
                last = key
                out.append((tuple(stack), key, n))
    return out


def leaf_lists(text: str):
    """(enclosing block names, list name, entries) for every innermost {...} block; comments dropped."""
    text = re.sub(r"//[^\n]*", "", text)
    stack, out = [], []
    for m in re.finditer(r"(\w+)?\s*\{|\}", text):
        if m.group(0) == "}":
            name, start, has_child = stack.pop()
            if not has_child:
                entries = [e for e in re.split(r"[;\s,]+", text[start:m.start()]) if e]
                out.append((tuple(n for n, _, _ in stack), name, entries))
            if stack:
                stack[-1] = (stack[-1][0], stack[-1][1], True)
        else:
            if stack:
                stack[-1] = (stack[-1][0], stack[-1][1], True)
            stack.append((m.group(1), m.end(), False))
    return out


def rewrite_union_quests(text: str, realm_keys, descendants, overrides=None, deleted=()):
    """Def text that rewrites the realm lists (keys ending in 'realms') of every union quest that names
    a missing realm, plus `delete Quest` for quests whose own_realms goal lost every realm and for the
    `deleted` ones. overrides: {quest: {"lists": {(path, name): entries}, "values": {path: line}}},
    hand-made lists and values (data/union_quests.csv) that replace vanilla's."""
    overrides = overrides or {}
    parts, dropped = [], []
    for m in re.finditer(r"def (\w+) : UnionQuest\s*\{", text):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        quest = m.group(1)
        if quest in deleted:
            parts.append(f"delete {quest}")
            dropped.append(quest)
            continue
        ov = overrides.get(quest, {"lists": {}, "values": {}})
        edits = []
        drop = False
        for path, name, entries in leaf_lists(text[m.end():i - 1]):
            if (path, name) in ov["lists"]:
                continue
            if not name or not name.endswith("realms") or all(e in realm_keys for e in entries):
                continue
            kept = [e for e in entries if e in realm_keys]
            if name.startswith("eligable"):
                kept += [d for e in entries for d in descendants.get(e, []) if d not in kept]
            if name == "own_realms" and not kept:
                drop = True
                break
            edits.append(nested(path, f"delete {name} {{ {'; '.join(kept)}; }}" if kept else f"delete {name}"))
        for (path, name), entries in ov["lists"].items():
            edits.append(nested(path, f"delete {name} {{ {'; '.join(entries)}; }}"))
        for path, line in ov["values"].items():
            edits.append(nested(path, line))
        if drop:
            parts.append(f"delete {quest}")
            dropped.append(quest)
        elif edits:
            parts.append(nested((quest,), "\n".join(edits)))
    return parts, dropped


# fixed settlements per set of data/fixed_settlements.csv; trade sets are trade center candidates
# (Economy.InitTradeCenterRealms takes the realms of presets with the tradeCenter feature first)
FIXED_SETS = {
    "rome": ("HRERomeRealmSet", {"Monastery": 3}, False),
    "church": ("HREChurchRealmSet", {"Monastery": 2}, False),
    "church_castle": ("HREChurchCastleRealmSet", {"Monastery": 2, "Keep": 1}, False),
    "trade_capital": ("HRETradeCapitalRealmSet", {"Village": 3}, True),
    "trade": ("HRETradeRealmSet", {"Village": 2}, True),
    "fortress": ("HREFortressRealmSet", {"Keep": 3}, False),
    "castle": ("HRECastleRealmSet", {"Keep": 2}, False),
}


def fixed_settlement_presets(path: Path, realm_keys) -> str:
    """One preset per set, placed in exactly the listed realms (occurances = their number, 100 %)."""
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    bad = [f"{r['key']} ({r['set']})" for r in rows if r["key"] not in realm_keys or r["set"] not in FIXED_SETS]
    if bad:
        raise SystemExit(f"{path.name}: unknown provinces or sets: {', '.join(bad)}")
    dup = [k for k, n in Counter(r["key"] for r in rows).items() if n > 1]
    if dup:
        raise SystemExit(f"{path.name}: a province takes one preset only: {', '.join(dup)}")
    defs = []
    for s, (name, settlements, trade) in FIXED_SETS.items():
        keys = [r["key"] for r in rows if r["set"] == s]
        if not keys:
            continue
        body = [f"occurances = {len(keys)}", "required_settlements", "{"]
        body += [f"\t{k} = {n}" for k, n in settlements.items()] + ["}"]
        if trade:
            body += ["features", "{", "\ttradeCenter", "}"]
        body += ["desired_in_realms = 100", "{"] + [f"\t{k}" for k in keys] + ["}"]
        defs.append(f"def {name} : RealmFeaturesPreset\n{{\n" + "\n".join("\t" + b for b in body) + "\n}")
    return "extend RealmFeaturesPresets\n{\neurope\n{\n" + "\n".join(defs) + "\n}\n}"


def nested(path, inner: str) -> str:
    text = inner
    for key in reversed(path):
        text = f"{key}\n{{\n{text}\n}}"
    return text


def union_targets(text: str, dropped, realm_keys, descendants, kingdoms) -> set[str]:
    """Kingdoms that the union quests of a union_quest.def text unlock, leaving out dropped quests and
    quests no kingdom can receive (none of their eligible realms or kingdoms is left)."""
    out = set()
    for m in re.finditer(r"def (\w+) : UnionQuest\s*\{", text):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        body = text[m.end():i - 1]
        target = re.search(r'unlock_union\s*=\s*"(\w+)"', body)
        lists = leaf_lists(body)
        eligible = [e for _, name, entries in lists if name == "eligable_realms" for e in entries]
        eligible_kingdoms = [e for _, name, entries in lists if name == "eligable_kingdoms" for e in entries]
        reachable = any(e in realm_keys or descendants.get(e) for e in eligible) or \
            any(k in kingdoms for k in eligible_kingdoms)
        if m.group(1) not in dropped and target and reachable:
            out.add(target.group(1))
    return out


UNION_QUEST_DEFAULTS = {"active_period": "early mid late", "religion": "Catholic"}
QUEST_TEXTS = ("name", "short_description", "long_description")


def union_quests(hre: Path, provinces, realm_keys, vanilla_quests):
    """Hand-made union quests (data/union_quests.csv). A row with a new key becomes a `def … : UnionQuest`
    block; a row with a vanilla key overrides that quest's lists (and culture) where its columns are
    filled. Eligible realms: the provinces of the `regions` (prefixes of the NUTS codes in
    data/provinces.csv) plus `eligible_realms`. The game requires every activate condition, so a quest
    with eligible realms and kingdoms reaches only the listed kingdoms that hold a listed realm. Returns new def blocks, overrides for
    rewrite_union_quests, texts (key, en, de), the kingdoms the new quests unlock, and the vanilla
    quests to delete (data/union_quests_deleted.csv)."""
    defs, overrides, texts, unions = [], {}, [], set()
    for q in csv.DictReader(open(hre / "union_quests.csv", encoding="utf-8")):
        key = q["quest"]
        regions = q["regions"].split()
        eligible = [p["key"] for p in provinces if any(p["row"]["region"].startswith(r) for r in regions)]
        eligible += [k for k in q["eligible_realms"].split() if k not in eligible]
        goal = q["own_realms"].split()
        unknown = [k for k in goal + q["eligible_realms"].split() if k not in realm_keys]
        if unknown:
            raise SystemExit(f"data/union_quests.csv {key}: unknown realms {unknown}")
        cultures = ", ".join(f'"{c}"' for c in q["cultures"].split())
        lists = {}
        if eligible:
            lists[(("activate_conditions",), "eligable_realms")] = eligible
        for col, name in (("eligible_kingdoms", "eligable_kingdoms"), ("excluded_kingdoms", "excluded_kingdoms")):
            if q[col]:
                lists[(("activate_conditions",), name)] = q[col].split()
        if goal:
            lists[(("complete_conditions",), "own_realms")] = goal
        if key in vanilla_quests:
            overrides[key] = {"lists": lists, "values": {("activate_conditions",): f"culture = [{cultures}]"}
                              if cultures else {}}
        else:
            if not goal or not (eligible or q["eligible_kingdoms"]) or not q["unlock_union"]:
                raise SystemExit(f"data/union_quests.csv {key}: a new quest needs own_realms, eligible realms "
                                 f"or kingdoms, and unlock_union")
            periods = ", ".join(f'"{p}"' for p in (q["active_period"] or UNION_QUEST_DEFAULTS["active_period"]).split())
            act = "".join(f"\t\t{name} {{ {'; '.join(v)}; }}\n" for (path, name), v in lists.items()
                          if path == ("activate_conditions",))
            defs.append(
                f"def {key} : UnionQuest\n{{\n"
                f"\tname = \"{q['name']}\"\n\tshort_description = \"{q['short_description']}\"\n"
                f"\tlong_description = \"{q['long_description']}\"\n\tcategory = \"Main\"\n"
                f"\tactivate_conditions\n\t{{\n\t\tactive_period = [{periods}]\n"
                + (f"\t\tculture = [{cultures}]\n" if cultures else "") + act +
                f"\t}}\n\tcomplete_conditions\n\t{{\n\t\town_realms {{ {'; '.join(goal)}; }}\n"
                f"\t\treligion = \"{q['religion'] or UNION_QUEST_DEFAULTS['religion']}\"\n\t}}\n"
                f"\toutcome\n\t{{\n\t\tunlock_union = \"{q['unlock_union']}\"\n\t}}\n}}")
            unions.add(q["unlock_union"])
        for field in QUEST_TEXTS:
            if q[field]:
                texts.append((f"UnionQuests.europe.{key}.{field}", q[field], q[field + "_de"]))
    deleted = [r["quest"] for r in csv.DictReader(open(hre / "union_quests_deleted.csv", encoding="utf-8"))]
    clash = set(deleted) & ({d.split()[1] for d in defs} | set(overrides))
    if clash:
        raise SystemExit(f"data/union_quests_deleted.csv deletes quests union_quests.csv defines: {sorted(clash)}")
    return defs, overrides, texts, unions, deleted


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("vanilla", type=Path, help="vanilla Maps/europe")
    ap.add_argument("hre", type=Path)
    ap.add_argument("mod", type=Path, help="mod folder (URAH_HRE)")
    ap.add_argument("--preview", type=Path)
    args = ap.parse_args()
    out = args.mod / "Maps" / "europe"
    vanilla = args.vanilla
    sovereign = vanilla.parent.parent

    color, prows = load_provinces(args.hre)
    periods, period_kings, kingdom_arms = hre_periods.load(args.hre)
    keyrows = list(csv.DictReader(open(args.hre / "vanilla_keys.csv", encoding="utf-8")))
    vkey_of = {int(r["color"][1:], 16): r for r in keyrows if r["action"] in ("province", "required")}
    seas = [r for r in keyrows if r["action"] == "sea"]

    # ---- realms and ids: land provinces in provinces.csv order, then seas
    vanilla_names = {r.name for r in koh2map.realm_ids(vanilla).values()}
    provinces = []
    used = set()
    for r in prows:
        c = int(r["color"][1:], 16)
        name = clean_name(r["name"])
        vk = vkey_of.get(c)
        if vk:
            key = vk["key"]
        else:
            key = ascii_key(name)
            if key in vanilla_names or key in used:
                key = f"{key}_{r['country']}"
            n = 2
            while key in vanilla_names or key in used:
                key, n = f"{ascii_key(name)}_{n}", n + 1
        used.add(key)
        provinces.append(dict(color=c, key=key, name=name, row=r, vanilla=vk,
                              kind=vk["action"] if vk else "new"))
    n_land = len(provinces)
    gap = {p["key"] for p in provinces} ^ set(periods)
    if gap:
        raise SystemExit(f"data/periods.csv and the province drawing disagree on: {', '.join(sorted(gap))}")
    sea_realms = [dict(key=s["key"], lat=float(s["lat"]), lon=float(s["lon"])) for s in seas]

    rid = np.zeros((H, W), np.int32)
    for i, p in enumerate(provinces):
        rid[color == p["color"]] = i + 1
    sea = rid == 0
    seeds = [pp.lonlat_to_px(s["lon"], s["lat"]) for s in sea_realms]
    sea_lab = partition_sea(sea, seeds)
    for i in range(len(sea_realms)):
        rid[sea_lab == i] = -(n_land + 1 + i)
    keys = {i + 1: p["key"] for i, p in enumerate(provinces)}
    keys.update({-(n_land + 1 + i): s["key"] for i, s in enumerate(sea_realms)})
    print(f"{n_land} land realms ({sum(p['kind'] == 'new' for p in provinces)} new keys), {len(sea_realms)} sea realms")

    # ---- realm map and bounds
    cols = np.minimum(((np.arange(2048) + 0.5) * pp.SIZE_X / 2048).astype(int), W - 1)
    rows_z = np.minimum(((np.arange(2048) + 0.5) * pp.SIZE_Z / 2048).astype(int), H - 1)
    realm_map = rid[(H - 1 - rows_z)[None, :], cols[:, None]]  # [x, z]
    out.mkdir(parents=True, exist_ok=True)
    koh2map.write_realms_bin(out / "realms.bin", realm_map.astype(np.int16))
    # realm border distance field (red channel of the world view's _SDF, fitted on vanilla's sdf_ex:
    # red = 35 x distance in 1024-px units to the nearest realm border, coasts included); the game
    # only rebuilds the kingdom channel at runtime. Raw uint8 1024², row 0 = south (URAH_HRE plugin)
    img = realm_map.T[::-1]
    edge = np.zeros(img.shape, bool)
    # borders between two sea realms are not drawn (vanilla has none; they showed as straight lines in the water)
    land_px = img > 0
    edge[:, 1:] |= (img[:, 1:] != img[:, :-1]) & (land_px[:, 1:] | land_px[:, :-1])
    edge[1:, :] |= (img[1:, :] != img[:-1, :]) & (land_px[1:, :] | land_px[:-1, :])
    edge = edge[::2, ::2] | edge[1::2, ::2] | edge[::2, 1::2] | edge[1::2, 1::2]
    sdf_r = np.clip(35 * ndimage.distance_transform_edt(~edge), 0, 255).astype(np.uint8)
    (out / "sdf_r.bin").write_bytes(sdf_r[::-1].tobytes())

    order = list(range(1, n_land + 1)) + [-(n_land + 1 + i) for i in range(len(sea_realms))]
    bounds = []
    for r in order:
        ys, xs = np.nonzero(rid == r)
        bounds.append([xs.min(), 0, H - 1 - ys.max(), xs.max() + 1, 0, H - ys.min()])
    koh2map.write_realm_bounds(out / "RealmBounds.bin", np.array(bounds, np.float32), vanilla / "RealmBounds.bin")

    # ---- adjacency and borders
    segs = boundary_segments(rid)
    neighbors = defaultdict(set)
    records = []
    for (a, b), edges in segs.items():
        neighbors[a].add(b)
        neighbors[b].add(a)
        for line in chain(edges):
            pts = smooth_resample(line)
            if pts is not None and len(pts):
                records.append((a, b, pts))
    koh2map.write_border_records(out / "RealmBordersTrapezoidData.bin", records)
    koh2map.write_border_records(out / "KingdomBordersTrapezoidData.bin",
                                 records + [(b, a, p) for a, b, p in records])
    print(f"{len(segs)} realm pairs, {len(records)} border lines")

    # ---- terrain at pixels, settlement sites
    heights = load_heights(out / "terrain.pmt")
    gy, gx = np.gradient(heights)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    flat = ndimage.maximum_filter(slope, size=7) < 14
    relief = ndimage.maximum_filter(slope, size=5)  # for the fallback village sites
    shore_ok = ndimage.maximum_filter(slope, size=3) < SHORE_SLOPE
    shore_steep = ndimage.maximum_filter(slope, size=3) < STEEP_SHORE_SLOPE
    rivers_bin = out / "rivers.bin"
    river_banks = np.zeros((H, W), bool)  # the carved banks are steep: left out of the province relief
    riverbed = np.zeros((H, W), bool)
    if rivers_bin.exists():  # no settlements in the river beds (tools/hre_rivers.py)
        import hre_rivers
        from PIL import ImageDraw
        layer = Image.new("L", (W, H), 0)
        dr = ImageDraw.Draw(layer)
        banks = Image.new("L", (W, H), 0)
        drb = ImageDraw.Draw(banks)
        for pts in hre_rivers.read_rivers(rivers_bin):
            xy = [(float(x), H - 1 - float(z)) for x, _, z, _ in pts[::2]]
            visible = float(pts[:, 3].max()) * hre_rivers.VISIBLE_SHARE
            dr.line(xy, fill=255, width=int(round(visible)) + 4)
            drb.line(xy, fill=255, width=int(round(visible)) + 12)
        river_banks = np.array(banks) > 0
        riverbed = np.array(layer) > 0
        flat &= ~riverbed
        shore_ok &= np.array(layer) == 0
        shore_steep &= np.array(layer) == 0
    land = rid > 0
    sea_dist = ndimage.distance_transform_edt(land)
    edge = np.zeros_like(land)
    edge[:, 1:] |= rid[:, 1:] != rid[:, :-1]
    edge[:, :-1] |= rid[:, 1:] != rid[:, :-1]
    edge[1:, :] |= rid[1:, :] != rid[:-1, :]
    edge[:-1, :] |= rid[1:, :] != rid[:-1, :]
    land_edge = np.zeros_like(land)  # borders between two land provinces only (coasts left out)
    for a, b, sa, sb in ((rid[:, 1:], rid[:, :-1], np.s_[:, 1:], np.s_[:, :-1]),
                         (rid[1:, :], rid[:-1, :], np.s_[1:, :], np.s_[:-1, :])):
        d = (a != b) & (a > 0) & (b > 0)
        land_edge[sa] |= d
        land_edge[sb] |= d
    # the map frame is a border too: past it is void, settlements there hang over the edge
    for e in (edge, land_edge):
        e[0, :] = e[-1, :] = True
        e[:, 0] = e[:, -1] = True
    border_dist = ndimage.distance_transform_edt(~edge)
    land_border_dist = ndimage.distance_transform_edt(~land_edge)
    yy, xx = np.ogrid[:H, :W]
    frame_dist = np.minimum(np.minimum(xx, W - 1 - xx), np.minimum(yy, H - 1 - yy))
    inradius = ndimage.maximum(land_border_dist, rid, index=np.arange(1, n_land + 1))

    # political view relief: realmsdata blue channel for every land pixel (fit on vanilla:
    # B ~ 2 + 2.3 * slope in degrees; sea is set to 255 by the plugin)
    blue = np.clip(2 + 2.3 * slope[(H - 1 - rows_z)[None, :], cols[:, None]], 0, 254).astype(np.uint8)  # [x, z]
    with open(out / "realmsdata_patch.bin", "wb") as f:
        f.write(b"PRB1" + struct.pack("<4i", 0, 0, 2048, 2048))
        f.write(np.ascontiguousarray(blue.T).tobytes())  # [z][x]

    # main town of each province: the biggest GeoNames place (towns over 5000 people; English names
    # for big cities like vanilla's Vienna / Prague, local names for small towns)
    # data/periods.csv can name a medieval main town instead (Mainz over Worms, Padua over Conselve)
    def plain(s):
        return norm(s).replace("ue", "u").replace("oe", "o").replace("ae", "a")

    wanted = {i + 1: plain(periods[p["key"]]["suggested_town"]) for i, p in enumerate(provinces)
              if periods[p["key"]]["suggested_town"]}
    biggest, towns, chosen_town = {}, defaultdict(list), {}
    with open(GEO / "cities5000.txt", encoding="utf-8") as f:
        for line in f:
            c = line.split("\t")
            x, y = pp.lonlat_to_px(float(c[5]), float(c[4]))
            x, y = int(x), int(y)
            if 0 <= x < W and 0 <= y < H and rid[y, x] > 0:
                r, pop = int(rid[y, x]), int(c[14] or 0)
                towns[r].append((pop, clean_name(c[1]), x, y))
                if r not in biggest or pop > biggest[r][0]:
                    biggest[r] = (pop, clean_name(c[1]), x, y)
                if r in wanted and wanted[r] in {plain(n) for n in [c[1], c[2]] + c[3].split(",") if n}:
                    name = clean_name(c[1]) if plain(c[1]) == wanted[r] else \
                        periods[provinces[r - 1]["key"]]["suggested_town"]
                    if r not in chosen_town or pop > chosen_town[r][0]:
                        chosen_town[r] = (pop, name, x, y)
    missing_towns = sorted(provinces[r - 1]["key"] for r in wanted if r not in chosen_town)
    print(f"{len(chosen_town)} suggested main towns found"
          + (f", not in GeoNames (biggest town kept): {', '.join(missing_towns)}" if missing_towns else ""))
    # provinces with a real coastline get a coastal castle (vanilla: 64 % of the realms at sea); the
    # vanilla keys keep vanilla's choice (geo_features.csv has_coastal_city)
    coast_len = {r: int(n) for r, n in zip(*np.unique(rid[(sea_dist >= 1) & (sea_dist < 2)], return_counts=True))}
    vgeo = {}
    for row in csv.reader(open(vanilla / "geo_features.csv", encoding="utf-8-sig"), delimiter=";"):
        if len(row) > 3:
            vgeo[row[0]] = row[3] == "true"

    vtext = (vanilla / "settlements.def").read_text(encoding="utf-8-sig")
    vcastles = vanilla_castles(vtext)
    centroid = {}
    for i, p in enumerate(provinces):
        ys, xs = np.nonzero(rid == i + 1)
        centroid[i + 1] = (xs.mean(), ys.mean())
        p["area"] = len(xs)
    # donor: nearest province holding a real vanilla key (history, castle layout)
    donors = [i + 1 for i, p in enumerate(provinces) if p["kind"] == "province"]
    for i, p in enumerate(provinces):
        r = i + 1
        p["donor"] = r if p["kind"] == "province" else min(
            donors, key=lambda d: np.hypot(centroid[d][0] - centroid[r][0], centroid[d][1] - centroid[r][1]))

    rng = np.random.default_rng(1)
    n_villages = village_targets(provinces, args.hre)
    next_id = FIRST_NEW_SETTLEMENT_ID
    town_keys = set()
    settlement_defs = []
    moved_castles = []
    renamed_towns = []
    for i, p in enumerate(provinces):
        r = i + 1
        mine = rid == r
        donor = provinces[p["donor"] - 1]
        vc = vcastles.get(donor["vanilla"]["castle"]) if donor["vanilla"] else None
        if p["kind"] == "province" and vc:
            target = pp.lonlat_to_px(float(p["vanilla"]["lon"]), float(p["vanilla"]["lat"]))
            town = p["vanilla"]["castle"]
            at = int(rid[int(np.clip(target[1], 0, H - 1)), int(np.clip(target[0], 0, W - 1))])
            if at != r:
                # the vanilla castle's place lies in another land province (Kent's Canterbury in
                # London's): with a suggested town in data/periods.csv the castle goes there and
                # takes its name, else it moves to the biggest town and keeps the vanilla name
                if at > 0 and r in chosen_town:
                    renamed_towns.append(f"{p['key']} ({town} -> {chosen_town[r][1]})")
                    _, town, *target = chosen_town[r]
                    p["renamed_town"] = True
                else:
                    target = biggest.get(r, (0, None, *centroid[r]))[2:]
            sid = vc[0]
        else:
            if r in chosen_town:
                _, town, tx, ty = chosen_town[r]
                target = (tx, ty)
            elif r in biggest:
                _, town, tx, ty = biggest[r]
                target = (tx, ty)
            else:
                town, target = p["name"], centroid[r]
            sid = next_id
            next_id += 1
        wants_coast = coast_len.get(r, 0) >= COASTAL_MIN_SHORE and (
            vgeo.get(p["key"], True) if p["kind"] == "province" else True) and p["key"] not in INLAND_CASTLES
        if r in chosen_town and p["kind"] != "province":  # the suggested town decides
            wants_coast = bool(sea_dist[int(target[1]), int(target[0])] <= COASTAL_TOWN_DIST)
        elif wants_coast and p["kind"] != "province":
            near = [t for t in towns[r] if sea_dist[t[3], t[2]] <= COASTAL_TOWN_DIST]
            if near:  # the biggest coastal town becomes the castle town
                _, town, tx, ty = max(near)
                target = (tx, ty)
        ok = mine & flat & (sea_dist >= 3) & (border_dist >= 5) & (frame_dist >= FRAME_MARGIN_CASTLE)
        if wants_coast:  # not to a far shore of the province (London to the Kent coast)
            ok_coast = ok & (sea_dist <= 9) & \
                ((xx - target[0]) ** 2 + (yy - target[1]) ** 2 <= COASTAL_CASTLE_SHIFT ** 2)
            if ok_coast.any():
                ok = ok_coast
        # away from the land borders like vanilla's castles: the nearest site to the town at least
        # `margin` inside, within CASTLE_MAX_SHIFT of it; else the site deepest inside there
        margin = float(np.clip(CASTLE_BORDER_SHARE * inradius[i], CASTLE_BORDER_MIN, CASTLE_BORDER_MAX))
        reach = ok & ((xx - target[0]) ** 2 + (yy - target[1]) ** 2 <= CASTLE_MAX_SHIFT ** 2)
        if (reach & (land_border_dist >= margin)).any():
            ok = reach & (land_border_dist >= margin)
        elif reach.any():
            ok = reach & (land_border_dist >= land_border_dist[reach].max() - 1)
        cx, cy = pick_site(ok, target, mine & (sea_dist >= 2) & (frame_dist >= FRAME_MARGIN_CASTLE))
        shift = float(np.hypot(cx - target[0], cy - target[1]))
        if shift > CASTLE_MAX_SHIFT:
            moved_castles.append(f"{p['key']} ({town}, {shift:.0f})")
        coastal = bool(sea_dist[cy, cx] < 12)
        if p["kind"] != "province" or p.get("renamed_town"):
            tk = ascii_key(town)
            while tk in town_keys or tk in vanilla_names or tk in used:
                tk += "_town"
            town_keys.add(tk)
            p["town_key"], p["town_name"] = tk, town
        body = vc[2] if vc else vcastles["Inverness"][2]
        houses = (re.search(r'houses = "(\w+)"', body) or re.search(r'architecture = "(\w+)"', body))
        style = houses.group(1) if houses else "Central"
        wx, wz = world_xz(cx, cy)
        settlement_defs.append(f'\t\tdef {sid} : Castle\n\t\t{{\n\t\t\tname = "{town}"\n\t\t\tposition = {wx:.2f}, {wz:.2f}\n'
                               f'\t\t\tcoastal = {str(coastal).lower()}\n{body}\n\t\t}}\n')
        p.update(castle=(cx, cy), coastal_city=coastal)
        # villages: farthest-point sampling on flat land; the score also counts the distance to the
        # province border, so they spread through the province instead of lining its edge
        n_vill = n_villages[p["key"]]
        cand = mine & flat & (sea_dist >= 2) & (border_dist >= 4) & (frame_dist >= FRAME_MARGIN_VILLAGE)
        if coast_len.get(r, 0) >= COASTAL_MIN_SHORE:  # the flat test rules out the whole shore strip
            strip = mine & (sea_dist >= 3) & (sea_dist < COASTAL_VILLAGE_DIST) & (border_dist >= 4) & \
                (frame_dist >= FRAME_MARGIN_VILLAGE)
            cand |= strip & (shore_ok if (strip & shore_ok).sum() >= STEEP_SHORE_MIN else shore_steep)
        ys, xs = np.nonzero(cand)
        if len(ys) > 4000:  # thin out, but keep every shore site
            at_shore = sea_dist[ys, xs] < COASTAL_VILLAGE_DIST
            k = rng.choice(len(ys), 4000, replace=False)
            k = np.union1d(k, np.flatnonzero(at_shore))
            ys, xs = ys[k], xs[k]
        inner = VILLAGE_BORDER_WEIGHT * border_dist[ys, xs]
        chosen = [(cx, cy)]
        d = np.hypot(xs - cx, ys - cy)
        # provinces at the sea get some villages on the shore first (vanilla: realms with a coastal
        # castle have 1-4 coastal villages, about half of theirs); the spread alone keeps them inland.
        # A long coast allows more, the number varies
        n_coast = int(rng.integers(1, 1 + np.clip(coast_len[r] // COAST_PER_VILLAGE, 1, COASTAL_VILLAGES_MAX))) \
            if coast_len.get(r, 0) >= COASTAL_MIN_SHORE else 0
        shore = sea_dist[ys, xs] < COASTAL_VILLAGE_DIST
        spacing = np.sqrt(p["area"] / (n_vill + 1))
        shore_spacing = max(8.0, COASTAL_VILLAGE_SPACING * spacing)
        min_spacing = max(8.0, VILLAGE_MIN_SPACING * spacing)
        for k in range(n_vill):
            on_shore = k < n_coast and (shore & (d >= shore_spacing)).any()
            if len(d) == 0 or d.max() < 8:
                break
            j = int(np.argmax(np.where(shore, d, -1.0) if on_shore else np.minimum(d, inner)))
            if d[j] < min_spacing:  # no room left on flat land; the fallback below may add some
                break
            chosen.append((int(xs[j]), int(ys[j])))
            d = np.minimum(d, np.hypot(xs - xs[j], ys - ys[j]))
        # mountain provinces with little flat land: more villages on steeper ground, flattest first
        for limit in FALLBACK_SLOPES:
            if len(chosen) - 1 >= MIN_VILLAGES:
                break
            fys, fxs = np.nonzero(mine & (relief < limit) & ~riverbed & (sea_dist >= 2) & (border_dist >= 3)
                                  & (frame_dist >= FRAME_MARGIN_VILLAGE))
            if len(fys) == 0:
                continue
            fd = np.min([np.hypot(fxs - vx, fys - vy) for vx, vy in chosen], axis=0)
            finner = VILLAGE_BORDER_WEIGHT * border_dist[fys, fxs]
            while len(chosen) - 1 < MIN_VILLAGES:
                j = int(np.argmax(np.minimum(fd, finner)))
                if fd[j] < max(FALLBACK_SPACING, VILLAGE_MIN_SPACING * spacing):
                    break
                chosen.append((int(fxs[j]), int(fys[j])))
                fd = np.minimum(fd, np.hypot(fxs - fxs[j], fys - fys[j]))
        for vx, vy in chosen[1:]:
            wx, wz = world_xz(vx, vy)
            lines = [f"\t\t\tposition = {wx:.2f}, {wz:.2f}"]
            if sea_dist[vy, vx] < COASTAL_VILLAGE_DIST:
                lines.append("\t\t\tcoastal = true")
                p["coastal_city"] = True  # coastal settlements make the province Coastal (has_coastal_city)
            if rng.random() < 0.85:
                lines.append("\t\t\tforest = true")
            lines.append(f'\t\t\thouses = "{style}"')
            settlement_defs.append(f"\t\tdef {next_id} : Settlement\n\t\t{{\n" + "\n".join(lines) + "\n\t\t}\n")
            next_id += 1
    (out / "settlements.def").write_text(
        "// Generated by tools/import_hre.py: replaces every europe settlement.\nSettlements\n{\n\tdelete europe\n\t{\n"
        + "\n".join(settlement_defs) + "\t}\n}\n", encoding="utf-8")
    print(f"{len(settlement_defs)} settlements ({n_land} castles)")
    if renamed_towns:
        print(f"vanilla castles named after their suggested town: {', '.join(renamed_towns)}")
    if moved_castles:
        print(f"castles more than {CASTLE_MAX_SHIFT} units from their town: {', '.join(moved_castles)}")

    # ---- display names: modern administrative names become the province's main town
    taken_names = {p["name"] for p in provinces if not ADMIN_NAME.search(p["name"])}
    for p in provinces:
        p["display"] = p["name"]
        if p["kind"] != "province" and ADMIN_NAME.search(p["name"]) and p["town_name"] not in taken_names:
            p["display"] = p["town_name"]
            taken_names.add(p["town_name"])
    renamed = sum(p["display"] != p["name"] for p in provinces)
    print(f"{renamed} administrative province names replaced by their main town")

    # ---- realms.def
    def castle_world(r):
        return np.array(world_xz(*provinces[r - 1]["castle"]))

    # river features: has_rivers (castle illustration) = a river runs through the province;
    # has_river_city (the "Large Rivers" province feature, ProvinceFeatureDistribution) = one of the
    # great rivers (Danube, Rhine, Elbe, Oder, Vistula, Loire, ..., GREAT_RIVERS_BY_NAME) flows
    # through or along the province, so the feature follows those rivers as a band instead of
    # dotting castles near any river
    # (counted on all courses, rivers_all.bin, also those without water on the map; their widths are
    # the old generator's, which the thresholds are set on)
    river_len = defaultdict(float)
    great_px = defaultdict(int)
    rivers_all = out / "rivers_all.bin"
    if rivers_all.exists():
        band = Image.new("L", (W, H), 0)
        dr = ImageDraw.Draw(band)
        for pts in hre_rivers.read_rivers(rivers_all):
            seg = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 2]))
            cols = np.clip(pts[1:, 0].astype(int), 0, W - 1)
            rows = np.clip(H - 1 - pts[1:, 2].astype(int), 0, H - 1)
            for r, s in zip(rid[rows, cols], seg):
                if r > 0:
                    river_len[int(r)] += float(s)
            if pts[:, 3].max() < GREAT_RIVER_WIDTH:
                continue
            wide = pts[:, 3] >= GREAT_RIVER_MIN_LOCAL
            edges = np.flatnonzero(np.diff(np.concatenate([[0], wide.astype(np.int8), [0]])))
            for i0, i1 in zip(edges[::2], edges[1::2]):
                if i1 - i0 > 1:
                    dr.line([(float(x), H - 1 - float(z)) for x, _, z, _ in pts[i0:i1:2]], fill=255,
                            width=2 * GREAT_RIVER_BAND + 1)
        named = hre_rivers.named_courses(GREAT_RIVERS_BY_NAME)
        if len(named) == 0:
            print(f"warning: none of {', '.join(GREAT_RIVERS_BY_NAME)} found in the river sources")
        for name, xz in named:
            start = np.array(hre_rivers.lonlat_to_world(*GREAT_RIVERS_BY_NAME[name]))
            xz = xz[int(np.argmin(np.hypot(*(xz - start).T))):]
            dr.line([(float(x), H - 1 - float(z)) for x, z in xz], fill=255, width=2 * GREAT_RIVER_BAND + 1)
        hit = (np.array(band) > 0) & (rid > 0)
        for r, c in zip(*np.unique(rid[hit], return_counts=True)):
            great_px[int(r)] = int(c)
    river_city = {r for r, c in great_px.items() if c / (2 * GREAT_RIVER_BAND + 1) >= GREAT_RIVER_MIN_LENGTH}
    has_rivers = {r for r, length in river_len.items() if length >= MIN_RIVER_LENGTH} | river_city
    print(f"{len(has_rivers)} provinces with rivers, {len(river_city)} along a great river (Large Rivers)")

    wine = {l.strip() for l in open(args.hre / "wine_provinces.txt", encoding="utf-8")
            if l.strip() and not l.startswith("#")}
    unknown = wine - {p["key"] for p in provinces}
    if unknown:
        raise SystemExit(f"data/wine_provinces.txt names unknown provinces: {', '.join(sorted(unknown))}")
    pirates = {l.strip() for l in open(args.hre / "pirate_provinces.txt", encoding="utf-8")
               if l.strip() and not l.startswith("#")}
    unknown = pirates - {p["key"] for p in provinces}
    if unknown:
        raise SystemExit(f"data/pirate_provinces.txt names unknown provinces: {', '.join(sorted(unknown))}")
    inland = sorted(p["key"] for p in provinces if p["key"] in pirates and not p["coastal_city"])
    if inland:
        print(f"pirate provinces without a coastal castle (no Pirates feature): {', '.join(inland)}")
    terrain_count = defaultdict(int)
    blocks = []
    for i, p in enumerate(provinces):
        r = i + 1
        nb = sorted(neighbors[r], key=lambda k: keys[k])
        through = {n for n in nb if n > 0}  # land realms only: the game makes kingdom neighbours of them
        for s in (n for n in nb if n < 0):
            for o in neighbors[s]:
                if o > 0 and o != r and np.linalg.norm(castle_world(o) - castle_world(r)) < THROUGH_SEA_DIST:
                    through.add(o)
        seas_count = sum(1 for n in nb if n < 0)
        s = slope[(rid == r) & ~river_banks]
        if s.size == 0:
            s = slope[rid == r]
        mount = bool((s > 30).mean() > MOUNTAIN_STEEP)
        # terrain classes for settlement type weights (Defs/hre_settlements.def): median slope of the
        # relief, ~30 % of the provinces flat, ~30 % hilly, 67 mountainous
        med = float(np.median(s))
        terrain = "mountains" if mount else "flat" if med < FLAT_SLOPE else "hills" if med >= HILL_SLOPE else ""
        flags = (f"\t\t\tbool terrain_{terrain} = true\n" if terrain else "") + \
            ("\t\t\tbool wine_region = true\n" if p["key"] in wine else "")
        terrain_count[terrain or "rolling"] += 1
        nb_text = "\n".join(f"\t\t\t\t{keys[n]}" for n in nb)
        th_text = "\n".join(f"\t\t\t\t{keys[n]}" for n in sorted(through, key=lambda k: keys[k]))
        rgb = ",".join(str(v) for v in ((p["color"] >> 16) & 255, (p["color"] >> 8) & 255, p["color"] & 255))
        blocks.append(f"\t\tdef {p['key']} : Realm\n\t\t{{\n\t\t\tmap_color = \"{rgb}\"\n"
                      f"\t\t\tneighbors\n\t\t\t{{\n{nb_text}\n\t\t\t}}\n"
                      f"\t\t\tneighbors_through_sea\n\t\t\t{{\n{th_text}\n\t\t\t}}\n"
                      f"\t\t\thas_rivers = {str(r in has_rivers).lower()}\n"
                      f"\t\t\thas_river_city = {str(r in river_city).lower()}\n"
                      f"\t\t\thas_coastal_city = {str(p['coastal_city']).lower()}\n"
                      f"\t\t\thas_mountains = {str(mount).lower()}\n\t\t\tseas_count = {seas_count}\n"
                      f"\t\t\thas_distant_port = {str(p['key'] in pirates and p['coastal_city']).lower()}\n"
                      f"{flags}\t\t}}\n")
    for i, s in enumerate(sea_realms):
        r = -(n_land + 1 + i)
        nb_text = "\n".join(f"\t\t\t\t{keys[n]}" for n in sorted(neighbors[r], key=lambda k: keys[k]))
        rgb = f"{(17 * i) % 64},{60 + (23 * i) % 120},{180 + (31 * i) % 70}"
        blocks.append(f"\t\tdef {s['key']} : Realm\n\t\t{{\n\t\t\tmap_color = \"{rgb}\"\n\t\t\tbool isSeaRealm = true\n"
                      f"\t\t\tneighbors\n\t\t\t{{\n{nb_text}\n\t\t\t}}\n\t\t\tneighbors_through_sea\n\t\t}}\n")
    (out / "realms.def").write_text(
        "// Generated by tools/import_hre.py: replaces every europe realm (land first, then seas).\n"
        "Realms\n{\n\tdelete europe\n\t{\n" + "\n".join(blocks) + "\t}\n}\n", encoding="utf-8")
    print("terrain: " + ", ".join(f"{k} {v}" for k, v in sorted(terrain_count.items()))
          + f"; {len(wine)} wine regions")

    # ---- realms.csv
    vraw = (vanilla / "realms.csv").read_text(encoding="utf-8-sig").splitlines()
    header = vraw[0]
    vrows = defaultdict(list)
    for line in vraw[1:]:
        cells = next(csv.reader([line]))
        if cells and cells[0] and not cells[0].startswith("//"):
            vrows[cells[0].split(".", 1)[0]].append(cells)
    vanilla_by_row = {c[0]: list(c) for rows in vrows.values() for c in rows}  # the table edits vrows' cells
    table = []
    for p in provinces:
        donor = provinces[p["donor"] - 1]["key"]
        if p["kind"] == "province" or p["key"] in KEEP_VANILLA_ROWS:
            for cells in vrows[p["key"]]:
                if p.get("renamed_town") and "." not in cells[0]:
                    cells = list(cells)
                    cells[1] = p["town_key"]
                table.append(cells)
            continue
        for cells in vrows[donor]:
            cells = list(cells)
            period = cells[0].split(".", 1)[1] if "." in cells[0] else None
            cells[0] = p["key"] + (f".{period}" if period else "")
            if period is None:
                cells[1] = p["town_key"]
                for col in (7, 8):  # CoreOf / HistoricalOf / CoA belong to the donor
                    if col < len(cells):
                        cells[col] = ""
            table.append(cells)
    hdr = next(csv.reader([header]))
    table = hre_periods.apply_realms(table, hdr, provinces, periods)
    # recruitable units: data/units.csv (tools/hre_units.py)
    unit_rules = hre_units.load(args.hre, vanilla.parent.parent / "Defs")
    source_key = {p["key"]: p["key"] if p["kind"] == "province" or p["key"] in KEEP_VANILLA_ROWS
                  else provinces[p["donor"] - 1]["key"] for p in provinces}
    kingdom_keys = {c[0].split(".", 1)[0] for c in csv.reader((vanilla / "kingdoms.csv").open(encoding="utf-8-sig")) if c and c[0]}
    kingdom_keys |= set(period_kings)
    print(hre_units.apply_realms(table, hdr, source_key, vanilla_by_row, unit_rules, kingdom_keys))
    # fame only from data/important_realms.csv: copied donor rows would spread Genoa's or Venice's to
    # their neighbours, and vanilla's holy cities are not on the map. One column per period; the
    # period rows carry it, the base row (like the owner) the late value
    fame = {r["key"]: r for r in csv.DictReader(open(args.hre / "important_realms.csv", encoding="utf-8"))}
    unknown = set(fame) - {p["key"] for p in provinces}
    if unknown:
        raise SystemExit(f"data/important_realms.csv names unknown provinces: {', '.join(sorted(unknown))}")
    fcol = hdr.index("Fame")
    for cells in table:
        if len(cells) > fcol:
            key, _, per = cells[0].partition(".")
            cells[fcol] = fame[key][per or "L"] if key in fame else ""
    buf = io.StringIO()
    wr = csv.writer(buf, lineterminator="\n")
    for cells in table:
        wr.writerow(cells)
    (out / "realms.csv").write_text(header + "\n" + buf.getvalue(), encoding="utf-8")

    # owners per period: data/periods.csv (see tools/hre_periods.py)
    realm_keys = {p["key"] for p in provinces}
    owned = {per: defaultdict(list) for per in PERIODS}
    for p in provinces:
        for per in PERIODS:
            owned[per][hre_periods.owner(periods, p["key"], per)].append(p["key"])
    owners = {per: set(owned[per]) for per in PERIODS}

    # union quests: vanilla's with their realm lists rewritten, plus the hand-made ones of
    # data/union_quests.csv (new quests, and vanilla quests with new lists); data/union_quests_deleted.csv
    uq = (vanilla / "union_quest.def").read_text(encoding="utf-8-sig")
    descendants = defaultdict(list)  # vanilla key -> new provinces that copied its history
    for p in provinces:
        if p["kind"] != "province":
            descendants[provinces[p["donor"] - 1]["key"]].append(p["key"])
    vanilla_quests = set(re.findall(r"def (\w+) : UnionQuest", uq))
    new_quest_defs, quest_overrides, quest_texts, new_unions, deleted_quests = \
        union_quests(args.hre, provinces, realm_keys, vanilla_quests)
    quest_parts, dropped = rewrite_union_quests(uq, realm_keys, descendants, quest_overrides, deleted_quests)
    landed = set().union(*owners.values())
    unions = union_targets(uq, dropped, realm_keys, descendants, landed) | new_unions

    # ---- kingdoms.csv: kingdoms that own land (or are overlords of one that does), vanilla rows edited
    # and new kingdoms added from data/period_kingdoms.csv. Kingdoms that a union quest unlocks keep
    # their base row without land (ChangeNameAndCulture takes culture, titles and arms from it)
    kraw = (vanilla / "kingdoms.csv").read_text(encoding="utf-8-sig").splitlines()
    khdr = next(csv.reader([kraw[0]]))
    kcells = [next(csv.reader([l])) for l in kraw[1:]]
    vassal_col, cap_col = khdr.index("VassalOf"), khdr.index("CapitalProvince")
    keep = set().union(*owners.values())
    changed = True
    while changed:
        changed = False
        for c in kcells:
            if c and c[0] and c[0].split(".", 1)[0] in keep and len(c) > vassal_col \
                    and c[vassal_col] not in ("", "-") and c[vassal_col] not in keep:
                keep.add(c[vassal_col])
                changed = True
    # landless union targets (also Germany, which the stale vanilla VassalOf column would keep as overlord)
    union_only = unions - landed
    keep |= unions
    kcells_all = kcells
    kcells = [c for c in kcells if not c or not c[0] or c[0].startswith("//") or c[0].split(".", 1)[0] in keep]
    kcells = [c for c in kcells if not c or not c[0] or c[0].split(".", 1)[0] not in union_only or "." not in c[0]]
    for c in kcells:
        if c and c[0] in union_only:
            c[cap_col] = "-"
    # landless kingdoms that only a union quest creates (data/union_kingdoms.csv): a base row like
    # vanilla's Germany
    have = {c[0] for c in kcells if c and c[0]}
    for r in csv.DictReader(open(args.hre / "union_kingdoms.csv", encoding="utf-8")):
        if r["kingdom"] in have or r["kingdom"] in landed:
            raise SystemExit(f"data/union_kingdoms.csv {r['kingdom']}: already a kingdom of the map")
        if r["kingdom"] not in unions or r["kingdom"] not in kingdom_arms:
            raise SystemExit(f"data/union_kingdoms.csv {r['kingdom']}: no union quest unlocks it, or no "
                             f"data/kingdom_arms.csv row")
        c = ["-"] * len(khdr)
        c[0] = r["kingdom"]
        for h in ("Traditions", "MapColor", "UnitsPrimary", "UnitSecondary", "CoA"):
            c[khdr.index(h)] = ""
        c[khdr.index("Culture")], c[khdr.index("Religion")] = r["culture"], r["religion"]
        c[khdr.index("NobilityLevel")] = r["level"]
        kcells.append(c)
        have.add(r["kingdom"])
    missing = sorted(u for u in unions if u not in have and u not in period_kings)
    if missing:
        raise SystemExit(f"union quests unlock kingdoms without a kingdoms.csv row: {missing} "
                         f"(add them to data/union_kingdoms.csv)")
    kout = hre_periods.apply_kingdoms(kcells, khdr, period_kings, owned)
    # every capital is a realm the kingdom holds in that period (vanilla edge kingdoms lost theirs)
    base_cap = {}
    for c in kout:
        if c and c[0] and "." not in c[0] and not c[0].startswith("//"):
            if c[cap_col] not in ("", "-") and c[cap_col] not in realm_keys:
                mine = [k for per2 in PERIODS for k in owned[per2].get(c[0], [])]
                c[cap_col] = mine[0] if mine else "-"
            base_cap[c[0]] = c[cap_col]
    for c in kout:
        if not c or not c[0] or c[0].startswith("//") or "." not in c[0]:
            continue
        name, _, per = c[0].partition(".")
        cap = c[cap_col] if c[cap_col] not in ("", "-") else base_cap.get(name, "-")
        if per in PERIODS and owned[per].get(name) and cap not in owned[per][name]:
            c[cap_col] = owned[per][name][0]
    hre_units.apply_kingdoms(kout, khdr, unit_rules)
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(kout)
    (out / "kingdoms.csv").write_text(kraw[0] + "\n" + buf.getvalue(), encoding="utf-8")
    (out / "replace.txt").write_text("realms.csv\nkingdoms.csv\n", encoding="utf-8")
    print(f"{len(table)} realms.csv rows, {len({c[0].split('.')[0] for c in kout if c and c[0]})} kingdoms "
          f"({', '.join(f'{per} {len(owners[per])}' for per in PERIODS)} with land)")

    # ---- coats of arms. Free atlas tiles: crests of deleted realms and of vanilla kingdoms without land,
    # those that were nearest the HRE first (CoA.csv merges with vanilla). New HRE kingdoms
    # (data/kingdom_arms.csv) get the first ones, tools/paint_hre_coa.py paints their arms into them;
    # the remaining new province keys get the old crests with their map / army colours.
    craw = list(csv.reader(open(vanilla / "CoA.csv", encoding="utf-8-sig")))
    chdr, crows = craw[0], [r for r in craw[1:] if r and r[0]]
    base = {r[0]: r for r in crows if "." not in r[0]}
    kept_kingdoms = {c[0].split(".", 1)[0] for c in kout if c and c[0] and not c[0].startswith("//")}
    vanilla_kingdoms = {c[0].split(".", 1)[0] for c in kcells_all if c and c[0] and not c[0].startswith("//")}
    gone_keys = sorted((vanilla_names - realm_keys) | (vanilla_kingdoms - kept_kingdoms - realm_keys))
    loc = {r["key"]: (float(r["lat"]), float(r["lon"])) for r in keyrows if r["lat"]}
    in_use = {base[n][1] for n in base if n not in gone_keys}
    free = []
    for k in sorted(gone_keys, key=lambda k: np.hypot(*(np.subtract(loc.get(k, (0, 0)), (50, 11))))):
        if k in base and base[k][1] not in in_use and base[k][1] not in {f[1] for f in free}:
            free.append(base[k])
    painted = [k for k, a in kingdom_arms.items() if a["arms"]]
    # kingdom map colours: as far as possible (palette index, as the game picks) from every neighbour
    adjacency = defaultdict(set)
    for (a, b) in segs:
        if a > 0 and b > 0:
            for per in PERIODS:
                ka = hre_periods.owner(periods, keys[a], per)
                kb = hre_periods.owner(periods, keys[b], per)
                if ka != kb:
                    adjacency[ka].add(kb)
                    adjacency[kb].add(ka)
    fixed = {k: int(base[k][2]) for k in kept_kingdoms if k in base and k not in painted and int(base[k][2]) >= 0}
    size = {k: sum(len(owned[per].get(k, ())) for per in PERIODS) for k in painted}
    colors = hre_periods.pick_colors(sorted(painted, key=lambda k: -size[k]), fixed, adjacency)
    new_rows = []
    for k in painted:
        crest = free.pop()  # farthest from the HRE first: keeps the nearby crests for provinces
        new_rows.append([k, crest[1], str(colors[k]), "-1", "-1"])
        new_rows += [[f"{k}.{per}", "0", "-1", "-1", "-1"] for per in PERIODS]
    for p in provinces:
        if p["kind"] == "province" or p["key"] in base or p["key"] in kingdom_arms:
            continue
        if not free:
            break
        crest = free.pop(0)
        new_rows.append([p["key"]] + crest[1:])
        new_rows += [[f"{p['key']}.{per}", "0", "-1", "-1", "-1"] for per in PERIODS]
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows([chdr] + new_rows)
    (out / "CoA.csv").write_text(buf.getvalue(), encoding="utf-8")
    print(f"{len(painted)} new kingdoms got an atlas tile for their arms, "
          f"{len(new_rows) // 4 - len(painted)} new provinces a crest freed by deleted realms ({len(free)} left)")

    # ---- defs that name deleted realms
    gone = sorted(vanilla_names - realm_keys - {s["key"] for s in sea_realms})
    gone_re = re.compile(r"\b(" + "|".join(map(re.escape, gone)) + r")\b")
    quests = dropped
    (out / "union_quest.def").write_text(
        "// Generated by tools/import_hre.py: realm lists of union quests without the realms the HRE map\n"
        "// does not have; eligible realms also list the new provinces carved from them.\n"
        + nested(("UnionQuests", "europe"), "\n".join(quest_parts + new_quest_defs)) + "\n", encoding="utf-8")
    # quest texts: languages.csv of the mod merges with the game's (Defs.LoadTextFileWithMods)
    buf = io.StringIO()
    wr = csv.writer(buf, delimiter=";", lineterminator="\n", quoting=csv.QUOTE_ALL)
    wr.writerow(["ID", "en", "de"])
    wr.writerows(quest_texts)
    (args.mod / "Texts").mkdir(parents=True, exist_ok=True)
    (args.mod / "Texts" / "languages.csv").write_text(buf.getvalue(), encoding="utf-8")
    fp = (vanilla / "feature_presets.def").read_text(encoding="utf-8-sig")
    dels = defaultdict(set)
    for path, key, n in def_paths(fp):
        if key in gone:
            dels[path].add(key)
    parts = [nested(path, "\n".join(f"delete {k}" for k in sorted(ks))) for path, ks in sorted(dels.items())]
    # all vanilla presets are off (occurances = 0): the feature ones pinned features to vanilla realm
    # names the HRE map happens to share (Franconia, Tyrol, Bavaria ...) against the feature balance of
    # Defs/hre_features.def, the settlement ones scattered their unnamed occurrences at random
    vanilla_sets = sorted({path[-1] for path, key, n in def_paths(fp)
                           if key == "occurances" and path[:2] == ("RealmFeaturesPresets", "europe")})
    parts += [nested(("RealmFeaturesPresets", "europe", k), "occurances = 0") for k in vanilla_sets]
    parts.append(fixed_settlement_presets(args.hre / "fixed_settlements.csv", realm_keys))
    (out / "feature_presets.def").write_text(
        "// Generated by tools/import_hre.py: preset entries for realms the HRE map does not have; the\n"
        "// vanilla presets switched off (occurances = 0); fixed settlements of data/fixed_settlements.csv.\n"
        + "\n".join(parts) + "\n", encoding="utf-8")
    # the new-campaign screen selects this realm's kingdom: Munich is Bavarian in all three periods
    start = next((p["key"] for p in provinces if p["key"] == "Rosenheim"), provinces[0]["key"])
    (out / "map.def").write_text(f"extend Maps\n{{\n\teurope\n\t{{\n\t\tstarting_realm = \"{start}\"\n\t}}\n}}\n",
                                 encoding="utf-8")
    print(f"{len(new_quest_defs)} new union quests, {len(quest_overrides)} with hand-made lists, "
          f"{len(union_only)} landless union kingdoms kept "
          f"({', '.join(sorted(union_only))})")
    print(f"{len(quests)} union quests deleted (no realm of their goal left, or union_quests_deleted.csv), "
          f"{len(quest_parts) - len(quests)} rewritten; {sum(map(len, dels.values()))} feature preset entries deleted")

    # ---- texts: new province and town names, kingdom names (English forms, German names), ruler
    # names the game does not know, German title sets of Defs/hre_nobility.def
    names = {}
    for p in provinces:
        if p["kind"] != "province":
            names["tn_" + p["key"]] = p["display"]
        if "town_key" in p:
            names["tn_" + p["town_key"]] = p["town_name"]
    knames = {k: a for k, a in kingdom_arms.items() if a["name_en"]}
    vchars = set()
    for row in csv.reader(open(sovereign / "Texts" / "en" / "en_character_names.csv", encoding="utf-8-sig"),
                          delimiter=";"):
        if row and row[0].startswith("cn_"):
            vchars.add(row[0][3:].rsplit(".", 1)[0])
    rulers = set()
    kcol = {h: i for i, h in enumerate(khdr)}
    for c in kout:
        if c and c[0] and not c[0].startswith("//"):
            for h in ("KingNameAge", "QueenNameAge"):
                v = c[kcol[h]] if len(c) > kcol[h] else ""
                if v not in ("", "-"):
                    rulers.add((v.split(".")[0], "m" if h == "KingNameAge" else "f"))
    new_chars = sorted((n, s) for n, s in rulers if n not in vchars)
    de_dir = sovereign / "Texts" / "de"
    for lang_dir in sorted((sovereign / "Texts").iterdir()):
        src = lang_dir / f"{lang_dir.name}_teritories.csv"
        if not src.exists():
            continue
        lang = lang_dir.name
        text = src.read_text(encoding="utf-8-sig").splitlines()
        quoted = text[0].startswith('"')
        delim = "," if text[0].startswith('"*",') else ";"  # tr, hi, ind use commas
        head = next(csv.reader([text[0]], delimiter=delim))
        default = next((next(csv.reader([l], delimiter=delim)) for l in text[1:3]
                        if l.strip('"').startswith("default")), None)
        has_default = default is not None and any("{" in c for c in default)
        buf = io.StringIO()
        wr = csv.writer(buf, delimiter=delim, lineterminator="\n",
                        quoting=csv.QUOTE_ALL if quoted else csv.QUOTE_MINIMAL)
        wr.writerow(head)
        if default:
            wr.writerow(default)
        for k, v in names.items():
            wr.writerow([k, v] + ([""] if has_default else [v]) * (len(head) - 2))
        for k, a in sorted(knames.items()):
            if lang == "en":
                forms = hre_periods.english_forms(a["name_en"], a["adjective_en"])
                wr.writerow([f"tn_{k}"] + (forms + [""] * len(head))[:len(head) - 1])
            else:
                v = a["name_de"] if lang == "de" else a["name_en"]
                wr.writerow([f"tn_{k}", v] + ([""] if has_default else [v]) * (len(head) - 2))
        dst = args.mod / "Texts" / lang / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(buf.getvalue(), encoding="utf-8-sig")
        # ruler names: same layout as the language's <lang>_character_names.csv (header, default forms)
        cn = (lang_dir / f"{lang}_character_names.csv").read_text(encoding="utf-8-sig").splitlines()
        cdelim = "," if cn[0].startswith('"*",') else ";"
        chead, cdefault = (next(csv.reader([l], delimiter=cdelim)) for l in cn[:2])
        buf = io.StringIO()
        wr = csv.writer(buf, delimiter=cdelim, lineterminator="\n",
                        quoting=csv.QUOTE_ALL if cn[0].startswith('"') else csv.QUOTE_MINIMAL)
        wr.writerow(chead)
        wr.writerow(cdefault)
        for n, s in new_chars:
            wr.writerow([f"cn_{n}.{s}", n.replace("_", " ")] + [""] * (len(chead) - 2))
        (args.mod / "Texts" / lang / f"{lang}_hre_character_names.csv").write_text(buf.getvalue(), encoding="utf-8-sig")
    # German kingdom types and ruler titles (English comes from Defs/hre_nobility.def)
    kt = list(csv.reader(open(de_dir / "de_kingdomtypes.csv", encoding="utf-8-sig"), delimiter=";"))
    ti = list(csv.reader(open(de_dir / "de_titles.csv", encoding="utf-8-sig"), delimiter=";"))
    kt_rows, ti_rows = hre_periods.german_title_tables(kt[0], kt[1], ti[0], ti[1])
    for fname, rows, quote in (("de_hre_kingdomtypes.csv", kt_rows, csv.QUOTE_ALL),
                               ("de_hre_titles.csv", ti_rows, csv.QUOTE_MINIMAL)):
        buf = io.StringIO()
        csv.writer(buf, delimiter=";", lineterminator="\n", quoting=quote).writerows(rows)
        (args.mod / "Texts" / "de" / fname).write_text(buf.getvalue(), encoding="utf-8-sig")
    print(f"{len(names)} province / town and {len(knames)} kingdom names written for "
          f"{len(list((args.mod / 'Texts').iterdir()))} languages; {len(new_chars)} new ruler names: "
          + ", ".join(n for n, _ in new_chars))

    if args.preview:
        args.preview.mkdir(parents=True, exist_ok=True)
        rgb = np.zeros((H, W, 3), np.uint8)
        rgb[land] = np.stack([(color >> 16) & 255, (color >> 8) & 255, color & 255], -1)[land]
        for i in range(len(sea_realms)):
            rgb[sea_lab == i] = (30 + 25 * i % 90, 60 + 37 * i % 100, 150 + 13 * i % 100)
        img = Image.fromarray(rgb)
        from PIL import ImageDraw
        dr = ImageDraw.Draw(img)
        for (a, b, pts) in records:
            xy = [(float(x), float(H - z)) for x, _, z in pts]
            if len(xy) > 1:
                dr.line(xy, fill=(0, 0, 0), width=1)
        for p in provinces:
            cx, cy = p["castle"]
            dr.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=(255, 255, 255), outline=(0, 0, 0))
        for line in settlement_defs:
            if ": Settlement" in line:
                m = re.search(r"position = ([\d.]+), ([\d.]+)", line)
                x, y = float(m.group(1)), H - float(m.group(2))
                dr.rectangle((x - 1.5, y - 1.5, x + 1.5, y + 1.5), fill=(40, 40, 40))
        img.save(args.preview / "import_preview.png")


if __name__ == "__main__":
    main()
