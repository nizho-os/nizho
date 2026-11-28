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

After 3.05 s the "Horizon" beats follow (timings T below, from the approved preview): a line of light
draws out under the mark, the letters of NIZHO rise from it one by one, the line opens in the middle
and OPERATING SYSTEM settles into the gap with its letters drawing together, and a glint runs once
across the name. The line, the letters, the tagline glyphs and the glint frames are drawn here; where
they stand and how they move is nizho.script (opacity, position and width only).

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
N_WGLINT = 28       # frames of the glint that runs once across the name (1.1 s at 25 fps)

# The Horizon beats, in seconds (the approved preview, ~/nizho-film/src/paint/Boot.tsx), and its
# geometry in units, centre-relative: the preview's y minus 540.
T = {"line": 3.0, "letters": 3.3, "open": 4.25, "tag": 4.75, "glint": 5.2}
MARK_H = 300                    # the mark's frame, shown this tall (units)
MARK_DY, WORD_DY, LINE_DY = 420 - 540, 668 - 540, 725 - 540   # LINE_DY: the line's middle; it is 2 units thick
LINE_HALF = 280                 # each half of the line when fully drawn (units)
LINE_PAD = 18                   # room round the line for its glow (units)
WORD_SIZE, WORD_TRACK = 70, 80  # NIZHO: font size and letter gap (units)
WORD_RISE = 34                  # how far each letter rises from (units); used by nizho.script
TAG = "OPERATING SYSTEM"
TAG_SIZE = 19                   # Inter 500, units
TAG_BASE_DY = 729.4 - 540       # the tagline's baseline, centre-relative (units)
TAG_TRACK = (0.62, 0.42)        # letter gap in ems: where it starts and where it settles
INK = (236, 241, 239)           # #ECF1EF


def clamp(v):
    return max(0.0, min(1.0, v))


def ease(v):
    v = clamp(v)
    return v * v * (3 - 2 * v)


def ease_io(v):
    v = clamp(v)
    return 4 * v ** 3 if v < 0.5 else 1 - (-2 * v + 2) ** 3 / 2


def bezier(x1, y1, x2, y2, x):
    """CSS cubic-bezier(x1, y1, x2, y2) at x (0..1)."""
    lo, hi = 0.0, 1.0
    for _ in range(40):
        s_ = (lo + hi) / 2
        if 3 * (1 - s_) ** 2 * s_ * x1 + 3 * (1 - s_) * s_ ** 2 * x2 + s_ ** 3 < x:
            lo = s_
        else:
            hi = s_
    s_ = (lo + hi) / 2
    return 3 * (1 - s_) ** 2 * s_ * y1 + 3 * (1 - s_) * s_ ** 2 * y2 + s_ ** 3


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

    # ---- the name: one picture per letter, each on the whole word's canvas so they line up. They are
    # drawn 25 % brighter than the splash green (the approved preview's brightness), so the rise has
    # no brightness step to play. ----
    f = inter_medium(px(WORD_SIZE))
    track = px(WORD_TRACK)
    word = "NIZHO"
    widths = [f.getlength(ch) for ch in word]
    total = sum(widths) + track * (len(word) - 1)
    cw, chh = int(total + px(80)), px(140)
    x = (cw - total) / 2
    bright = tuple(min(255, int(round(c * 1.25))) for c in G_HEX)
    letters = []
    for i, (ch, w) in enumerate(zip(word, widths)):
        img = Image.new("RGBA", (cw, chh))
        ImageDraw.Draw(img).text((x + w / 2, chh / 2), ch, font=f, fill=bright + (255,), anchor="mm")
        img.save(os.path.join(out, f"l{i}.png"), optimize=True)
        letters.append(np.asarray(img)[..., 3].astype(np.float32) / 255)
        x += w + track
    # The letters rise WORD_RISE units and stay above the line, except for the first moments, when
    # they are nearly transparent; check how much of a glyph could dip below it.
    bottom = max(np.nonzero(a > 0.15)[0].max() for a in letters) / A + (WORD_DY - chh / A / 2)
    dip = bottom + WORD_RISE - (LINE_DY - 2)
    print(f"make-assets: letters end {bottom:.1f} u under the screen centre; at the start of the rise {dip:.1f} u would "
          f"cross the line (opacity there is under 0.1)")

    # ---- the glint across the name: a soft band, 100 degrees, masked to the letters ----
    union = np.maximum.reduce(letters)
    yy, xx = np.mgrid[0:chh, 0:cw].astype(np.float32)
    th = np.deg2rad(100)
    glen = abs(cw * np.sin(th)) + abs(chh * np.cos(th))
    pct = 0.5 + ((xx - cw / 2) * np.sin(th) - (yy - chh / 2) * np.cos(th)) / glen
    spark = np.array([240, 255, 250]) / 255
    base = np.array(bright) / 255
    shine = 1 - (1 - base) * (1 - spark)         # the letters with the band at full strength, screened
    for j in range(N_WGLINT):
        gx = -0.25 + 1.5 * bezier(0.65, 0, 0.35, 1, j / (N_WGLINT - 1))
        al = np.clip(1 - np.abs(pct - gx) / 0.14, 0, 1) * union
        rgba = np.dstack([np.broadcast_to(shine, (chh, cw, 3)), al])
        Image.fromarray((rgba * 255 + 0.5).astype(np.uint8), "RGBA").save(os.path.join(out, f"s{j}.png"), optimize=True)

    # ---- OPERATING SYSTEM: one picture per glyph (Inter 500), so the letters drawing together is only
    # glyph positions. Advances include kerning; nizho.script adds the letter gap. ----
    tf = inter_medium(px(TAG_SIZE))
    tpad, tbase, th_ = px(4), px(24), px(32)
    adv = [(tf.getlength(TAG[:j + 1]) - tf.getlength(TAG[:j])) / A for j in range(len(TAG))]
    has = []
    for j, ch in enumerate(TAG):
        has.append(0 if ch == " " else 1)
        if ch == " ":
            continue
        gw = int(np.ceil(adv[j] * A)) + 2 * tpad
        img = Image.new("RGBA", (gw, th_))
        ImageDraw.Draw(img).text((tpad, tbase), ch, font=tf, fill=INK + (255,), anchor="ls")
        img.save(os.path.join(out, f"o{j}.png"), optimize=True)

    # ---- the horizon line: its two halves as gradient pictures, drawn at full length; nizho.script
    # scales them by width. Outer end clear, brightest at the centre end, with a soft glow round it. ----
    lw, lh = int(round((LINE_HALF + 2 * LINE_PAD) * A)), int(round((2 + 2 * LINE_PAD) * A))
    body = np.zeros((lh, lw), np.float32)
    r0, r1 = int(round(LINE_PAD * A)), int(round((LINE_PAD + 2) * A))
    c0, c1 = int(round(LINE_PAD * A)), int(round((LINE_PAD + LINE_HALF) * A))
    u = (np.arange(c0, c1) - c0 + 0.5) / (c1 - c0)           # 0 at the outer end, 1 at the centre end
    body[r0:r1, c0:c1] = np.clip(u / 0.7, 0, 1)[None, :]
    col = np.where((u < 0.7)[:, None], np.array([123, 224, 184]),
                   np.array([123, 224, 184]) + (u[:, None] - 0.7) / 0.3 * (np.array([200, 255, 232]) - np.array([123, 224, 184]))) / 255
    colour = np.zeros((lh, lw, 3), np.float32); colour[:] = np.array([123, 224, 184]) / 255
    colour[r0:r1, c0:c1] = col[None]
    rect = np.zeros((lh, lw), np.float32); rect[r0:r1, c0:c1] = 1
    glow = blur(rect, 6 * A) * 0.5                           # box-shadow 0 0 12px at half strength
    a_out = body + glow * (1 - body)
    rgb_out = (colour * body[..., None] + (G_HEX[0] / 255, G_HEX[1] / 255, G_HEX[2] / 255) * (glow * (1 - body))[..., None]) \
        / np.maximum(a_out, 1e-4)[..., None]
    left = np.dstack([np.clip(rgb_out, 0, 1), a_out])
    for name, arr in (("lineL.png", left), ("lineR.png", left[:, ::-1])):
        Image.fromarray((arr * 255 + 0.5).astype(np.uint8), "RGBA").save(os.path.join(out, name), optimize=True)

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
    h = m.rh
    lines = [f"global.NA = {n};", f"global.NG = {N_GLINT};", f"global.NW = {N_WGLINT};", f"global.FPS = {FPS};",
             f"global.A = {A};", f"global.AF = {AF};",
             f"global.MARK_K = {MARK_H / h:.6f};",
             f"global.MARK_DY = {MARK_DY};", f"global.WORD_DY = {WORD_DY};", f"global.LINE_DY = {LINE_DY};",
             f"global.LINE_HALF = {LINE_HALF};", f"global.LINE_PAD = {LINE_PAD};", f"global.WORD_RISE = {WORD_RISE};",
             f"global.T_LINE = {T['line']};", f"global.T_LETTERS = {T['letters']};", f"global.T_OPEN = {T['open']};",
             f"global.T_TAG = {T['tag']};", f"global.T_GLINT = {T['glint']};",
             f"global.TAG_N = {len(TAG)};", f"global.TAG_SIZE = {TAG_SIZE};",
             f"global.TAG_TRACK0 = {TAG_TRACK[0]};", f"global.TAG_TRACK1 = {TAG_TRACK[1]};",
             f"global.TAG_PAD = {tpad / A:.4f};", f"global.TAG_BASE = {tbase / A:.4f};", f"global.TAG_BASE_DY = {TAG_BASE_DY:.4f};",
             f"global.TAG_SUM = {sum(adv):.4f};"]
    lines += [f"global.TAG_ADV[{j}] = {a:.4f};" for j, a in enumerate(adv)]
    lines += [f"global.TAG_HAS[{j}] = {v};" for j, v in enumerate(has)]
    script = script.replace(marker, "\n".join(lines))
    open(os.path.join(out, "nizho.script"), "w", encoding="utf-8").write(script)
    shutil.copy(os.path.join(src, "nizho.plymouth"), os.path.join(out, "nizho.plymouth"))
    print(f"make-assets: {n} opening frames {m.rw}x{m.rh}, {N_GLINT} ribbon glint + {N_WGLINT} name glint frames")


if __name__ == "__main__":
    main()
