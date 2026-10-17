"""Paint the political view's paper map (BigMap_WV, 8192x8192) for the HRE map's coastline.

Usage: python tools/paint_hre_bigmap.py <BigMap_WV.png> <mod_map_dir> [--preview <png>]

Reads <mod_map_dir>/terrain.pmt for the coastline and relief. Land and sea are vanilla's own paper
and sea textures (clean areas of BigMap_WV with the drawn lines filtered out, mirror-tiled), with
the coast effects measured on vanilla: an ink line ~10 px wide, land darkening over ~25 px and sea
darkening over ~30 px next to it. Mountains get a light relief shading and vanilla's own hand-drawn
peak glyphs (cut from its Alps and Carpathians), rivers from rivers.bin are thin blue-grey lines,
sea names are set in the game's PirataOne font. The frame band and the
compass rose of the vanilla map stay (transparent there).

Output: <mod_map_dir>/bigmap_tile_<i>_<j>.png (4 x 4 tiles of 2048 px, RGBA) and
bigmap_patches.txt, drawn over the texture by the URAH_HRE plugin (BigMapPatch).
"""

from __future__ import annotations

import argparse
import gzip
import struct
from pathlib import Path

import sys

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).parent))
Image.MAX_IMAGE_PIXELS = None
N = 8192
TILE = 2048
SIZE_X, SIZE_Z = 3750.0, 2625.0
WATER = 10.0

# vanilla measurements (image pixels)
INK = np.array([25, 30, 29], np.float32)
INK_HALF_WIDTH = 5.0
LAND_BAND, LAND_DARK = 25.0, 0.78   # land brightness 86 -> 110 over 25 px
SEA_BAND, SEA_DARK = 30.0, 0.73     # sea brightness 52 -> 71 over 30 px
FRAME = (84, 62, 84, 62)            # left, top, right, bottom band kept from vanilla
COMPASS = ((487, 750), 345)         # vanilla compass rose kept
# clean texture sources in vanilla (x0, y0, x1, y1)
LAND_SOURCE = (5400, 1300, 6900, 2700)
SEA_SOURCE = (200, 4300, 1400, 5800)


def heights_at_pixels(pmt: Path) -> np.ndarray:
    """Terrain height at every BigMap pixel centre (row 0 = north), float32."""
    raw = gzip.open(pmt).read()
    res = struct.unpack("<i", raw[4:8])[0]
    max_h = struct.unpack("<f", raw[16:20])[0]
    hm = np.frombuffer(raw, "<u2", res * res, 20).reshape(res, res).astype(np.float32) * max_h / 65535
    xs = (np.arange(N, dtype=np.float32) + 0.5) / N * (res - 1)
    out = np.empty((N, N), np.float32)
    for r0 in range(0, N, 512):
        rows = np.arange(r0, min(r0 + 512, N))
        zs = (1 - (rows + 0.5) / N) * (res - 1)
        zz, xx = np.meshgrid(zs.astype(np.float32), xs, indexing="ij")
        out[r0:r0 + len(rows)] = ndimage.map_coordinates(hm, [zz, xx], order=1)
    return out


def clean_texture(src: np.ndarray) -> np.ndarray:
    """Remove thin dark strokes (rivers, routes, seams) and dots: grey closing, then a light blur
    of what the closing changed so the mottling stays."""
    closed = np.stack([ndimage.grey_closing(src[..., c], size=(17, 17)) for c in range(3)], -1)
    return np.where(closed - src > 12, ndimage.uniform_filter(closed, size=(3, 3, 1)), src)


def flatten(src: np.ndarray) -> np.ndarray:
    """Remove the source's large stains (they repeat visibly when tiled), keep the fine grain."""
    low = ndimage.gaussian_filter(src, (120, 120, 0))
    return src / np.maximum(low, 1) * src.reshape(-1, 3).mean(0)


def varied_fill(src: np.ndarray, h: int, w: int, seed: int) -> np.ndarray:
    """Three mirror-tiled copies at different offsets, blended by smooth noise, times soft stains:
    no visible repeat at map scale."""
    sh, sw = src.shape[:2]
    rng = np.random.default_rng(seed)
    layers, weights = [], []
    for k in range(3):
        dy, dx = int(rng.integers(sh)), int(rng.integers(sw))
        layers.append(np.roll(mirrored_fill(src, h + sh, w + sw), (-dy, -dx), (0, 1))[:h, :w])
        weights.append(np.exp(1.5 * fractal((h, w), seed * 10 + k, 256)))
    total = sum(weights)
    out = sum(l * (wt / total)[..., None] for l, wt in zip(layers, weights))
    stains = 1 + 0.06 * fractal((h, w), seed * 10 + 7, 512) + 0.03 * fractal((h, w), seed * 10 + 8, 128)
    return out * stains[..., None]


def mirrored_fill(src: np.ndarray, h: int, w: int) -> np.ndarray:
    sh, sw = src.shape[:2]
    row = np.concatenate([src if i % 2 == 0 else src[:, ::-1] for i in range(w // sw + 1)], 1)[:, :w]
    full = np.concatenate([row if j % 2 == 0 else row[::-1] for j in range(h // sh + 1)], 0)[:h]
    return full


def smoothstep(x, a, b):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def fractal(shape, seed, scale):
    rng = np.random.default_rng(seed)
    small = rng.standard_normal((shape[0] // scale + 2, shape[1] // scale + 2)).astype(np.float32)
    small = ndimage.gaussian_filter(small, 1.0)
    return ndimage.zoom(small, scale, order=1)[: shape[0], : shape[1]] / (small.std() + 1e-6)


HATCH_SOURCES = [(3000, 3300, 4800, 4200), (4800, 2900, 6000, 3700)]  # vanilla Alps, Carpathians
HATCH_INK = np.array([40, 34, 27], np.float32)
FONT = Path(__file__).resolve().parent.parent / "data" / "fonts" / "PirataOne-Regular.ttf"  # data/fonts/README.txt
LABEL_INK = np.array([28, 32, 31], np.float32)
# (text, lat, lon, size px, rotation degrees)
SEA_LABELS = [
    ("North Sea", 54.9, 3.4, 130, 0),
    ("Baltic Sea", 55.25, 16.2, 120, 0),
    ("The Channel", 50.15, -0.6, 90, 12),
    ("Bay of Biscay", 45.6, -1.75, 80, 70),
    ("Adriatic Sea", 44.45, 13.15, 90, -38),
]


def latlon_px(lat, lon):
    from make_hre_terrain import world_to_latlon  # noqa: F401  (keeps the frame definition in one place)
    from make_hre_terrain import KM_PER_DEG_LON, KM_PER_UNIT, LAT0, LON0
    x = SIZE_X / 2 + (lon - LON0) * KM_PER_DEG_LON / KM_PER_UNIT
    z = SIZE_Z / 2 + (lat - LAT0) * 111.2 / KM_PER_UNIT
    return x / SIZE_X * N, (1 - z / SIZE_Z) * N


def hatch_stamps(vanilla: np.ndarray):
    """Single-peak glyphs cut from vanilla's hatched mountains: dark brownish strokes (rivers are
    bluish and dropped), grouped by proximity, as alpha masks."""
    stamps = []
    for x0, y0, x1, y1 in HATCH_SOURCES:
        c = vanilla[y0:y1, x0:x1].astype(np.float32)
        lum = c.mean(-1)
        bg = ndimage.grey_closing(lum, size=(15, 15))
        a = np.clip((bg - lum - 18) / 45, 0, 1)
        a[c[..., 2] > c[..., 0] + 4] = 0  # rivers and lakes
        groups, n = ndimage.label(ndimage.binary_dilation(a > 0.25, iterations=4))
        for k, sl in enumerate(ndimage.find_objects(groups)):
            hgt, wid = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
            if not (40 <= hgt <= 160 and 40 <= wid <= 200):
                continue
            m = (groups[sl] == k + 1) * a[sl]
            if m.sum() > 150:
                stamps.append(m.astype(np.float32))
    return stamps


def stamp_mountains(img, h, land, stamps, seed=5):
    """Place glyphs over high ground, denser the higher; back (north) first so front peaks overlap."""
    rng = np.random.default_rng(seed)
    # real ranges only: smoothed elevation, so the terrain's small hills get no glyphs
    smooth = ndimage.zoom(ndimage.gaussian_filter(h[::8, ::8], 5), 8, order=1)[:N, :N]
    weight = np.clip((smooth - 20.0) / 2.5, 0, 1) * land
    ys, xs = np.nonzero(weight[::8, ::8] > 0)
    ys, xs = ys * 8, xs * 8
    order = rng.permutation(len(ys))
    cell = 80
    occupied = np.zeros((N // cell + 1, N // cell + 1), bool)
    placed = []
    for i in order:
        y, x = int(ys[i]) + int(rng.integers(8)), int(xs[i]) + int(rng.integers(8))
        if y >= N or x >= N or rng.random() > weight[y, x] or occupied[y // cell, x // cell]:
            continue
        occupied[y // cell, x // cell] = True
        placed.append((y, x))
    for y, x in sorted(placed):
        st = stamps[int(rng.integers(len(stamps)))]
        if rng.random() < 0.5:
            st = st[:, ::-1]
        hh, ww = st.shape
        y0, x0 = y - hh, x - ww // 2  # glyph base on the point
        if y0 < 0 or x0 < 0 or x0 + ww > N:
            continue
        reg = img[y0:y, x0:x0 + ww].astype(np.float32)
        a = st[..., None] * land[y0:y, x0:x0 + ww, None]
        img[y0:y, x0:x0 + ww] = np.clip(reg * (1 - a) + HATCH_INK * a, 0, 255).astype(np.uint8)
    return len(placed)


RIVER_INK = np.array([72, 92, 94], np.float32)


def draw_rivers(img, land, path):
    """Rivers (tools/hre_rivers.py) as thin blue-grey lines like vanilla's, on land only."""
    from PIL import ImageDraw
    import hre_rivers
    layer = Image.new("L", (N, N), 0)
    draw = ImageDraw.Draw(layer)
    for pts in hre_rivers.read_rivers(path):
        for k in range(0, len(pts) - 1, 16):  # pieces, so the line widens downstream like the river
            seg = pts[k:k + 18:2]
            xy = [(float(x) / SIZE_X * N, (1 - float(z) / SIZE_Z) * N) for x, _, z, _ in seg]
            width = max(1, int(round(float(seg[:, 3].mean()) * hre_rivers.VISIBLE_SHARE * 0.9)))
            if len(xy) > 1:
                draw.line(xy, fill=215, width=width, joint="curve")
    a = (np.array(layer, np.float32) / 255 * land)[..., None]
    for r0 in range(0, N, 1024):
        sl = slice(r0, r0 + 1024)
        img[sl] = np.clip(img[sl] * (1 - a[sl]) + RIVER_INK * a[sl], 0, 255).astype(np.uint8)


def draw_labels(img):
    from PIL import ImageDraw, ImageFont
    for text, lat, lon, size, rot in SEA_LABELS:
        font = ImageFont.truetype(str(FONT), size)
        l, t, r, b = font.getbbox(text)
        layer = Image.new("L", (r - l + 20, b - t + 20), 0)
        ImageDraw.Draw(layer).text((10 - l, 10 - t), text, font=font, fill=235)
        layer = layer.rotate(rot, resample=Image.BICUBIC, expand=True)
        a = np.array(layer, np.float32)[..., None] / 255
        cx, cy = latlon_px(lat, lon)
        x0, y0 = int(cx - a.shape[1] / 2), int(cy - a.shape[0] / 2)
        if x0 < 0 or y0 < 0 or x0 + a.shape[1] > N or y0 + a.shape[0] > N:
            continue
        reg = img[y0:y0 + a.shape[0], x0:x0 + a.shape[1]].astype(np.float32)
        img[y0:y0 + a.shape[0], x0:x0 + a.shape[1]] = np.clip(reg * (1 - a) + LABEL_INK * a, 0, 255).astype(np.uint8)


PV_RES = 2048


def write_pv_ocean(out: Path, h: np.ndarray, land: np.ndarray) -> None:
    """Data textures of the political view's ocean (material "Political Ocean", BSG/PV_Ocean), which
    otherwise still draw vanilla's coastline: _CoastDistance ("coastline europe", grey = distance to
    the coastline, white from ~120 px) and _Heights ("heights", R = G = 3.48 * h + 28, B = 0.4 * h + 12,
    fitted on vanilla). Raw RGB24 bytes, row 0 = south like Unity textures: pv_coast.bin (grey) and
    pv_heights.bin, loaded by the URAH_HRE plugin."""
    step = N // PV_RES
    small_land = land[::step, ::step]
    small_h = h[::step, ::step]
    edge = small_land ^ ndimage.binary_erosion(small_land)
    dist = ndimage.distance_transform_edt(~edge)
    coast = np.clip(dist * 255 / 120, 0, 255).astype(np.uint8)
    rgb = np.stack([np.clip(3.482 * small_h + 28.08, 0, 255)] * 2 + [np.clip(0.399 * small_h + 12.36, 0, 255)], -1)
    (out / "pv_coast.bin").write_bytes(np.repeat(coast[::-1, :, None], 3, axis=2).tobytes())
    (out / "pv_heights.bin").write_bytes(rgb[::-1].astype(np.uint8).tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bigmap", type=Path)
    ap.add_argument("mod_map", type=Path)
    ap.add_argument("--preview", type=Path)
    args = ap.parse_args()

    vanilla = np.array(Image.open(args.bigmap).convert("RGB"))
    land_src = clean_texture(vanilla[LAND_SOURCE[1]:LAND_SOURCE[3], LAND_SOURCE[0]:LAND_SOURCE[2]].astype(np.float32))
    sea_src = clean_texture(vanilla[SEA_SOURCE[1]:SEA_SOURCE[3], SEA_SOURCE[0]:SEA_SOURCE[2]].astype(np.float32))
    land_src, sea_src = flatten(land_src), flatten(sea_src)
    # match the vanilla mean colours of plain land / sea
    land_src *= np.array([116, 107, 89], np.float32) / land_src.reshape(-1, 3).mean(0)
    sea_src *= np.array([66, 80, 77], np.float32) / sea_src.reshape(-1, 3).mean(0)

    h = heights_at_pixels(args.mod_map / "terrain.pmt")
    land = h > WATER
    d_land = ndimage.distance_transform_edt(land).astype(np.float32)   # land pixels: distance to sea
    d_sea = ndimage.distance_transform_edt(~land).astype(np.float32)   # sea pixels: distance to land
    signed = np.where(land, d_land, -d_sea)

    # relief: a soft hillshade in the mountains only (vanilla paper is flat; its mountains are hatched)
    gy, gx = np.gradient(ndimage.gaussian_filter(h, 16))
    shade = np.clip(1 + 4 * (gy - gx), 0.85, 1.1)
    relief = smoothstep(h, 22, 27)
    del gy, gx

    img = np.empty((N, N, 3), np.uint8)
    wobble = fractal((N, N), 3, 64) * 1.2  # irregular ink width like the hand-drawn vanilla coast
    land_tex = varied_fill(land_src, N, N, 1)
    sea_tex = varied_fill(sea_src, N, N, 2)
    for r0 in range(0, N, 1024):
        sl = slice(r0, r0 + 1024)
        lt, st = land_tex[sl], sea_tex[sl]
        s = signed[sl]
        land_f = (LAND_DARK + (1 - LAND_DARK) * smoothstep(s, INK_HALF_WIDTH, INK_HALF_WIDTH + LAND_BAND))
        land_f = land_f * (1 + (shade[sl] - 1) * relief[sl])
        sea_f = SEA_DARK + (1 - SEA_DARK) * smoothstep(-s, INK_HALF_WIDTH, INK_HALF_WIDTH + SEA_BAND)
        col = np.where((s > 0)[..., None], lt * land_f[..., None], st * sea_f[..., None])
        ink = np.clip(INK_HALF_WIDTH + wobble[sl] + 0.5 - np.abs(s), 0, 1)[..., None]
        col = col * (1 - ink) + INK * ink
        img[sl] = np.clip(col, 0, 255).astype(np.uint8)

    rivers = args.mod_map / "rivers.bin"
    if rivers.exists():
        draw_rivers(img, land, rivers)
    stamps = hatch_stamps(vanilla)
    placed = stamp_mountains(img, h, land, stamps)
    draw_labels(img)
    print(f"{len(stamps)} mountain glyphs from vanilla, {placed} placed; {len(SEA_LABELS)} sea labels")

    alpha = np.full((N, N), 255, np.uint8)
    l, t, r, b = FRAME
    alpha[:t] = alpha[N - b:] = 0
    alpha[:, :l] = alpha[:, N - r:] = 0
    (cx, cy), rad = COMPASS
    yy, xx = np.ogrid[:N, :N]
    alpha[(xx - cx) ** 2 + (yy - cy) ** 2 < rad ** 2] = 0

    out = args.mod_map
    write_pv_ocean(out, h, land)
    lines = []
    for j in range(N // TILE):
        for i in range(N // TILE):
            ys, xs = slice(j * TILE, (j + 1) * TILE), slice(i * TILE, (i + 1) * TILE)
            tile = np.dstack([img[ys, xs], alpha[ys, xs]])
            name = f"bigmap_tile_{i}_{j}.png"
            Image.fromarray(tile, "RGBA").save(out / name, optimize=False, compress_level=6)
            lines.append(f"{name} {i * TILE} {j * TILE}")
    (out / "bigmap_patches.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines)} tiles, land {land.mean() * 100:.1f}%")
    if args.preview:
        prev = np.where((alpha > 0)[..., None], img, vanilla)
        Image.fromarray(prev).resize((2048, 2048), Image.LANCZOS).save(args.preview)
        Image.fromarray(prev[1400:2600, 3200:4600]).save(args.preview.with_name(args.preview.stem + "_crop.png"))


if __name__ == "__main__":
    main()
