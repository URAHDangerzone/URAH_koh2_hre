"""Clean up province borders in provinces.png: rivers as borders, no small enclaves.

Usage: python tools/snap_borders.py <hre_dir> <Maps/europe of URAH_HRE> [--write] [--preview out.png]

The provinces come from NUTS districts whose borders follow the real rivers, while the map's rivers
are Natural Earth courses: the two lie a few units apart, so a border along a river crosses it back
and forth and leaves slivers of a province on the far bank. Two passes, repeated until nothing
changes:

  river slivers  remove the river band (rivers.bin, visible width + RIVER_PAD) from every province;
                 a piece cut off from its province's main body this way, small (SLIVER_MAX_AREA px and
                 SLIVER_MAX_SHARE of the province), thin (no point farther than SLIVER_MAX_HALF_WIDTH
                 from its edge, so real land beyond a river stays) and touching a neighbour by land,
                 goes to that neighbour (the longest contact). The river band itself is then split between the
                 provinces on both banks (nearest land pixel), so the border runs down the river
  enclaves       a piece of a province that is not its main body, not an island and at most
                 ENCLAVE_MAX_AREA px goes to the neighbour around it (the longest contact)

Pieces holding a castle of the current settlements.def stay, so does every province named in
<hre_dir>/border_keep.txt (intentional exclaves; one provinces.csv name per line, # comments).
Without --write only the report and the preview are written; with it provinces.png is replaced
(rerun import_hre.py and the rest of build.sh afterwards).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).parent))
import urah_paths  # noqa: F401,E402
import hre_rivers  # noqa: E402
from propose_provinces import H, W  # noqa: E402

RIVER_PAD = 2  # pixels either side of the visible river water that count as river band
SLIVER_MAX_AREA = 1500  # px (1 px = 1 world unit² = ~0.26 km²)
SLIVER_MAX_SHARE = 0.10  # of the province
SLIVER_MAX_HALF_WIDTH = 10  # px: the piece's inradius; thicker pieces are real land on the far bank
SLIVER_ANY_AREA = 150  # px: pieces this small move whatever their shape
ENCLAVE_MAX_AREA = 1000
MIN_CONTACT = 3  # land contact (px) with the neighbour that takes a piece
EIGHT = np.ones((3, 3), bool)


def river_band(maps: Path) -> np.ndarray:
    layer = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(layer)
    for pts in hre_rivers.read_rivers(maps / "rivers.bin"):
        xy = [(float(x), H - 1 - float(z)) for x, _, z, _ in pts[::2]]
        width = float(pts[:, 3].max()) * hre_rivers.VISIBLE_SHARE
        dr.line(xy, fill=255, width=int(round(width)) + 2 * RIVER_PAD)
    return np.array(layer) > 0


def castle_pixels(maps: Path) -> list[tuple[int, int]]:
    text = (maps / "settlements.def").read_text(encoding="utf-8-sig")
    return [(int(float(x)), H - 1 - int(float(z)))
            for x, z in re.findall(r": Castle\s*\{[^{}]*?position = ([-\d.]+), ([-\d.]+)", text)]


def contacts(prov: np.ndarray, piece: np.ndarray, own: int, exclude: np.ndarray | None = None) -> Counter:
    """Pixel contact of a piece with other land provinces (8-neighbourhood), leaving out `exclude`."""
    ring = ndimage.binary_dilation(piece, EIGHT) & ~piece
    if exclude is not None:
        ring &= ~exclude
    ids = prov[ring]
    return Counter(int(c) for c in ids[(ids != 0) & (ids != own)])


def boxes(prov: np.ndarray, pad: int = 4) -> dict[int, tuple[slice, slice]]:
    """Bounding box (padded) of every land province colour."""
    cols, inv = np.unique(prov, return_inverse=True)
    objs = ndimage.find_objects(inv.reshape(prov.shape) + 1)
    out = {}
    for c, sl in zip(cols, objs):
        if c != 0 and sl is not None:
            out[int(c)] = (slice(max(sl[0].start - pad, 0), sl[0].stop + pad),
                           slice(max(sl[1].start - pad, 0), sl[1].stop + pad))
    return out


def snap(prov: np.ndarray, band: np.ndarray, protected: np.ndarray, keep: set[int], log: list) -> int:
    """One pass of both rules; returns the number of pixels that changed province."""
    changed = 0
    land = prov != 0
    # ---- river slivers
    for c, sl in boxes(prov).items():
        if c in keep:
            continue
        P, B = prov[sl], band[sl]
        mine = P == c
        lab, n = ndimage.label(mine & ~B)
        if n < 2:
            continue
        sizes = ndimage.sum(np.ones_like(lab), lab, np.arange(1, n + 1))
        main_i = int(np.argmax(sizes)) + 1
        total = mine.sum()
        for i in range(1, n + 1):
            a = sizes[i - 1]
            if i == main_i or a > SLIVER_MAX_AREA or a > SLIVER_MAX_SHARE * total:
                continue
            piece = lab == i
            if (piece & protected[sl]).any():
                continue
            if a > SLIVER_ANY_AREA and ndimage.distance_transform_edt(piece).max() > SLIVER_MAX_HALF_WIDTH:
                continue
            cont = contacts(P, piece, c, exclude=B)
            if not cont:
                continue
            to, n_px = cont.most_common(1)[0]
            if n_px < MIN_CONTACT:
                continue
            P[piece] = to
            changed += int(a)
            ys, xs = np.nonzero(piece)
            log.append(("river", c, to, int(a), int(xs.mean()) + sl[1].start, int(ys.mean()) + sl[0].start))
    # the river band to the province of the nearest land beyond it
    if changed:
        outside = land & ~band
        _, (iy, ix) = ndimage.distance_transform_edt(~outside, return_indices=True)
        sel = band & land & ~protected
        near = prov[iy[sel], ix[sel]]
        changed += int((near != prov[sel]).sum())
        prov[sel] = near
    # ---- enclaves
    for c, sl in boxes(prov).items():
        if c in keep:
            continue
        P = prov[sl]
        lab, n = ndimage.label(P == c, EIGHT)
        if n < 2:
            continue
        sizes = ndimage.sum(np.ones_like(lab), lab, np.arange(1, n + 1))
        main_i = int(np.argmax(sizes)) + 1
        for i in range(1, n + 1):
            if i == main_i or sizes[i - 1] > ENCLAVE_MAX_AREA:
                continue
            piece = lab == i
            if (piece & protected[sl]).any():
                continue
            cont = contacts(P, piece, c)
            if not cont:
                continue  # an island
            to = cont.most_common(1)[0][0]
            P[piece] = to
            changed += int(sizes[i - 1])
            ys, xs = np.nonzero(piece)
            log.append(("enclave", c, to, int(sizes[i - 1]), int(xs.mean()) + sl[1].start, int(ys.mean()) + sl[0].start))
    return changed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("hre", type=Path, help="folder with provinces.png / provinces.csv")
    ap.add_argument("maps", type=Path, help="URAH_HRE/Maps/europe (rivers.bin, settlements.def)")
    ap.add_argument("--write", action="store_true", help="replace provinces.png")
    ap.add_argument("--preview", type=Path, help="changed pixels over the old borders")
    args = ap.parse_args()

    img = np.array(Image.open(args.hre / "provinces.png").convert("RGB")).astype(np.int32)
    prov = img[..., 0] << 16 | img[..., 1] << 8 | img[..., 2]
    old = prov.copy()
    name = {int(r["color"][1:], 16): r["name"] for r in csv.DictReader(open(args.hre / "provinces.csv", encoding="utf-8"))}
    keep_file = args.hre / "border_keep.txt"
    keep_names = set()
    if keep_file.exists():
        keep_names = {ln.split("#")[0].strip() for ln in keep_file.read_text(encoding="utf-8").splitlines()} - {""}
    keep = {c for c, n in name.items() if n in keep_names}
    if keep_names - set(name.values()):
        print(f"border_keep.txt names not in provinces.csv: {', '.join(sorted(keep_names - set(name.values())))}")

    band = river_band(args.maps)
    protected = np.zeros((H, W), bool)
    for x, y in castle_pixels(args.maps):
        protected[max(y - 3, 0):y + 4, max(x - 3, 0):x + 4] = True

    log = []
    for it in range(4):
        n = snap(prov, band, protected, keep, log)
        print(f"pass {it + 1}: {n} px changed")
        if not n:
            break

    by_kind = Counter(k for k, *_ in log)
    print(f"{by_kind['river']} river slivers, {by_kind['enclave']} enclaves moved; "
          f"{int((prov != old).sum())} px ({(prov != old).sum() * 0.26:.0f} km²) in "
          f"{len(set(np.unique(old[prov != old])) | set(np.unique(prov[prov != old])))} provinces")
    for kind, frm, to, a, x, y in sorted(log, key=lambda r: -r[3])[:40]:
        print(f"  {kind:7} {name.get(frm, frm):28} -> {name.get(to, to):28} {a:5} px at {x}, {y}")

    if args.preview:
        rgb = np.full((H, W, 3), 230, np.uint8)
        rgb[old == 0] = (40, 40, 60)
        edge = np.zeros((H, W), bool)
        edge[:, 1:] |= old[:, 1:] != old[:, :-1]
        edge[1:, :] |= old[1:, :] != old[:-1, :]
        rgb[edge] = (110, 110, 110)
        rgb[band & (old != 0)] = (140, 180, 235)
        rgb[(prov != old) & (prov != 0)] = (230, 60, 30)
        Image.fromarray(rgb).save(args.preview)
        print(f"preview: {args.preview}")
    if args.write:
        out = np.stack([(prov >> 16) & 255, (prov >> 8) & 255, prov & 255], -1).astype(np.uint8)
        Image.fromarray(out).save(args.hre / "provinces.png")
        print(f"wrote {args.hre / 'provinces.png'}")


if __name__ == "__main__":
    main()
