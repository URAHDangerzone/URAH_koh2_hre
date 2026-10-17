"""Paint the HRE map's kingdom arms into the game's CoA atlases (shield, flag, banner, small_shield).

Usage: python tools/paint_hre_coa.py <vanilla_map_dir> <hre_dir> <mod_dir> [--preview <png>]

Reads <hre_dir>/kingdom_arms.csv (arms per kingdom) and <mod_dir>/Maps/europe/CoA.csv (the atlas tile
import_hre.py gave each kingdom; run it first), paints every arms into its tile of copies of vanilla's
atlases and writes <mod_dir>/Maps/europe/CoatOfArmsAtlas<Mode>.png. Tiles: see koh2coa.py (URAH_shared).

Vanilla crests are flat: plain tinctures, no shading or outline. Charges (eagle, lion, griffin, bear,
fish, fleur-de-lis ...) are cut out of vanilla crests of the same mode and recoloured; partitions,
ordinaries and simple charges (cross, wheel, rose, star ...) are drawn.

Arms: space-separated layers, painted in order, `name:arg:arg`:
  field:C   perpale|perfess|perbend|quarterly|chequy|lozengy|dancetty:C1:C2   barry:C1:C2:n
  fess|pale|bend|chief|base|cross|chevron|bordure|inescutcheon|billets:C   checkyfess:C1:C2
  wheel|escarbuncle|rose|star|stars3|crosier|pillar|gonfanon|antlers:C
  charge:NAME:C[:scale[:x:y]]   (C may be C1/C2: a charge striped in two tinctures)
Tinctures: gold silver red blue lightblue black green. On banners (tall) horizontal partitions and
ordinaries turn vertical, as vanilla's banners do.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import urah_paths  # noqa: F401,E402  (puts URAH_shared on sys.path)
from koh2coa import MODES, tile_box  # noqa: E402

SS = 4  # supersampling
# charge: (source crest id, how to find the charge: 'dark' pixels or 'diff' from the field colour)
CHARGES = {
    "eagle": (19, "dark"), "lion": (165, "dark"), "lions3": (469, "dark"), "bear": (196, "dark"),
    "oxhead": (233, "dark"), "griffin": (384, "diff"), "fish": (129, "diff"), "fleur": (58, "diff"),
}
# tinctures sampled from vanilla crests: (crest id, which colour)
SAMPLES = {"gold": (19, "field"), "black": (19, "dark"), "red": (110, "field"), "silver": (356, "field"),
           "blue": (184, "field"), "lightblue": (389, "light_blue"), "green": (386, "green")}


def field_color(tile):
    edge = np.concatenate([tile[0], tile[-1], tile[:, 0], tile[:, -1]])[:, :3]
    vals, counts = np.unique(edge, axis=0, return_counts=True)
    return vals[np.argmax(counts)]


def sample_palette(atlas):
    w, h = MODES["shield"]
    pal = {}
    for name, (cid, how) in SAMPLES.items():
        tile = np.asarray(atlas.crop(tile_box(atlas, cid, w, h)).convert("RGB")).reshape(-1, 3).astype(int)
        if how == "field":
            pal[name] = tuple(field_color(tile.reshape(h, w, 3)))
            continue
        lum = tile.sum(1)
        if how == "dark":
            sel = tile[lum < 150]
        elif how == "green":
            sel = tile[(tile[:, 1] > tile[:, 0] + 25) & (tile[:, 1] > tile[:, 2] + 25)]
        else:  # light_blue
            sel = tile[(tile[:, 2] > tile[:, 0] + 60)]
        vals, counts = np.unique(sel, axis=0, return_counts=True)
        pal[name] = tuple(vals[np.argmax(counts)])
    return pal


def charge_mask(atlas, mode, name):
    """Alpha mask (float 0..1) of a charge, cropped to its bounding box."""
    cid, how = CHARGES[name]
    w, h = MODES[mode]
    tile = np.asarray(atlas.crop(tile_box(atlas, cid, w, h)).convert("RGB")).astype(float)
    if how == "dark":
        a = np.clip((120 - tile.max(2)) / 60, 0, 1)  # near black only (red is dark too)
    else:
        d = np.sqrt(((tile - field_color(tile.astype(int))) ** 2).sum(2))
        a = np.clip((d - 25) / 60, 0, 1)
    ys, xs = np.nonzero(a > 0.3)
    return a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


class Painter:
    def __init__(self, w, h, mode, atlas, pal):
        self.W, self.H = w * SS, h * SS
        self.w, self.h = w, h
        self.mode, self.atlas, self.pal = mode, atlas, pal
        self.img = np.zeros((self.H, self.W, 3), float)
        self.tall = mode == "banner"
        self.m = min(self.W, self.H)

    def color(self, c):
        return np.array(self.pal[c], float)

    def fill(self, mask, c):
        """Paint colour c (or 'c1/c2' horizontal stripes) where mask (float HxW) is set."""
        a = mask[..., None]
        if "/" in c:
            c1, c2 = c.split("/")
            rows = (np.arange(self.H) // max(1, self.H // 10)) % 2
            col = np.where(rows[:, None, None] == 0, self.color(c1), self.color(c2))
        else:
            col = self.color(c)
        self.img = self.img * (1 - a) + col * a

    def poly(self, *polys):
        im = Image.new("L", (self.W, self.H), 0)
        dr = ImageDraw.Draw(im)
        for pts in polys:
            dr.polygon([(x * self.W, y * self.H) for x, y in pts], fill=255)
        return np.asarray(im, float) / 255

    def shapes(self, draw):
        im = Image.new("L", (self.W, self.H), 0)
        draw(ImageDraw.Draw(im))
        return np.asarray(im, float) / 255

    def grid(self):
        y, x = np.mgrid[0:self.H, 0:self.W]
        return (x + 0.5) / self.W, (y + 0.5) / self.H

    # ---- partitions and ordinaries (u: 0..1 left-right, v: 0..1 top-bottom)
    def layer(self, op, args):
        u, v = self.grid()
        t = 0.2 * self.m  # ordinary width in pixels
        if op == "field":
            self.fill(np.ones((self.H, self.W)), args[0])
        elif op in ("perpale", "perfess"):
            vertical = op == "perpale" or self.tall
            self.fill(np.ones_like(u), args[0])
            self.fill(((u >= 0.5) if vertical else (v >= 0.5)).astype(float), args[1])
        elif op == "perbend":
            self.fill(np.ones_like(u), args[0])
            self.fill((u * self.W - v * self.H > 0).astype(float), args[1])
        elif op == "quarterly":
            self.fill(np.ones_like(u), args[0])
            self.fill(((u >= 0.5) ^ (v >= 0.5)).astype(float), args[1])
        elif op == "chequy":
            n = 5
            cu, cv = (u * self.W // (self.m / n)).astype(int), (v * self.H // (self.m / n)).astype(int)
            self.fill(np.ones_like(u), args[0])
            self.fill(((cu + cv) % 2).astype(float), args[1])
        elif op == "lozengy":
            d = self.m / 3.2
            x, y = u * self.W, v * self.H
            a, b = np.floor((x + 0.6 * y) / d), np.floor((x - 0.6 * y) / d)
            self.fill(np.ones_like(u), args[0])
            self.fill(((a + b) % 2).astype(float), args[1])
        elif op == "barry":
            n = int(args[2]) if len(args) > 2 else 6
            k = ((u if self.tall else v) * n).astype(int)
            self.fill(np.ones_like(u), args[0])
            self.fill((k % 2).astype(float), args[1])
        elif op == "dancetty":
            self.fill(np.ones_like(u), args[1])
            n = 3
            if self.tall:
                xs = 0.5 + 0.12 * np.where((np.floor(v * n * 2) % 2) == 0, (v * n * 2) % 1, 1 - (v * n * 2) % 1)
                self.fill((u < xs).astype(float), args[0])
            else:
                ys = 0.5 - 0.1 + 0.2 * np.where((np.floor(u * n * 2) % 2) == 0, 1 - (u * n * 2) % 1, (u * n * 2) % 1)
                self.fill((v < ys).astype(float), args[0])
        elif op in ("fess", "pale"):
            vertical = op == "pale" or self.tall
            if vertical:
                self.fill((abs(u - 0.5) * self.W < t / 2 * 1.3).astype(float), args[0])
            else:
                self.fill((abs(v - 0.5) * self.H < t / 2 * 1.3).astype(float), args[0])
        elif op == "bend":
            # band from the top-left to the bottom-right corner
            dist = np.abs(u * self.W * self.H - v * self.H * self.W) / np.hypot(self.W, self.H)
            self.fill((dist < t * 0.55).astype(float), args[0])
        elif op == "chief":
            self.fill((v < (0.3 if not self.tall else 0.22)).astype(float), args[0])
        elif op == "base":
            self.fill((v > (0.72 if not self.tall else 0.8)).astype(float), args[0])
        elif op == "cross":
            self.fill(((abs(u - 0.5) * self.W < t / 2) | (abs(v - 0.5) * self.H < t / 2)).astype(float), args[0])
        elif op == "chevron":
            x, y = (u - 0.5) * self.W, v * self.H
            top = self.H * 0.28 + np.abs(x) * 1.0
            self.fill(((y > top) & (y < top + t * 1.1)).astype(float), args[0])
        elif op == "bordure":
            b = 0.09 * self.m
            x, y = u * self.W, v * self.H
            self.fill(((x < b) | (x > self.W - b) | (y < b) | (y > self.H - b)).astype(float), args[0])
        elif op == "inescutcheon":
            cw, ch = 0.42, 0.42 * self.W / self.H * 1.25
            if self.mode == "flag":
                cw, ch = 0.26, 0.6
            pts = [(0.5 - cw / 2, 0.5 - ch / 2), (0.5 + cw / 2, 0.5 - ch / 2), (0.5 + cw / 2, 0.5 + ch * 0.15),
                   (0.5, 0.5 + ch / 2), (0.5 - cw / 2, 0.5 + ch * 0.15)]
            self.fill(self.poly(pts), args[0])
        elif op == "checkyfess":
            band = (abs((u if self.tall else v) - 0.5) * (self.W if self.tall else self.H) < t * 0.75)
            along = (v if self.tall else u) * (self.H if self.tall else self.W)
            across = ((u if self.tall else v) - 0.5) * (self.W if self.tall else self.H)
            cell = t * 0.75
            chk = ((np.floor(along / cell) + np.floor(across / cell)) % 2).astype(bool)
            self.fill((band & chk).astype(float), args[0])
            self.fill((band & ~chk).astype(float), args[1])
        elif op == "billets":
            d = self.m / 4.2
            im = Image.new("L", (self.W, self.H), 0)
            dr = ImageDraw.Draw(im)
            bw, bh = d * 0.22, d * 0.42
            for j in range(-1, int(self.H / d) + 2):
                for i in range(-1, int(self.W / d) + 2):
                    cx, cy = (i + 0.5 * (j % 2)) * d + d / 2, j * d + d / 2
                    dr.rectangle((cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2), fill=255)
            self.fill(np.asarray(im, float) / 255, args[0])
        elif op == "charge":
            self.charge(*args)
        else:
            self.figure(op, args[0])

    def place(self, a, scale, cx, cy):
        """Paste mask a (float) fitted into a box of scale * min(W, H) centred at (cx, cy)."""
        box = scale * self.m
        f = min(box / a.shape[1], box / a.shape[0]) if self.mode != "flag" else min(box * 1.15 / a.shape[1], box / a.shape[0])
        nw, nh = max(1, int(a.shape[1] * f)), max(1, int(a.shape[0] * f))
        im = Image.fromarray((a * 255).astype(np.uint8)).resize((nw, nh), Image.LANCZOS)
        out = Image.new("L", (self.W, self.H), 0)
        out.paste(im, (int(cx * self.W - nw / 2), int(cy * self.H - nh / 2)))
        return np.asarray(out, float) / 255

    def charge(self, name, c, scale="0.8", x="0.5", y="0.5"):
        a = charge_mask(self.atlas, self.mode, name)
        self.fill(self.place(a, float(scale), float(x), float(y)), c)

    def figure(self, op, c):
        m, W, H = self.m, self.W, self.H
        cx, cy = W / 2, H / 2

        def draw(dr):
            if op == "wheel":
                r, th = 0.3 * m, 0.07 * m
                dr.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
                dr.ellipse((cx - r + th, cy - r + th, cx + r - th, cy + r - th), fill=0)
                for k in range(6):
                    ang = np.pi / 6 + k * np.pi / 3
                    dr.line((cx, cy, cx + np.cos(ang) * r, cy + np.sin(ang) * r), fill=255, width=int(th * 0.8))
                dr.ellipse((cx - th, cy - th, cx + th, cy + th), fill=255)
            elif op == "escarbuncle":
                r, th = 0.38 * m, 0.045 * m
                for k in range(8):
                    ang = k * np.pi / 4
                    ex, ey = cx + np.cos(ang) * r, cy + np.sin(ang) * r
                    dr.line((cx, cy, ex, ey), fill=255, width=int(th))
                    dr.ellipse((ex - th * 1.4, ey - th * 1.4, ex + th * 1.4, ey + th * 1.4), fill=255)
                dr.ellipse((cx - th * 2.6, cy - th * 2.6, cx + th * 2.6, cy + th * 2.6), fill=255)
                dr.ellipse((cx - th * 1.3, cy - th * 1.3, cx + th * 1.3, cy + th * 1.3), fill=0)
            elif op == "rose":
                r = 0.13 * m
                for k in range(5):
                    ang = -np.pi / 2 + k * 2 * np.pi / 5
                    px, py = cx + np.cos(ang) * r, cy + np.sin(ang) * r
                    dr.ellipse((px - r, py - r, px + r, py + r), fill=255)
            elif op in ("star", "stars3"):
                spots = [(cx, cy, 0.3 * m)] if op == "star" else \
                    [(W * 0.3, H * 0.6, 0.13 * m), (W * 0.7, H * 0.6, 0.13 * m), (W * 0.5, H * 0.83, 0.13 * m)]
                for sx, sy, r in spots:
                    pts = []
                    for k in range(12):
                        ang = -np.pi / 2 + k * np.pi / 6
                        rr = r if k % 2 == 0 else r * 0.45
                        pts.append((sx + np.cos(ang) * rr, sy + np.sin(ang) * rr))
                    dr.polygon(pts, fill=255)
            elif op == "crosier":
                th = 0.06 * m
                top, bot = cy - 0.25 * m, cy + 0.4 * m
                dr.rectangle((cx - th / 2, top, cx + th / 2, bot), fill=255)
                r = 0.13 * m
                dr.arc((cx - 2 * r + th / 2, top - r * 2, cx + th / 2, top), 180, 360 + 90, fill=255, width=int(th))
                dr.arc((cx - 2 * r + th / 2, top - r * 2, cx + th / 2, top), 0, 360, fill=255, width=int(th))
            elif op == "pillar":
                th = 0.1 * m
                dr.rectangle((cx - th / 2, cy - 0.25 * m, cx + th / 2, cy + 0.25 * m), fill=255)
                for k, wd in enumerate((0.42, 0.32, 0.22)):
                    y0 = cy + 0.25 * m + k * 0.05 * m
                    dr.rectangle((cx - wd * m / 2, y0, cx + wd * m / 2, y0 + 0.05 * m), fill=255)
                dr.rectangle((cx - 0.12 * m, cy - 0.3 * m, cx + 0.12 * m, cy - 0.25 * m), fill=255)
                dr.rectangle((cx - th / 3, cy - 0.42 * m, cx + th / 3, cy - 0.3 * m), fill=255)
                dr.rectangle((cx - 0.07 * m, cy - 0.39 * m, cx + 0.07 * m, cy - 0.35 * m), fill=255)
            elif op == "gonfanon":
                w0, h0 = 0.62 * m, 0.38 * m
                x0, y0 = cx - w0 / 2, cy - 0.3 * m
                dr.rectangle((x0, y0, x0 + w0, y0 + h0), fill=255)
                for k in range(3):
                    tx = x0 + k * w0 / 3
                    dr.polygon([(tx, y0 + h0), (tx + w0 / 3 * 0.8, y0 + h0), (tx + w0 / 6, y0 + h0 + 0.3 * m)], fill=255)
                for k in range(3):
                    rx = x0 + w0 / 6 + k * w0 / 3
                    dr.ellipse((rx - 0.04 * m, y0 - 0.08 * m, rx + 0.04 * m, y0), outline=255, width=int(0.02 * m))
            elif op == "antlers":
                for k in range(3):
                    y0 = H * (0.25 + 0.25 * k)
                    x0, x1 = cx - 0.36 * m, cx + 0.36 * m
                    th = 0.05 * m
                    dr.line((x0, y0, x1, y0 - 0.04 * m), fill=255, width=int(th))
                    for j in range(4):
                        tx = x0 + (j + 0.6) * (x1 - x0) / 4.4
                        dr.line((tx, y0 - j * 0.01 * m, tx + 0.04 * m, y0 - 0.11 * m), fill=255, width=int(th * 0.8))
            else:
                raise SystemExit(f"unknown arms layer {op}")
        self.fill(self.shapes(draw), c)

    def result(self):
        img = Image.fromarray(np.clip(self.img, 0, 255).astype(np.uint8))
        return img.resize((self.w, self.h), Image.BOX)


def paint(spec, mode, atlas, pal):
    w, h = MODES[mode]
    p = Painter(w, h, mode, atlas, pal)
    for token in spec.split():
        op, *args = token.split(":")
        p.layer(op, args)
    return p.result()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("vanilla", type=Path, help="vanilla Maps/europe (coatOfArmsAtlas*.png)")
    ap.add_argument("hre", type=Path)
    ap.add_argument("mod", type=Path)
    ap.add_argument("--preview", type=Path)
    args = ap.parse_args()
    out = args.mod / "Maps" / "europe"
    arms = {r["kingdom"]: r["arms"] for r in csv.DictReader(open(args.hre / "kingdom_arms.csv", encoding="utf-8"))
            if r["arms"]}
    crest = {r[0]: int(r[1]) for r in csv.reader(open(out / "CoA.csv", encoding="utf-8")) if r and r[0] in arms}
    missing = sorted(set(arms) - set(crest))
    if missing:
        raise SystemExit(f"no CoA.csv tile for {', '.join(missing)} (run import_hre.py first)")

    atlases = {m: Image.open(args.vanilla / f"coatOfArmsAtlas{m}.png").convert("RGB") for m in MODES}
    pal = sample_palette(atlases["shield"])
    sheet_tiles = []
    for k in sorted(arms):
        row = []
        for mode in ("shield", "flag", "banner"):
            img = paint(arms[k], mode, atlases[mode], pal)
            atlases[mode].paste(img, tile_box(atlases[mode], crest[k], *MODES[mode])[:2])
            row.append(img)
            if mode == "shield":
                small = img.resize(MODES["small_shield"], Image.LANCZOS)
                atlases["small_shield"].paste(small, tile_box(atlases["small_shield"], crest[k], *MODES["small_shield"])[:2])
        sheet_tiles.append((k, row))
    for mode, img in atlases.items():
        img.save(out / f"CoatOfArmsAtlas{mode}.png")
    print(f"{len(arms)} kingdom arms painted into 4 atlases -> {out}")

    if args.preview:
        cols = 6
        cw, chh = 72 + 128 + 64 + 24, 128 + 8
        rows = (len(sheet_tiles) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cw, rows * chh), "white")
        dr = ImageDraw.Draw(sheet)
        for i, (k, (s, f, b)) in enumerate(sheet_tiles):
            x, y = (i % cols) * cw, (i // cols) * chh
            sheet.paste(s, (x, y))
            sheet.paste(f, (x + 76, y))
            sheet.paste(b, (x + 208, y))
            dr.text((x, y + 100), k, fill=(0, 0, 0))
        sheet.save(args.preview)


if __name__ == "__main__":
    main()
