"""Check that the impassable mountain cores do not cut the HRE map's provinces apart.

Usage: python tools/check_hre_reachability.py [<mod_map_dir>] [--before <mod_map_dir>]
       (after tools/make_hre_mountains.py; default URAH_HRE/Maps/europe)

On the realm grid (realms.bin, 2048², ~1.8 x 1.3 units per cell) the land minus the cores of
mountains.bin (what FullPathfinding stamps impassable; rivers have crossings every few units and are
ignored) must leave
- every province's land one area around its castle (reports provinces where less than MIN_SHARE of
  it is reachable from the castle without leaving the province), and
- every pair of land neighbours connected through their two provinces.
Problems the land itself has (without any core) are not counted. With --before (an older build of the
same provinces), only the problems that build did not have are reported as new.
tools/make_hre_mountains.py uses Grid to take out the cores that cause problems.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).parent))
import urah_paths  # noqa: F401,E402  (puts URAH_shared on sys.path)
import koh2map  # noqa: E402

SIZE_X, SIZE_Z = 3750.0, 2625.0
MIN_SHARE = 0.97


class Grid:
    """Realm grid [z, x] with the castles, for reachability under a set of mountain cores."""

    def __init__(self, d: Path):
        self.rid = koh2map.read_realms_bin(d / "realms.bin").T
        self.n = self.rid.shape[0]
        self.cx, self.cz = SIZE_X / self.n, SIZE_Z / self.n
        self.castles = {}
        text = (d / "settlements.def").read_text(encoding="utf-8")
        for x, z in re.findall(r": Castle\s*\{[^{}]*?position = ([\d.]+), ([\d.]+)", text):
            i, j = min(int(float(z) / self.cz), self.n - 1), min(int(float(x) / self.cx), self.n - 1)
            self.castles[int(self.rid[i, j])] = (i, j)
        objs = ndimage.find_objects(np.clip(self.rid, 0, None))
        self.box = {r: objs[r - 1] for r in self.castles if r - 1 < len(objs) and objs[r - 1] is not None}
        pairs = set()
        for a, b in ((self.rid[:, :-1], self.rid[:, 1:]), (self.rid[:-1, :], self.rid[1:, :])):
            m = (a > 0) & (b > 0) & (a != b)
            pairs |= {(min(p, q), max(p, q)) for p, q in zip(a[m].tolist(), b[m].tolist())}
        self.pairs = sorted(p for p in pairs if p[0] in self.box and p[1] in self.box)

    def cores(self, mountains):
        """Boolean grid of the impassable cores of [(x, z, scale, rot, core, base, prefab)]."""
        out = np.zeros(self.rid.shape, bool)
        for x, z, _, _, core, _, _ in mountains:
            if core <= 0:
                continue
            i0, i1 = max(int((z - core) / self.cz), 0), min(int((z + core) / self.cz) + 1, self.n)
            j0, j1 = max(int((x - core) / self.cx), 0), min(int((x + core) / self.cx) + 1, self.n)
            zz = (np.arange(i0, i1) + 0.5)[:, None] * self.cz - z
            xx = (np.arange(j0, j1) + 0.5)[None, :] * self.cx - x
            out[i0:i1, j0:j1] |= zz ** 2 + xx ** 2 <= core ** 2
        return out

    def _window(self, realms):
        sl = [self.box[r] for r in realms]
        return (slice(min(s[0].start for s in sl), max(s[0].stop for s in sl)),
                slice(min(s[1].start for s in sl), max(s[1].stop for s in sl)))

    def _seed(self, mine, r, w):
        i, j = self.castles[r][0] - w[0].start, self.castles[r][1] - w[1].start
        if not mine[i, j]:  # the castle cell itself is blocked: its nearest free cell
            ii, jj = np.nonzero(mine)
            if not len(ii):
                return None
            k = np.argmin((ii - i) ** 2 + (jj - j) ** 2)
            i, j = ii[k], jj[k]
        return i, j

    def province_share(self, r, cores):
        """Share of province r's free land reachable from its castle."""
        w = self._window([r])
        mine = (self.rid[w] == r) & ~cores[w]
        seed = self._seed(mine, r, w)
        if seed is None:
            return 0.0
        labels, _ = ndimage.label(mine)
        return (labels == labels[seed]).sum() / max(mine.sum(), 1)

    def pair_connected(self, a, b, cores):
        w = self._window([a, b])
        both = ((self.rid[w] == a) | (self.rid[w] == b)) & ~cores[w]
        sa, sb = self._seed(both & (self.rid[w] == a), a, w), self._seed(both & (self.rid[w] == b), b, w)
        if sa is None or sb is None:
            return False
        labels, _ = ndimage.label(both)
        return labels[sa] == labels[sb]

    def problems(self, cores):
        """({province: reachable share}, [neighbour pairs not connected]) caused by the cores."""
        if not hasattr(self, "_land_ok"):  # what the land allows without any core
            none = np.zeros_like(cores)
            self._land_ok = ({r for r in self.box if self.province_share(r, none) >= MIN_SHARE},
                             {p for p in self.pairs if self.pair_connected(*p, none)})
        cut = {}
        for r in self._land_ok[0]:
            s = self.province_share(r, cores)
            if s < MIN_SHARE:
                cut[r] = s
        split = [p for p in self.pairs if p in self._land_ok[1] and not self.pair_connected(*p, cores)]
        return cut, split

    def world(self, ij):
        return np.array([(ij[1] + 0.5) * self.cx, (ij[0] + 0.5) * self.cz])

    def cut_off(self, r, cores):
        """World x, z of the castle of r and of the middle of its land the castle cannot reach."""
        w = self._window([r])
        mine = (self.rid[w] == r) & ~cores[w]
        seed = self._seed(mine, r, w)
        labels, _ = ndimage.label(mine)
        ii, jj = np.nonzero(mine & (labels != labels[seed]))
        far = (w[0].start + ii.mean(), w[1].start + jj.mean())
        return self.world(self.castles[r]), self.world(far)

    def window_world(self, realms):
        w = self._window(realms)
        return w[1].start * self.cx, w[1].stop * self.cx, w[0].start * self.cz, w[0].stop * self.cz


def main() -> None:
    import make_hre_mountains
    ap = argparse.ArgumentParser()
    ap.add_argument("mod_map", type=Path, nargs="?", default=urah_paths.REPO / "URAH_HRE/Maps/europe")
    ap.add_argument("--before", type=Path)
    args = ap.parse_args()
    names = {i: k for i, (k, _, _) in enumerate(koh2map.parse_realms_def(args.mod_map / "realms.def"), 1)}

    def run(d):
        g = Grid(d)
        return g.problems(g.cores(make_hre_mountains.read_mountains(d / "mountains.bin")))

    cut, split = run(args.mod_map)
    old_cut, old_split = run(args.before) if args.before else ({}, [])
    new_cut = {r: s for r, s in cut.items() if r not in old_cut}
    new_split = [p for p in split if p not in old_split]
    print(f"provinces with land cut off from the castle by mountain cores: {len(cut)} ({len(new_cut)} new)")
    for r, s in sorted(cut.items(), key=lambda t: t[1]):
        print(f"  {names.get(r, r)}: {s * 100:.0f} % reachable{' (new)' if r in new_cut else ''}")
    print(f"land neighbours not connected through their two provinces: {len(split)} ({len(new_split)} new)")
    for a, b in split:
        print(f"  {names.get(a, a)} - {names.get(b, b)}{' (new)' if (a, b) in new_split else ''}")
    if new_cut or new_split:
        sys.exit(1)


if __name__ == "__main__":
    main()
