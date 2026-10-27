"""Recruitable units of the HRE map (used by import_hre.py), from the hand-made table data/units.csv.

A castle recruits its kingdom's unit set (kingdoms.csv UnitsSet, else the culture's in cultures.csv),
the kingdom's Units, the LocalUnits of the province whose key is the kingdom's key, and the
LocalUnits of its own province. Provinces copy their LocalUnits from vanilla (own key or donor), but
the HRE kingdoms have no Units of their own, so units vanilla gave a whole kingdom (Germany's
crossbowmen) are lost when that kingdom is split up. Rows of data/units.csv (one column per period
suffix of hre_periods.ALL_PERIODS, C / O / E / M / L: units joined with "/", empty = no change in that
period; only the active periods are used):

  from_kingdom  key = vanilla kingdom: its units become LocalUnits of every province whose vanilla
                rows (own key or donor) that kingdom owned in the period
  province      key = HRE province: LocalUnits added in the period
  kingdom_set   key = kingdom: UnitsSet of its period row

The early periods (867, 999) do not inherit vanilla's LocalUnits as they are: most vanilla units are
12th-14th century (crossbows, men-at-arms, feudal knights ...). An empty or "-" period cell falls back
to the base row in the game, so it cannot clear them. While an early period is active, every province's
LocalUnits move from the base row into its period rows, and the early rows keep only the units of
UNIT_FROM that fit their year. Kingdom-wide Units (kingdoms.csv) get the same treatment, and kingdoms of
WESTERN_CULTURES recruit EARLY_UNIT_SET in the early periods instead of their culture's WesternUnitSet.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import hre_periods

KINDS = ("from_kingdom", "province", "kingdom_set")

# inherited LocalUnits that fit the early periods: unit -> earliest period suffix it fits. Units not
# listed are dropped before 1110 (data/units.csv can still add them to a C / O row).
UNIT_FROM = {
    "Viking_Special": "C",
    "Highlander_Special": "C",
    "Saracen_Special": "C",
    "Roman_Infantry_Special": "C",
    "Horse_Archer_CE": "O",  # the Magyars came in 895
    "Norman_Special": "O",  # Normandy from 911
}


def _fits(unit: str, per: str) -> bool:
    return not hre_periods.is_before(per, UNIT_FROM.get(unit, "E"))


def load(hre: Path, defs: Path):
    rows = list(csv.DictReader(open(hre / "units.csv", encoding="utf-8")))
    units = set(re.findall(r"^def (\w+)\s*:", (defs / "units.def").read_text(encoding="utf-8-sig"), re.M))
    sets = set(re.findall(r"^def (\w+)\s*:\s*AvailableUnits", (defs / "AvailableUnits.def").read_text(encoding="utf-8-sig"), re.M))
    for r in rows:
        if r["kind"] not in KINDS:
            raise SystemExit(f"data/units.csv {r['key']}: unknown kind '{r['kind']}' ({', '.join(KINDS)})")
        for per in hre_periods.ALL_PERIODS:
            for u in filter(None, (r.get(per) or "").split("/")):
                if u not in (sets if r["kind"] == "kingdom_set" else units):
                    raise SystemExit(f"data/units.csv {r['key']} {per}: unknown {'unit set' if r['kind'] == 'kingdom_set' else 'unit'} '{u}'")
    return rows


def _cell(rows_by_key, key, per, col):
    """Value of a csv column in a period: the period row, else the base row ('-' = empty)."""
    for want in (f"{key}.{per}", key):
        c = rows_by_key.get(want)
        if c is not None and col < len(c) and c[col] not in ("", "-"):
            return c[col]
    return ""


def apply_realms(table, hdr, source_key, vanilla_rows, rules, kingdom_keys):
    """Add LocalUnits to the period rows of realms.csv. source_key: HRE province -> the vanilla key whose
    rows it copied; vanilla_rows: vanilla realms.csv cells by row key. Returns a report line."""
    owner_col, units_col = hdr.index("Owner"), hdr.index("LocalUnits")
    by_key = {c[0]: c for c in table if c and c[0]}
    periods = hre_periods.PERIODS
    early = [per for per in periods if hre_periods.is_before(per, "E")]
    dropped = 0
    if early:
        for p in source_key:
            base = by_key[p]
            inherited = base[units_col] if units_col < len(base) and base[units_col] not in ("", "-") else ""
            if not inherited:
                continue
            for per in periods:
                row = by_key[f"{p}.{per}"]
                row += [""] * (units_col + 1 - len(row))
                units = [u for u in (row[units_col] if row[units_col] not in ("", "-") else inherited).split("/") if u]
                keep = [u for u in units if _fits(u, per)]
                dropped += len(units) - len(keep)
                row[units_col] = "/".join(keep)
            base[units_col] = ""
    add = {per: {} for per in periods}  # per -> province -> units to add
    for r in rules:
        for per in periods:
            units = [u for u in (r.get(per) or "").split("/") if u]
            if not units:
                continue
            if r["kind"] == "from_kingdom":
                targets = [p for p, v in source_key.items() if _cell(vanilla_rows, v, per, owner_col) == r["key"]]
                if not targets:
                    raise SystemExit(f"data/units.csv from_kingdom {r['key']}: owned nothing on the map in {per}")
            elif r["kind"] == "province":
                if r["key"] not in source_key:
                    raise SystemExit(f"data/units.csv province {r['key']}: not a province of the map")
                targets = [r["key"]]
            else:
                continue
            for p in targets:
                add[per].setdefault(p, [])
                add[per][p] += [u for u in units if u not in add[per][p]]
    changed = 0
    for per in periods:
        for p, units in add[per].items():
            row = by_key.get(f"{p}.{per}")
            if row is None:
                raise SystemExit(f"realms.csv has no {p}.{per} row")
            have = [u for u in _cell(by_key, p, per, units_col).split("/") if u]
            new = have + [u for u in units if u not in have]
            if new != have:
                row += [""] * (units_col + 1 - len(row))
                row[units_col] = "/".join(new)
                changed += 1
    wide = sorted({p for per in periods for p in add[per] if p in kingdom_keys})
    note = f" (kingdom-wide through the kingdom of the same key: {', '.join(wide)})" if wide else ""
    if early:
        note += f"; {dropped} inherited units dropped from the {'/'.join(early)} rows (UNIT_FROM)"
    return f"units: {changed} province period rows got extra LocalUnits{note}"


# cultures whose unit set (cultures.csv) is WesternUnitSet: men-at-arms and feudal knights instead of
# the generic heavy swordsmen and cavalry. Their kingdoms get EARLY_UNIT_SET in the early periods.
WESTERN_CULTURES = {"German", "Saxon", "Austrian", "English", "Flemish", "Dutch", "French", "Germanic", "WestGermanic"}
EARLY_UNIT_SET = "EuropeanUnitSet"


def apply_kingdoms(kout, khdr, rules):
    set_col, units_col, cult_col = khdr.index("UnitsSet"), khdr.index("Units"), khdr.index("Culture")
    by_key = {c[0]: c for c in kout if c and c[0] and not c[0].startswith("//")}
    early = [per for per in hre_periods.PERIODS if hre_periods.is_before(per, "E")]
    if early:
        # kingdom-wide Units (vanilla's Germany crossbowmen, England's longbowmen ...) fall back from the
        # base row like LocalUnits: move them into the period rows, the early rows keep what fits
        bases = [c for k, c in by_key.items() if "." not in k]
        for base in bases:
            k = base[0]
            inherited = base[units_col] if len(base) > units_col and base[units_col] not in ("", "-") else ""
            for per in hre_periods.PERIODS:
                row = by_key.get(f"{k}.{per}")
                if row is None:
                    if not inherited:
                        continue
                    row = by_key[f"{k}.{per}"] = ["-"] * len(khdr)  # carries only the moved Units
                    row[0] = f"{k}.{per}"
                    kout.insert(kout.index(base) + 1, row)
                row += [""] * (len(khdr) - len(row))
                if inherited:
                    units = [u for u in (row[units_col] if row[units_col] not in ("", "-") else inherited).split("/") if u]
                    row[units_col] = "/".join(u for u in units if _fits(u, per)) or ""
                if per in early and row[set_col] in ("", "-"):
                    cult = row[cult_col] if row[cult_col] not in ("", "-") else base[cult_col]
                    if cult in WESTERN_CULTURES:
                        row[set_col] = EARLY_UNIT_SET
            if inherited:
                base[units_col] = ""
    for r in rules:
        if r["kind"] != "kingdom_set":
            continue
        for per in hre_periods.PERIODS:
            if not r.get(per):
                continue
            row = by_key.get(f"{r['key']}.{per}")
            if row is None:
                raise SystemExit(f"data/units.csv kingdom_set {r['key']}: kingdoms.csv has no {r['key']}.{per} row")
            row[set_col] = r[per]
