"""ReRoot demo v2: Gemini-TTS narration, word-synced cues, spring motion graphics, karaoke captions.

    uv run --with numpy python render2.py VIDEO_DIR [--preview] [--audio-only] [--only N]
"""

import base64
import bisect
import hashlib
import io
import json
import math
import re
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

V = Path(sys.argv[1])
sys.path.insert(0, str(V))
import script2 as S  # noqa: E402

PREVIEW = "--preview" in sys.argv
FPS = 10 if PREVIEW else 30
OW, OH = 1920, 1080
SR = 24000
REC = V / getattr(S, "REC_DIR", "rec2")
ASSETS = V / "assets"
CACHE = V / "cache2"
CACHE.mkdir(exist_ok=True)

# ---------- look ----------
BG_TOP, BG_BOT = (8, 30, 21), (3, 11, 8)
GREEN, LEAF, MINT = (34, 197, 94), (134, 239, 172), (187, 247, 208)
AMBER, RED, BLUE, SKY = (245, 158, 11), (239, 68, 68), (59, 130, 246), (125, 211, 252)
WHITE, MUTED, INK = (255, 255, 255), (160, 186, 172), (6, 20, 12)
BROWN = (146, 104, 62)
AV = "/System/Library/Fonts/Avenir Next.ttc"
WEIGHT = {"bold": 0, "demi": 2, "medium": 5, "regular": 7, "heavy": 8}
COLORS = {"green": GREEN, "amber": AMBER, "blue": BLUE, "red": RED}


@lru_cache(maxsize=None)
def font(size, weight="demi"):
    return ImageFont.truetype(AV, max(8, int(size)), index=WEIGHT[weight])


def fit_font(text, max_w, size, weight="demi"):
    while size > 12 and font(size, weight).getlength(text) > max_w:
        size -= 2
    return font(size, weight)


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease(x):
    x = clamp(x)
    return x * x * (3 - 2 * x)


def ease_out(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def spring(x, k=1.9):
    """easeOutBack: overshoots then settles (the 'pop')."""
    x = clamp(x)
    c3 = k + 1
    return 1 + c3 * (x - 1) ** 3 + k * (x - 1) ** 2


def bounce(x):
    x = clamp(x)
    n, d = 7.5625, 2.75
    if x < 1 / d:
        return n * x * x
    if x < 2 / d:
        x -= 1.5 / d
        return n * x * x + 0.75
    if x < 2.5 / d:
        x -= 2.25 / d
        return n * x * x + 0.9375
    x -= 2.625 / d
    return n * x * x + 0.984375


def popv(t, t0, d=0.42):
    return 0.0 if t < t0 else spring((t - t0) / d)


def fadev(t, t0, d=0.25):
    return 0.0 if t < t0 else ease_out((t - t0) / d)


def lerp(a, b, f):
    return a + (b - a) * f


# ---------- sprites ----------

def composite(frame, sprite, x, y):
    """alpha_composite a sprite whose top-left is (x, y), cropping at the frame edges."""
    x, y = int(round(x)), int(round(y))
    w, h = sprite.size
    sx, sy = max(0, -x), max(0, -y)
    ex, ey = min(w, OW - x), min(h, OH - y)
    if ex <= sx or ey <= sy:
        return
    frame.alpha_composite(sprite, (x + sx, y + sy), (sx, sy, ex, ey))


def place(frame, sprite, cx, cy, scale=1.0, alpha=1.0, rot=0.0):
    if scale <= 0.02 or alpha <= 0.01:
        return
    sp = sprite
    if abs(scale - 1) > 0.005:
        sp = sp.resize((max(1, int(sp.width * scale)), max(1, int(sp.height * scale))), Image.BICUBIC)
    if rot:
        sp = sp.rotate(rot, resample=Image.BICUBIC, expand=True)
    if alpha < 0.999:
        a = sp.getchannel("A").point(lambda v: int(v * alpha))
        sp = sp.copy()
        sp.putalpha(a)
    composite(frame, sp, cx - sp.width / 2, cy - sp.height / 2)


@lru_cache(maxsize=512)
def text_sprite(s, size, weight="demi", color=WHITE, max_w=None, shadow=True):
    f = fit_font(s, max_w, size, weight) if max_w else font(size, weight)
    l, t, r, b = f.getbbox(s)
    pad = 14
    im = Image.new("RGBA", (r - l + pad * 2, b - t + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if shadow:
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((pad - l + 2, pad - t + 4), s, font=f, fill=(0, 0, 0, 150))
        im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(5)))
    d.text((pad - l, pad - t), s, font=f, fill=color)
    return im


@lru_cache(maxsize=64)
def pill_sprite(s, size, fg, bg, max_w=None):
    f = fit_font(s, max_w - 40 if max_w else 10_000, size, "bold")
    tw = f.getlength(s)
    h = int(size * 1.75)
    im = Image.new("RGBA", (int(tw + h * 0.9), h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, im.width - 1, h - 1], radius=h // 2, fill=bg)
    d.text((im.width / 2, h / 2), s, font=f, fill=fg, anchor="mm")
    return im


@lru_cache(maxsize=32)
def card_sprite(w, h, fill=(255, 255, 255, 18), outline=(255, 255, 255, 52), r=28):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=fill, outline=outline, width=2)
    return im


@lru_cache(maxsize=32)
def tree_sprite(size, color=(52, 168, 83), dark=(30, 120, 60)):
    s = size
    im = Image.new("RGBA", (s, int(s * 1.25)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([s * 0.44, s * 0.7, s * 0.56, s * 1.22], radius=int(s * 0.04), fill=(120, 84, 52))
    d.ellipse([s * 0.12, s * 0.28, s * 0.88, s * 0.92], fill=dark)
    d.ellipse([s * 0.18, s * 0.02, s * 0.82, s * 0.66], fill=color)
    d.ellipse([s * 0.3, s * 0.12, s * 0.5, s * 0.3], fill=tuple(min(255, c + 60) for c in color))
    return im


@lru_cache(maxsize=32)
def sapling_sprite(size, color=(74, 222, 128)):
    s = size
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.line([(s * 0.5, s * 0.95), (s * 0.5, s * 0.42)], fill=(110, 80, 50), width=max(2, s // 12))
    d.ellipse([s * 0.12, s * 0.28, s * 0.52, s * 0.56], fill=color)
    d.ellipse([s * 0.48, s * 0.12, s * 0.9, s * 0.44], fill=color)
    d.ellipse([s * 0.3, s * 0.85, s * 0.7, s * 1.0], fill=(80, 60, 40))
    return im


@lru_cache(maxsize=8)
def drop_sprite(size, color=SKY):
    s = size
    im = Image.new("RGBA", (s, int(s * 1.35)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.polygon([(s * 0.5, 0), (s * 0.08, s * 0.72), (s * 0.92, s * 0.72)], fill=color)
    d.ellipse([s * 0.06, s * 0.42, s * 0.94, s * 1.3], fill=color)
    d.ellipse([s * 0.26, s * 0.62, s * 0.42, s * 0.86], fill=(255, 255, 255, 170))
    return im


@lru_cache(maxsize=8)
def server_sprite(w, h, blink):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=22, fill=(30, 41, 59), outline=(100, 116, 139), width=3)
    rows = 5
    for i in range(rows):
        y0 = 22 + i * (h - 44) / rows
        d.rounded_rectangle([20, y0, w - 20, y0 + (h - 44) / rows - 12], radius=10, fill=(51, 65, 85))
        for k in range(3):
            on = (blink + i * 2 + k) % 4 != 0
            d.ellipse([w - 70 + k * 16, y0 + 18, w - 60 + k * 16, y0 + 28], fill=(34, 197, 94) if on else (20, 60, 30))
        d.rectangle([34, y0 + 16, w * 0.5, y0 + 30], fill=(71, 85, 105))
    return im


@lru_cache(maxsize=8)
def lake_sprite(w, h):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([0, 0, w - 1, h - 1], fill=(37, 99, 235), outline=(147, 197, 253), width=3)
    d.arc([w * 0.2, h * 0.25, w * 0.6, h * 0.6], 200, 330, fill=(191, 219, 254), width=3)
    d.arc([w * 0.45, h * 0.45, w * 0.85, h * 0.8], 200, 330, fill=(191, 219, 254), width=3)
    return im


@lru_cache(maxsize=4)
def leaf_sprite(size):
    s = size
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.28), fill=(22, 101, 52))
    d.ellipse([s * 0.26, s * 0.2, s * 0.78, s * 0.72], fill=WHITE)
    d.polygon([(s * 0.26, s * 0.78), (s * 0.5, s * 0.5), (s * 0.55, s * 0.55)], fill=WHITE)
    d.line([(s * 0.3, s * 0.74), (s * 0.66, s * 0.36)], fill=(22, 101, 52), width=max(2, int(s * 0.05)))
    return im


@lru_cache(maxsize=8)
def stamp_sprite(text, size, color):
    f = font(size, "heavy")
    tw = f.getlength(text)
    w, h = int(tw + size * 1.4), int(size * 1.9)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([4, 4, w - 5, h - 5], radius=18, outline=color + (255,), width=10)
    d.text((w / 2, h / 2), text, font=f, fill=color + (255,), anchor="mm")
    rng = np.random.default_rng(3)  # worn ink
    a = np.array(im.getchannel("A"))
    a[rng.random(a.shape) < 0.07] = 0
    im.putalpha(Image.fromarray(a))
    return im


def burst(frame, cx, cy, t, t0, color, n=14, radius=150, size=12, life=0.6, seed=0):
    dt = t - t0
    if dt < 0 or dt > life:
        return
    f = dt / life
    rng = np.random.default_rng(seed)
    d = ImageDraw.Draw(frame)
    for i in range(n):
        ang = 2 * math.pi * i / n + rng.random() * 0.5
        dist = radius * ease_out(f) * (0.7 + rng.random() * 0.5)
        r = size * (1 - f) * (0.6 + rng.random() * 0.6)
        x, y = cx + math.cos(ang) * dist, cy + math.sin(ang) * dist
        c = color + (int(255 * (1 - f)),)
        d.ellipse([x - r, y - r, x + r, y + r], fill=c)


def ring(frame, cx, cy, t, t0, color, r0=20, r1=180, life=0.7, width=6):
    dt = t - t0
    if dt < 0 or dt > life:
        return
    f = ease_out(dt / life)
    r = lerp(r0, r1, f)
    ImageDraw.Draw(frame).ellipse([cx - r, cy - r, cx + r, cy + r], outline=color + (int(230 * (1 - f)),), width=width)


_BG = {}


def background(t):
    key = int(t * 12)
    if key in _BG:
        return _BG[key].copy()
    w, h = 384, 216
    g = np.linspace(0, 1, h)[:, None, None]
    arr = (np.array(BG_TOP) * (1 - g) + np.array(BG_BOT) * g) * np.ones((h, w, 1))
    yy, xx = np.mgrid[0:h, 0:w]
    tt = key / 12
    for (cx, cy, r, col, sp) in [(0.2, 0.3, 0.35, (22, 101, 52), 0.21), (0.8, 0.7, 0.4, (14, 116, 144), 0.17), (0.6, 0.15, 0.25, (21, 128, 61), 0.27)]:
        px = (cx + 0.06 * math.sin(tt * sp * 6.28)) * w
        py = (cy + 0.06 * math.cos(tt * sp * 5.1)) * h
        m = np.exp(-(((xx - px) ** 2 + (yy - py) ** 2) / (2 * (r * w) ** 2)))[..., None]
        arr = arr * (1 - 0.45 * m) + np.array(col) * 0.45 * m
    img = Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB").resize((OW, OH), Image.BILINEAR).convert("RGBA")
    if len(_BG) < 400:
        _BG[key] = img
    return img.copy()


def headline(frame, t, t0, text, y, size=72, color=WHITE, x=None, max_w=1700, weight="bold"):
    s = popv(t, t0)
    if s <= 0:
        return
    sp = text_sprite(text, size, weight, color, max_w)
    place(frame, sp, x if x is not None else OW / 2, y, s, fadev(t, t0, 0.15))


# ---------- intro scenes (all timings come from cue words) ----------

def g_hook(frame, t, c, dur):
    headline(frame, t, c["q"], "Quick question.", 150, 90, LEAF)
    a = fadev(t, c["forest"] - 0.25, 0.35)
    if a > 0:
        place(frame, card_sprite(1700, 400, (34, 64, 38, 150), (255, 255, 255, 20), 44), OW / 2, 760, 1, a)
    road_t, x0r, x1r = c["road"], 560, 700
    for r in range(3):
        for i in range(7):
            x, y = 230 + i * 105 + (r % 2) * 50, 650 + r * 100
            k = r * 7 + i
            s_ = popv(t, c["forest"] + k * 0.03)
            if x0r - 40 <= x <= x1r + 40 and t > road_t:
                hit = road_t + 0.55 * (y - 560) / 380
                if t > hit:
                    burst(frame, x, y - 30, t, hit, BROWN, n=12, radius=110, size=12, seed=k)
                    s_ *= max(0.0, 1 - ease_out((t - hit) / 0.2))
            place(frame, tree_sprite(118), x, y - 30, s_)
    if t > road_t:
        h = int(380 * ease_out((t - road_t) / 0.55))
        if h > 2:
            road = Image.new("RGBA", (x1r - x0r, h), (55, 60, 66, 255))
            rd = ImageDraw.Draw(road)
            for yy in range(10, h, 56):
                rd.rectangle([(x1r - x0r) // 2 - 5, yy, (x1r - x0r) // 2 + 5, yy + 30], fill=(250, 204, 21))
            composite(frame, road, x0r, 570)
        place(frame, pill_sprite("HIGHWAY", 34, INK, (250, 204, 21)), (x0r + x1r) / 2, 515, popv(t, road_t + 0.15))
    pt = c["plant"]
    if t > pt:
        k = ease_out((t - pt) / 0.45)
        arr = Image.new("RGBA", (330, 90), (0, 0, 0, 0))
        ad = ImageDraw.Draw(arr)
        ad.line([(10, 45), (10 + 270 * k, 45)], fill=GREEN + (255,), width=12)
        if k > 0.9:
            ad.polygon([(278, 18), (322, 45), (278, 72)], fill=GREEN + (255,))
        composite(frame, arr, 1000, 700)
        place(frame, pill_sprite("PLANT IT BACK", 34, INK, GREEN), 1530, 535, popv(t, pt + 0.1))
    sv = c["survive"]
    rng = np.random.default_rng(5)
    dies = rng.random(12) < 0.6
    for i in range(12):
        col, row = i % 4, i // 4
        x, y = 1380 + col * 105 + row * 26, 640 + row * 100
        s_ = popv(t, pt + 0.2 + i * 0.05)
        color, rot = (74, 222, 128), 0.0
        if dies[i] and t > sv + i * 0.05:
            f = ease_out((t - sv - i * 0.05) / 0.45)
            color = tuple(int(lerp(a_, b_, f)) for a_, b_ in zip(color, (150, 110, 70)))
            rot = (22 * f + 4 * math.sin((t - sv) * 18) * (1 - f)) * (1 if i % 2 else -1)
        place(frame, sapling_sprite(96, color), x, y, s_, rot=rot)
    if t > sv:
        wob = 10 * math.sin((t - sv) * 8) * math.exp(-(t - sv) * 1.6)
        place(frame, text_sprite("?", 300, "heavy", AMBER), 1800, 330, popv(t, sv + 0.05, 0.5), rot=wob)
        headline(frame, t, sv + 0.2, "Do they survive?", 340, 70, WHITE, x=1400, max_w=700)


def counter_text(t, t0, target, fmt, d=1.1):
    return fmt(target * ease_out((t - t0) / d)) if t >= t0 else fmt(0)


def g_water(frame, t, c, dur):
    headline(frame, t, 0.05, "Water works the same way.", 130, 62, SKY, max_w=1600)
    ai = c["ai"]
    s_ = popv(t, ai)
    if s_ > 0:
        place(frame, server_sprite(260, 360, int(t * 6) % 4), 330, 500, s_)
        place(frame, pill_sprite("AI RUNS ON WATER", 28, INK, SKY), 330, 740, popv(t, ai + 0.2))
        for k in range(3):
            ph = ((t - ai) * 0.9 + k / 3) % 1
            if t - ai > 0.3:
                place(frame, drop_sprite(34), 470 + 6 * k, 310 + ph * 300, 1, 1 - ph)
    g0 = c["gallons"]
    sc = popv(t, g0, 0.5)
    if sc > 0:
        shake = 10 * math.sin((t - g0) * 40) * math.exp(-(t - g0) * 6) if t - g0 < 0.6 else 0
        place(frame, card_sprite(620, 330), 960 + shake, 480, sc)
        place(frame, text_sprite(counter_text(t, g0, 7.7, lambda v: f"{v:.1f}"), 150, "heavy", SKY), 960 + shake, 430, sc)
        place(frame, text_sprite("BILLION GALLONS", 40, "bold", WHITE, 540), 960, 540, sc)
        place(frame, text_sprite("replenished by Google in 2025", 28, "medium", MUTED, 540, False), 960, 595, sc)
        burst(frame, 960, 450, t, g0, SKY, n=18, radius=260, size=14, seed=11)
    p0 = c["pledge"]
    sp = popv(t, p0)
    if sp > 0:
        cx, cy, R = 1590, 480, 150
        ringimg = Image.new("RGBA", (2 * R + 40, 2 * R + 40), (0, 0, 0, 0))
        rd = ImageDraw.Draw(ringimg)
        rd.ellipse([20, 20, 2 * R + 20, 2 * R + 20], outline=(255, 255, 255, 40), width=22)
        prog = 0.78 * ease_out((t - p0) / 1.2)
        rd.arc([20, 20, 2 * R + 20, 2 * R + 20], -90, -90 + 360 * prog, fill=GREEN + (255,), width=22)
        place(frame, ringimg, cx, cy, sp)
        place(frame, text_sprite(f"{int(round(prog * 100))}%", 84, "heavy", LEAF), cx, cy - 6, sp)
        place(frame, text_sprite("of its water use", 28, "medium", MUTED, 360, False), cx, cy + 200, sp)
        place(frame, pill_sprite("GOAL 120%", 26, INK, LEAF), cx, cy + 250, popv(t, p0 + 0.9))
    l0 = c["lakes"]
    place(frame, pill_sprite("165 WATER PROJECTS", 30, INK, SKY), 960, 770, popv(t, l0 + 0.1))
    for i in range(5):
        s_ = popv(t, l0 + i * 0.07)
        x = 560 + i * 200
        place(frame, lake_sprite(150, 60), x, 860, s_)
        ring(frame, x, 860, t, l0 + i * 0.07 + 0.2, SKY, 10, 100, 0.8, 4)
    a0 = c["ask"]
    if t > a0:
        headline(frame, t, a0, "How do you check every lake?", 975, 54, WHITE, max_w=1400)
        lens_x = lerp(500, 1400, ease((t - a0) / 1.6))
        lens = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lens)
        ld.ellipse([10, 10, 100, 100], outline=WHITE + (255,), width=10)
        ld.line([(90, 90), (130, 130)], fill=WHITE + (255,), width=14)
        place(frame, lens, lens_x, 840, popv(t, a0, 0.3))


TILE = 470


@lru_cache(maxsize=4)
def tile(name):
    im = Image.open(ASSETS / name).convert("RGB").resize((TILE, TILE), Image.BICUBIC).convert("RGBA")
    m = Image.new("L", (TILE, TILE), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, TILE - 1, TILE - 1], radius=26, fill=255)
    im.putalpha(m)
    return im


def g_badkhal(frame, t, c, dur):
    t0 = c["t0"]
    headline(frame, t, t0, "Badkhal Lake, near Delhi", 140, 64, WHITE, max_w=1300)
    pin_y = lerp(-80, 140, bounce((t - t0) / 0.8)) if t > t0 else -200
    pin = Image.new("RGBA", (60, 80), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pin)
    pd.ellipse([4, 0, 56, 52], fill=RED + (255,))
    pd.polygon([(10, 38), (50, 38), (30, 78)], fill=RED + (255,))
    pd.ellipse([20, 14, 40, 34], fill=WHITE + (255,))
    w = text_sprite("Badkhal Lake, near Delhi", 64, "bold", WHITE, 1300).width
    place(frame, pin, OW / 2 - w / 2 - 30, pin_y, 1 if t > t0 else 0)
    items = [("badkhal_2019.png", "2019", "DRY FOR YEARS", AMBER, "t1"),
             ("badkhal_2024.png", "2024", "WATER'S BACK", SKY, "t2"),
             ("badkhal_2025.png", "2025", "CHOKED WITH GREEN", GREEN, "t3")]
    for i, (name, yr, lab, col, key) in enumerate(items):
        k = c[key]
        s = popv(t, k, 0.5)
        x = 180 + TILE / 2 + i * (TILE + 70)
        y = 540
        if s > 0:
            place(frame, tile(name), x, y, s, rot=(1.5 - i * 1.5) * (1 - ease((t - k) / 0.6)))
            place(frame, pill_sprite(yr, 30, WHITE, (0, 0, 0, 200)), x - TILE / 2 + 70, y - TILE / 2 + 44, popv(t, k + 0.15))
            place(frame, pill_sprite(lab, 30, INK, col, TILE), x, y + TILE / 2 + 52, popv(t, k + 0.25))
            if key == "t2":
                ring(frame, x, y, t, k + 0.2, SKY, 30, 260, 0.9, 8)
            burst(frame, x, y + TILE / 2 + 52, t, k + 0.25, col, n=12, radius=140, size=10, seed=i + 20)
    st = c["stamp"]
    if t > st:
        s = 1.0 + 1.6 * (1 - ease_out((t - st) / 0.22))
        place(frame, stamp_sprite("NOT PROOF", 110, RED), OW / 2, 540, s, fadev(t, st, 0.12), rot=-9)
        burst(frame, OW / 2, 540, t, st + 0.2, (200, 200, 200), n=24, radius=420, size=12, seed=7)
        headline(frame, t, st + 0.35, "Funding a lake isn't proof of water.", 1000, 50, WHITE, max_w=1500)


def g_logo(frame, t, c, dur):
    lg = c["logo"]
    s = popv(t, lg, 0.55)
    place(frame, leaf_sprite(170), OW / 2 - 330, 430, s)
    ring(frame, OW / 2 - 330, 430, t, lg + 0.1, GREEN, 60, 320, 0.9, 8)
    ring(frame, OW / 2 - 330, 430, t, lg + 0.3, LEAF, 60, 420, 1.0, 5)
    burst(frame, OW / 2 - 330, 430, t, lg + 0.05, LEAF, n=22, radius=330, size=13, seed=31)
    x = OW / 2 - 210
    for i, ch in enumerate("ReRoot"):
        sp = text_sprite(ch, 168, "bold", WHITE, shadow=False)
        place(frame, sp, x + sp.width / 2 - 14, 432, popv(t, lg + 0.12 + i * 0.06, 0.4))
        x += font(168, "bold").getlength(ch)
    headline(frame, t, lg + 0.7, "Plant trees where they'll survive.", 640, 50, MINT, max_w=1400)
    t2 = c["tag2"]
    headline(frame, t, t2, "Then prove they did.", 720, 58, LEAF, max_w=1200, weight="heavy")
    if t > t2 + 0.2:
        k = ease_out((t - t2 - 0.2) / 0.5)
        w = font(58, "heavy").getlength("Then prove they did.")
        ImageDraw.Draw(frame).rounded_rectangle([OW / 2 - w / 2, 768, OW / 2 - w / 2 + w * k, 778], radius=5, fill=GREEN)


TECH = [("Earth Engine", "satellites: Sentinel-1/2, Dynamic World, JRC, CHIRPS, ERA5"),
        ("Gemini on Vertex AI", "the agent: 22 tools, plans its own steps"),
        ("BigQuery + Vertex AI", "1-hectare grid + survival model (AUC 0.96)"),
        ("Cloud Run + Pub/Sub", "API, region builder jobs, alert pipeline"),
        ("Firestore + Firebase", "live alert feed to phones, Firebase Hosting"),
        ("Text-to-Speech", "Hindi voice notes for caretakers")]


def g_outro(frame, t, c, dur):
    f0 = c["fill"]
    n = len(S.CHAPTERS)
    prog = clamp((t - f0) / 1.2) * n
    filled = int(prog)
    fp = (filled, spring((prog - filled) * 1.5)) if filled < n and t > f0 else None
    draw_timeline(frame, OW / 2, 150, 0.8, fadev(t, 0.0, 0.3), min(prog, n - 1), filled, None, t, None,
                  "All five stops, verified from space" if filled >= n else "The full loop", fp)
    for i, (name, sub) in enumerate(TECH):
        k = c[f"t{i + 1}"]
        s_ = popv(t, k, 0.45)
        if s_ <= 0:
            continue
        col, row = i % 3, i // 3
        cx, cy = 360 + col * 600, 415 + row * 190
        place(frame, card_sprite(560, 165), cx, cy, s_)
        place(frame, text_sprite(name, 42, "bold", WHITE, 500), cx, cy - 24, s_)
        place(frame, text_sprite(sub, 24, "medium", MUTED, 500, False), cx, cy + 34, s_)
        burst(frame, cx, cy, t, k, GREEN, n=10, radius=200, size=9, seed=40 + i)
    headline(frame, t, c["t6"] + 0.7, "Every number from real data, with its source.", 770, 42, WHITE, max_w=1600, weight="demi")
    lg = c["logo"]
    if t > lg:
        s_ = popv(t, lg, 0.5)
        place(frame, leaf_sprite(80), 650, 870, s_)
        place(frame, text_sprite("Planting isn't the finish line. Proof is.", 44, "bold", LEAF, 1100), 1130, 870, s_)
    u = c.get("url")
    if u is not None and t > u:
        s_ = popv(t, u, 0.5)
        place(frame, pill_sprite("Try it at rerootearth.com", 46, INK, GREEN), 960, 985, s_)
        burst(frame, 960, 985, t, u, LEAF, n=20, radius=320, size=11, seed=77)
        ring(frame, 960, 985, t, u + 0.1, GREEN, 60, 420, 0.9, 6)


TL_W, TL_H = 1500, 236


def draw_timeline(frame, cx, cy, scale, alpha, line_to, filled, current=None, t=0.0, pops=None, title=None, fill_pop=None):
    """The 5-stop journey. line_to: node index the green line reaches; filled: nodes done (int);
    pops: per-node appear scale; fill_pop: (node, scale) for the node being ticked off right now."""
    if alpha <= 0.01 or scale <= 0.02:
        return
    L = Image.new("RGBA", (TL_W, TL_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(L)
    d.rounded_rectangle([0, 0, TL_W - 1, TL_H - 1], radius=40, fill=(9, 22, 16, 255), outline=(70, 96, 82, 255), width=2)
    n = len(S.CHAPTERS)
    x0, x1, y = 150, TL_W - 150, 132
    xs = [x0 + (x1 - x0) * i / (n - 1) for i in range(n)]
    d.rounded_rectangle([x0, y - 5, x1, y + 5], radius=5, fill=(52, 68, 60, 255))
    lx = x0 + (x1 - x0) * clamp(line_to / (n - 1))
    if lx > x0:
        d.rounded_rectangle([x0, y - 5, lx, y + 5], radius=5, fill=GREEN)
    if title:
        tf = fit_font(title, TL_W - 120, 38, "bold")
        d.text((TL_W / 2, 50), title, font=tf, fill=LEAF, anchor="mm")
    for i, x in enumerate(xs):
        sc = 1.0 if pops is None else pops[i]
        if sc <= 0.02:
            continue
        r = 30 * sc
        done = i < filled
        if fill_pop and fill_pop[0] == i:
            r *= fill_pop[1]
        if done:
            d.ellipse([x - r, y - r, x + r, y + r], fill=GREEN)
            d.line([(x - r * 0.45, y + r * 0.02), (x - r * 0.1, y + r * 0.38), (x + r * 0.5, y - r * 0.35)], fill=WHITE, width=max(2, int(6 * sc)))
        elif i == current:
            pr = r + 10 + 6 * math.sin(t * 6)
            d.ellipse([x - pr, y - pr, x + pr, y + pr], outline=(96, 170, 120, 255), width=4)
            d.ellipse([x - r, y - r, x + r, y + r], fill=(22, 101, 52), outline=LEAF, width=4)
            d.text((x, y), str(i + 1), font=font(30 * sc, "bold"), fill=WHITE, anchor="mm")
        else:
            d.ellipse([x - r, y - r, x + r, y + r], fill=(24, 38, 31, 255), outline=(120, 140, 130, 255), width=4)
            d.text((x, y), str(i + 1), font=font(28 * sc, "demi"), fill=(200, 212, 205, 255), anchor="mm")
        lab = S.CHAPTERS[i][1]
        col = LEAF if i == current else WHITE if done else (190, 205, 197)
        d.text((x, y + 62), lab, font=font(28 * max(0.6, sc), "bold" if i == current else "demi"), fill=col, anchor="mm")
    place(frame, L, cx, cy, scale, alpha)


def chapter_overlay(frame, tl, chapter):
    """Big journey card at the top for the first seconds of a chapter: ticks off the last stop, lights up this one."""
    t_in, t_out = 0.05, 2.9
    if tl < t_in or tl > t_out + 0.4:
        return
    y = lerp(-140, 150, spring((tl - t_in) / 0.5)) if tl < t_out else lerp(150, -160, ease((tl - t_out) / 0.4))
    a = 1.0 if tl < t_out else 1 - ease((tl - t_out) / 0.4)
    f = ease((tl - 0.55) / 0.45)
    line_to = (chapter - 1 + f) if chapter > 0 else 0.0
    filled = chapter if tl > 0.6 else max(0, chapter - 1)
    fp = (chapter - 1, spring((tl - 0.6) / 0.35)) if chapter > 0 and tl > 0.6 else None
    title = f"Stop {chapter + 1} of {len(S.CHAPTERS)}  ·  {S.CHAPTERS[chapter][0]}"
    draw_timeline(frame, OW / 2, y, 0.86, a, line_to, filled, chapter, tl, None, title, fp)
    if chapter > 0 and 0.6 < tl < 1.3:
        n = len(S.CHAPTERS)
        x = OW / 2 + (150 + (TL_W - 300) * (chapter - 1) / (n - 1) - TL_W / 2) * 0.86
        burst(frame, x, y + (132 - TL_H / 2) * 0.86, tl, 0.6, GREEN, n=12, radius=90, size=8, seed=chapter)


def g_journey(frame, t, c, dur):
    st = c["stops"]
    headline(frame, t, 0.0, "From planting to proof", 330, 64, LEAF, max_w=1500)
    pops = [popv(t, st + i * 0.13, 0.4) for i in range(len(S.CHAPTERS))]
    for i in range(len(S.CHAPTERS)):
        x = OW / 2 + (150 + (TL_W - 300) * i / (len(S.CHAPTERS) - 1) - TL_W / 2) * 1.12
        burst(frame, x, 560 + (132 - TL_H / 2) * 1.12, t, st + i * 0.13, LEAF, n=8, radius=70, size=7, seed=60 + i)
    draw_timeline(frame, OW / 2, 560, 1.12, fadev(t, 0.0, 0.3), 0.0, 0, None, t, pops, "Five stops")


GFX = {"journey": g_journey, "hook": g_hook, "water": g_water, "badkhal": g_badkhal, "logo": g_logo, "outro": g_outro}

# ---------- narration: TTS + word alignment ----------

CUE = re.compile(r"\{([a-z]+):([^}]*)\}")


def parse(text):
    """-> plain text, words [str], cues [(word_index, kind, value)]."""
    words, cues, pending = [], [], []
    for tok in re.split(r"(\{[a-z]+:[^}]*\})|\s+", text):
        if not tok:
            continue
        m = CUE.fullmatch(tok)
        if m:
            pending.append((m.group(1), m.group(2)))
            continue
        # a cue glued to a word: "{g:x}word"
        while True:
            m = re.match(r"\{([a-z]+):([^}]*)\}", tok)
            if not m:
                break
            pending.append((m.group(1), m.group(2)))
            tok = tok[m.end():]
        if not tok:
            continue
        for k, v in pending:
            cues.append((len(words), k, v))
        pending = []
        words.append(tok)
    for k, v in pending:
        cues.append((len(words), k, v))
    return " ".join(words), words, cues


def tts(text, take=0):
    key = hashlib.sha1(json.dumps([S.VOICE, S.STYLE, text] + ([take] if take else [])).encode()).hexdigest()[:16]
    path = CACHE / f"tts_{key}.wav"
    if path.exists():
        return path
    from google.cloud import texttospeech as T
    from reroot.core.auth import get_credentials
    c = T.TextToSpeechClient(credentials=get_credentials())
    last = None
    for _ in range(4):
        try:
            r = c.synthesize_speech(
                input=T.SynthesisInput(text=text, prompt=S.STYLE),
                voice=T.VoiceSelectionParams(language_code=S.VOICE["lang"], name=S.VOICE["name"], model_name=S.VOICE["model"]),
                audio_config=T.AudioConfig(audio_encoding=T.AudioEncoding.LINEAR16, sample_rate_hertz=SR,
                                           speaking_rate=S.VOICE.get("rate", 1.0)), timeout=180)
            path.write_bytes(r.audio_content)
            return path
        except Exception as e:  # noqa: BLE001
            last = e
            print("tts retry", str(e)[:120], flush=True)
    raise last


def stt(path):
    """Word timings from Speech-to-Text v2 (Chirp 2), cached per audio file."""
    key = hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    out = CACHE / f"stt2_{key}.json"
    if out.exists():
        return json.loads(out.read_text())
    import requests
    from google.auth.transport.requests import Request
    from reroot.core.auth import get_credentials
    creds = get_credentials()
    creds.refresh(Request())
    body = {"config": {"autoDecodingConfig": {}, "languageCodes": [S.VOICE["lang"]], "model": "chirp_2",
                       "features": {"enableWordTimeOffsets": True}},
            "content": base64.b64encode(path.read_bytes()).decode()}
    r = requests.post("https://us-central1-speech.googleapis.com/v2/projects/reroot-hack-2026/locations/us-central1/recognizers/_:recognize",
                      json=body, timeout=240, headers={"Authorization": f"Bearer {creds.token}", "x-goog-user-project": "reroot-hack-2026"})
    r.raise_for_status()
    sec = lambda v: float(str(v or "0s").rstrip("s"))  # noqa: E731
    ws = [{"w": w["word"], "s": sec(w.get("startOffset")), "e": sec(w.get("endOffset"))}
          for res in r.json().get("results", []) for w in res["alternatives"][0].get("words", [])]
    out.write_text(json.dumps(ws))
    return ws


def norm(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())


def align(words, rec, dur):
    """Start time of every script word: matched to recognised words, the rest interpolated."""
    a = [norm(w) for w in words]
    b = [norm(x["w"]) for x in rec]
    times = [None] * len(words)
    ends = [None] * len(words)
    for blk in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            times[blk.a + k] = rec[blk.b + k]["s"]
            ends[blk.a + k] = rec[blk.b + k]["e"]
    known = [i for i, t in enumerate(times) if t is not None]
    if not known:
        return [dur * i / len(words) for i in range(len(words))], [dur * (i + 1) / len(words) for i in range(len(words))]
    last_end = max(e for e in ends if e is not None)
    for i in range(len(words)):
        if times[i] is not None:
            continue
        lo = max([k for k in known if k < i], default=None)
        hi = min([k for k in known if k > i], default=None)
        if lo is not None and hi is not None:
            times[i] = lerp(times[lo], times[hi], (i - lo) / (hi - lo))
        elif lo is not None:
            times[i] = lerp(times[lo], last_end, (i - lo) / (len(words) - lo))
        else:
            times[i] = lerp(0.1, times[hi], i / hi)
    for i in range(len(words)):
        if ends[i] is None:
            ends[i] = times[i + 1] if i + 1 < len(words) else min(dur, times[i] + 0.4)
    return times, ends


def wav_dur(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def read_wav(p):
    with wave.open(str(p)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if w.getnchannels() == 2:
            a = a.reshape(-1, 2).mean(axis=1)
        sr = w.getframerate()
    if sr != SR:
        a = np.interp(np.arange(0, len(a), sr / SR), np.arange(len(a)), a)
    return a


# ---------- recording ----------

FRAMES = json.loads((REC / "frames.json").read_text())
FT = [f["t"] for f in FRAMES]
EVENTS = json.loads((REC / "events.json").read_text())
MARKS = {e["name"]: e["t"] for e in EVENTS if e["kind"] == "mark"}
for _e in EVENTS:
    if _e["kind"] == "click" and _e["name"] and _e["name"] not in MARKS:
        MARKS[_e["name"]] = _e["t"]
BOX_EVENTS = {}
for _e in EVENTS:
    if _e["kind"] in ("box", "click") and _e.get("box") and _e["name"]:
        BOX_EVENTS.setdefault(_e["name"], []).append((_e["t"], _e["box"]))
for _n, _evs in BOX_EVENTS.items():
    MARKS.setdefault(f"{_n}_first", _evs[0][0])
MOVES = [e for e in EVENTS if e["kind"] == "move"]
CLICKS = [e for e in EVENTS if e["kind"] == "click"]
VW, VH = 1440, 810
DSF = Image.open(REC / "frames" / f"{FRAMES[0]['i']:06d}.jpg").size[0] / VW
WORK = [(MARKS[k], MARKS[k[:-5] + "_done"]) for k in MARKS if k.endswith("_send") and k[:-5] + "_done" in MARKS]


def at(ref):
    if ref is None:
        return None
    m = re.fullmatch(r"([a-z0-9_]+)([+-][0-9.]+)?", ref)
    return MARKS[m.group(1)] + (float(m.group(2)) if m.group(2) else 0.0)


def box_named(name, t_ref=None):
    evs = BOX_EVENTS[name]
    if t_ref is None:
        return evs[-1][1]
    before = [b for t, b in evs if t <= t_ref]
    return before[-1] if before else evs[0][1]


def resolve_box(spec):
    if isinstance(spec, str):
        return box_named(spec)
    if "rect" in spec:
        x, y, w, h = spec["rect"]
        return {"x": x, "y": y, "width": w, "height": h}
    return box_named(spec["name"], at(spec.get("at")))


def cam_rect(spec):
    if spec in (None, "full"):
        return (0.0, 0.0, float(VW), float(VH))
    if "union" in spec:
        bs = [resolve_box(n) for n in spec["union"]]
        x0, y0 = min(b["x"] for b in bs), min(b["y"] for b in bs)
        x1, y1 = max(b["x"] + b["width"] for b in bs), max(b["y"] + b["height"] for b in bs)
        x, y, w, h = x0, y0, x1 - x0, y1 - y0
    else:
        b = resolve_box(spec)
        x, y, w, h = b["x"], b["y"], b["width"], b["height"]
    pad = spec.get("pad", 30)
    x, y, w, h = x - pad, y - pad, w + 2 * pad, h + 2 * pad
    cw = min(VW, max(w, h * 16 / 9, spec.get("min_w") or 860, 860))
    ch = cw * 9 / 16
    x0 = clamp(x + w / 2 - cw / 2, 0, VW - cw)
    y0 = clamp(y + h / 2 - ch / 2, 0, VH - ch)
    return (x0, y0, cw, ch)


@lru_cache(maxsize=8)
def src_frame(i):
    return Image.open(REC / "frames" / f"{FRAMES[i]['i']:06d}.jpg").convert("RGB")


def frame_at(t):
    return src_frame(max(0, bisect.bisect_right(FT, t) - 1))


def cursor_pos(t):
    pos = None
    for m in MOVES:
        if t < m["t0"]:
            break
        if t <= m["t1"]:
            f = ease((t - m["t0"]) / max(0.01, m["t1"] - m["t0"]))
            return (lerp(m["frm"][0], m["to"][0], f), lerp(m["frm"][1], m["to"][1], f))
        pos = tuple(m["to"])
    return pos


@lru_cache(maxsize=4)
def cursor_img(scale10):
    s = int(36 * scale10 / 10)
    im = Image.new("RGBA", (s + 6, s + 6), (0, 0, 0, 0))
    pts = [(0.08, 0.04), (0.08, 0.82), (0.29, 0.63), (0.43, 0.95), (0.56, 0.89), (0.42, 0.58), (0.7, 0.58)]
    pts = [(x * s, y * s) for x, y in pts]
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon([(x + 2, y + 3) for x, y in pts], fill=(0, 0, 0, 120))
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(2)))
    d = ImageDraw.Draw(im)
    d.polygon(pts, fill=(255, 255, 255, 255))
    d.line(pts + [pts[0]], fill=(10, 10, 10, 255), width=max(1, s // 16))
    return im


def to_out(pt, rect):
    x0, y0, cw, ch = rect
    return ((pt[0] - x0) / cw * OW, (pt[1] - y0) / ch * OH)


def draw_highlight(frame, rect, hl, a, pop, st=None):
    spec = hl["box"]
    if isinstance(spec, dict) and spec.get("live"):
        evs = [bx for tt, bx in BOX_EVENTS[spec["name"]] if st is not None and tt <= st]
        if not evs:
            return  # the element doesn't exist yet at this moment of the recording
        b = evs[-1]
    else:
        b = resolve_box(spec)
    pad = 8
    p0 = to_out((b["x"] - pad, b["y"] - pad), rect)
    p1 = to_out((b["x"] + b["width"] + pad, b["y"] + b["height"] + pad), rect)
    # grow from the centre with a spring
    cx, cy = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    sc = lerp(1.12, 1.0, pop)
    p0 = (cx + (p0[0] - cx) * sc, cy + (p0[1] - cy) * sc)
    p1 = (cx + (p1[0] - cx) * sc, cy + (p1[1] - cy) * sc)
    p0 = (clamp(p0[0], 6, OW - 6), clamp(p0[1], 6, OH - 6))
    p1 = (clamp(p1[0], 6, OW - 6), clamp(p1[1], 6, OH - 6))
    col = COLORS.get(hl.get("color", "green"), GREEN)
    if hl.get("dim", True):
        dim = Image.new("RGBA", (OW, OH), (4, 12, 8, int(120 * a)))
        hole = Image.new("L", (OW, OH), 255)
        ImageDraw.Draw(hole).rounded_rectangle([p0[0], p0[1], p1[0], p1[1]], radius=18, fill=0)
        frame.paste(Image.alpha_composite(frame, dim), (0, 0), hole)
    glow = Image.new("RGBA", (int(p1[0] - p0[0]) + 80, int(p1[1] - p0[1]) + 80), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle([40, 40, glow.width - 40, glow.height - 40], radius=18, outline=col + (int(190 * a),), width=14)
    composite(frame, glow.filter(ImageFilter.GaussianBlur(10)), p0[0] - 40, p0[1] - 40)
    ImageDraw.Draw(frame).rounded_rectangle([p0[0], p0[1], p1[0], p1[1]], radius=18, outline=col + (int(255 * a),), width=5)
    if hl.get("label"):
        sp = pill_sprite(hl["label"], 30, INK, col, 760)
        lw, lh = sp.size
        # prefer above, else below, else inside the top edge; always inside the frame
        if p0[1] - lh - 12 >= 8:
            ly = p0[1] - lh - 12
        elif p1[1] + 12 + lh <= OH - 150:
            ly = p1[1] + 12
        else:
            ly = p0[1] + 12
        lx = clamp(p0[0], 16, OW - lw - 16)
        place(frame, sp, lx + lw / 2, ly + lh / 2, spring(clamp(pop * 1.0)), a)


def draw_chip(frame, text, a):
    if not text or a <= 0:
        return
    sp = chip_sprite(text)
    place(frame, sp, 36 + sp.width / 2, 60, a)


@lru_cache(maxsize=16)
def chip_sprite(text):
    f = font(28, "bold")
    h = 49
    w = int(f.getlength(text) + 84)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=(8, 18, 13, 225))
    d.ellipse([22, h / 2 - 8, 38, h / 2 + 8], fill=GREEN)
    d.text((54, h / 2), text, font=f, fill=WHITE, anchor="lm")
    return im


@lru_cache(maxsize=16)
def ff_sprite(speed):
    f = font(26, "bold")
    text = f"{speed}x  Gemini working, sped up"
    h = 46
    w = int(f.getlength(text) + 96)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=(245, 158, 11, 235))
    for k in (0, 1):  # fast-forward arrows
        x = 22 + k * 15
        d.polygon([(x, h / 2 - 9), (x + 14, h / 2), (x, h / 2 + 9)], fill=(20, 14, 2))
    d.text((64, h / 2), text, font=f, fill=(20, 14, 2), anchor="lm")
    return im


def draw_ff(frame, speed, a):
    sp = ff_sprite(int(round(speed)))
    place(frame, sp, OW - 36 - sp.width / 2, 60, a)


def captions(frame, words, starts, ends, tl, a=1.0):
    """Karaoke captions: the current phrase, spoken words bright, the current word green."""
    if not words or a <= 0:
        return
    phrases, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        if (w[-1] in ".?!:" or (w[-1] in ",;" and len(cur) >= 5) or len(cur) >= 11):
            phrases.append(cur)
            cur = []
    if cur:
        phrases.append(cur)
    pi = 0
    for k, ph in enumerate(phrases):
        if tl >= starts[ph[0]] - 0.12:
            pi = k
    ph = phrases[pi]
    if tl > ends[ph[-1]] + 0.6 and pi == len(phrases) - 1:
        return
    f = font(38, "demi")
    space = f.getlength(" ")
    widths = [f.getlength(words[i]) for i in ph]
    lines, line, lw = [], [], 0.0
    for i, w in zip(ph, widths):
        if line and lw + space + w > 1500:
            lines.append(line)
            line, lw = [], 0.0
        line.append((i, w))
        lw += (space if len(line) > 1 else 0) + w
    lines.append(line)
    lh = 54
    box_w = max(sum(w for _, w in ln) + space * (len(ln) - 1) for ln in lines) + 70
    box_h = lh * len(lines) + 30
    y0 = OH - box_h - 40
    bg = card_sprite(int(box_w), int(box_h), (6, 16, 11, 215), (255, 255, 255, 30), 22)
    place(frame, bg, OW / 2, y0 + box_h / 2, 1, a)
    d = ImageDraw.Draw(frame)
    for li, ln in enumerate(lines):
        tw = sum(w for _, w in ln) + space * (len(ln) - 1)
        x = OW / 2 - tw / 2
        y = y0 + 15 + li * lh + lh / 2
        for i, w in ln:
            if starts[i] <= tl < ends[i] + 0.05:
                col = LEAF
            elif tl >= starts[i]:
                col = WHITE
            else:
                col = (200, 214, 206)
            d.text((x, y), words[i], font=f, fill=col + (int(255 * a),), anchor="lm")
            x += w + space


# ---------- timeline ----------

def good_take(plain):
    """Re-synthesise until every year in the script is heard in the audio (Gemini-TTS can misspeak)."""
    want = sorted(re.findall(r"\b(?:19|20)\d\d\b", plain))
    for take in range(5):
        path = tts(plain, take)
        heard = " ".join(w["w"] for w in stt(path))
        got = sorted(re.findall(r"\b(?:19|20)\d\d\b", heard))
        if all(got.count(y) >= want.count(y) for y in set(want)) and not (set(got) - set(want)):
            return path
        print(f"take {take} misspoke years: want {want} heard {got}", flush=True)
    return path


def build():
    groups = []
    for sc in SCENES_:
        g = sc.get("group")
        if g and groups and groups[-1]["name"] == g:
            groups[-1]["scenes"].append(sc)
        else:
            groups.append({"name": g, "scenes": [sc]})
    for G in groups:
        G["parsed"] = [parse(sc["text"]) for sc in G["scenes"]]
        G["plain"] = " ".join(pp[0] for pp in G["parsed"])
    with ThreadPoolExecutor(6) as ex:
        paths = list(ex.map(lambda G: good_take(G["plain"]), groups))
    plans, t = [], 0.0
    prev_rect = cam_rect("full")
    lead = 0.12
    for G, path in zip(groups, paths):
        nd = wav_dur(path)
        all_words = [w for pp in G["parsed"] for w in pp[1]]
        g_starts, g_ends = align(all_words, stt(path), nd)
        g_starts = [x + lead for x in g_starts]
        g_ends = [x + lead for x in g_ends]
        offs, o = [], 0
        for pp in G["parsed"]:
            offs.append(o)
            o += len(pp[1])
        bounds = [0.0] + [g_starts[offs[k]] - 0.1 for k in range(1, len(offs))]
        for k, (sc, (plain, words, cues)) in enumerate(zip(G["scenes"], G["parsed"])):
            s_first = bounds[k]
            last = k + 1 == len(G["scenes"])
            dur = (nd + lead + sc.get("tail", 0.4) - s_first) if last else bounds[k + 1] - s_first
            starts = [g_starts[offs[k] + i] - s_first for i in range(len(words))]
            ends = [g_ends[offs[k] + i] - s_first for i in range(len(words))]
            scene_end = (nd + lead - s_first) if last else dur
            cue_t = lambda i, starts=starts, scene_end=scene_end: starts[i] if i < len(starts) else scene_end  # noqa: E731
            extra = None
            if sc.get("extra_audio") and last:
                ex_ = sc["extra_audio"]
                extra = {"path": V / ex_["path"], "at": scene_end + ex_.get("gap", 0.3), "gain": ex_.get("gain", 1.0)}
                dur = extra["at"] + ex_["dur"] + 0.4
            pl = {"sc": sc, "start": t, "dur": dur, "words": words, "starts": starts, "ends": ends,
                  "wav": path if k == 0 else None, "lead": lead, "extra": extra}
            if sc["kind"] == "gfx":
                pl["cues"] = {v: cue_t(i) for i, kk, v in cues if kk == "g"}
            else:
                anchors = [(0.0, at(sc["src_start"]))]
                cams, hls = [], []
                for i, kk, v in cues:
                    ct = cue_t(i)
                    if kk == "src":
                        anchors.append((ct, at(v)))
                    elif kk == "cam":
                        cams.append((max(0.0, ct - 0.25), cam_rect(S.ROIS[v])))
                    elif kk == "hl":
                        hls.append((ct, None if v == "off" else v))
                last_n, last_s = anchors[-1]
                end_src = at(sc["src_end"]) if sc.get("src_end") else min(MARKS["end"], last_s + (dur - last_n))
                anchors.append((dur, end_src))
                pl["anchors"] = anchors
                pl["cams"] = cams
                pl["prev_rect"] = prev_rect
                if cams:
                    prev_rect = cams[-1][1]
                spans = []
                for jj, (ht, hid) in enumerate(hls):
                    if hid is None:
                        continue
                    nxt = hls[jj + 1][0] if jj + 1 < len(hls) else dur - 0.15
                    spans.append((ht, nxt, S.HLS[hid]))
                pl["hls"] = spans
            plans.append(pl)
            t += dur
    return plans, t


def src_time(p, tl):
    """Piecewise map from scene time to recording time. Within each anchor gap the footage plays at 1x
    and holds before the action, so the anchored moment lands on its word; long gaps are sped up."""
    an = p["anchors"]
    for (n0, s0), (n1, s1) in zip(an, an[1:]):
        if tl <= n1 or (n1, s1) == an[-1]:
            dn, ds = n1 - n0, s1 - s0
            if ds < 0:  # rewind (e.g. scroll back to the top)
                return s0 + (tl - n0) * ds / max(dn, 1e-3), -ds / max(dn, 1e-3)
            if ds == 0:
                return s0, 1.0
            if ds >= dn:
                speed = ds / max(dn, 1e-3)
                return s0 + (tl - n0) * speed, speed
            hold_until = n1 - ds
            return (s0 if tl < hold_until else s0 + (tl - hold_until)), 1.0
    return an[-1][1], 1.0


def cam_at(p, tl):
    rect = p["prev_rect"]
    for ct, r in p["cams"]:
        if tl >= ct:
            rect = tuple(lerp(a, b, ease((tl - ct) / 0.75)) for a, b in zip(rect, r))
    return rect


def render_screen(p, tl):
    st, speed = src_time(p, tl)
    rect = cam_at(p, tl)
    img = frame_at(st)
    x0, y0, cw, ch = rect
    crop = img.crop((int(x0 * DSF), int(y0 * DSF), int((x0 + cw) * DSF), int((y0 + ch) * DSF)))
    frame = crop.resize((OW, OH), Image.BICUBIC).convert("RGBA")
    for h0, h1, hl in p["hls"]:
        if hl.get("until") and st > at(hl["until"]):
            continue
        if h0 <= tl <= h1:
            a = min(fadev(tl, h0, 0.2), 1 - ease((tl - (h1 - 0.2)) / 0.2))
            draw_highlight(frame, rect, hl, a, spring((tl - h0) / 0.4), st)
    cp = cursor_pos(st)
    if cp:
        o = to_out(cp, rect)
        for c in CLICKS:
            dt = st - c["t"]
            if 0 <= dt < 0.6:
                ring(frame, o[0], o[1], dt, 0, GREEN, 14, 80, 0.6, 6)
        zoom = OW / rect[2]
        composite(frame, cursor_img(int(10 * min(1.7, 0.9 + 0.25 * zoom))), o[0], o[1])
    working = any(a <= st <= b for a, b in WORK)
    if speed > 1.6 and working:
        draw_ff(frame, speed, 1.0)
    return frame


def sfx_track(plans, n):
    """Pops on gfx cues, clicks on cursor clicks, a whoosh into each scene."""
    out = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(1)

    def add(t, snd, gain):
        i = int(t * SR)
        if 0 <= i < n:
            out[i:i + len(snd)] += snd[: n - i] * gain

    tt = np.arange(int(0.09 * SR)) / SR
    pop = np.sin(2 * np.pi * (900 - 5000 * tt) * tt) * np.exp(-tt * 45)
    tc = np.arange(int(0.03 * SR)) / SR
    click = (rng.random(len(tc)) * 2 - 1) * np.exp(-tc * 220) * 0.8
    tw = np.arange(int(0.45 * SR)) / SR
    noise = rng.random(len(tw)) * 2 - 1
    noise = np.convolve(noise, np.ones(18) / 18, mode="same")
    whoosh = noise * np.sin(np.pi * tw / tw[-1]) ** 2
    for p in plans:
        if p is not plans[0]:
            add(p["start"] - 0.12, whoosh, 0.25)
        if p["sc"]["kind"] == "gfx":
            for name, ct in p["cues"].items():
                add(p["start"] + ct, pop, 0.16)
        else:
            for c in CLICKS:
                for k in range(int(p["dur"] * 30)):
                    tl = k / 30
                    if abs(src_time(p, tl)[0] - c["t"]) < 1 / 30 and p["anchors"][0][1] <= c["t"] <= p["anchors"][-1][1]:
                        add(p["start"] + tl, click, 0.35)
                        break
    return out


def main():
    global SCENES_
    SCENES_ = S.SCENES
    if "--only" in sys.argv:
        k = int(sys.argv[sys.argv.index("--only") + 1])
        SCENES_ = [S.SCENES[k]]
    plans, total = build()
    print(f"total {total:.1f}s")
    for p in plans:
        sc = p["sc"]
        print(f"  {p['start']:6.1f} +{p['dur']:5.1f} {sc['kind']:6s} {sc.get('draw', sc.get('chip', ''))[:30]:30s} words={len(p['words'])}")
    n = int((total + 0.6) * SR)
    mix = np.zeros(n, dtype=np.float32)
    for p in plans:
        if not p["wav"]:
            continue
        a = read_wav(p["wav"])
        i = int((p["start"] + p["lead"]) * SR)
        mix[i:i + len(a)] += a[: max(0, n - i)]
        if p["extra"]:
            e = read_wav(p["extra"]["path"]) * p["extra"]["gain"]
            i = int((p["start"] + p["extra"]["at"]) * SR)
            mix[i:i + len(e)] += e[: max(0, n - i)]
    tt = np.arange(n) / SR
    pad = sum(np.sin(2 * np.pi * f * tt) for f in (110.0, 164.8, 220.5, 277.2)) / 4 * (0.5 + 0.5 * np.sin(2 * np.pi * tt / 8.0))
    speech = np.convolve(np.abs(mix), np.ones(SR // 4) / (SR // 4), mode="same")
    mix += pad * 0.018 * (1 - np.clip(speech * 25, 0, 0.75))
    mix += sfx_track(plans, n)
    fade = int(1.2 * SR)
    mix[-fade:] *= np.linspace(1, 0, fade)
    peak = np.abs(mix).max()
    if peak > 0.98:
        mix *= 0.98 / peak
    out_wav = V / "mix2.wav"
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())
    (V / "timing2.json").write_text(json.dumps([{"start": p["start"], "dur": p["dur"], "words": p["words"], "starts": p["starts"],
                                                  "cues": p.get("cues"), "anchors": p.get("anchors")} for p in plans], indent=1))
    if "--audio-only" in sys.argv:
        return
    out = V / ("preview2.mp4" if PREVIEW else "reroot_demo_v2.mp4")
    ow, oh = (960, 540) if PREVIEW else (OW, OH)
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ow}x{oh}",
                           "-r", str(FPS), "-i", "-", "-i", str(out_wav), "-c:v", "libx264", "-preset", "slow" if not PREVIEW else "medium",
                           "-crf", "26" if PREVIEW else "15", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                           "-movflags", "+faststart", "-shortest", str(out)], stdin=subprocess.PIPE)
    nframes = int(total * FPS)
    pi = 0
    prev_last = None
    for k in range(nframes):
        t = k / FPS
        while pi + 1 < len(plans) and t >= plans[pi + 1]["start"]:
            pi += 1
        p = plans[pi]
        tl = t - p["start"]
        sc = p["sc"]
        if sc["kind"] == "gfx":
            frame = background(t)
            GFX[sc["draw"]](frame, tl, p["cues"], p["dur"])
        else:
            frame = render_screen(p, tl)
            if sc.get("chapter") is not None:
                chapter_overlay(frame, tl, sc["chapter"])
            captions(frame, p["words"], p["starts"], p["ends"], tl)
        # transitions
        if pi > 0 and tl < 0.35 and prev_last is not None:
            f = ease(tl / 0.35)
            prev_kind, kind = plans[pi - 1]["sc"]["kind"], sc["kind"]
            if prev_kind == "gfx" and kind == "screen":  # circle reveal into the product
                r = lerp(0, math.hypot(OW, OH) / 2, f)
                m = Image.new("L", (OW, OH), 0)
                ImageDraw.Draw(m).ellipse([OW / 2 - r, OH / 2 - r, OW / 2 + r, OH / 2 + r], fill=255)
                base = prev_last.copy()
                base.paste(frame, (0, 0), m)
                frame = base
            elif not (prev_kind == "screen" and kind == "screen" and plans[pi - 1]["anchors"][-1][1] == p["anchors"][0][1]):
                z = prev_last.resize((int(OW * (1 + 0.06 * f)), int(OH * (1 + 0.06 * f))), Image.BILINEAR)
                z = z.crop(((z.width - OW) // 2, (z.height - OH) // 2, (z.width - OW) // 2 + OW, (z.height - OH) // 2 + OH))
                frame = Image.blend(z, frame, f)
        if k < FPS * 0.5:
            frame = Image.blend(Image.new("RGBA", (OW, OH), (0, 0, 0, 255)), frame, k / (FPS * 0.5))
        if pi + 1 < len(plans) and plans[pi + 1]["start"] - t <= 1.0 / FPS + 1e-6:
            prev_last = frame.copy()
        rgb = frame.convert("RGB")
        if PREVIEW:
            rgb = rgb.resize((ow, oh), Image.BILINEAR)
        ff.stdin.write(rgb.tobytes())
        if k % (FPS * 15) == 0:
            print(f"frame {k}/{nframes}", flush=True)
    ff.stdin.close()
    ff.wait()
    print("wrote", out)


SCENES_ = S.SCENES

if __name__ == "__main__":
    main()
