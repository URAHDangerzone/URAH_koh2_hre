"""Build a whole-map terrain replacement (heights + splat) for the HRE map from a real DEM.

Usage: python tools/make_hre_terrain.py <dem.nc> <out.pmt> [--preview <dir>] [--stats] [--no-rivers]

Rivers (tools/hre_rivers.py) are carved into the heights and painted on their beds; the water
courses go to rivers.bin, all courses (for the province features) to rivers_all.bin next to <out.pmt>.

The DEM is a NetCDF grid with `latitude`, `longitude`, `altitude` (metres), e.g. an
ETOPO1 subset from NOAA ERDDAP (see data/dem/README.txt):
  https://coastwatch.pfeg.noaa.gov/erddap/griddap/etopo180.nc?altitude[(43.7):1:(56.3)][(-2.7):1:(24.7)]

The frame is an equirectangular projection around (LAT0, LON0), scaled so
that 44..56 N fill the 2625 world units of the europe terrain (~0.51 km per unit, ~2.6x the
vanilla detail); the 3750 units of x then span about -2.3..24.3 E.

Heights and splat are calibrated on the vanilla terrain of the same regions (heights.bin and
the europe TerrainData splat maps; --stats prints the comparison):

    region              slope median   > 30 deg   height p50   main layers
    north German plain     7.8           3.4 %      13.0        GrassHill, Tick Grass, base grass
    German uplands         9.7           7.3 %      16.1        GrassHill, base grass, fields
    Alps                  13.0          22.0 %      18.7        base grass, GrassHill, rock 8

Real lowlands are much flatter than the game's, so lowland relief gets added noise.

Mountains are built like vanilla's: massifs (massif_mask: high ground well
above its valley floor, and the summit ridges of lower ranges) get sharp fine relief and rock, and
tools/make_hre_mountains.py sets the mountain meshes on them; the valleys between stay smooth and
green, and river valleys and the passes of data/mountain_passes.txt stay open. The high plateaus of
data/alpine_plateaus.txt stay open too and are flattened, so mountain provinces have land for villages.

.pmt format (gzip-compressed as a whole, little endian), read by the URAH_HRE plugin:
    char[4]  magic "PMT1"
    int32    heightmap resolution H (4097), alphamap resolution A (2048), layers (24)
    float32  max height (world units of 65535)
    uint16   heights[H][H]        row = z, column = x; world height = v / 65535 * max height
    uint8    layer_a[A][A]        dominant splat layer per texel (row = z)
    uint8    layer_b[A][A]        second splat layer
    uint8    weight_a[A][A]       weight of layer_a * 255; layer_b gets the rest
"""

from __future__ import annotations

import argparse
import gzip
import struct
from pathlib import Path

import numpy as np
from scipy import ndimage

import urah_paths  # noqa: F401,E402  (puts URAH_shared on sys.path)
from koh2terrain import (  # noqa: E402
    ALPHAMAP_RES, HEIGHTMAP_RES, LAYERS, MAX_HEIGHT, SIZE_X, SIZE_Z, WATER, fractal_noise, load_dem,
)

# frame: 44..56 N over the map height, centred on 50 N, 11 E
LAT0, LON0 = 50.0, 11.0
KM_PER_UNIT = 12.0 * 111.2 / SIZE_Z
KM_PER_DEG_LON = 111.32 * np.cos(np.radians(LAT0))

# elevation -> game height: 10.4 + K * metres^P after smoothing, plus relief noise
HEIGHT_K, HEIGHT_POWER = 0.2, 0.5
SMOOTH_SIGMA = 2.5  # heightmap samples; the 1.8 km DEM cells are ~3.9 samples here
RELIEF_AMPLITUDE = 2.5  # world units of rolling hill noise everywhere on land ...
RELIEF_RIDGES = 9.0  # ... plus sharp ridges, scaled by how rough the real terrain is
ROUGH_REF = 50.0  # metres of local relief that count as fully rough
NOISE_SCALES = (128, 48, 16)  # heightmap samples
RIDGE_SCALES = (80, 32)
RIDGES_FREE = 0.3  # share of the ridge relief between the high massifs (smooth valleys)

# massifs, on a MASSIF_CELL grid (world units)
MASSIF_CELL = 2.0
MASSIF_REL = 310.0       # metres above the valley floor ...
MASSIF_ELEV = 1300.0     # ... on high ground: the high massifs (Alps, Tatra)
VALLEY_WINDOW = 20.0     # world units (~10 km) over which the valley floor is the lowest ground
RANGE_SIGMA = 1.2        # summit ridges of lower ranges: this many deviations above the regional mean ...
RANGE_WINDOW = 75.0      # ... (gaussian sigma in world units, ~150 km window)
RANGE_REL = 150.0        # ... and this far above their valley floor
RANGE_ELEV = 450.0
RANGE_STEEP_REL = 270.0  # also ridges: rugged middle mountains this far above their valley floor ...
RANGE_STEEP_ELEV = 750.0  # ... and this high
RANGE_GAP = 14.0         # world units between a high massif and a ridge (free foothills)
RIVER_GAP = 10.0         # world units kept open along every river (also those without water)
PASS_WIDTH = 12.0        # world units kept open along data/mountain_passes.txt
MASSIF_MIN_AREA = 2000.0  # world units²: smaller patches (about two mesh footprints) are dropped
MASSIF_RELIEF = 3.2      # world units of sharp fine relief on the high massifs ...
RANGE_RELIEF = 2.0       # ... and on the summit ridges
MASSIF_SCALES = (14, 6)  # heightmap samples (~10 and ~4 world units)
PASSES_FILE = Path(__file__).resolve().parent.parent / "data" / "mountain_passes.txt"
# high plateaus (data/alpine_plateaus.txt): flattened to their mean height and kept free of massifs
PLATEAUS_FILE = Path(__file__).resolve().parent.parent / "data" / "alpine_plateaus.txt"
PLATEAU_FADE = 3.0       # world units over which a plateau blends into the terrain around it
PLATEAU_MARGIN = 6.0     # world units around a plateau kept free of massifs (mesh footprints)
PLATEAU_RELIEF = 0.6     # world units of gentle rolling relief left on a plateau
PLATEAU_BED_GAP = 4.0    # world units beside a river bed where a plateau leaves the bank alone

# coastline: smoothed elevation > 0, then islands / lakes smaller than MIN_AREA samples dropped
COAST_SIGMA = 4.0
MIN_AREA = 400
# vanilla median height by signed distance to the shoreline (world units, land positive)
COAST_PROFILE = ((-40.0, -10.0, -6.0, -3.0, -1.5, -0.5, 0.0, 0.5, 1.5, 2.5, 4.0, 6.5),
                 (0.0, 6.0, 7.3, 8.4, 9.1, 9.7, 10.0, 10.25, 10.85, 11.2, 11.5, 11.9))
COAST_BLEND = 10.0  # world units inland over which the profile hands over to the terrain

# terrain layers of the europe TerrainData (WV_layer_*)
SAND, ROCK8, BASE_GRASS, WEAK_GRASS, PEBBLES, GREEN_FIELDS, FIELDS = 0, 1, 2, 3, 4, 5, 6
DIRT, YELLOW_FIELDS, GRASS_HILL, DIRT_PEBBLES, ROCKY_GRASS, GRANITE, TICK_GRASS = 8, 9, 14, 15, 18, 19, 20
SNOW_GRASS, SNOW = 22, 23

# calibration regions (lat0, lat1, lon0, lon1) and the vanilla values of the same regions
REGIONS = {
    "north German plain": ((52.3, 54.3, 7.0, 20.0), (7.8, 3.4, 13.0)),
    "German uplands": ((48.6, 51.2, 7.5, 17.0), (9.7, 7.3, 16.1)),
    "Alps": ((46.0, 47.6, 7.0, 14.5), (13.0, 22.0, 18.7)),
}


def world_to_latlon(x, z):
    lon = LON0 + (x - SIZE_X / 2) * KM_PER_UNIT / KM_PER_DEG_LON
    lat = LAT0 + (z - SIZE_Z / 2) * KM_PER_UNIT / 111.2
    return lat, lon


def sample_dem(dem, x, z, order=3):
    """DEM elevation (m) at world coords."""
    lat, lon, alt = dem
    la, lo = world_to_latlon(x, z)
    fi = (la - lat[0]) / (lat[1] - lat[0])
    fj = (lo - lon[0]) / (lon[1] - lon[0])
    fi, fj = np.broadcast_arrays(fi, fj)
    if fi.min() < 0 or fi.max() > len(lat) - 1 or fj.min() < 0 or fj.max() > len(lon) - 1:
        raise ValueError("DEM does not cover the map frame")
    return ndimage.map_coordinates(alt, [fi, fj], order=order, mode="nearest").astype(np.float32)


def read_passes(path: Path = PASSES_FILE):
    """[(name, lon / lat polyline [n, 2])] from data/mountain_passes.txt: `name: lat lon; lat lon; ...`."""
    out = []
    if not path.exists():
        return out
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if not line:
            continue
        name, pts = line.split(":", 1)
        ll = np.array([[float(v) for v in p.split()] for p in pts.split(";") if p.strip()])
        out.append((name.strip(), ll[:, ::-1]))
    return out


def read_plateaus(path: Path = PLATEAUS_FILE):
    """[(name, x, z, radius)] in world units from data/alpine_plateaus.txt: `name: lat lon radius_km`."""
    import hre_rivers
    out = []
    if not path.exists():
        return out
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if not line:
            continue
        name, vals = line.split(":", 1)
        lat, lon, r_km = (float(v) for v in vals.split())
        x, z = hre_rivers.lonlat_to_world(lon, lat)
        out.append((name.strip(), float(x), float(z), r_km / KM_PER_UNIT))
    return out


def massif_mask(dem):
    """Mountain massifs on the MASSIF_CELL grid [z, x]: 2 = high massif (Alps, Tatra), 1 = summit ridge
    of a lower range, 0 = free. Cached: make_hre_mountains.py asks again for the same DEM."""
    if getattr(massif_mask, "cache", None) is not None:
        return massif_mask.cache
    import hre_rivers
    c = MASSIF_CELL
    gw, gh = int(SIZE_X / c), int(SIZE_Z / c)
    xs, zs = (np.arange(gw) + 0.5) * c, (np.arange(gh) + 0.5) * c
    elev = ndimage.gaussian_filter(np.clip(sample_dem(dem, xs[None, :], zs[:, None], order=1), 0, None), 1.0)
    floor = ndimage.gaussian_filter(ndimage.minimum_filter(elev, size=int(VALLEY_WINDOW / c)), VALLEY_WINDOW / c / 2)
    rel = elev - floor
    high = (rel > MASSIF_REL) & (elev > MASSIF_ELEV)
    mean = ndimage.gaussian_filter(elev, RANGE_WINDOW / c)
    sd = np.sqrt(np.clip(ndimage.gaussian_filter(elev ** 2, RANGE_WINDOW / c) - mean ** 2, 0, None))
    ridge = (elev > mean + RANGE_SIGMA * sd) & (rel > RANGE_REL) & (elev > RANGE_ELEV)
    ridge |= (rel > RANGE_STEEP_REL) & (elev > RANGE_STEEP_ELEV)  # rugged middle mountains (Dinaric, Carpathians)
    # open: every river valley and the passes
    lines = np.zeros((gh, gw), bool)
    for pts, _, _ in hre_rivers.load_courses():
        p = hre_rivers.resample(pts, c / 2)
        lines[np.clip((p[:, 1] / c).astype(int), 0, gh - 1), np.clip((p[:, 0] / c).astype(int), 0, gw - 1)] = True
    open_ = ndimage.distance_transform_edt(~lines) * c < RIVER_GAP / 2
    lines[:] = False
    for _, ll in read_passes():
        p = hre_rivers.resample(np.column_stack(hre_rivers.lonlat_to_world(ll[:, 0], ll[:, 1])), c / 2)
        lines[np.clip((p[:, 1] / c).astype(int), 0, gh - 1), np.clip((p[:, 0] / c).astype(int), 0, gw - 1)] = True
    open_ |= ndimage.distance_transform_edt(~lines) * c < PASS_WIDTH / 2
    for _, px, pz, r in read_plateaus():
        open_ |= np.hypot(xs[None, :] - px, zs[:, None] - pz) < r + PLATEAU_FADE + PLATEAU_MARGIN
    disk = np.hypot(*np.mgrid[-3:4, -3:4]) <= 3

    def clean(m):
        m = ndimage.binary_closing(m, disk) & ~open_
        m = ndimage.binary_opening(m, disk[1:-1, 1:-1])
        labels, _ = ndimage.label(m)
        big = np.bincount(labels.ravel()) * c * c >= MASSIF_MIN_AREA
        big[0] = False
        return big[labels]

    high = clean(high)
    ridge = clean(ridge & (ndimage.distance_transform_edt(~high) * c > RANGE_GAP))  # free foothills
    out = np.zeros((gh, gw), np.uint8)
    out[ridge] = 1
    out[high] = 2
    massif_mask.cache = out
    return out


def massif_weights(dem, shape, soft=3.0):
    """Soft weights (high massif, ridge of a lower range) on a [z, x] grid of the given shape covering the
    map, faded over ~soft world units at the edges."""
    m = massif_mask(dem)
    gh, gw = m.shape
    zi = (np.arange(shape[0]) + 0.5) / shape[0] * gh - 0.5
    xi = (np.arange(shape[1]) + 0.5) / shape[1] * gw - 0.5
    out = []
    for v in ((m == 2), (m == 1)):
        f = ndimage.gaussian_filter(v.astype(np.float32), soft / MASSIF_CELL)
        out.append(ndimage.map_coordinates(f, np.meshgrid(zi, xi, indexing="ij"), order=1, mode="nearest"))
    return out


def build_heights(dem):
    """Game heights on the heightmap samples, [z, x]; also returns the elevation in metres."""
    cx, cz = SIZE_X / (HEIGHTMAP_RES - 1), SIZE_Z / (HEIGHTMAP_RES - 1)
    xs = np.arange(HEIGHTMAP_RES, dtype=np.float32) * cx
    zs = np.arange(HEIGHTMAP_RES, dtype=np.float32) * cz
    elev = sample_dem(dem, xs[None, :], zs[:, None])
    land = coastline(elev)

    smooth = ndimage.gaussian_filter(np.clip(elev, 0, None), SMOOTH_SIGMA)
    g = 10.4 + HEIGHT_K * smooth ** HEIGHT_POWER
    base = g.copy()  # without relief noise: the real valleys, for the rivers
    # relief: hills everywhere, more where the real terrain is rough. Large scales only, the
    # vanilla terrain is rounded (it keeps 2/3 of its slope after smoothing with sigma 8)
    rough = ndimage.gaussian_filter(np.abs(elev - ndimage.gaussian_filter(elev, 12)), 12)
    rough = np.clip(rough / ROUGH_REF, 0, 1)
    noise = fractal_noise(elev.shape, 1, scales=NOISE_SCALES, weights=(0.6, 0.3, 0.1))
    # ridged noise gives the steep flanks the game's hills have (vanilla uplands: 7% above 30 deg)
    ridges = (1 - np.abs(fractal_noise(elev.shape, 5, scales=RIDGE_SCALES, weights=(0.7, 0.3))) / 2.5).clip(0, 1) ** 3
    mh, mr = massif_weights(dem, elev.shape)
    alpine, = massif_weights(dem, elev.shape, soft=40.0)[:1]  # around the high massifs
    damp = 1 - (1 - RIDGES_FREE) * np.clip(alpine * 3, 0, 1) * (1 - np.maximum(mh, mr))
    g += RELIEF_AMPLITUDE * (noise + 0.3) + RELIEF_RIDGES * rough * ridges * damp
    # massifs: sharp fine relief, peaks and gullies a few units apart (vanilla Alps under the meshes:
    # slope p50 30 deg, half of it steeper than 30)
    fine = (1 - np.abs(fractal_noise(elev.shape, 11, scales=MASSIF_SCALES, weights=(0.6, 0.4))) / 2.2).clip(0, 1) ** 2
    g += (MASSIF_RELIEF * mh + RANGE_RELIEF * mr) * (fine - 0.25)
    build_heights.massif = (mh, mr)
    build_heights.noise = noise

    # coast: vanilla's height profile across the shoreline, blended into the inland terrain and
    # continued offshore at ~0.3 per unit down to the sea floor
    dist_land = ndimage.distance_transform_edt(land, sampling=(cz, cx))
    dist_sea = ndimage.distance_transform_edt(~land, sampling=(cz, cx))
    signed = np.where(land, dist_land - 0.5 * cx, 0.5 * cx - dist_sea)
    profile = np.interp(signed, COAST_PROFILE[0], COAST_PROFILE[1])
    blend = np.clip(signed / COAST_BLEND, 0, 1) ** 2
    g = np.where(land, np.maximum(profile * (1 - blend) + g * blend, WATER + 0.1), profile + noise * 0.2 * (signed < -4))
    build_heights.base = np.where(land, np.maximum(base, WATER + 0.15), g).astype(np.float32)
    return np.clip(g, 0.0, MAX_HEIGHT - 0.5).astype(np.float32), elev


def flatten_plateaus(heights, noise, bed=None):
    """High plateaus (data/alpine_plateaus.txt) in place: the terrain at its mean height there with a
    gentle roll, blending into the slopes around them over PLATEAU_FADE. Runs
    after the rivers are carved (their valleys reshape the terrain far beside them); river beds (bed
    weight > 0) and PLATEAU_BED_GAP beside them keep their banks."""
    cx, cz = SIZE_X / (HEIGHTMAP_RES - 1), SIZE_Z / (HEIGHTMAP_RES - 1)
    for _, px, pz, r in read_plateaus():
        x0, x1 = (int(np.clip(v / cx, 0, HEIGHTMAP_RES)) for v in (px - r - PLATEAU_FADE, px + r + PLATEAU_FADE + cx))
        z0, z1 = (int(np.clip(v / cz, 0, HEIGHTMAP_RES)) for v in (pz - r - PLATEAU_FADE, pz + r + PLATEAU_FADE + cz))
        win = (slice(z0, z1), slice(x0, x1))
        d = np.hypot(np.arange(x0, x1)[None, :] * cx - px, np.arange(z0, z1)[:, None] * cz - pz)
        w = np.clip((r + PLATEAU_FADE - d) / PLATEAU_FADE, 0, 1)
        w = w * w * (3 - 2 * w)
        if bed is not None and (bed[win] > 0).any():
            gap = ndimage.distance_transform_edt(bed[win] <= 0, sampling=(cz, cx))
            w *= np.clip((gap - PLATEAU_BED_GAP) / PLATEAU_FADE, 0, 1)
        level = heights[win][d < r].mean()
        heights[win] = heights[win] * (1 - w) + (level + PLATEAU_RELIEF * noise[win]) * w


def coastline(elev):
    """Land mask with a rounded coastline and without islands, lakes and inlets below MIN_AREA."""
    land = ndimage.gaussian_filter(elev, COAST_SIGMA) > 0
    land = drop_small(land)
    return ~drop_small(~land)


def drop_small(mask):
    labels, _ = ndimage.label(mask)
    small = np.bincount(labels.ravel()) < MIN_AREA
    small[0] = False
    return mask & ~small[labels]


def slope_degrees(h, cell_z, cell_x):
    gz, gx = np.gradient(h, cell_z, cell_x)
    return np.degrees(np.arctan(np.hypot(gx, gz)))


def paint(heights, dem, bank=None):
    """Splat weights on the alphamap texels, reduced to the top two layers per texel."""
    hcx, hcz = SIZE_X / (HEIGHTMAP_RES - 1), SIZE_Z / (HEIGHTMAP_RES - 1)
    acx, acz = SIZE_X / ALPHAMAP_RES, SIZE_Z / ALPHAMAP_RES
    ax = (np.arange(ALPHAMAP_RES, dtype=np.float32) + 0.5) * acx
    az = (np.arange(ALPHAMAP_RES, dtype=np.float32) + 0.5) * acz
    AX_IDX, AZ_IDX = np.meshgrid(ax / hcx, az / hcz)
    g = ndimage.map_coordinates(heights, [AZ_IDX, AX_IDX], order=1, mode="nearest")
    slope = slope_degrees(g, acz, acx)
    lat, _ = world_to_latlon(0.0, az)
    lat = np.broadcast_to(lat[:, None], g.shape)

    shape = g.shape
    n1 = fractal_noise(shape, 2, scales=(40, 12), weights=(0.7, 0.3))
    n2 = fractal_noise(shape, 3, scales=(24, 8), weights=(0.6, 0.4))
    n3 = fractal_noise(shape, 4, scales=(16, 5), weights=(0.6, 0.4))
    hj = g + n1 * 0.8  # jitter the height bands
    if getattr(build_heights, "massif", None) is not None:
        mh, mr = (ndimage.map_coordinates(m, [AZ_IDX, AX_IDX], order=1, mode="nearest") for m in build_heights.massif)
    else:
        mh = mr = np.zeros(shape, np.float32)
    massif = np.clip(mh + 0.7 * mr, 0, 1)

    def band(v, lo, hi, soft):
        return np.clip((v - lo) / soft + 0.5, 0, 1) * np.clip((hi - v) / soft + 0.5, 0, 1)

    low = band(hj, -1e9, 16.5, 1.5)
    upland = band(hj, 16.5, 22.0, 1.5)
    high = band(hj, 22.0, 27.0, 1.5)
    peak = band(hj, 27.0, 1e9, 1.5)
    north = np.clip((lat - 51.8) / 1.2, 0, 1)
    flat = np.clip((14 - slope) / 6, 0, 1)
    # farmland patches on flat lowland, three field types picked by noise
    fields = np.clip((n2 - 0.1) * 2.5, 0, 1) * flat * (low + 0.4 * upland)
    pick = np.clip(n3 * 0.5 + 0.5, 0, 1)
    a = np.clip(n1 * 0.5 + 0.5, 0, 1)

    w = np.zeros(shape + (LAYERS,), np.float32)
    w[..., GRASS_HILL] = low * (0.55 + 0.2 * a) + upland * (0.3 * a) + high * 0.12
    w[..., TICK_GRASS] = low * north * 0.35 * (1 - a)
    w[..., BASE_GRASS] = low * 0.15 * (1 - a) + upland * (0.35 + 0.15 * (1 - a)) + high * 0.4 + peak * 0.3
    w[..., WEAK_GRASS] = low * 0.08 + upland * 0.2 * a + high * 0.12
    w[..., ROCKY_GRASS] = upland * 0.06 + high * 0.05
    w[..., ROCK8] = high * 0.3 + peak * 0.65
    w[..., DIRT] = low * 0.04 * a
    w[..., YELLOW_FIELDS] = fields * np.clip(1 - 3 * np.abs(pick - 0.2), 0, 1) * 0.9
    w[..., FIELDS] = fields * np.clip(1 - 3 * np.abs(pick - 0.55), 0, 1) * 0.9
    w[..., GREEN_FIELDS] = fields * np.clip(1 - 3 * np.abs(pick - 0.9), 0, 1) * 0.9 * (0.3 + north)

    # massifs: rock with rocky grass and base grass under and between the mountain meshes; the high
    # ground between massifs stays green (vanilla Alps: base grass, rock 8, GrassHill, weak grass, no snow)
    free_high = (high + peak) * (1 - massif)
    w[..., ROCK8] *= 1 - 0.85 * (1 - massif)
    w[..., BASE_GRASS] += free_high * 0.3 * a
    w[..., GRASS_HILL] += free_high * 0.3 * (1 - a)
    w[..., WEAK_GRASS] += free_high * 0.2
    w *= (1 - 0.7 * massif)[..., None]
    w[..., ROCK8] += massif * (0.3 + 0.15 * a)
    w[..., BASE_GRASS] += massif * (0.25 + 0.15 * (1 - a))
    w[..., ROCKY_GRASS] += massif * 0.1
    w[..., GRASS_HILL] += massif * 0.1 * a
    w[..., WEAK_GRASS] += massif * 0.07

    # rock on steep slopes, like vanilla (rock 8 takes over above ~25 deg); less in the massifs, whose
    # fine relief is steep everywhere (vanilla: rock 8 ~36 % under the meshes)
    steep = np.clip((slope - 22) / 15, 0, 1) * (1 - 0.5 * massif)
    w *= (1 - 0.7 * steep)[..., None]
    w[..., ROCK8] += steep * 0.5
    w[..., ROCKY_GRASS] += steep * 0.1 * (1 - steep)
    w[..., GRANITE] += np.clip((slope - 40) / 10, 0, 1) * 0.3

    # shore: a ~2 unit band along the sea fading into the grass. Stretches of it are rocky (vanilla
    # shores are about as much rock 8 as sand), always where the coast is steep; the rest is beach
    to_sea = ndimage.distance_transform_edt(g > WATER, sampling=(acz, acx))
    shore = np.clip((WATER + 1.1 + n3 * 0.2 - g) / 0.6, 0, 1)
    shore *= np.clip((3.0 + n3 - to_sea) / 1.0, 0, 1)  # low land far from the sea stays green
    coast_noise = fractal_noise(shape, 6, scales=(150, 50), weights=(0.7, 0.3))
    rocky = np.clip((coast_noise - 0.1) * 1.5 + steep, 0, 1)
    w *= (1 - shore)[..., None]
    w[..., SAND] += shore * (1 - rocky) * (0.75 + 0.15 * a) + shore * rocky * 0.1
    w[..., PEBBLES] += shore * (0.15 * (1 - rocky) + 0.2 * rocky)
    w[..., ROCK8] += shore * (0.1 * (1 - a) * (1 - rocky) + 0.55 * rocky)
    w[..., DIRT_PEBBLES] += shore * rocky * 0.15
    # river beds (tools/hre_rivers.py): sand and rock under the water; the banks keep the grass, like
    # vanilla's (no gravel strip)
    if bank is not None:
        rb = ndimage.map_coordinates(bank, [AZ_IDX, AX_IDX], order=1, mode="nearest")
        rb = np.clip(rb * (1.1 + 0.3 * n3), 0, 1)
        w *= (1 - rb)[..., None]
        w[..., SAND] += rb * (0.5 + 0.2 * a)
        w[..., ROCK8] += rb * (0.3 + 0.2 * (1 - a))
        w[..., PEBBLES] += rb * 0.1
    w[g <= WATER] = 0
    w[g <= WATER, SAND] = 1

    # vanilla texels are dominated by one layer (top 1 ~0.73, top 2 ~0.2): sharpen, keep two
    w = w ** 2
    order = np.argsort(-w, axis=-1)[..., :2]
    top = np.take_along_axis(w, order, -1)
    del w
    total = np.maximum(top.sum(-1), 1e-9)
    weight_a = np.round(top[..., 0] / total * 255).astype(np.uint8)
    return order[..., 0].astype(np.uint8), order[..., 1].astype(np.uint8), weight_a, g, slope


def write_pmt(path: Path, heights, layer_a, layer_b, weight_a):
    q = np.round(np.clip(heights / MAX_HEIGHT, 0, 1) * 65535).astype("<u2")
    # mtime=0: the same terrain gives the same bytes, so a rebuild does not change the mod checksum
    with gzip.GzipFile(path, "wb", compresslevel=6, mtime=0) as f:
        f.write(b"PMT1")
        f.write(struct.pack("<3if", HEIGHTMAP_RES, ALPHAMAP_RES, LAYERS, MAX_HEIGHT))
        f.write(q.tobytes())
        f.write(layer_a.tobytes())
        f.write(layer_b.tobytes())
        f.write(weight_a.tobytes())


def region_mask(shape, cell_x, cell_z, box, offset=0.0):
    lat0, lat1, lon0, lon1 = box
    x = (np.arange(shape[1]) + offset) * cell_x
    z = (np.arange(shape[0]) + offset) * cell_z
    lat, _ = world_to_latlon(0.0, z)
    _, lon = world_to_latlon(x, 0.0)
    return ((lat >= lat0) & (lat <= lat1))[:, None] & ((lon >= lon0) & (lon <= lon1))[None, :]


def print_stats(heights):
    cx, cz = SIZE_X / (HEIGHTMAP_RES - 1), SIZE_Z / (HEIGHTMAP_RES - 1)
    slope = slope_degrees(heights, cz, cx)
    land = heights > WATER
    print(f"land {land.mean() * 100:.1f} %, max height {heights.max():.2f}")
    print(f"{'region':20} {'slope med':>14} {'> 30 deg':>16} {'height p50':>14}   (vanilla)")
    for name, (box, (v_med, v_steep, v_h)) in REGIONS.items():
        m = region_mask(heights.shape, cx, cz, box) & land
        s, h = slope[m], heights[m]
        print(f"{name:20} {np.median(s):6.1f} ({v_med:5.1f}) {(s > 30).mean() * 100:7.1f}% ({v_steep:4.1f}%)"
              f" {np.median(h):6.1f} ({v_h:5.1f})")


def preview(out_dir: Path, heights, layer_a, layer_b, weight_a):
    from PIL import Image
    out_dir.mkdir(parents=True, exist_ok=True)
    small = heights[::4, ::4]
    img = np.clip(small / MAX_HEIGHT, 0, 1)[::-1]
    Image.fromarray((img * 255).astype(np.uint8)).save(out_dir / "hre_heights.png")
    palette = np.zeros((LAYERS, 3), np.float32)
    colors = {SAND: (215, 200, 160), PEBBLES: (150, 140, 120), BASE_GRASS: (90, 120, 55), WEAK_GRASS: (140, 145, 80),
              GREEN_FIELDS: (110, 160, 60), FIELDS: (170, 150, 80), DIRT: (130, 105, 75),
              YELLOW_FIELDS: (210, 190, 90), GRASS_HILL: (75, 110, 45), ROCKY_GRASS: (115, 120, 90),
              ROCK8: (120, 110, 100), DIRT_PEBBLES: (135, 120, 100), GRANITE: (85, 80, 80), TICK_GRASS: (95, 130, 70),
              SNOW_GRASS: (200, 205, 200), SNOW: (245, 248, 252)}
    for k, c in colors.items():
        palette[k] = c
    wa = weight_a.astype(np.float32)[..., None] / 255
    rgb = palette[layer_a] * wa + palette[layer_b] * (1 - wa)
    sea = heights[:-1:2, :-1:2] <= WATER
    rgb[sea] = (60, 75, 110)
    # hillshade from the heights for readability
    hs = np.clip(1 + 4 * np.gradient(heights[::2, ::2], axis=1) - 4 * np.gradient(heights[::2, ::2], axis=0), 0.5, 1.4)
    rgb = np.clip(rgb * hs[: rgb.shape[0], : rgb.shape[1], None], 0, 255)
    Image.fromarray(rgb[::-1].astype(np.uint8)).save(out_dir / "hre_splat.png")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dem", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--preview", type=Path)
    ap.add_argument("--stats", action="store_true", help="compare slopes and heights with vanilla regions")
    ap.add_argument("--no-rivers", action="store_true", help="skip carving rivers (tools/hre_rivers.py)")
    args = ap.parse_args()
    dem = load_dem(args.dem)
    heights, _ = build_heights(dem)
    bank = None
    if not args.no_rivers:
        import hre_rivers
        rivers, features, bank = hre_rivers.carve(heights, build_heights.base)
        hre_rivers.write_rivers(args.out.with_name("rivers.bin"), rivers)
        hre_rivers.write_rivers(args.out.with_name("rivers_all.bin"), features)
        print(f"{len(rivers)} river courses carved, {sum(len(p) for p, _ in rivers) * hre_rivers.STEP:.0f} units long; "
              f"{len(features)} courses for the province features")
        if hre_rivers.carve.dropped:
            print("dropped (end in no water): " + ", ".join(sorted(set(hre_rivers.carve.dropped))))
    flatten_plateaus(heights, build_heights.noise, bank)
    if args.stats:
        print_stats(heights)
    layer_a, layer_b, weight_a, _, _ = paint(heights, dem, bank)
    counts = np.bincount(layer_a.ravel(), minlength=LAYERS) / layer_a.size * 100
    print("dominant layers: " + ", ".join(f"{i}: {c:.1f}%" for i, c in enumerate(counts) if c >= 0.5))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_pmt(args.out, heights, layer_a, layer_b, weight_a)
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")
    if args.preview:
        preview(args.preview, heights, layer_a, layer_b, weight_a)


if __name__ == "__main__":
    main()
