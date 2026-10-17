"""Rivers of the HRE map: courses from Natural Earth, carved into the terrain heights.

Used by tools/make_hre_terrain.py. Sources (data/geo/README.txt):
  ne_10m_rivers_lake_centerlines.geojson  major rivers (scalerank <= 9) and their lines through lakes
  ne_10m_rivers_europe.geojson            Europe supplement (scalerank 10-12)

Like vanilla (18 wide rivers), only the bigger rivers get water:
scalerank <= VISIBLE_RANK plus the rivers in data/rivers_keep.txt. Every course with scalerank <=
MAX_RANK still counts for the province features (rivers_all.bin, tools/import_hre.py).

Each course is clipped to land and resampled, flowing from its higher end. A course that ends near
the coast is led into the sea, one that ends near another water course is led onto its centreline
(tributaries, and the pieces Natural Earth splits a river into), and one that ends nowhere is dropped.
The water surface follows the noise-free valley floor below the terrain; at a mouth it sinks below
the sea level. Across the course the ground follows vanilla's profile: a bed below the water, a steep
bank that buries the edge of the water mesh, then a broad valley.

rivers.bin (little endian), read by the URAH_HRE plugin and tools/paint_hre_bigmap.py:
    "PMRV", int32 rivers, per river: int32 points, then points x float32 x, y (water surface), z, width
    of the water mesh in world units (its edges lie inside the banks, VISIBLE_SHARE of it shows).
rivers_all.bin: the same format, all courses on land, y = 0, width = the water width of the old
generator (the province feature thresholds of tools/import_hre.py are in those units).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from make_hre_terrain import HEIGHTMAP_RES, KM_PER_DEG_LON, KM_PER_UNIT, LAT0, LON0, SIZE_X, SIZE_Z, WATER

ROOT = Path(__file__).resolve().parent.parent
GEO = ROOT / "data" / "geo"
SOURCES = ("ne_10m_rivers_lake_centerlines.geojson", "ne_10m_rivers_europe.geojson")
KEEP_FILE = ROOT / "data" / "rivers_keep.txt"
STEP = 0.5          # world units between course samples
MIN_LENGTH = 25.0   # world units; shorter pieces are dropped
MAX_RANK = 10       # Natural Earth scalerank; the smallest European rivers (11, 12) are left out
VISIBLE_RANK = 9    # water meshes for these ranks (and rivers_keep.txt)

# water surface: the valley floor smoothed along the course, this far below it
SURFACE_DROP = 1.0
SURFACE_MIN = WATER + 0.25  # on land; the bed is never cut to the sea level (the game reads <= 10 as sea)
BED_MIN = WATER + 0.12
MOUTH_SURFACE = WATER - 0.2  # vanilla rivers end 0.2 below the sea surface
MOUTH_RAMP = 12.0   # world units before the shoreline over which the surface sinks to SURFACE_MIN
SEA_RUN = 5.0       # world units a mouth runs out into the sea
COAST_REACH = 70.0  # a course ending this close to the sea is led into it (estuaries)
JOIN_REACH = 40.0   # a course ending this close to another water course is led onto it

# cross-section relative to the water surface (vanilla median, 18 rivers): inside the water mesh by
# the share of its half width, then by world units beyond its edge
BED_T = (0.0, 0.25, 0.5, 0.75, 1.0)
BED_REL = (-1.0, -0.85, -0.35, 0.35, 0.8)  # the first three are scaled by depth_for()
BANK_E = (0.0, 1.0, 2.0, 4.0, 8.0, 12.0, 24.0)
BANK_REL = (0.8, 1.0, 1.25, 1.6, 2.2, 2.6, 3.3)
CUT_E = (0.0, 2.0, 8.0, 24.0)    # how far the valley may be cut below the terrain ...
CUT_MAX = (9.0, 1.5, 0.4, 0.0)   # ... by units beyond the edge (none further out)
LEVEE = 1.5         # units beyond the edge where the bank is raised to at least +0.6 (hides the edge)
VALLEY = (3.0, 30.0)  # world units beyond the edge where the relief noise fades back in (no canyon walls)
VISIBLE_SHARE = 0.7  # share of the mesh width that shows above the banks

SOURCE_WIDTH = 0.55  # a course starts at this share of its mouth width and widens downstream
SOURCE_TAPER = 10.0  # world units over which a river grows from a thin tip at its source


def depth_for(width):
    """Bed below the water surface at the centre: ~0.9 like vanilla's rivers (7 wide), less on streams."""
    return np.clip(0.08 * np.asarray(width) + 0.45, 0.6, 1.1)


def smoothstep(x, a, b):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def width_for(scalerank: float) -> float:
    """Water mesh width at the mouth by Natural Earth rank (vanilla: 5 / 7 / 12.5 at p10 / 50 / 90)."""
    return float(np.interp(scalerank, [2, 4, 5, 6, 7, 8, 9, 10], [9.5, 8.5, 7.5, 6.0, 5.0, 4.2, 3.6, 3.0]))


def legacy_water_width(scalerank: float, along) -> np.ndarray:
    """Water width of the old generator, which the province feature thresholds were set on."""
    w = float(np.interp(scalerank, [2, 5, 8, 10], [3.0, 2.3, 1.5, 1.0])) * (0.45 + 0.55 * along)
    return w + 2 * (0.12 * w + 0.1) / 0.25 + 0.3


def lonlat_to_world(lon, lat):
    x = SIZE_X / 2 + (np.asarray(lon) - LON0) * KM_PER_DEG_LON / KM_PER_UNIT
    z = SIZE_Z / 2 + (np.asarray(lat) - LAT0) * 111.2 / KM_PER_UNIT
    return x, z


def chain_lines(lines, tol=0.02):
    """Join the pieces of one river (Natural Earth splits them) end to end; tol in degrees."""
    lines = [np.asarray(l, np.float64)[:, :2] for l in lines]
    chains = []
    while lines:
        cur = lines.pop(0)
        joined = True
        while joined:
            joined = False
            for i, l in enumerate(lines):
                if np.hypot(*(cur[-1] - l[0])) < tol:
                    cur = np.vstack([cur, l[1:]])
                elif np.hypot(*(cur[-1] - l[-1])) < tol:
                    cur = np.vstack([cur, l[::-1][1:]])
                elif np.hypot(*(cur[0] - l[-1])) < tol:
                    cur = np.vstack([l[:-1], cur])
                elif np.hypot(*(cur[0] - l[0])) < tol:
                    cur = np.vstack([l[::-1][:-1], cur])
                else:
                    continue
                lines.pop(i)
                joined = True
                break
        chains.append(cur)
    return chains


def group_rivers(lakes=True):
    """{(source file, river name): [line pieces, best rank]} for the rivers the map draws, with their
    lines through lakes (the map has no lakes) unless lakes is False."""
    groups = {}
    for name in SOURCES:
        path = GEO / name
        if not path.exists():
            continue
        for f in json.load(open(path, encoding="utf-8"))["features"]:
            p, g = f["properties"], f["geometry"]
            rank = p.get("scalerank") or 10
            if rank > MAX_RANK or not lakes and p.get("featurecla", "").lower().startswith("lake"):
                continue
            key = (name, p.get("name") or p.get("rivernum") or id(f))
            lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
            entry = groups.setdefault(key, [[], rank])
            entry[0].extend(lines)
            entry[1] = min(entry[1], rank)
    return groups


def keep_names():
    if not KEEP_FILE.exists():
        return set()
    return {l.split("#")[0].strip() for l in open(KEEP_FILE, encoding="utf-8")} - {""}


def load_courses(lakes=True):
    """(points [n, 2] world x, z; scalerank; name) per river touching the frame, pieces of the same
    river joined so the width can grow along its whole length."""
    out = []
    for (_, name), (lines, rank) in group_rivers(lakes).items():
        for a in chain_lines(lines):
            x, z = lonlat_to_world(a[:, 0], a[:, 1])
            if x.max() < 0 or x.min() > SIZE_X or z.max() < 0 or z.min() > SIZE_Z:
                continue
            out.append((np.column_stack([x, z]), rank, str(name)))
    return out


def named_courses(names):
    """(name, world x, z polyline [n, 2]) of the drawn rivers with these Natural Earth names."""
    out = []
    for (_, name), (lines, _) in group_rivers().items():
        if name in names:
            for a in chain_lines(lines):
                out.append((name, np.column_stack(lonlat_to_world(a[:, 0], a[:, 1]))))
    return out


def resample(pts, step=STEP):
    d = np.hypot(*np.diff(pts, axis=0).T)
    t = np.concatenate([[0], np.cumsum(d)])
    s = np.arange(0, t[-1], step)
    return np.column_stack([np.interp(s, t, pts[:, 0]), np.interp(s, t, pts[:, 1])])


def straight(a, b, step=STEP):
    """Points from a (exclusive) to b (inclusive)."""
    n = max(int(np.ceil(np.hypot(*(b - a)) / step)), 1)
    t = np.arange(1, n + 1)[:, None] / n
    return a + (b - a) * t


def feature_courses(smooth, sample):
    """The courses as the old generator cut them (runs where the smoothed valley floor is above the
    sea, without the lines through lakes), with its water widths: the province features
    (tools/import_hre.py) keep their calibration."""
    out = []
    for pts, rank, _ in load_courses(lakes=False):
        pts = resample(pts)
        inside = (pts[:, 0] > 2) & (pts[:, 0] < SIZE_X - 2) & (pts[:, 1] > 2) & (pts[:, 1] < SIZE_Z - 2)
        h = np.where(inside, sample(smooth, pts[:, 0], pts[:, 1]), 0.0)
        on_land = inside & (h > WATER + 0.3)
        edges = np.flatnonzero(np.diff(np.concatenate([[0], on_land.astype(np.int8), [0]])))
        for a, b in zip(edges[::2], edges[1::2]):
            if (b - a) * STEP < MIN_LENGTH:
                continue
            run = pts[a:b]
            if h[a:b][: max((b - a) // 10, 1)].mean() < h[a:b][-max((b - a) // 10, 1):].mean():
                run = run[::-1]
            along = np.arange(len(run)) / max(len(run) - 1, 1)
            out.append((np.column_stack([run[:, 0], np.zeros(len(run)), run[:, 1]]), legacy_water_width(rank, along)))
    return out


def carve(heights: np.ndarray, base: np.ndarray):
    """Carve the river beds into heights ([z, x] heightmap samples, world units) in place. base is
    the terrain without relief noise: along the rivers the relief fades into it, so they run in
    the DEM's real valleys instead of cutting canyons through the noise.
    Returns the water courses [(points [n, 3] x, surface y, z), mesh widths [n]], the courses for the
    province features (same form, all ranks, legacy widths) and the bed weight per sample (1 under
    the water, 0 on the banks) for painting."""
    cx, cz = SIZE_X / (HEIGHTMAP_RES - 1), SIZE_Z / (HEIGHTMAP_RES - 1)
    smooth = ndimage.gaussian_filter(base, 2.0)
    land = heights > WATER
    to_sea = ndimage.distance_transform_edt(land, sampling=(cz, cx))
    gz_sea, gx_sea = np.gradient(ndimage.gaussian_filter(to_sea, 4.0), cz, cx)

    def sample(arr, x, z, order=1):
        return ndimage.map_coordinates(arr, [z / cz, x / cx], order=order, mode="nearest")

    def at(arr, p, order=1):
        return float(sample(arr, np.atleast_1d(p[0]), np.atleast_1d(p[1]), order)[0])

    def in_frame(p, margin=2.0):
        return (p[..., 0] > margin) & (p[..., 0] < SIZE_X - margin) & (p[..., 1] > margin) & (p[..., 1] < SIZE_Z - margin)

    keep = keep_names()
    runs, features = [], feature_courses(smooth, sample)
    for pts, rank, name in load_courses():
        pts = resample(pts)
        inside = in_frame(pts)
        on_land = inside & (sample(land.astype(np.float32), pts[:, 0], pts[:, 1], 0) > 0.5)
        edges = np.flatnonzero(np.diff(np.concatenate([[0], on_land.astype(np.int8), [0]])))
        for a, b in zip(edges[::2], edges[1::2]):
            if (b - a) * STEP < MIN_LENGTH:
                continue
            run = pts[a:b]
            hr = sample(smooth, run[:, 0], run[:, 1])
            k = max(len(run) // 10, 1)
            flip = hr[:k].mean() < hr[-k:].mean()
            if flip:  # flow from the higher end
                run, a, b = run[::-1], b - 1, a - 1
            if rank > VISIBLE_RANK and name not in keep:
                continue
            # the sea: Natural Earth's line continues into it, or ends short of the coast
            step = -1 if flip else 1
            nxt, sea = b, []
            if 0 <= nxt < len(pts) and inside[nxt]:
                j = nxt
                while 0 <= j < len(pts) and inside[j] and len(sea) * STEP < SEA_RUN:
                    sea.append(pts[j])
                    j += step
            elif at(to_sea, run[-1]) < COAST_REACH:
                # Natural Earth stops where the estuary begins: go on towards the nearest sea, turning
                # gradually from the river's own direction
                d = run[-1] - run[max(len(run) - 1 - int(5 / STEP), 0)]
                d /= np.hypot(*d) + 1e-9
                p, ext = run[-1].copy(), []
                for _ in range(int(2 * COAST_REACH / STEP)):
                    g = np.array([at(gx_sea, p), at(gz_sea, p)])
                    g = -g / (np.hypot(*g) + 1e-9)
                    d = 0.85 * d + 0.15 * g
                    d /= np.hypot(*d) + 1e-9
                    p = p + d * STEP
                    if not in_frame(p):
                        break
                    if at(to_sea, p) <= 0:
                        sea.append(p)
                        if len(sea) * STEP >= SEA_RUN:
                            break
                    elif sea:
                        break
                    else:
                        ext.append(p)
                if sea:
                    run = np.vstack([run] + ([np.array(ext)] if ext else []))
            runs.append(dict(pts=run, sea=np.array(sea).reshape(-1, 2), rank=rank, name=name,
                             src_edge=not in_frame(run[0], 8.0), end_edge=not in_frame(run[-1], 8.0)))

    # surfaces: the smoothed valley floor; mouths sink to the sea level
    for r in runs:
        run = r["pts"]
        hr = sample(smooth, run[:, 0], run[:, 1])
        s = np.maximum(ndimage.gaussian_filter1d(hr, 25, mode="nearest") - SURFACE_DROP, SURFACE_MIN)
        if len(r["sea"]):
            tail = np.arange(len(run))[::-1] * STEP  # units before the shoreline
            s = np.where(tail < MOUTH_RAMP, SURFACE_MIN + (s - SURFACE_MIN) * smoothstep(tail, 0, MOUTH_RAMP), s)
            ss = np.interp(np.arange(1, len(r["sea"]) + 1) * STEP, [0, 3.0], [SURFACE_MIN, MOUTH_SURFACE])
            r["pts"] = np.vstack([run, r["sea"]])
            s = np.concatenate([s, ss])
        r["surface"] = s

    # joins: a course ending (or starting) near another one is led onto its centreline
    def join(sel, at_end):
        allp = np.concatenate([r["pts"] for r in sel])
        owner = np.concatenate([np.full(len(r["pts"]), i) for i, r in enumerate(sel)])
        idx = np.concatenate([np.arange(len(r["pts"])) for r in sel])
        tree = cKDTree(allp)
        for i, r in enumerate(sel):
            if at_end and (len(r["sea"]) or r["end_edge"]) or not at_end and r["src_edge"]:
                continue
            p = r["pts"][-1 if at_end else 0]
            near = [j for j in tree.query_ball_point(p, JOIN_REACH) if owner[j] != i]
            if not near:
                continue
            j = min(near, key=lambda j: np.hypot(*(allp[j] - p)))
            o, q = sel[owner[j]], idx[j]
            target, ts = o["pts"][q], o["surface"][q]
            link = straight(p, target)
            if at_end:
                n = int(15 / STEP)
                s = np.concatenate([r["surface"], np.full(len(link), ts)])
                w = np.clip((np.arange(len(s)) - (len(s) - len(link) - n)) / max(n + len(link), 1), 0, 1)
                r["surface"] = s * (1 - w) + ts * w
                r["pts"] = np.vstack([r["pts"], link])
                r["joined_end"] = (owner[j], q)
            else:
                r["pts"] = np.vstack([link[::-1], r["pts"]])
                r["surface"] = np.concatenate([np.linspace(ts, r["surface"][0], len(link)), r["surface"]])
                r["joined_src"] = (owner[j], q)

    join(runs, at_end=True)
    join(runs, at_end=False)
    dropped = [r["name"] for r in runs
               if not (len(r["sea"]) or r["end_edge"] or "joined_end" in r)]
    runs = [r for r in runs if len(r["sea"]) or r["end_edge"] or "joined_end" in r]

    # widths: grow downstream; a continuation starts at least as wide as what flows into it
    for r in runs:
        along = np.arange(len(r["pts"])) / max(len(r["pts"]) - 1, 1)
        w = width_for(r["rank"]) * (SOURCE_WIDTH + (1 - SOURCE_WIDTH) * along)
        if not r["src_edge"] and "joined_src" not in r:  # a real source: taper to a tip
            w *= 0.15 + 0.85 * smoothstep(np.arange(len(w)) * STEP, 0, SOURCE_TAPER)
        r["width"] = w
    for _ in range(3):
        for r in runs:
            ends = [o for o in runs if "joined_end" in o and o is not r and np.hypot(*(o["pts"][-1] - r["pts"][0])) < 1.0]
            if ends:
                r["width"] = np.maximum(r["width"], max(o["width"][-1] for o in ends) * np.exp(-np.arange(len(r["width"])) * STEP / 400))
    rivers = [(np.column_stack([r["pts"][:, 0], r["surface"], r["pts"][:, 1]]), r["width"]) for r in runs]
    carve.dropped = dropped

    # nearest course sample for every heightmap sample: draw the courses, then a distance transform
    idx = np.zeros(heights.shape, np.int32)
    surf_of, width_of = [0.0], [0.0]
    for pts, width in rivers:
        i = np.clip(np.round(pts[:, 2] / cz).astype(int), 0, heights.shape[0] - 1)
        j = np.clip(np.round(pts[:, 0] / cx).astype(int), 0, heights.shape[1] - 1)
        start = len(surf_of)
        surf_of.extend(pts[:, 1].tolist())
        width_of.extend(np.asarray(width, np.float32).tolist())
        # wider courses win where two meet
        order = np.argsort(np.asarray(width))
        cur = idx[i[order], j[order]]
        better = (cur == 0) | (np.array(width_of)[cur] <= np.asarray(width)[order])
        idx[i[order][better], j[order][better]] = np.arange(start, start + len(pts))[order][better]
    surf_of, width_of = np.array(surf_of, np.float32), np.array(width_of, np.float32)
    dist, (ii, jj) = ndimage.distance_transform_edt(idx == 0, sampling=(cz, cx), return_indices=True)
    near = idx[ii, jj]
    s, w = surf_of[near], width_of[near]
    half = np.maximum(w / 2, 0.3)
    e = dist - half  # beyond the edge of the water mesh
    reach = (near > 0) & (e < BANK_E[-1])
    # valley floor: relief noise fades out towards the river, and towards the smaller rivers that get
    # no water (they keep their valleys)
    dry = np.zeros(heights.shape, bool)
    for pts, _ in features:
        dry[np.clip(np.round(pts[:, 2] / cz).astype(int), 0, heights.shape[0] - 1),
            np.clip(np.round(pts[:, 0] / cx).astype(int), 0, heights.shape[1] - 1)] = True
    e_dry = ndimage.distance_transform_edt(~dry, sampling=(cz, cx)) - 1.0
    valley = land
    keep_relief = smoothstep(np.minimum(np.where(near > 0, e, np.inf), e_dry), VALLEY[0], VALLEY[1])
    heights[valley] = (base * (1 - keep_relief) + heights * keep_relief)[valley]
    d = depth_for(w)
    t = np.clip(dist / half, 0, 1)
    inner = np.interp(t, BED_T, BED_REL)
    inner = np.where(inner < 0, inner * d, inner)
    target = s + np.where(e < 0, inner, np.interp(e, BANK_E, BANK_REL))
    cut = np.maximum(target, heights - np.interp(e, CUT_E, CUT_MAX))
    new = np.minimum(heights, cut)
    # the bank buries the mesh edge: raise low ground beside the water to +0.6
    floor = s + 0.6 - np.clip(e - LEVEE, 0, None) * 0.25
    new = np.where(land & (e >= 0) & (e < LEVEE + 2.4), np.maximum(new, floor), new)
    new = np.where(land, np.maximum(new, BED_MIN), new)
    heights[reach] = new[reach]
    bed = (np.clip((0.75 * half - dist) / 1.0, 0, 1) * (near > 0)).astype(np.float32)
    return rivers, features, bed


def write_rivers(path: Path, rivers) -> None:
    with open(path, "wb") as f:
        f.write(b"PMRV" + struct.pack("<i", len(rivers)))
        for pts, width in rivers:
            f.write(struct.pack("<i", len(pts)))
            data = np.column_stack([pts, np.asarray(width)]).astype("<f4")
            f.write(data.tobytes())


def read_rivers(path: Path):
    raw = path.read_bytes()
    n = struct.unpack_from("<i", raw, 4)[0]
    off, out = 8, []
    for _ in range(n):
        k = struct.unpack_from("<i", raw, off)[0]
        off += 4
        out.append(np.frombuffer(raw, "<f4", k * 4, off).reshape(k, 4))
        off += k * 16
    return out
