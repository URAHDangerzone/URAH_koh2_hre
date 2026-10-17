"""Propose provinces for the HRE map from real administrative districts.

Usage: python tools/propose_provinces.py <terrain.pmt> <out_dir> [--core-km2 N] [--edge-km2 N]

Inputs (data/geo/, see README.txt there):
  nuts3_2021.geojson   Eurostat GISCO NUTS 2021 level 3, 1:3M, EPSG:4326 (© EuroGeographics)
  ne_admin1.geojson    Natural Earth 1:10m admin-1 (public domain), for countries without NUTS

Everything is rasterized on the pathfinding grid: 3750 x 2625 pixels, 1 pixel = 1 world unit
(~0.51 km), so the image has the map's real aspect ratio. Land comes from terrain.pmt.

Districts are merged into provinces of a target area inside their NUTS-2 region (HRE core, small
provinces) or NUTS-1 region / country (edges, large provinces): k seeds per region, then contiguous
growth over the district adjacency graph. The result is a proposal to correct by hand.

Outputs:
  provinces.png        one RGB colour per province, sea black; row 0 = north. Edit this one
  provinces.csv        colour, proposed name, region, country, tier, districts, area
  provinces_preview.png  relief, borders and names, for reviewing
  reference.png        relief + coastline + country borders, a background layer for painting
"""

from __future__ import annotations

import argparse
import colorsys
import heapq
import csv
import gzip
import json
import struct
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from make_hre_terrain import KM_PER_DEG_LON, KM_PER_UNIT, LAT0, LON0, SIZE_X, SIZE_Z, WATER

W, H = int(SIZE_X), int(SIZE_Z)
KM2_PER_PX = KM_PER_UNIT ** 2
GEO = Path(__file__).resolve().parent.parent / "data" / "geo"

# NUTS prefixes of the HRE core (around 1250: Germany, Low Countries, Bohemia / Moravia / Silesia,
# Austria, Switzerland, Alsace / Lorraine / Franche-Comté / Savoy, imperial Italy, Pomerania)
CORE = ("DE", "AT", "CH", "LI", "CZ", "NL", "BE", "LU", "SI",
        "FRF1", "FRF3", "FRC2", "FRK2",
        "ITC", "ITH", "ITI1",
        "PL42", "PL43", "PL51", "PL52", "PL22")
# one name for bilingual districts: the language of the place in the HRE period
NAME_OVERRIDES = {
    "ITH10": "Bozen",                 # Bolzano-Bozen
    "ITC20": "Aosta",                 # Valle d'Aosta/Vallée d'Aoste
    "BE100": "Brussel",               # Arr. de Bruxelles-Capitale/Arr. Brussel-Hoofdstad
    "AT126": "Wiener Umland Nord",    # Wiener Umland/Nordteil
    "AT127": "Wiener Umland Süd",     # Wiener Umland/Südteil
    "CH025": "Delémont",              # canton Jura, same name as the French département
}

DEFER = 1e6  # added to the growth cost of districts a full group passes on

NE_COUNTRIES = ("BLR", "UKR", "RUS", "BIH", "MDA", "XKX")  # not covered by NUTS 2021 (Serbia and Montenegro are)


def lonlat_to_px(lon, lat):
    x = SIZE_X / 2 + (np.asarray(lon) - LON0) * KM_PER_DEG_LON / KM_PER_UNIT
    z = SIZE_Z / 2 + (np.asarray(lat) - LAT0) * 111.2 / KM_PER_UNIT
    return x, (H - 1) - z  # image column, row (north up)


def load_land(pmt: Path) -> np.ndarray:
    """Land mask on the pixel grid (row 0 = north) from the terrain heights."""
    raw = gzip.open(pmt).read()
    res = struct.unpack("<i", raw[4:8])[0]
    max_h = struct.unpack("<f", raw[16:20])[0]
    heights = np.frombuffer(raw, "<u2", res * res, 20).reshape(res, res).astype(np.float32) * max_h / 65535
    zs = (np.arange(H) + 0.5) * (res - 1) / SIZE_Z
    xs = (np.arange(W) + 0.5) * (res - 1) / SIZE_X
    h = ndimage.map_coordinates(heights, np.meshgrid(zs, xs, indexing="ij"), order=1)
    return (h > WATER)[::-1]


def polygons(geometry):
    if geometry["type"] == "Polygon":
        yield geometry["coordinates"][0]
    elif geometry["type"] == "MultiPolygon":
        for poly in geometry["coordinates"]:
            yield poly[0]


def load_districts():
    """(id, name, country, region keys, rings) for every district touching the frame."""
    out = []
    for f in json.load(open(GEO / "nuts3_2021.geojson", encoding="utf-8"))["features"]:
        p = f["properties"]
        nuts = p["NUTS_ID"]
        out.append(dict(code=nuts, name=NAME_OVERRIDES.get(nuts, p["NAME_LATN"]), country=p["CNTR_CODE"],
                        core=nuts.startswith(CORE), nuts2=nuts[:4], nuts1=nuts[:3],
                        rings=list(polygons(f["geometry"]))))
    for f in json.load(open(GEO / "ne_admin1.geojson", encoding="utf-8"))["features"]:
        p = f["properties"]
        if p["adm0_a3"] not in NE_COUNTRIES:
            continue
        code = p.get("iso_3166_2") or p["name"]
        out.append(dict(code=code, name=p["name"], country=p["adm0_a3"], core=False,
                        nuts2=code, nuts1=p["adm0_a3"], rings=list(polygons(f["geometry"]))))
    return out


def rasterize(districts, land):
    """District index + 1 per land pixel (0 = sea). Bigger districts first so enclaves stay on top."""
    img = Image.new("I", (W, H), 0)
    draw = ImageDraw.Draw(img)
    order = []
    for i, d in enumerate(districts):
        rings = []
        for ring in d["rings"]:
            ring = np.asarray(ring)
            x, y = lonlat_to_px(ring[:, 0], ring[:, 1])
            if x.max() < -50 or x.min() > W + 50 or y.max() < -50 or y.min() > H + 50:
                continue
            rings.append(list(zip(x.tolist(), y.tolist())))
        if rings:
            area = sum(abs(np.trapezoid(np.array(r)[:, 1], np.array(r)[:, 0])) for r in rings)
            order.append((area, i, rings))
    for _, i, rings in sorted(order, key=lambda t: -t[0]):
        for r in rings:
            if len(r) >= 3:
                draw.polygon(r, fill=i + 1)
    ids = np.array(img, dtype=np.int32)
    # land the districts miss (coastline differences) goes to the nearest district
    missing = land & (ids == 0)
    if missing.any():
        _, (iy, ix) = ndimage.distance_transform_edt(ids == 0, return_indices=True)
        ids[missing] = ids[iy[missing], ix[missing]]
    ids[~land] = 0
    return ids


MERGE_FACTOR = 0.5  # core regions below this many target areas join a neighbour


def merge_small(regions, area, adj, target):
    """Core regions much smaller than a province (Berlin, Hamburg, Vienna, Prague, ...) join the
    neighbouring region they share the most borders with."""
    region_of = {m: key for key, ms in regions.items() for m in ms}
    merged = 0
    for key in sorted(regions, key=lambda k: sum(area[m] for m in regions[k])):
        ms = regions.get(key)
        if not ms or key[0] != "core" or sum(area[m] for m in ms) >= MERGE_FACTOR * target:
            continue
        links = defaultdict(int)
        for m in ms:
            for n in adj[m]:
                if region_of.get(n, key) != key:
                    links[region_of[n]] += 1
        if not links:
            continue
        dest = max(links, key=lambda k: (k[0] == "core", links[k]))
        regions[dest].extend(ms)
        for m in ms:
            region_of[m] = dest
        del regions[key]
        merged += 1
    return merged


TINY_FACTOR = 0.4  # groups below this many target areas join the neighbour they share most borders with


def absorb_tiny(groups, area, adj, min_area):
    """Fold groups that ended up far below the target (a city district on its own) into a neighbour."""
    while True:
        size = defaultdict(float)
        for m, g in groups.items():
            size[g] += area[m]
        if len(size) < 2:
            return groups
        g = min(size, key=size.get)
        if size[g] >= min_area:
            return groups
        links = defaultdict(int)
        for m, gm in groups.items():
            if gm == g:
                for n in adj[m]:
                    if n in groups and groups[n] != g:
                        links[groups[n]] += 1
        if not links:
            return groups
        dest = max(links, key=links.get)
        groups = {m: (dest if gm == g else gm) for m, gm in groups.items()}


def adjacency(ids):
    pairs = set()
    for a, b in ((ids[:, :-1], ids[:, 1:]), (ids[:-1, :], ids[1:, :])):
        m = (a != b) & (a > 0) & (b > 0)
        for p, q in set(zip(a[m].tolist(), b[m].tolist())):
            pairs.add((min(p, q), max(p, q)))
    adj = defaultdict(set)
    for p, q in pairs:
        adj[p].add(q)
        adj[q].add(p)
    return adj


def partition(members, area, centroid, adj, k):
    """Split districts (raster ids) into k contiguous groups: area-weighted k-means++ seeds, then
    Dijkstra growth over the adjacency graph (edge cost = centroid distance), each group capped
    near the target area so the growth stays balanced."""
    members = list(members)
    if k <= 1 or len(members) <= 1:
        return {m: 0 for m in members}
    k = min(k, len(members))
    pts = np.array([centroid[m] for m in members])
    wts = np.array([area[m] for m in members], np.float64)
    rng = np.random.default_rng(len(members))
    seeds = [int(np.argmax(wts))]
    for _ in range(1, k):
        d2 = np.min(((pts[:, None, :] - pts[seeds][None, :, :]) ** 2).sum(-1), axis=1) * wts
        seeds.append(int(rng.choice(len(members), p=d2 / d2.sum())) if d2.sum() > 0 else 0)
    # a few Lloyd steps on centroids
    for _ in range(8):
        lab = np.argmin(((pts[:, None, :] - pts[seeds][None, :, :]) ** 2).sum(-1), axis=1)
        centers = np.array([np.average(pts[lab == g], axis=0, weights=wts[lab == g]) if (lab == g).any()
                            else pts[seeds[g]] for g in range(k)])
        seeds = [int(np.argmin(((pts - c) ** 2).sum(-1))) for c in centers]
    seeds = list(dict.fromkeys(seeds))
    target = wts.sum() / len(seeds)
    index = {m: i for i, m in enumerate(members)}
    group = {}
    size = np.zeros(len(seeds))
    heap = [(0.0, g, members[s]) for g, s in enumerate(seeds)]
    heapq.heapify(heap)
    while heap:
        cost, g, m = heapq.heappop(heap)
        if m in group:
            continue
        if size[g] > 1.35 * target and cost < DEFER:
            # over target: other groups get the first chance; this group takes it later if nobody does
            heapq.heappush(heap, (cost + DEFER, g, m))
            continue
        group[m] = g
        size[g] += wts[index[m]]
        for n in adj[m]:
            if n in index and n not in group:
                heapq.heappush(heap, (cost + float(np.hypot(*(centroid[n] - centroid[m]))), g, n))
    # districts not connected to any seed (islands): nearest group by centroid
    for m in members:
        if m not in group:
            group[m] = int(np.argmin([np.hypot(*(centroid[m] - pts[s])) for s in seeds]))
    return group


def distinct_colors(n, seed=7):
    rng = np.random.default_rng(seed)
    used = set()
    out = []
    golden = 0.618033988749895
    h = rng.random()
    while len(out) < n:
        h = (h + golden) % 1.0
        s = 0.45 + 0.4 * rng.random()
        v = 0.55 + 0.4 * rng.random()
        c = tuple(int(round(255 * t)) for t in colorsys.hsv_to_rgb(h, s, v))
        if c not in used and c != (0, 0, 0):
            used.add(c)
            out.append(c)
    return out


def hillshade(pmt: Path) -> np.ndarray:
    raw = gzip.open(pmt).read()
    res = struct.unpack("<i", raw[4:8])[0]
    heights = np.frombuffer(raw, "<u2", res * res, 20).reshape(res, res).astype(np.float32) * 32 / 65535
    zs = (np.arange(H) + 0.5) * (res - 1) / SIZE_Z
    xs = (np.arange(W) + 0.5) * (res - 1) / SIZE_X
    h = ndimage.map_coordinates(heights, np.meshgrid(zs, xs, indexing="ij"), order=1)[::-1]
    gy, gx = np.gradient(h)
    shade = np.clip(0.75 + 1.2 * (gy - gx), 0.35, 1.25)  # light from the north-west
    return h, shade


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pmt", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--core-km2", type=float, default=3500.0, help="target province area in the HRE core")
    ap.add_argument("--edge-km2", type=float, default=16000.0, help="target province area outside it")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    land = load_land(args.pmt)
    districts = load_districts()
    ids = rasterize(districts, land)
    present = np.unique(ids[ids > 0])
    area = dict(zip(present.tolist(), (ndimage.sum(np.ones_like(ids), ids, present) * KM2_PER_PX).tolist()))
    cy, cx = np.array(ndimage.center_of_mass(np.ones_like(ids), ids, present)).T
    centroid = {m: np.array([x, y]) for m, x, y in zip(present.tolist(), cx, cy)}
    adj = adjacency(ids)

    # regions: NUTS-2 in the core, NUTS-1 (or country) at the edges
    regions = defaultdict(list)
    for m in present.tolist():
        d = districts[m - 1]
        key = ("core", d["nuts2"]) if d["core"] else ("edge", d["nuts1"])
        regions[key].append(m)
    merged = merge_small(regions, area, adj, args.core_km2)

    province_of = {}
    provinces = []
    for (tier, key), members in sorted(regions.items()):
        total = sum(area[m] for m in members)
        target = args.core_km2 if tier == "core" else args.edge_km2
        k = max(1, int(round(total / target)))
        groups = absorb_tiny(partition(members, area, centroid, adj, k), area, adj, TINY_FACTOR * target)
        for g in sorted(set(groups.values())):
            ms = [m for m in members if groups[m] == g]
            biggest = max(ms, key=lambda m: area[m])
            pid = len(provinces) + 1
            provinces.append(dict(id=pid, tier=tier, region=key, country=districts[biggest - 1]["country"],
                                  name=districts[biggest - 1]["name"], districts=[districts[m - 1]["code"] for m in ms],
                                  area=sum(area[m] for m in ms)))
            for m in ms:
                province_of[m] = pid

    lut = np.zeros(len(districts) + 1, np.int32)
    for m, p in province_of.items():
        lut[m] = p
    prov = lut[ids]
    colors = distinct_colors(len(provinces))
    palette = np.zeros((len(provinces) + 1, 3), np.uint8)
    for p, c in zip(provinces, colors):
        p["color"] = "#%02x%02x%02x" % c
        palette[p["id"]] = c
    Image.fromarray(palette[prov]).save(args.out / "provinces.png")

    with open(args.out / "provinces.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f, lineterminator="\n")
        wr.writerow(["color", "name", "tier", "region", "country", "area_km2", "districts"])
        for p in provinces:
            wr.writerow([p["color"], p["name"], p["tier"], p["region"], p["country"], round(p["area"]), " ".join(p["districts"])])

    # reference and preview
    _, shade = hillshade(args.pmt)
    base = np.where(land[..., None], np.array([226, 214, 180]) * shade[..., None], np.array([150, 175, 200]))
    country = np.array([hash(districts[m - 1]["country"]) % 100000 if m else -1 for m in range(len(districts) + 1)])[ids]
    cborder = np.zeros_like(land)
    cborder[:, 1:] |= country[:, 1:] != country[:, :-1]
    cborder[1:, :] |= country[1:, :] != country[:-1, :]
    coast = land ^ ndimage.binary_erosion(land)
    ref = base.copy()
    ref[ndimage.binary_dilation(cborder & land)] = (120, 40, 40)
    ref[coast] = (40, 50, 70)
    Image.fromarray(np.clip(ref, 0, 255).astype(np.uint8)).save(args.out / "reference.png")

    pborder = np.zeros_like(land)
    pborder[:, 1:] |= prov[:, 1:] != prov[:, :-1]
    pborder[1:, :] |= prov[1:, :] != prov[:-1, :]
    tint = np.where(land[..., None], 0.55 * base + 0.45 * palette[prov] * shade[..., None], base)
    tint[pborder & land] = (60, 40, 30)
    tint[ndimage.binary_dilation(cborder & land) & land] = (140, 20, 20)
    img = Image.fromarray(np.clip(tint, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
    pcy, pcx = np.array(ndimage.center_of_mass(np.ones_like(prov), prov, [p["id"] for p in provinces])).T
    for p, x, y in zip(provinces, pcx, pcy):
        draw.text((x, y), p["name"][:18], fill=(20, 20, 20), font=font, anchor="mm")
    img.save(args.out / "provinces_preview.png")

    core = [p for p in provinces if p["tier"] == "core"]
    edge = [p for p in provinces if p["tier"] == "edge"]
    print(f"{len(districts)} districts, {len(present)} on land in the frame; "
          f"{merged} small regions merged into neighbours")
    print(f"{len(provinces)} provinces: {len(core)} core (mean {np.mean([p['area'] for p in core]):.0f} km²), "
          f"{len(edge)} edge (mean {np.mean([p['area'] for p in edge]):.0f} km²)")


if __name__ == "__main__":
    main()
