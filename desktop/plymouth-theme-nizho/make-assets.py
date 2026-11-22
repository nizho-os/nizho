#!/usr/bin/env python3
"""Draw the Nizho boot splash ("Awakening") ahead of time.

    python3 make-assets.py SRC OUT

SRC holds logo-src.png, nizho.script and nizho.plymouth; OUT receives the pictures, the script and
the theme file. Plymouth cannot trace outlines or mask while it runs, so the opening is drawn here as
frames that nizho.script plays:

1. a point of light wakes at the top of the ribbon;
2. two pens of light trace the true outline of each part of the mark (the ribbon and the two
   stones), running in opposite directions and meeting;
3. the light floods inward from the outlines and becomes the mark; the outlines fade.

Units: one unit is one pixel of a 1080-line screen. The frames are drawn at AF pixels a unit and
everything else at A; nizho.script scales both to the screen.
"""
import os
import shutil
import subprocess
import sys
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

A = 1.5             # pixels a unit: letters, prompt box, dots
AF = 1.0            # pixels a unit: the animation frames (soft light; small keeps memory low)
FPS = 25            # frames a second of the opening
S = 280             # height of the mark, in units
MARGIN = 44         # room round the mark for the glow, in units
SS = 3              # supersampling for the outlines

GREEN = np.array([0.302, 0.706, 0.569])      # #4db491, Nizho's splash green
LIGHT = np.array([0.88, 1.0, 0.95])
G_HEX = (77, 180, 145)

# Timeline of the opening in seconds (the same as the approved preview).
T_DRAW = [(0.35, 1.85), (0.55, 1.95), (0.65, 2.05)]     # ribbon, left stone, right stone
T_FILL = (1.75, 2.75)
T_RIM = (2.35, 3.05)
T_END = 3.05
N_GLINT = 28


def clamp(v):
    return max(0.0, min(1.0, v))


def ease(v):
    v = clamp(v)
    return v * v * (3 - 2 * v)


def ease_io(v):
    v = clamp(v)
    return 4 * v ** 3 if v < 0.5 else 1 - (-2 * v + 2) ** 3 / 2


def span(t, a, b):
    return clamp((t - a) / (b - a))


# ---- the logo, cut into its three parts --------------------------------------------------------
def grow(m):
    g = m.copy()
    g[1:] |= m[:-1]; g[:-1] |= m[1:]; g[:, 1:] |= m[:, :-1]; g[:, :-1] |= m[:, 1:]
    return g


def erode(m):
    e = m.copy()
    e[1:] &= m[:-1]; e[:-1] &= m[1:]; e[:, 1:] &= m[:, :-1]; e[:, :-1] &= m[:, 1:]
    return e


def components(mask):
    lab = np.zeros(mask.shape, np.int32)
    sizes = [0]
    h, w = mask.shape
    for y, x in zip(*np.nonzero(mask)):
        if lab[y, x]:
            continue
        n = len(sizes); sizes.append(0)
        q = deque([(y, x)]); lab[y, x] = n
        while q:
            cy, cx = q.popleft(); sizes[n] += 1
            for yy, xx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not lab[yy, xx]:
                    lab[yy, xx] = n; q.append((yy, xx))
    return lab, sizes


def cut_logo(path):
    """The logo's pixels (RGBA floats, cropped) and masks of the ribbon, left stone and right stone."""
    im = np.asarray(Image.open(path).convert("RGBA")).astype(np.int32)
    ys, xs = np.nonzero(im[..., 3] > 20)
    im = im[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    r, g, b, a = (im[..., i] for i in range(4))
    vis = a > 20
    ribbon = (g > np.maximum(r, b) * 1.12 + 3) & vis
    lab, sizes = components(vis & ~ribbon)
    big = [int(i) for i in np.argsort(sizes)[-2:]]
    if min(sizes[i] for i in big) < 0.1 * vis.sum():
        sys.exit("make-assets: logo-src.png does not have two stones")
    cx = [np.nonzero(lab == i)[1].mean() for i in big]
    li, ri = (big if cx[0] < cx[1] else big[::-1])
    left, right = lab == li, lab == ri
    # The ribbon's bright crease is grey-white, so it lands with the left stone: anything of the
    # left stone right of the stone's own columns is the crease.
    xx = np.arange(im.shape[1])[None, :]
    edge = int(np.nonzero(left.sum(0) > 0.1 * left.sum(0).max())[0].max()) + 3
    crease = left & (xx > edge)
    left &= ~crease; ribbon |= crease
    parts = [ribbon, left, right]
    soft = im[..., 3] > 2
    for _ in range(6):                     # the anti-aliased fringe goes to the nearest part
        taken = parts[0] | parts[1] | parts[2]
        parts = [p | (grow(p) & soft & ~taken) for p in parts]
    parts[0] |= soft & ~(parts[0] | parts[1] | parts[2])
    return im.astype(np.float32) / 255, parts


def inner_distance(m):
    d = np.zeros(m.shape, np.float32); cur = m.copy()
    while cur.any():
        d += cur; cur = erode(cur)
    return d


def trace(mask):
    """The outer boundary of a part, in order (Moore neighbour tracing), as (y, x) points."""
    h, w = mask.shape
    pad = np.zeros((h + 2, w + 2), bool); pad[1:-1, 1:-1] = mask
    ys, xs = np.nonzero(pad)
    i = np.lexsort((xs, ys))[0]
    start = (int(ys[i]), int(xs[i]))
    dirs = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]
    p, b = start, (start[0], start[1] - 1)
    out = [p]
    for _ in range(400000):
        k = dirs.index((b[0] - p[0], b[1] - p[1]))
        for j in range(1, 9):
            k2 = (k + j) % 8
            q = (p[0] + dirs[k2][0], p[1] + dirs[k2][1])
            if pad[q]:
                pb = dirs[(k2 - 1) % 8]
                b = (p[0] + pb[0], p[1] + pb[1]); p = q
                break
        if p == start:
            break
        out.append(p)
    return np.array(out, np.float32) - 1


def smooth_resample(c, n, win=9):
    c = np.concatenate([c[-win:], c, c[:win]])
    k = np.ones(win) / win
    c = np.stack([np.convolve(c[:, 0], k, "same"), np.convolve(c[:, 1], k, "same")], 1)[win:-win]
    closed = np.vstack([c, c[:1]])
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(closed, axis=0).T))])
    u = np.linspace(0, s[-1], n, endpoint=False)
    return np.stack([np.interp(u, s, closed[:, 0]), np.interp(u, s, closed[:, 1])], 1)


def blur(a, r):
    return np.asarray(Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(r))).astype(np.float32) / 255


class Mark:
    def __init__(self, logo):
        self.rgba, parts = cut_logo(logo)
        self.parts = [p.astype(np.float32) for p in parts]
        self.dist = [inner_distance(p) for p in parts]
        self.dmax = [d.max() for d in self.dist]
        h0, w0 = self.rgba.shape[:2]
        self.h0, self.w0 = h0, w0
        self.sc = S * AF / h0                                    # frame pixels a logo pixel
        self.rw = int(round(w0 * self.sc + 2 * MARGIN * AF))
        self.rh = int(round(h0 * self.sc + 2 * MARGIN * AF))
        self.ox = (self.rw - w0 * self.sc) / 2
        self.oy = (self.rh - h0 * self.sc) / 2
        self.cont = []
        for p in parts:
            big = Image.fromarray((p * 255).astype(np.uint8)).resize((w0 * 3, h0 * 3), Image.LANCZOS)
            self.cont.append(smooth_resample(trace(np.asarray(big) > 127) / 3, 1400))
        rib = self.cont[0]
        self.apex = rib[np.argmin(rib[:, 0])]
        self.starts = [int(np.argmin(np.hypot(c[:, 0] - self.apex[0], c[:, 1] - self.apex[1]))) for c in self.cont]
        yy, xx = np.mgrid[0:self.rh, 0:self.rw].astype(np.float32)
        px_, py_ = self.at(self.apex)
        self.r2 = (xx - px_) ** 2 + (yy - py_) ** 2
        full = self.layer([1, 1, 1], 0)
        self.halo = blur(full[..., 3], 20 * AF)

    def at(self, pt, ss=1):
        y, x = pt
        return ((self.ox + x * self.sc) * ss, (self.oy + y * self.sc) * ss)

    def layer(self, fill, flash):
        """The real mark, each part revealed from its outline inward by fill[k] (0..1)."""
        a = np.zeros((self.h0, self.w0), np.float32)
        for k in range(3):
            if fill[k] <= 0:
                continue
            thr = fill[k] * (self.dmax[k] + 18)
            a += self.parts[k] * np.clip((thr - self.dist[k]) / 18, 0, 1)
        src = self.rgba.copy()
        src[..., 3] *= np.clip(a, 0, 1)
        if flash > 0:
            w = (self.parts[0] * flash)[..., None]       # fresh light on the ribbon; stones stay stone
            src[..., :3] = src[..., :3] * (1 - w) + (GREEN * 0.4 + LIGHT * 0.6) * w
        im = Image.fromarray((np.clip(src, 0, 1) * 255).astype(np.uint8), "RGBA").resize(
            (int(round(self.w0 * self.sc)), int(round(self.h0 * self.sc))), Image.LANCZOS)
        out = Image.new("RGBA", (self.rw, self.rh))
        out.alpha_composite(im, (int(round(self.ox)), int(round(self.oy))))
        return np.asarray(out).astype(np.float32) / 255

    def strokes(self, draw, rim):
        img = Image.new("L", (self.rw * SS, self.rh * SS))
        d = ImageDraw.Draw(img)
        for c, s0, f in zip(self.cont, self.starts, draw):
            if f <= 0:
                continue
            n = len(c); reach = int(f * n / 2); tail = 0.10 * n
            for direction in (1, -1):
                pts = [self.at(c[(s0 + direction * i) % n], SS) for i in range(reach + 1)]
                for i in range(len(pts) - 1):
                    hot = np.exp(-(reach - i) / tail) * (1 if f < 1 else 0)
                    v = min(1.0, rim * 0.6 + hot)
                    if v > 0.01:
                        d.line([pts[i], pts[i + 1]], fill=int(255 * v),
                               width=max(1, int(2.0 * AF * SS + 2.8 * AF * SS * hot)))
        return np.asarray(img.resize((self.rw, self.rh), Image.LANCZOS)).astype(np.float32) / 255

    def frame(self, t):
        point = ease(span(t, 0.0, 0.5)) * (1 - ease(span(t, 0.5, 0.9)))
        draw = [ease_io(span(t, a, b)) for a, b in T_DRAW]
        fill = [ease_io(span(t, T_FILL[0] + 0.08 * k, T_FILL[1] + 0.08 * k)) for k in range(3)]
        rim = 1 - ease(span(t, *T_RIM))
        flash = 0.55 * (1 - ease(span(t, T_FILL[0] + 0.3, T_RIM[1])))
        L = np.zeros((self.rh, self.rw, 3), np.float32)
        lay = self.layer(fill, flash)
        ma = lay[..., 3]
        if ma.max() > 0:
            L += blur(ma, 20 * AF)[..., None] * GREEN * 0.10
            L = L * (1 - ma[..., None]) + lay[..., :3] * ma[..., None]
        s = self.strokes(draw, rim)
        if s.max() > 0:
            L += s[..., None] * (GREEN * 0.6 + LIGHT * 0.5 * s[..., None]) + blur(s, 4 * AF)[..., None] * GREEN * 0.8 \
                 + blur(s, 15 * AF)[..., None] * GREEN * 0.5
        if point > 0:
            L += (np.exp(-self.r2 / (2 * (3.2 * AF) ** 2)) * 1.3 * point)[..., None] * LIGHT \
                 + (np.exp(-self.r2 / (2 * (20 * AF) ** 2)) * 0.6 * point)[..., None] * GREEN
        return L, ma

    def glint(self, g):
        """A short light running along the ribbon's outline, G of the way round (0..1)."""
        c, s0 = self.cont[0], self.starts[0]
        img = Image.new("L", (self.rw * SS, self.rh * SS)); d = ImageDraw.Draw(img)
        n = len(c); head = int(g * n)
        for i in range(max(0, head - 90), head):
            v = 1 - (head - i) / 90
            d.line([self.at(c[(s0 + i) % n], SS), self.at(c[(s0 + i + 1) % n], SS)],
                   fill=int(255 * v), width=max(1, int(1.8 * AF * SS)))
        s = np.asarray(img.resize((self.rw, self.rh), Image.LANCZOS)).astype(np.float32) / 255
        return s[..., None] * LIGHT * 0.7 + blur(s, 6 * AF)[..., None] * GREEN * 0.6


def save_light(L, solid, path):
    """Save light drawn over black as a picture that looks the same over a dark screen: alpha is the
    brightest channel (or the mark's own coverage, which is solid), the colour is divided by it."""
    a = np.clip(np.maximum(L.max(-1), solid), 0, 1)
    rgb = np.where(a[..., None] > 1e-4, L / np.maximum(a[..., None], 1e-4), 0)
    rgba = np.dstack([np.clip(rgb, 0, 1), a])
    Image.fromarray((rgba * 255 + 0.5).astype(np.uint8), "RGBA").save(path, optimize=True)


def inter_medium(size):
    """Inter Medium from whichever Inter files fontconfig knows (Fedora ships the variable font)."""
    out = subprocess.run(["fc-list", ":", "file", "family"], capture_output=True, text=True).stdout
    files = sorted({l.partition(":")[0].strip() for l in out.splitlines()
                    if {f.strip() for f in l.partition(":")[2].split(",")} & {"Inter", "Inter Variable"}})
    files.sort(key=lambda f: (not f.endswith(".ttc"), "Variable" not in f, "Italic" in f))
    for path in files:
        if "Italic" in path:
            continue
        if path.endswith(".ttc"):
            for i in range(64):
                try:
                    f = ImageFont.truetype(path, size, index=i)
                except OSError:
                    break
                if f.getname()[0] in ("Inter", "Inter Variable") and f.getname()[1] == "Medium":
                    return f
        else:
            f = ImageFont.truetype(path, size)
            try:
                f.set_variation_by_name("Medium")
            except (OSError, ValueError):
                if f.getname()[1] != "Medium":
                    continue
            return f
    sys.exit("make-assets: Inter Medium not found")


def px(u):
    return int(round(u * A))


def main():
    src, out = sys.argv[1:3]
    os.makedirs(out, exist_ok=True)
    m = Mark(os.path.join(src, "logo-src.png"))

    # ---- the opening, frame by frame; the last frame is the mark at rest ----
    n = int(T_END * FPS) + 1
    for i in range(n):
        L, ma = m.frame(min(i / FPS, T_END))
        save_light(L, ma, os.path.join(out, f"a{i}.png"))
    # ---- the resting glow (breathes) and the glint that runs along the ribbon now and then ----
    save_light(m.halo[..., None] * GREEN * 0.16, 0, os.path.join(out, "halo.png"))
    for i in range(N_GLINT):
        save_light(m.glint((i + 0.5) / N_GLINT), 0, os.path.join(out, f"g{i}.png"))

    # ---- the name: one picture per letter, each on the whole word's canvas so they line up ----
    f = inter_medium(px(35))
    track = px(40)
    word = "NIZHO"
    widths = [f.getlength(ch) for ch in word]
    total = sum(widths) + track * (len(word) - 1)
    cw, chh = int(total + px(40)), px(70)
    x = (cw - total) / 2
    for i, (ch, w) in enumerate(zip(word, widths)):
        img = Image.new("RGBA", (cw, chh))
        ImageDraw.Draw(img).text((x + w / 2, chh / 2), ch, font=f, fill=G_HEX + (255,), anchor="mm")
        img.save(os.path.join(out, f"l{i}.png"), optimize=True)
        x += w + track

    # ---- the passphrase box and its dots, and the bar for system updates (Nizho's look) ----
    bw, bh = px(380), px(48)
    box = Image.new("RGBA", (bw, bh))
    ImageDraw.Draw(box).rounded_rectangle((1, 1, bw - 2, bh - 2), radius=px(10), fill=(15, 20, 28, 255),
                                          outline=(42, 52, 64, 255), width=max(1, px(1.5)))
    box.save(os.path.join(out, "entry.png"))
    dd = px(9)
    dimg = Image.new("RGBA", (dd * 4, dd * 4))
    ImageDraw.Draw(dimg).ellipse((0, 0, dd * 4 - 1, dd * 4 - 1), fill=G_HEX + (255,))
    dimg.resize((dd, dd), Image.LANCZOS).save(os.path.join(out, "dot.png"))
    Image.new("RGBA", (16, 6), G_HEX + (255,)).save(os.path.join(out, "bar.png"))
    Image.new("RGBA", (16, 6), (42, 52, 64, 255)).save(os.path.join(out, "barback.png"))

    # ---- nizho.script with the frame counts, and the theme file ----
    script = open(os.path.join(src, "nizho.script"), encoding="utf-8").read()
    marker = "// @FRAMES@"
    if script.count(marker) != 1:
        sys.exit("make-assets: nizho.script must hold exactly one '// @FRAMES@' line")
    script = script.replace(marker, f"global.NA = {n};\nglobal.NG = {N_GLINT};\nglobal.FPS = {FPS};\n"
                                     f"global.A = {A};\nglobal.AF = {AF};")
    open(os.path.join(out, "nizho.script"), "w", encoding="utf-8").write(script)
    shutil.copy(os.path.join(src, "nizho.plymouth"), os.path.join(out, "nizho.plymouth"))
    print(f"make-assets: {n} opening frames {m.rw}x{m.rh}, {N_GLINT} glint frames")


if __name__ == "__main__":
    main()
