"""Place vanilla mountain models on the HRE map's massifs, like vanilla's mountain ranges.

Usage: python tools/make_hre_mountains.py <mod_map_dir> [--dem <dem.nc>] [--preview <png>]

Reads terrain.pmt, rivers.bin and settlements.def (run after tools/import_hre.py, before the trees and
roads, which keep clear of the mountains) and the massifs of tools/make_hre_terrain.py (massif_mask,
from the same DEM), and writes mountains.bin for the URAH_HRE plugin, which builds them from vanilla's
prefabs (assets/europe/mountains/background mountains pack/prefabs/mountainNN.prefab) and stamps their
cores into the pathfinding as impassable (slope 100, like the game's PathDataBuilder.BuildMountains).

Vanilla: 992 meshes, prefabs ~85 local units footprint radius at scale ~0.19
(0.15-0.24; height scale 1.36x), overlapping in massifs (spacing 14-15) that cover 67 % of the Alps,
sunk so that ~3 of their ~11 units show. Here, balanced for the 3x more detailed map:
- high massifs (Alps, Tatra): spacing HIGH_SPACING; only the meshes in the heart of a massif (at least a
  footprint radius inside it) get an impassable core, so valleys and passes stay walkable
- summit ridges of lower ranges (Black Forest, Vosges, Harz, Ore Mountains, Sudetes, Dinaric Alps):
  sparser, decoration only (core 0)
- each mesh is sunk so that SHOW units of it stand above the ground at its centre, never above TOP_MAX

mountains.bin (little endian): "PMM2", int32 count, per mountain float32 x, z (world units), scale,
rotation (degrees), core radius (world units, 0 = walkable), base height (world units), int32 prefab
number. ("PMMT", the older format, has no base height.)
"""

from __future__ import annotations

import argparse
import gzip
import re
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).parent))
import urah_paths  # noqa: F401,E402  (puts URAH_shared on sys.path)
import hre_rivers  # noqa: E402

SIZE_X, SIZE_Z = 3750.0, 2625.0
CELL = 2.0               # = make_hre_terrain.MASSIF_CELL
HIGH_SPACING = 16.0      # world units between meshes on the high massifs ...
RIDGE_SPACING = 17.0     # ... and on the ridges of lower ranges
CENTER_INSET = 8.0       # mesh centres at least this far inside the massif
CLEAR_SETTLEMENT = 30.0  # no mountain this close to a castle or village
CLEAR_RIVER = 10.0
CORE = 0.69              # impassable core radius / footprint radius
MESH_RADIUS = 85.0       # prefab footprint radius in local units
VISIBLE_RADIUS = 0.65    # share of the footprint radius that stands above the ground (the meshes are sunk)
HEIGHT_SCALE = 1.3       # = MountainBuilder.HeightScale
SHOW = {2: 4.5, 1: 3.5}  # world units above the ground at the centre (vanilla ~3)
TOP_MAX = 30.0
PREFABS = {16: 239, 8: 176, 4: 115, 18: 111, 13: 100, 15: 64, 17: 38, 12: 34, 14: 22, 3: 20, 11: 19, 5: 17}
# prefab heights in local units (bundle europe, mesh bounds)
PREFAB_HEIGHT = {16: 42.8, 8: 39.5, 4: 49.0, 18: 38.4, 13: 52.7, 15: 54.8, 17: 43.4, 12: 50.9, 14: 49.4, 3: 50.0,
                 11: 40.5, 5: 50.5}
DEM = Path(__file__).resolve().parent.parent / "data" / "dem" / "hre_etopo.nc"


def read_mountains(path: Path):
    """[(x, z, scale, rotation, core radius, base height or nan, prefab)] from mountains.bin; [] if there is
    none."""
    if not path.exists():
        return []
    raw = path.read_bytes()
    n = struct.unpack_from("<i", raw, 4)[0]
    if raw[:4] == b"PMM2":
        return [struct.unpack_from("<6fi", raw, 8 + 28 * i) for i in range(n)]
    return [(*m[:5], float("nan"), m[5]) for m in (struct.unpack_from("<5fi", raw, 8 + 24 * i) for i in range(n))]


def mountain_mask(path: Path, shape, cell: float, which: str = "core", margin: float = 0.0):
    """Boolean [z, x] grid of cells under the mountains' cores, full footprints or the part of the
    footprint above the ground ("visible") (+ margin)."""
    gh, gw = shape
    mask = np.zeros(shape, bool)
    zz, xx = np.ogrid[:gh, :gw]
    for x, z, scale, _, core, _, _ in read_mountains(path):
        if which == "core" and core <= 0:
            continue
        r = (core if which == "core" else MESH_RADIUS * scale * (VISIBLE_RADIUS if which == "visible" else 1)) + margin
        i0, i1 = max(int((z - r) / cell), 0), min(int((z + r) / cell) + 1, gh)
        j0, j1 = max(int((x - r) / cell), 0), min(int((x + r) / cell) + 1, gw)
        sub = ((zz[i0:i1] + 0.5) * cell - z) ** 2 + ((xx[:, j0:j1] + 0.5) * cell - x) ** 2 <= r * r
        mask[i0:i1, j0:j1] |= sub
    return mask


def place(ok, spacing, rng):
    """Random points on the True cells of ok (CELL grid), at least spacing apart (dart throwing)."""
    ys, xs = np.nonzero(ok)
    bucket = spacing / np.sqrt(2)
    grid = {}
    out = []
    for i in rng.permutation(len(ys)):
        x, z = (xs[i] + rng.random()) * CELL, (ys[i] + rng.random()) * CELL
        bx, bz = int(x / bucket), int(z / bucket)
        if any(np.hypot(x - p[0], z - p[1]) < spacing
               for dx in (-2, -1, 0, 1, 2) for dz in (-2, -1, 0, 1, 2) for p in grid.get((bx + dx, bz + dz), ())):
            continue
        grid.setdefault((bx, bz), []).append((x, z))
        out.append((x, z))
    return out


def open_cores(d: Path, out: list) -> int:
    """Take out (core 0) the impassable cores that cut a province apart or separate two neighbours
    (tools/check_hre_reachability.py), one at a time, nearest to the blocked way first. Returns their
    number."""
    if not (d / "realms.bin").exists():
        return 0
    import check_hre_reachability as reach
    grid = reach.Grid(d)
    freed = 0
    for _ in range(200):
        cores = grid.cores(out)
        cut, split = grid.problems(cores)
        if not cut and not split:
            break
        for item in list(cut) + split:
            if isinstance(item, tuple):
                p, q = grid.world(grid.castles[item[0]]), grid.world(grid.castles[item[1]])
                realms = list(item)
            else:
                p, q = grid.cut_off(item, cores)
                realms = [item]
            x0, x1, z0, z1 = grid.window_world(realms)
            seg = q - p
            best, best_d = None, np.inf
            for k, m in enumerate(out):
                if m[4] <= 0 or not (x0 - m[4] <= m[0] <= x1 + m[4] and z0 - m[4] <= m[1] <= z1 + m[4]):
                    continue
                t = np.clip(np.dot(np.array(m[:2]) - p, seg) / max(np.dot(seg, seg), 1e-9), 0, 1)
                dist = np.hypot(*(np.array(m[:2]) - (p + t * seg))) - m[4]
                if dist < best_d:
                    best, best_d = k, dist
            if best is not None:
                out[best] = (*out[best][:4], 0.0, *out[best][5:])
                freed += 1
    return freed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mod_map", type=Path)
    ap.add_argument("--dem", type=Path, default=DEM)
    ap.add_argument("--preview", type=Path)
    args = ap.parse_args()
    d = args.mod_map
    rng = np.random.default_rng(17)

    raw = gzip.open(d / "terrain.pmt").read()
    res = struct.unpack("<i", raw[4:8])[0]
    max_h = struct.unpack("<f", raw[16:20])[0]
    hm = np.frombuffer(raw, "<u2", res * res, 20).reshape(res, res).astype(np.float32) * max_h / 65535
    gw, gh = int(SIZE_X / CELL), int(SIZE_Z / CELL)

    def ground(x, z):
        return ndimage.map_coordinates(hm, [np.asarray(z) / SIZE_Z * (res - 1), np.asarray(x) / SIZE_X * (res - 1)], order=1)

    import make_hre_terrain
    from koh2terrain import load_dem
    massif = make_hre_terrain.massif_mask(load_dem(args.dem))
    assert massif.shape == (gh, gw), "CELL must match make_hre_terrain.MASSIF_CELL"
    inside = {v: ndimage.distance_transform_edt(massif >= v) * CELL for v in (1, 2)}

    blocked = np.zeros((gh, gw), bool)
    text = (d / "settlements.def").read_text(encoding="utf-8")
    for x, z in re.findall(r"position = ([\d.]+), ([\d.]+)", text):
        blocked[min(int(float(z) / CELL), gh - 1), min(int(float(x) / CELL), gw - 1)] = True
    clear = ndimage.distance_transform_edt(~blocked) * CELL > CLEAR_SETTLEMENT
    if (d / "rivers.bin").exists():
        river = np.zeros((gh, gw), bool)
        for pts in hre_rivers.read_rivers(d / "rivers.bin"):
            river[np.clip((pts[:, 2] / CELL).astype(int), 0, gh - 1), np.clip((pts[:, 0] / CELL).astype(int), 0, gw - 1)] = True
        clear &= ndimage.distance_transform_edt(~river) * CELL > CLEAR_RIVER

    keys = np.array(list(PREFABS))
    weights = np.array(list(PREFABS.values()), np.float64)
    out = []
    for kind, spacing, mean_scale in ((2, HIGH_SPACING, 0.21), (1, RIDGE_SPACING, 0.19)):
        ok = (massif == kind) & (inside[kind] >= CENTER_INSET) & clear
        for x, z in place(ok, spacing, rng):
            scale = float(np.clip(rng.normal(mean_scale, 0.025), 0.15, 0.27))
            prefab = int(rng.choice(keys, p=weights / weights.sum()))
            radius = MESH_RADIUS * scale
            i, j = min(int(z / CELL), gh - 1), min(int(x / CELL), gw - 1)
            core = radius * CORE if kind == 2 and inside[2][i, j] >= radius else 0.0
            # ground under the middle of the mesh (the massif relief is rough: a low percentile)
            a = rng.random(24) * 2 * np.pi
            r = np.sqrt(rng.random(24)) * radius * 0.5
            g = float(np.percentile(ground(x + np.cos(a) * r, z + np.sin(a) * r), 30))
            height = PREFAB_HEIGHT[prefab] * scale * HEIGHT_SCALE
            base = min(g - (height - SHOW[kind]), TOP_MAX - height)
            out.append((x, z, scale, float(rng.random() * 360), core, base, prefab))
    freed = open_cores(d, out)
    with open(d / "mountains.bin", "wb") as f:
        f.write(b"PMM2" + struct.pack("<i", len(out)))
        for m in out:
            f.write(struct.pack("<6fi", *m))
    core_area = sum(np.pi * m[4] ** 2 for m in out)
    n_high = sum(1 for m in out if massif[min(int(m[1] / CELL), gh - 1), min(int(m[0] / CELL), gw - 1)] == 2)
    print(f"{len(out)} mountains ({n_high} on high massifs, {sum(1 for m in out if m[4] > 0)} with an impassable core) "
          f"on {(massif == 2).sum() * CELL ** 2 / 1e3:.0f}k units² of high massif and {(massif == 1).sum() * CELL ** 2 / 1e3:.0f}k "
          f"of ridges; impassable cores {core_area / 1e3:.0f}k units² ({freed} cores taken out so no province is cut "
          f"apart, tools/check_hre_reachability.py)")

    if args.preview:
        h = ground(*np.meshgrid((np.arange(gw) + 0.5) * CELL, (np.arange(gh) + 0.5) * CELL))
        img = np.stack([np.clip(h / 32 * 255, 0, 255)] * 3, -1).astype(np.uint8)
        im = Image.fromarray(img)
        from PIL import ImageDraw
        dr = ImageDraw.Draw(im)
        for x, z, scale, _, core, _, _ in out:
            r = MESH_RADIUS * scale / CELL
            dr.ellipse((x / CELL - r, z / CELL - r, x / CELL + r, z / CELL + r), outline=(160, 90, 40))
            if core > 0:
                dr.ellipse((x / CELL - core / CELL, z / CELL - core / CELL, x / CELL + core / CELL, z / CELL + core / CELL),
                           fill=(120, 60, 30))
        im.transpose(Image.FLIP_TOP_BOTTOM).save(args.preview)


if __name__ == "__main__":
    main()
