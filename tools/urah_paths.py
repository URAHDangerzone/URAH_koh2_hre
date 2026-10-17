"""Paths of this mod repo and of the URAH_shared python modules, which it puts on sys.path.

URAH_shared is the workspace's shared/ when this repo sits in koh2-knowledge/mods/, else the
submodule external/URAH_shared. VANILLA is the game's data folder as shipped (Maps, Defs, Texts):
$KOH2_VANILLA, else KoH2Data/Sovereign in the workspace.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKSPACE = REPO.parent.parent
SHARED = next((p for p in (WORKSPACE / "shared", REPO / "external" / "URAH_shared") if (p / "python").is_dir()), None)
if SHARED is None:
    raise SystemExit("URAH_shared not found - run: git submodule update --init")
if str(SHARED / "python") not in sys.path:
    sys.path.insert(0, str(SHARED / "python"))
VANILLA = Path(os.environ.get("KOH2_VANILLA", WORKSPACE / "KoH2Data" / "Sovereign"))
