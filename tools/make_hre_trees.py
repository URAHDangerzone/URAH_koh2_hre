"""Generate the HRE map's forests (tree instances for the campaign terrain).

Usage: python tools/make_hre_trees.py <mod_map_dir> [--preview <png>]

Reads <mod_map_dir>/terrain.pmt and settlements.def (tools/import_hre.py) and writes trees.bin, which
the URAH_HRE plugin loads into TerrainData.treeInstances instead of the vanilla trees.

Calibrated on the vanilla trees of the same regions (europe TerrainData, 488k instances):
    region              trees / unit²   forest cover (4-unit cells)   trees per forest cell
    north German plain     0.07              25 %                         4.6
    German uplands         0.11              38 %                         4.5
    Alps                   0.12              45 %                         4.3
and their prototype mix (indices into the terrain's tree prototypes, see TREES below).

trees.bin (little endian): "PMTR", int32 count, then per tree float32 x, y, z (normalized terrain
coordinates, y = height / terrain height), float32 width scale, height scale, rotation, uint8 prototype.
"""

from __future__ import annotations

import argparse
import gzip
import re
import struct
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

SIZE_X, SIZE_Z = 3750.0, 2625.0
WATER = 10.0
CELL = 4.0  # world units

# terrain tree prototypes (WV_*): index -> name
CONIFER, POPLAR, TREE1, TREE2, TREE3, BIRCH, CONIFER_A, BUSH1 = 1, 2, 3, 4, 5, 6, 12, 13
# prototype mix per zone, from vanilla (share of trees)
MIX = {
    "plain": {TREE2: 0.66, TREE1: 0.19, CONIFER: 0.07, CONIFER_A: 0.03, POPLAR: 0.02, BIRCH: 0.03},
    "upland": {CONIFER_A: 0.32, TREE1: 0.24, TREE2: 0.23, BUSH1: 0.12, CONIFER: 0.05, TREE3: 0.03, POPLAR: 0.01},
    "alpine": {CONIFER_A: 0.41, CONIFER: 0.36, TREE1: 0.18, BUSH1: 0.05},
}
FOREST_DENSITY = 3.6 / CELL ** 2   # trees per unit² inside forest (vanilla 4.5, thinned for frame rate)
SCATTER_DENSITY = 0.006            # lone trees per unit² outside forest
TREE_LINE = (24.5, 26.5)           # smoothed game height where forest fades out (~1900 m plus relief)


def load_heights(pmt: Path):
    raw = gzip.open(pmt).read()
    res = struct.unpack("<i", raw[4:8])[0]
    max_h = struct.unpack("<f", raw[16:20])[0]
    hm = np.frombuffer(raw, "<u2", res * res, 20).reshape(res, res).astype(np.float32) * max_h / 65535
    return hm, res, max_h


def height_at(hm, res, x, z):
    return ndimage.map_coordinates(hm, [z / SIZE_Z * (res - 1), x / SIZE_X * (res - 1)], order=1)


def fractal(shape, seed, scales, weights):
    rng = np.random.default_rng(seed)
    total = np.zeros(shape, np.float32)
    for s, w in zip(scales, weights):
        n = ndimage.gaussian_filter(rng.standard_normal(shape).astype(np.float32), s / 3)
        total += w * n / (n.std() + 1e-9)
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mod_map", type=Path)
    ap.add_argument("--preview", type=Path)
    args = ap.parse_args()
    rng = np.random.default_rng(11)

    hm, res, max_h = load_heights(args.mod_map / "terrain.pmt")
    gw, gh = int(SIZE_X / CELL), int(SIZE_Z / CELL)
    cx = (np.arange(gw) + 0.5) * CELL
    cz = (np.arange(gh) + 0.5) * CELL
    X, Z = np.meshgrid(cx, cz)
    h = height_at(hm, res, X, Z)  # [z, x] cells
    hs = ndimage.gaussian_filter(h, 6)
    gz, gx = np.gradient(h, CELL)
    slope = np.degrees(np.arctan(np.hypot(gx, gz)))
    land = h > WATER + 0.3
    coast = ndimage.distance_transform_edt(land) * CELL

    # forest cover target by zone: 25% on the plain, 38% in the uplands, 52% in the mountains
    cover = np.interp(hs, [12, 16, 20], [0.25, 0.38, 0.52])
    cover *= np.clip((TREE_LINE[1] - hs) / (TREE_LINE[1] - TREE_LINE[0]), 0, 1)
    noise = fractal(h.shape, 3, scales=(20, 8, 3), weights=(0.55, 0.3, 0.15))
    # threshold per cell so the local share of forest matches the target cover
    from scipy.special import ndtri
    forest = noise > ndtri(1 - np.clip(cover, 1e-3, 1 - 1e-3)) * noise.std()
    forest &= land & (slope < 35) & (coast > 3)

    # keep settlements clear
    text = (args.mod_map / "settlements.def").read_text(encoding="utf-8")
    sites = np.zeros_like(land)
    radius = np.zeros(h.shape, np.float32)
    for kind, x, z in re.findall(r": (Castle|Settlement)\s*\{[^{}]*?position = ([\d.]+), ([\d.]+)", text):
        i, j = min(int(float(x) / CELL), gw - 1), min(int(float(z) / CELL), gh - 1)
        sites[j, i] = True
        radius[j, i] = max(radius[j, i], 12.0 if kind == "Castle" else 6.0)
    dist, (iy, ix) = ndimage.distance_transform_edt(~sites, return_indices=True)
    jitter = 1 + 0.25 * fractal(h.shape, 9, scales=(3,), weights=(1,))
    clear = dist * CELL < radius[iy, ix] * jitter
    rivers_bin = args.mod_map / "rivers.bin"
    if rivers_bin.exists():  # river beds and banks stay open
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        import hre_rivers
        half = np.zeros(clear.shape, np.float32)  # visible half width of the water, plus the bank
        for pts in hre_rivers.read_rivers(rivers_bin):
            i = np.clip((pts[:, 0] / CELL).astype(int), 0, gw - 1)
            j = np.clip((pts[:, 2] / CELL).astype(int), 0, gh - 1)
            np.maximum.at(half, (j, i), pts[:, 3] * hre_rivers.VISIBLE_SHARE / 2 + 1.5)
        rd, (ri, rj) = ndimage.distance_transform_edt(half == 0, return_indices=True)
        clear |= rd * CELL < half[ri, rj]
    import make_hre_mountains  # no trees in the mountain models (their sunk outer flanks are ground: trees, as in vanilla)
    clear |= make_hre_mountains.mountain_mask(args.mod_map / "mountains.bin", clear.shape, CELL, "visible")
    forest &= ~clear
    scatter = land & ~forest & ~clear & (slope < 30) & (coast > 2) & (cover > 0)

    # trees per cell (Poisson), positions uniform inside the cell
    counts = rng.poisson(np.where(forest, FOREST_DENSITY, np.where(scatter, SCATTER_DENSITY, 0)) * CELL ** 2)
    jj, ii = np.nonzero(counts)
    reps = counts[jj, ii]
    tz = (np.repeat(jj, reps) + rng.random(reps.sum())) * CELL
    tx = (np.repeat(ii, reps) + rng.random(reps.sum())) * CELL
    th = height_at(hm, res, tx, tz)
    ok = th > WATER + 0.2
    tx, tz, th = tx[ok], tz[ok], th[ok]
    n = len(tx)

    # prototype by zone (blend plain -> upland -> alpine with the smoothed height)
    hz = ndimage.map_coordinates(hs, [tz / CELL - 0.5, tx / CELL - 0.5], order=1)
    zone_w = np.stack([np.clip((16 - hz) / 3, 0, 1), np.clip(1 - np.abs(hz - 17.5) / 3, 0, 1), np.clip((hz - 18.5) / 2.5, 0, 1)])
    zone = np.argmax(zone_w + 0.3 * rng.random(zone_w.shape), axis=0)
    proto = np.zeros(n, np.uint8)
    for zi, name in enumerate(("plain", "upland", "alpine")):
        m = zone == zi
        keys = np.array(list(MIX[name]))
        p = np.array(list(MIX[name].values()))
        proto[m] = rng.choice(keys, m.sum(), p=p / p.sum())

    width = np.clip(rng.normal(1.0, 0.13, n), 0.7, 1.3).astype(np.float32)
    rot = (rng.random(n) * 2 * np.pi).astype(np.float32)
    data = np.zeros(n, dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("w", "<f4"), ("h", "<f4"),
                              ("r", "<f4"), ("p", "u1")])
    data["x"], data["y"], data["z"] = tx / SIZE_X, th / max_h, tz / SIZE_Z
    data["w"], data["h"], data["r"], data["p"] = width, width, rot, proto
    with open(args.mod_map / "trees.bin", "wb") as f:
        f.write(b"PMTR" + struct.pack("<i", n))
        f.write(data.tobytes())
    landcells = land.sum()
    print(f"{n} trees, {n / (landcells * CELL ** 2):.3f} per land unit², forest on {forest.sum() / landcells:.0%} of land; "
          + ", ".join(f"{k}: {(proto == k).mean():.0%}" for k in sorted(set(proto.tolist()))))

    if args.preview:
        img = np.zeros((gh, gw, 3), np.uint8)
        img[land] = (200, 190, 160)
        img[~land] = (70, 90, 120)
        col = {CONIFER: (20, 70, 40), CONIFER_A: (30, 90, 50), TREE1: (70, 120, 40), TREE2: (90, 140, 50),
               TREE3: (110, 140, 60), BUSH1: (140, 150, 80), POPLAR: (120, 160, 60), BIRCH: (160, 170, 110)}
        pi_, pj = (tx / CELL).astype(int).clip(0, gw - 1), (tz / CELL).astype(int).clip(0, gh - 1)
        for k, c in col.items():
            m = proto == k
            img[pj[m], pi_[m]] = c
        Image.fromarray(img[::-1]).save(args.preview)


if __name__ == "__main__":
    main()
