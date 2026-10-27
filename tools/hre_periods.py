"""Starting periods of the HRE map: owners, cultures, kingdoms, titles and names (used by import_hre.py).

Reads the hand-researched tables in data/:
  periods.csv          owner kingdom of every province per period (ALL_PERIODS), suggested main town
  period_kingdoms.csv  kingdom rows per period: ruler, overlord, capital, culture, titles, level
  kingdom_arms.csv     names (English, German) and arms of kingdoms without a vanilla crest
See data/PERIODS.md and data/PERIODS_EARLY.md.

Vanilla's periods E / M / L are always on. The early periods C (867) and O (999) are switched on
by their data: a period is active once every province has an owner in its periods.csv column; an
empty column leaves it off (the map then builds exactly as with three periods), a partly filled one
too, with a note. PERIODS holds the active suffixes in game order after load().
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

# suffix -> key in Periods.europe (CampaignVars start_period option), year; game order, earliest first.
# Suffixes are one letter: the game reads only the last character of papacy.csv keys. C and O are not
# vanilla's: the URAH_HRE plugin resolves them (PeriodSuffixPatch).
ALL_PERIODS = {
    "C": ("carolingian", 867),
    "O": ("ottonian", 999),
    "E": ("early", 1110),
    "M": ("mid", 1224),
    "L": ("late", 1360),
}
VANILLA_PERIODS = ("E", "M", "L")
PERIOD_COLS = {per: f"{per}_{year}" for per, (_, year) in ALL_PERIODS.items()}
PERIODS = VANILLA_PERIODS  # active periods, set by load()

# kingdoms whose people are pagan in a period (their provinces' religion follows)
PAGAN = {"C": set(),  # filled with the 867 / 999 kingdoms (PERIODS_EARLY.md)
         "O": set(),
         "E": {"Liutizi", "Hevelli", "Rugia", "Mecklenburg", "Pomerania", "Prussia", "Yotvingia", "Lithuania"},
         "M": {"Prussia", "Yotvingia", "Lithuania"},
         "L": {"Lithuania"}}
AGES = set("ICJYAOV")
# vanilla rulers that do not fit the period year (all other vanilla rulers and families are kept)
RULER_FIXES = {("Bavaria", "M")}
FAMILY_FIXES = {("Bavaria", "M"): {"QueenNameAge": "Ludmila.O", "Child_1_NameSexAge": "Otto.m.Y"}}


def active_periods(periods) -> tuple[str, ...]:
    """Suffixes of the periods whose periods.csv column names an owner for every province."""
    out = []
    for per in ALL_PERIODS:
        filled = sum(1 for r in periods.values() if (r.get(PERIOD_COLS[per]) or "").strip())
        if per in VANILLA_PERIODS:
            if filled < len(periods):
                raise SystemExit(f"data/periods.csv: {PERIOD_COLS[per]} has no owner for "
                                 f"{len(periods) - filled} provinces")
            out.append(per)
        elif filled == len(periods):
            out.append(per)
        elif filled:
            print(f"period {per} ({ALL_PERIODS[per][1]}) left off: {PERIOD_COLS[per]} names owners for "
                  f"{filled} of {len(periods)} provinces")
    return tuple(out)


def is_before(per: str, than: str) -> bool:
    """per is an earlier period than `than` (game order of ALL_PERIODS)."""
    order = list(ALL_PERIODS)
    return order.index(per) < order.index(than)


def load(hre: Path):
    global PERIODS
    periods = {r["key"]: r for r in csv.DictReader(open(hre / "periods.csv", encoding="utf-8"))}
    PERIODS = active_periods(periods)
    kings = defaultdict(dict)
    for r in csv.DictReader(open(hre / "period_kingdoms.csv", encoding="utf-8")):
        if r["period"] not in ALL_PERIODS:
            raise SystemExit(f"data/period_kingdoms.csv {r['kingdom']}: unknown period '{r['period']}'")
        if r["period"] in PERIODS:  # rows of a period still being filled in wait
            kings[r["kingdom"]][r["period"]] = r
    arms = {r["kingdom"]: r for r in csv.DictReader(open(hre / "kingdom_arms.csv", encoding="utf-8"))}
    return periods, dict(kings), arms


def owner(periods, key, per):
    return periods[key][PERIOD_COLS[per]]


# ---------------------------------------------------------------- cultures

def culture_rule(country: str, region: str, key: str, per: str):
    """Culture of an HRE core province in a period, None where the current (vanilla / donor) culture stays."""
    r2, r3 = region[:3], region[:4]
    if country == "AT":
        # 867: Carantanian and Pannonian Slavs (no Slovene culture; Croatian is the nearest South Slavic
        # one), Bavarian settlement only in the Danube valley; Ostarrichi is Austrian from 999
        if per == "C":
            return "Croatian" if r3 in ("AT11", "AT13", "AT21", "AT22") else "Bavarian"
        return "Bavarian" if r3 in ("AT32", "AT33", "AT34") else "Austrian"
    if country == "SI":
        return "Croatian" if is_before(per, "E") else None
    if country == "DE":
        if r3 in ("DE21", "DE22", "DE23"):
            return "Bavarian"
        if r2 in ("DE3", "DE4"):  # Brandenburg: Wends until the Ascanian conquest
            return "Pomeranian" if is_before(per, "M") else "German"
        if r2 == "DE8":  # Mecklenburg and Western Pomerania: Slavic until the 13th century settlement
            return "Pomeranian" if is_before(per, "L") else "German"
        if key == "Ostholstein":  # Wagria
            return "Pomeranian" if is_before(per, "M") else "Saxon"
        if r2 == "DED" and is_before(per, "E"):  # Sorbs east of the Saale (867, 999)
            return "Pomeranian"
        if r2 in ("DE5", "DE6", "DE9", "DED", "DEE", "DEF") or r3 in ("DEA3", "DEA4", "DEA5"):
            return "Saxon"
        return "German"
    if country == "CH":
        if r3 == "CH07":
            return "Italian"
        if key in ("Valais", "Vaud", "Freiburg", "Delemont"):
            return "French"
        return "Swiss"
    if country == "CZ":
        return "Moravian" if r3 in ("CZ06", "CZ07", "CZ08") and key != "Kraj_Vysocina" else "Bohemian"
    if country == "BE":
        return "French" if r2 == "BE3" else "Flemish"
    if country == "NL":
        return "Frisian" if r2 == "NL1" else "Dutch"
    if country == "FR" and region.startswith(("FRF1", "FRF3", "FRC2", "FRK2")):
        if r3 == "FRF1" or key == "Moselle":
            return "German"
        if key in ("Isere", "Drome", "Ardeche"):
            return "Occitan"
        return "French"
    if country == "IT":
        return "Italian"
    if country == "PL" and r3 in ("PL22", "PL43", "PL51", "PL52"):
        return "Polish"
    if country == "PL" and r3 == "PL42":
        return "Pomeranian"
    return None


# ---------------------------------------------------------------- realms.csv

def apply_realms(table, hdr, provinces, periods):
    """Set owners per period on the realms.csv rows (list of cell lists, edited in place / extended) and
    make cultures and religions follow the period. Returns the new table."""
    col = {h: i for i, h in enumerate(hdr)}
    rows = defaultdict(dict)
    order = []
    for cells in table:
        cells += [""] * (len(hdr) - len(cells))
        key, _, per = cells[0].partition(".")
        if key not in rows:
            order.append(key)
        rows[key][per or None] = cells
    by_key = {p["key"]: p for p in provinces}
    for key in order:
        if key not in periods:
            continue
        p = by_key[key]
        r = rows[key]
        base = r[None]
        for per in PERIODS:
            if per not in r:
                cells = ["-"] * len(hdr)
                cells[0] = f"{key}.{per}"
                for c in ("CoreOf", "HistoricalOf", "CoA", "Fame"):
                    cells[col[c]] = ""
                r[per] = cells
        base[col["Owner"]] = owner(periods, key, "L")
        core = p["row"]["tier"] == "core" or p["row"]["country"] in ("AT", "CH", "CZ", "DE", "BE", "NL", "LU", "SI")
        cult = {per: culture_rule(p["row"]["country"], p["row"]["region"], key, per) if core else None
                for per in PERIODS}
        if cult["L"]:
            base[col["Culture"]] = cult["L"]
        for per in PERIODS:
            cells = r[per]
            own = owner(periods, key, per)
            cells[col["Owner"]] = own
            if cult[per]:
                cells[col["Culture"]] = cult[per] if cult[per] != base[col["Culture"]] else "-"
            rel = cells[col["Religion"]] if cells[col["Religion"]] not in ("", "-") else base[col["Religion"]]
            if own in PAGAN[per]:
                cells[col["Religion"]] = "Pagan"
            elif rel == "Pagan":
                cells[col["Religion"]] = "Catholic"
    out = []
    for key in order:
        r = rows[key]
        out.append(r[None])
        out.extend(r[per] for per in PERIODS if per in r)
        out.extend(v for k, v in r.items() if k not in (None, *PERIODS))
    return out


# ---------------------------------------------------------------- kingdoms.csv

def _ruler(ruler: str) -> str:
    if ruler in ("", "-"):
        return "-"
    return ruler if ruler.rsplit(".", 1)[-1] in AGES else ruler + ".A"


def apply_kingdoms(kcells, khdr, kings, owned):
    """Edit / add kingdoms.csv rows from period_kingdoms.csv. kcells: vanilla rows kept by the importer
    (list of cell lists), owned: {period: {kingdom: [realms]}}. Returns the new row list."""
    col = {h: i for i, h in enumerate(khdr)}
    width = len(khdr)
    rows = defaultdict(dict)
    order = []
    passthrough = []
    for c in kcells:
        if not c or not c[0] or c[0].startswith("//"):
            passthrough.append(c)
            continue
        c += ["-"] * (width - len(c))
        name, _, per = c[0].partition(".")
        if name not in rows:
            order.append(name)
        rows[name][per or None] = c

    def blank(key):
        c = ["-"] * width
        c[0] = key
        for h in ("Traditions", "MapColor", "UnitsPrimary", "UnitSecondary", "CoA"):
            c[col[h]] = ""
        return c

    family = ("QueenNameAge", "Child_1_NameSexAge", "Child_2_NameSexAge", "Child_3_NameSexAge", "Child_4_NameSexAge")
    kings_new = {k for k in kings if k not in rows}
    for k, pers in kings.items():
        if k not in rows:
            order.append(k)
            first = pers[next(p for p in PERIODS if p in pers)]
            base = blank(k)
            base[col["Culture"]] = first["culture"]
            base[col["Religion"]] = "Catholic"
            base[col["GovernType"]] = first["govern"]
            base[col["CapitalProvince"]] = first["capital"]
            base[col["NobilityLevel"]] = first["level"]
            rows[k][None] = base
        r = rows[k]
        vanilla = k not in kings_new
        vanilla_base = r.get(None)
        for per, d in pers.items():
            c = r.get(per)
            if c is None:
                c = r[per] = blank(f"{k}.{per}")
            c[col["CapitalProvince"]] = d["capital"]
            c[col["VassalOf"]] = d["vassal_of"] if d["vassal_of"] != "-" else ""
            if d["titles"]:
                c[col["NobilityTitles"]] = d["titles"]
            c[col["NobilityLevel"]] = d["level"]
            rel = c[col["Religion"]] if c[col["Religion"]] not in ("", "-") else \
                (vanilla_base[col["Religion"]] if vanilla_base is not None else "-")
            if vanilla and c[col["Culture"]] in ("", "-") and \
                    (vanilla_base is None or vanilla_base[col["Culture"]] in ("", "-")):
                c[col["Culture"]] = d["culture"]  # a period vanilla had no row for
                if rel in ("", "-"):
                    rel = "Catholic"
                    c[col["Religion"]] = rel
            if not vanilla:
                c[col["Culture"]] = d["culture"]
                c[col["GovernType"]] = d["govern"]
                c[col["Religion"]] = "Pagan" if k in PAGAN[per] else "Catholic"
            elif k in PAGAN[per]:
                c[col["Religion"]] = "Pagan"
            elif rel == "Pagan":
                c[col["Religion"]] = "Catholic"
            has_ruler = c[col["KingNameAge"]] not in ("", "-")
            if not vanilla or not has_ruler or (k, per) in RULER_FIXES:
                ruler = _ruler(d["ruler"])
                c[col["KingNameAge"]] = ruler if ruler != "-" else ""
                for h in family:
                    c[col[h]] = FAMILY_FIXES.get((k, per), {}).get(h, "")
                if d["king_class"]:
                    c[col["KingClass"]] = d["king_class"]
        if vanilla_base is not None and any(p in pers for p in PERIODS):
            vanilla_base[col["VassalOf"]] = "-"  # overlords are set per period

    # vassal chains end at the top overlord (the game expects vassals of independent kingdoms), and an
    # overlord must hold land in the period
    for per in PERIODS:
        def vassal_of(k):
            c = rows.get(k, {}).get(per)
            v = c[col["VassalOf"]] if c is not None else "-"
            if v == "-" and None in rows.get(k, {}):
                v = rows[k][None][col["VassalOf"]]
            return "" if v in ("-", "") else v
        for k in list(rows):
            if per not in rows[k] or not owned[per].get(k):
                continue
            top, seen = vassal_of(k), {k}
            while top and vassal_of(top) and top not in seen:
                seen.add(top)
                top = vassal_of(top)
            if top and not owned[per].get(top):
                top = ""
            rows[k][per][col["VassalOf"]] = top
    out = list(passthrough)
    for k in order:
        r = rows[k]
        if None in r:
            out.append(r[None])
        out.extend(r[p] for p in PERIODS if p in r)
        out.extend(v for p, v in r.items() if p not in (None, *PERIODS))
    return out


# ---------------------------------------------------------------- map colours

NUM_COLORS = 33  # kingdom colour palette (Assets/Europe/kingdom colors.png, CoA.csv map_color 0..32)


def color_dist(a, b):
    d = abs(a - b)
    return min(d, NUM_COLORS - d)


def pick_colors(new_keys, fixed, adjacency):
    """Map colour index for every key in new_keys (biggest first): the index farthest (cyclic distance,
    as the game's own pick) from the colours of all neighbouring kingdoms in any period."""
    colors = dict(fixed)
    for k in new_keys:
        nb = [colors[n] for n in adjacency.get(k, ()) if n in colors]
        best = max(range(NUM_COLORS), key=lambda i: (min((color_dist(i, c) for c in nb), default=NUM_COLORS),
                                                       -sum(1 for c in colors.values() if c == i)))
        colors[k] = best
    return {k: colors[k] for k in new_keys}


# ---------------------------------------------------------------- texts

DE_TYPES = {  # NobilityTitles: German name, feminine
    "Archbishop": ("Erzbistum", False), "ElectorBishop": ("Kurfürstentum", False),
    "Elector": ("Kurfürstentum", False), "PrinceBishop": ("Hochstift", False),
    "PrinceAbbot": ("Fürstabtei", True), "Patriarch": ("Patriarchat", False),
    "Landgrave": ("Landgrafschaft", True), "Burgrave": ("Burggrafschaft", True),
    "Principality": ("Fürstentum", False), "Dauphin": ("Dauphiné", False),
    "Lordship": ("Herrschaft", True), "FreeCity": ("Reichsstadt", True),
    "Commune": ("Kommune", True), "League": ("Bund", None),
}
DE_KINGDOM_LEVEL = {"Commune": ("Republik", True)}
DE_RULERS = {  # NobilityTitles: (male, plural, female)
    "Archbishop": ("Erzbischof", "Erzbischöfe", "Äbtissin"),
    "ElectorBishop": ("Erzbischof und Kurfürst", "Erzbischöfe und Kurfürsten", "Äbtissin"),
    "Elector": ("Kurfürst", "Kurfürsten", "Kurfürstin"),
    "PrinceBishop": ("Fürstbischof", "Fürstbischöfe", "Äbtissin"),
    "PrinceAbbot": ("Fürstabt", "Fürstäbte", "Äbtissin"),
    "Patriarch": ("Patriarch", "Patriarchen", "Äbtissin"),
    "Landgrave": ("Landgraf", "Landgrafen", "Landgräfin"),
    "Burgrave": ("Burggraf", "Burggrafen", "Burggräfin"),
    "Principality": ("Fürst", "Fürsten", "Fürstin"),
    "Dauphin": ("Dauphin", "Dauphins", "Dauphine"),
    "Lordship": ("Herr", "Herren", "Herrin"),
    "FreeCity": ("Bürgermeister", "Bürgermeister", "Herrin"),
    "Commune": ("Podestà", "Podestà", "Herrin"),
    "League": ("Rektor", "Rektoren", "Herrin"),
}


def de_type_row(key, word, fem, ncols):
    if fem is False:
        return [key, word] + [""] * (ncols - 2)
    if fem:  # columns: base genour genyour datour datyour akkour nomour nom akk dat gen
        return [key, word, f"unserer {word}", f"Eurer {word}", f"unserer {word}", f"Eurer {word}",
                f"unsere {word}", f"unsere {word}", f"die {word}", f"die {word}", f"der {word}", f"der {word}"]
    return [key, word, f"unseres {word}es", f"Eures {word}es", f"unserem {word}", f"Eurem {word}",
            f"unseren {word}", f"unser {word}", f"der {word}", f"den {word}", f"dem {word}", f"des {word}es"]


def german_title_tables(kt_head, kt_default, ti_head, ti_default):
    """Rows for Texts/de/de_hre_kingdomtypes.csv and de_hre_titles.csv."""
    kt = [kt_head, kt_default]
    for nob, (word, fem) in DE_TYPES.items():
        kword, kfem = DE_KINGDOM_LEVEL.get(nob, (word, fem))
        kt.append(de_type_row(f"{nob}Nobility.Duchy", word, fem, len(kt_head)))
        kt.append(de_type_row(f"{nob}Nobility.Kingdom", kword, kfem, len(kt_head)))
    ti = [ti_head, ti_default]
    for nob, (male, plural, female) in DE_RULERS.items():
        for k in ("Duke", "King"):
            ti.append([f"{nob}Nobility.{k}", male, plural] + [""] * (len(ti_head) - 3))
        for k in ("Duchess", "Queen"):
            ti.append([f"{nob}Nobility.{k}", female] + [""] * (len(ti_head) - 2))
    return kt, ti


def english_forms(name: str, adj: str):
    """en_teritories columns: base, adjective, possessive, 'a' + adjective, people."""
    adj = adj or name
    article = "an" if adj[:1].lower() in "aeiou" else "a"
    people = adj if adj.endswith("s") else adj + "s"
    return [name, adj, name + ("'" if name.endswith("s") else "'s"), f"{article} {adj}", people]
