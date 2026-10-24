"""Generate the HRE map's roads: a net between nearby settlements, built like vanilla's AutoRoads.

Usage: python tools/make_hre_roads.py <mod_map_dir> [--out <dir>] [--radius <units>] [--preview <png>]

Reads terrain.pmt, rivers.bin, realms.bin, realms.def and settlements.def from <mod_map_dir> (run
after tools/import_hre.py) and writes the two files vanilla's AutoRoads / LinesBatching load (both
through the mod lookup):

  RoadsTrapezoidData.bin   float32 triples: per road the midpoints of its ~1 unit segments at terrain
                           height (WorldMap.GenerateRoadData), then (-1, -1, -1)
  RoadsSegmentData.bin     float32 x 6 per road: bounds extent, first float index, last float index
                           (terminator included), length, source and destination settlement id

Every two settlements (castle or village, any realm) closer than the radius are a candidate, laid
shortest first as least-cost paths on a 3-unit grid (slope penalty, no sea, river crossings
expensive). Every road laid makes its cells cheaper, so later roads join existing ones, and a
candidate whose route runs mostly along the net is dropped as redundant. Neighbouring castles the
net only links by a long detour get a direct road; a settlement no road reaches gets one to its
nearest settlement. The URAH_HRE plugin
also marks the road nodes in the pathfinding (faster travel, like vanilla roads).
"""

from __future__ import annotations

import argparse
import gzip
import re
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

sys.path.insert(0, str(Path(__file__).parent))
import hre_rivers  # noqa: E402
import urah_paths  # noqa: F401,E402  (puts URAH_shared on sys.path)
import koh2map  # noqa: E402

SIZE_X, SIZE_Z = 3750.0, 2625.0
WATER = 10.0
CELL = 3.0
RIVER_COST = 25.0      # extra cost of a river cell (crossings)
REUSE = 0.55           # cost factor of cells a road already uses
MAX_RANGE = 450.0      # world units a direct castle link's search reaches
RADIUS = 130.0         # settlements closer than this get a road candidate (vanilla AutoRoads: 100 on a
                       # map with ~1.3x denser settlements)
BAND = 10.0            # candidates are laid in distance bands; costs (road reuse) refresh per band
MIN_NEW_CELLS = 4      # a candidate is redundant when fewer of its cells are off the net than this ...
NEW_SHARE = 0.25       # ... or than this share of its length
DETOUR = 1.8           # neighbouring castles further apart on the net than this x straight get a direct road


def load_heights(pmt: Path):
    raw = gzip.open(pmt).read()
    res = struct.unpack("<i", raw[4:8])[0]
    max_h = struct.unpack("<f", raw[16:20])[0]
    hm = np.frombuffer(raw, "<u2", res * res, 20).reshape(res, res).astype(np.float32) * max_h / 65535
    return hm, res


def height_at(hm, res, x, z):
    return ndimage.map_coordinates(hm, [np.asarray(z) / SIZE_Z * (res - 1), np.asarray(x) / SIZE_X * (res - 1)], order=1)


def grid_graph(cost):
    """8-neighbour graph; edge weight = mean cell cost x step length."""
    h, w = cost.shape
    idx = np.arange(h * w).reshape(h, w)
    rows, cols, vals = [], [], []
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = idx[0:h - dy, max(0, -dx):w - max(0, dx)]
        b = idx[dy:h, max(0, dx):w - max(0, -dx)]
        ca = cost.ravel()[a.ravel()]
        cb = cost.ravel()[b.ravel()]
        wgt = (ca + cb) / 2 * np.hypot(dy, dx)
        ok = np.isfinite(wgt)
        rows.append(a.ravel()[ok])
        cols.append(b.ravel()[ok])
        vals.append(wgt[ok])
    r, c, v = np.concatenate(rows), np.concatenate(cols), np.concatenate(vals)
    return csr_matrix((np.concatenate([v, v]), (np.concatenate([r, c]), np.concatenate([c, r]))), shape=(h * w, h * w))


def path_cells(pred, src, dst):
    out = [dst]
    while out[-1] != src and out[-1] >= 0:
        out.append(pred[out[-1]])
    return out[::-1] if out[-1] == src else None


def smooth(pts, passes=3):
    pts = pts.astype(np.float64)
    for _ in range(passes):
        if len(pts) > 2:
            pts[1:-1] = (pts[:-2] + 2 * pts[1:-1] + pts[2:]) / 4
    return pts


def resample(pts, step=1.0):
    d = np.hypot(*np.diff(pts, axis=0).T)
    t = np.concatenate([[0], np.cumsum(d)])
    if t[-1] < step:
        return pts
    s = np.linspace(0, t[-1], int(t[-1] / step) + 1)
    return np.column_stack([np.interp(s, t, pts[:, 0]), np.interp(s, t, pts[:, 1])])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mod_map", type=Path)
    ap.add_argument("--out", type=Path, help="folder for the two .bin files (default: mod_map)")
    ap.add_argument("--radius", type=float, default=RADIUS)
    ap.add_argument("--preview", type=Path)
    args = ap.parse_args()
    d = args.mod_map
    out_dir = args.out or d

    hm, res = load_heights(d / "terrain.pmt")
    gw, gh = int(SIZE_X / CELL), int(SIZE_Z / CELL)
    X, Z = np.meshgrid((np.arange(gw) + 0.5) * CELL, (np.arange(gh) + 0.5) * CELL)
    h = height_at(hm, res, X, Z)
    gz, gx = np.gradient(ndimage.gaussian_filter(h, 0.7), CELL)
    slope = np.degrees(np.arctan(np.hypot(gx, gz)))
    cost = 1 + (slope / 12) ** 2 * 3
    cost[slope > 30] = 60
    cost[h <= WATER + 0.05] = np.inf  # land is at least WATER + 0.1 (make_hre_terrain), the game reads <= 10 as sea
    if (d / "rivers.bin").exists():
        river = np.zeros((gh, gw), bool)
        for pts in hre_rivers.read_rivers(d / "rivers.bin"):
            river[np.clip((pts[:, 2] / CELL).astype(int), 0, gh - 1), np.clip((pts[:, 0] / CELL).astype(int), 0, gw - 1)] = True
        cost[river] += RIVER_COST

    import make_hre_mountains  # roads go around the impassable mountain cores
    cost[make_hre_mountains.mountain_mask(d / "mountains.bin", cost.shape, CELL, "core", margin=2.0)] = np.inf

    # settlements and their realms
    text = (d / "settlements.def").read_text(encoding="utf-8")
    rmap = koh2map.read_realms_bin(d / "realms.bin")
    sites = []
    for sid, kind, x, z in re.findall(r"def (\d+) : (Castle|Settlement)\s*\{[^{}]*?position = ([\d.]+), ([\d.]+)", text):
        x, z = float(x), float(z)
        realm = int(rmap[min(int(x * 2048 / SIZE_X), 2047), min(int(z * 2048 / SIZE_Z), 2047)])
        sites.append(dict(id=int(sid), kind=kind, x=x, z=z, realm=realm,
                          cell=min(int(z / CELL), gh - 1) * gw + min(int(x / CELL), gw - 1)))
    # a settlement on a blocked cell (shore, mountain core margin) gets a passable 3 x 3 patch
    for s in sites:
        cz, cx = divmod(s["cell"], gw)
        patch = cost[max(cz - 1, 0):cz + 2, max(cx - 1, 0):cx + 2]
        patch[~np.isfinite(patch)] = 2.0
    castles = {s["realm"]: s for s in sites if s["kind"] == "Castle"}
    defs = koh2map.parse_realms_def(d / "realms.def")
    key_id = {name: i + 1 for i, (name, sea, _) in enumerate(n for n in defs if not n[1])}
    neigh = {}
    block = None
    for line in (d / "realms.def").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*def (\w+) : Realm", line)
        if m:
            block, mode = m.group(1), None
            neigh[block] = set()
        elif block and line.strip() == "neighbors":
            mode = "n"
        elif block and line.strip().startswith("neighbors_through_sea"):
            mode = None
        elif block and mode == "n" and re.match(r"^\s*\w+\s*$", line) and line.strip() not in ("{", "}"):
            neigh[block].add(line.strip())

    pairs = set()
    for a, ns in neigh.items():
        for b in ns:
            if a in key_id and b in key_id:
                pairs.add(tuple(sorted((key_id[a], key_id[b]))))

    # Like vanilla's AutoRoads: every two settlements (castle or village, any realm) closer than the
    # radius are a candidate, laid shortest first; a candidate whose route runs mostly along roads
    # already laid is redundant and dropped, so the roads merge into a net.
    roads = []
    road = np.zeros(gh * gw, bool)       # cells of laid roads
    near = np.zeros(gh * gw, bool)       # ... and the cells around them
    ring = [dz * gw + dx for dz in (-1, 0, 1) for dx in (-1, 0, 1)]

    def lay(src, dst, cells):
        cz, cx = np.divmod(np.array(cells), gw)
        pts = np.column_stack([(cx + 0.5) * CELL, (cz + 0.5) * CELL])
        pts[0], pts[-1] = (src["x"], src["z"]), (dst["x"], dst["z"])
        roads.append((src["id"], dst["id"], resample(smooth(resample(pts, CELL / 2)), 1.0)))
        cells = np.array(cells)
        road[cells] = True
        near[np.clip((cells[:, None] + np.array(ring)[None, :]).ravel(), 0, gh * gw - 1)] = True
        cost.ravel()[cells] = np.minimum(cost.ravel()[cells], cost.ravel()[cells] * REUSE)

    def route(graph, src, targets, reach):
        dist, pred = dijkstra(graph, indices=src["cell"], return_predecessors=True, limit=reach / CELL * 4)
        for t in targets:
            cells = path_cells(pred, src["cell"], t["cell"]) if np.isfinite(dist[t["cell"]]) else None
            yield t, (cells if cells and len(cells) >= 2 else None)

    pos = np.array([[s["x"], s["z"]] for s in sites])
    cand = []
    for i in range(len(sites)):
        dd = np.hypot(*(pos[i + 1:] - pos[i]).T)
        for j in np.nonzero(dd <= args.radius)[0]:
            cand.append((float(dd[j]), i, i + 1 + int(j)))
    cand.sort()
    redundant = 0
    for lo in np.arange(0, args.radius, BAND):
        band = [c for c in cand if lo <= c[0] < lo + BAND]
        if not band:
            continue
        graph = grid_graph(cost)
        by_src = {}
        for dd, i, j in band:
            by_src.setdefault(i, []).append(j)
        for i, js in by_src.items():
            for t, cells in route(graph, sites[i], [sites[j] for j in js], lo + BAND):
                if not cells:
                    continue
                if (~near[cells]).sum() < max(MIN_NEW_CELLS, NEW_SHARE * len(cells)):
                    redundant += 1
                    continue
                lay(sites[i], t, cells)
        print(f"  band {lo:.0f}-{lo + BAND:.0f}: {len(band)} candidates, {len(roads)} roads, {redundant} redundant")

    # neighbouring castles must be connected by the net without a long detour, else a direct road
    def net_graph():
        c = np.where(road, 1.0, np.inf).reshape(gh, gw)
        return grid_graph(c)

    graph, rgraph, added = grid_graph(cost), net_graph(), 0
    for a, b in sorted(pairs):
        if a not in castles or b not in castles:
            continue
        ca, cb = castles[a], castles[b]
        straight = np.hypot(ca["x"] - cb["x"], ca["z"] - cb["z"]) / CELL
        nd = dijkstra(rgraph, indices=ca["cell"], limit=straight * DETOUR)[cb["cell"]] if road[ca["cell"]] else np.inf
        if nd <= straight * DETOUR:
            continue
        for _, cells in route(graph, ca, [cb], MAX_RANGE):
            if cells:
                lay(ca, cb, cells)
                added += 1
    print(f"  castle links added: {added}")

    # a settlement no road reaches gets one to its nearest settlement
    graph, added = grid_graph(cost), 0
    for i, s in enumerate(sites):
        if road[s["cell"]]:
            continue
        dd = np.hypot(*(pos - pos[i]).T)
        dd[i] = np.inf
        for j in np.argsort(dd)[:4]:
            (_, cells), = route(graph, s, [sites[j]], dd[j] * 2)
            if cells:
                lay(s, sites[j], cells)
                added += 1
                break
    print(f"  links for unconnected settlements: {added}, still without road: "
          f"{sum(1 for s in sites if not road[s['cell']])}")

    trap, seg = [], []
    for src, dst, pts in roads:
        y = height_at(hm, res, pts[:, 0], pts[:, 1]) + 0.02
        p3 = np.column_stack([pts[:, 0], y, pts[:, 1]])
        start = len(trap) * 3
        mids = (p3[:-1] + p3[1:]) / 2
        trap.extend(mids.tolist())
        trap.append([-1.0, -1.0, -1.0])
        length = float(np.linalg.norm(np.diff(p3, axis=0), axis=1).sum())
        extent = float(np.linalg.norm((p3.max(0) - p3.min(0)) / 2))
        seg.append([extent, start, len(trap) * 3 - 1, length, src, dst])
    out_dir.mkdir(parents=True, exist_ok=True)
    np.asarray(trap, "<f4").tofile(out_dir / "RoadsTrapezoidData.bin")
    np.asarray(seg, "<f4").tofile(out_dir / "RoadsSegmentData.bin")
    total = sum(s[3] for s in seg)
    print(f"{len(seg)} roads, {total:.0f} units, {len(trap)} points")

    if args.preview:
        img = Image.new("RGB", (gw, gh), (70, 90, 120))
        a = np.array(img)
        a[h > WATER] = (200, 190, 160)
        img = Image.fromarray(a)
        dr = ImageDraw.Draw(img)
        for _, _, pts in roads:
            dr.line([(x / CELL, z / CELL) for x, z in pts[::3]], fill=(120, 60, 30), width=1)
        img.transpose(Image.FLIP_TOP_BOTTOM).save(args.preview)


if __name__ == "__main__":
    main()
