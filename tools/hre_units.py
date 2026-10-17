"""Recruitable units of the HRE map (used by import_hre.py), from the hand-made table data/units.csv.

A castle recruits its kingdom's unit set (kingdoms.csv UnitsSet, else the culture's in cultures.csv),
the kingdom's Units, the LocalUnits of the province whose key is the kingdom's key, and the
LocalUnits of its own province. Provinces copy their LocalUnits from vanilla (own key or donor), but
the HRE kingdoms have no Units of their own, so units vanilla gave a whole kingdom (Germany's
crossbowmen) are lost when that kingdom is split up. Rows of data/units.csv (E / M / L: units joined
with "/", empty = no change in that period):

  from_kingdom  key = vanilla kingdom: its units become LocalUnits of every province whose vanilla
                rows (own key or donor) that kingdom owned in the period
  province      key = HRE province: LocalUnits added in the period
  kingdom_set   key = kingdom: UnitsSet of its period row
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

PERIODS = ("E", "M", "L")
KINDS = ("from_kingdom", "province", "kingdom_set")


def load(hre: Path, defs: Path):
    rows = list(csv.DictReader(open(hre / "units.csv", encoding="utf-8")))
    units = set(re.findall(r"^def (\w+)\s*:", (defs / "units.def").read_text(encoding="utf-8-sig"), re.M))
    sets = set(re.findall(r"^def (\w+)\s*:\s*AvailableUnits", (defs / "AvailableUnits.def").read_text(encoding="utf-8-sig"), re.M))
    for r in rows:
        if r["kind"] not in KINDS:
            raise SystemExit(f"data/units.csv {r['key']}: unknown kind '{r['kind']}' ({', '.join(KINDS)})")
        for per in PERIODS:
            for u in filter(None, r[per].split("/")):
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
    add = {per: {} for per in PERIODS}  # per -> province -> units to add
    for r in rules:
        for per in PERIODS:
            units = [u for u in r[per].split("/") if u]
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
    for per in PERIODS:
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
    wide = sorted({p for per in PERIODS for p in add[per] if p in kingdom_keys})
    note = f" (kingdom-wide through the kingdom of the same key: {', '.join(wide)})" if wide else ""
    return f"units: {changed} province period rows got extra LocalUnits{note}"


def apply_kingdoms(kout, khdr, rules):
    set_col = khdr.index("UnitsSet")
    by_key = {c[0]: c for c in kout if c and c[0]}
    for r in rules:
        if r["kind"] != "kingdom_set":
            continue
        for per in PERIODS:
            if not r[per]:
                continue
            row = by_key.get(f"{r['key']}.{per}")
            if row is None:
                raise SystemExit(f"data/units.csv kingdom_set {r['key']}: kingdoms.csv has no {r['key']}.{per} row")
            row[set_col] = r[per]
