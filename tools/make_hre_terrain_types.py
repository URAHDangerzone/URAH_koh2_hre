"""Terrain types and climate zones for the HRE map (province features, AI, battle terrain).

Usage: python tools/make_hre_terrain_types.py <mod_map_dir>

Reads terrain.pmt, rivers.bin and trees.bin from <mod_map_dir> and writes terrain_types.bin and
climate_zones.bin (both loaded through the mod lookup): int32 width, height, then one byte per
8-unit tile, row = z. The vanilla files are 468 x 328 (TerrainTypes.tile_size = 8) followed by
leftovers of an older, larger version (the editor writes them with File.OpenWrite, no truncation).

Terrain type per tile, highest priority first like TerrainTypesBuilder (thresholds from
Defs/TerrainTypes.def): Mountains (high and steep), Ocean (90 % water), River (25 % river),
Forest (4+ trees), Hills (steep enough), Plains. Climate: Temperate; Mediterranean on the Ligurian
coast and in Provence; Nordic in southern Sweden.
"""

from __future__ import annotations

import argparse
import gzip
import struct
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).parent))
import hre_rivers  # noqa: E402
from make_hre_terrain import world_to_latlon  # noqa: E402

SIZE_X, SIZE_Z = 3750.0, 2625.0
WATER = 10.0
TILE = 8.0
PLAINS, HILLS, FOREST, RIVER, TOWN, LAKE, OCEAN, MOUNTAINS = range(8)
TROPICAL, DESERT, TEMPERATE, MEDITERRANEAN, NORDIC = range(5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mod_map", type=Path)
    args = ap.parse_args()
    d = args.mod_map
    w, h = int(SIZE_X // TILE), int(SIZE_Z // TILE)

    raw = gzip.open(d / "terrain.pmt").read()
    res = struct.unpack("<i", raw[4:8])[0]
    max_h = struct.unpack("<f", raw[16:20])[0]
    hm = np.frombuffer(raw, "<u2", res * res, 20).reshape(res, res).astype(np.float32) * max_h / 65535
    # 1-unit samples, then per-tile shares
    xs = (np.arange(w * 8) + 0.5) * TILE / 8
    zs = (np.arange(h * 8) + 0.5) * TILE / 8
    Z, X = np.meshgrid(zs / SIZE_Z * (res - 1), xs / SIZE_X * (res - 1), indexing="ij")
    hh = ndimage.map_coordinates(hm, [Z, X], order=1)
    gz, gx = np.gradient(hh, TILE / 8)
    slope = np.degrees(np.arctan(np.hypot(gx, gz)))

    def share(mask):
        return mask.reshape(h, 8, w, 8).mean(axis=(1, 3))

    water = share(hh <= WATER)
    steep = share(slope > 30)
    hilly = share(slope > 12)
    high = (hh.reshape(h, 8, w, 8).mean(axis=(1, 3)) > 22.5)

    river = np.zeros((h * 8, w * 8), bool)
    if (d / "rivers.bin").exists():
        for pts in hre_rivers.read_rivers(d / "rivers.bin"):
            half = float(pts[:, 3].mean()) * hre_rivers.VISIBLE_SHARE / 2
            for off in np.arange(-half, half + 0.01, 0.5):
                # mark samples across the river (perpendicular offsets are approximated by x and z)
                i = np.clip((pts[:, 2] / (TILE / 8)).astype(int), 0, h * 8 - 1)
                j = np.clip(((pts[:, 0] + off) / (TILE / 8)).astype(int), 0, w * 8 - 1)
                river[i, j] = True
                i2 = np.clip(((pts[:, 2] + off) / (TILE / 8)).astype(int), 0, h * 8 - 1)
                j2 = np.clip((pts[:, 0] / (TILE / 8)).astype(int), 0, w * 8 - 1)
                river[i2, j2] = True
    river_share = share(river)

    trees = np.zeros((h, w), np.int32)
    if (d / "trees.bin").exists():
        traw = (d / "trees.bin").read_bytes()
        n = struct.unpack_from("<i", traw, 4)[0]
        t = np.frombuffer(traw, dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("w", "<f4"), ("h", "<f4"),
                                       ("r", "<f4"), ("p", "u1")], count=n, offset=8)
        ti = np.clip((t["z"] * SIZE_Z / TILE).astype(int), 0, h - 1)
        tj = np.clip((t["x"] * SIZE_X / TILE).astype(int), 0, w - 1)
        np.add.at(trees, (ti, tj), 1)

    types = np.full((h, w), PLAINS, np.uint8)
    types[hilly >= 0.5] = HILLS
    types[trees >= 4] = FOREST
    types[river_share >= 0.25] = RIVER
    types[water >= 0.9] = OCEAN
    types[high & (steep >= 0.35)] = MOUNTAINS
    import make_hre_mountains
    cores = make_hre_mountains.mountain_mask(d / "mountains.bin", (h * 8, w * 8), TILE / 8, "core")
    types[share(cores) >= 0.5] = MOUNTAINS

    lat, lon = world_to_latlon((np.arange(w)[None, :] + 0.5) * TILE, (np.arange(h)[:, None] + 0.5) * TILE)
    lat, lon = np.broadcast_arrays(lat, lon)
    climate = np.full((h, w), TEMPERATE, np.uint8)
    climate[(lat < 44.9) & (lon > 4.0) & (lon < 12.5)] = MEDITERRANEAN
    climate[lat > 55.3] = NORDIC

    for name, grid in (("terrain_types.bin", types), ("climate_zones.bin", climate)):
        with open(d / name, "wb") as f:
            f.write(struct.pack("<2i", w, h))
            f.write(grid.tobytes())
    names = ["Plains", "Hills", "Forest", "River", "Town", "Lake", "Ocean", "Mountains"]
    counts = np.bincount(types.ravel(), minlength=8)
    print(f"terrain types {w}x{h}: " + ", ".join(f"{n} {c}" for n, c in zip(names, counts) if c))
    print("climate: " + ", ".join(f"{n} {c}" for n, c in zip(["Tropical", "Desert", "Temperate", "Mediterranean", "Nordic"],
                                                          np.bincount(climate.ravel(), minlength=5)) if c))


if __name__ == "__main__":
    main()
