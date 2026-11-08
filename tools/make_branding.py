"""Generates the channel banner (2560x1440) and profile picture (800x800) into assets/branding/.

    .venv/bin/python tools/make_branding.py
"""
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FONT = ROOT / "assets" / "fonts" / "Poppins-Bold.ttf"
OUT = ROOT / "assets" / "branding"
YELLOW, BLUE, BG = (255, 212, 0), (90, 170, 255), (6, 8, 22)


def font(size):
    return ImageFont.truetype(str(FONT), size)


def space(w, h, seed=7):
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), BG)
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    for color, cx, cy, r in [((60, 40, 140), 0.25, 0.4, 0.35), ((20, 80, 160), 0.75, 0.6, 0.4),
                             ((120, 40, 110), 0.55, 0.2, 0.25)]:
        g.ellipse([w * cx - w * r, h * cy - w * r, w * cx + w * r, h * cy + w * r], fill=color)
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(w // 10)), 0.55)
    d = ImageDraw.Draw(img)
    for _ in range(int(w * h / 1800)):
        x, y, s = rnd.randint(0, w), rnd.randint(0, h), rnd.choice([1, 1, 2, 2, 3])
        b = rnd.randint(140, 255)
        d.ellipse([x, y, x + s, y + s], fill=(b, b, min(255, b + 25)))
    return img


def spoon(img, cx, cy, scale, angle=-35):
    """A teaspoon whose bowl holds a tiny glowing planet and stars."""
    S = int(900 * scale)
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    bw, bl = S * 0.27, S * 0.40                 # oval bowl: narrower than long, like a real teaspoon
    bx, by = S * 0.5, S * 0.5                   # by = where the bowl meets the handle
    metal = (210, 215, 230, 255)
    # handle: thin neck that widens toward a rounded end
    d.polygon([(bx - S * 0.016, by - S * 0.01), (bx + S * 0.016, by - S * 0.01),
               (bx + S * 0.045, by + S * 0.47), (bx - S * 0.045, by + S * 0.47)], fill=metal)
    d.ellipse([bx - S * 0.045, by + S * 0.43, bx + S * 0.045, by + S * 0.51], fill=metal)
    d.ellipse([bx - bw / 2, by - bl, bx + bw / 2, by + S * 0.015], fill=metal)
    rim = S * 0.018
    d.ellipse([bx - bw / 2 + rim, by - bl + rim, bx + bw / 2 - rim, by + S * 0.015 - rim],
              fill=(14, 18, 44, 255))   # the bowl is a window into space
    bh = bl / 2                                  # used below to place stars around the planet
    pr = S * 0.07
    px, py = bx, by - bl * 0.5
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([px - pr * 2, py - pr * 2, px + pr * 2, py + pr * 2], fill=(255, 200, 60, 120))
    layer = Image.alpha_composite(layer, glow.filter(ImageFilter.GaussianBlur(S * 0.03)))
    d = ImageDraw.Draw(layer)
    d.ellipse([px - pr, py - pr, px + pr, py + pr], fill=YELLOW + (255,))
    d.arc([px - pr * 1.9, py - pr * 0.55, px + pr * 1.9, py + pr * 0.55], 200, 340, fill=BLUE + (255,), width=int(S * 0.012))
    rnd = random.Random(3)
    for _ in range(14):
        a, r = rnd.uniform(0, 2 * math.pi), rnd.uniform(pr * 1.4, bw * 0.38)
        x, y = px + math.cos(a) * r * 0.8, py + math.sin(a) * r * 1.3
        s = rnd.choice([2, 3, 4]) * scale * 2
        d.ellipse([x - s, y - s, x + s, y + s], fill=(255, 255, 255, 230))
    layer = layer.rotate(angle, resample=Image.BICUBIC, center=(bx, by))
    img.paste(layer, (int(cx - bx), int(cy - by)), layer)
    return img


def banner():
    W, H = 2560, 1440
    img = space(W, H)
    # Everything important sits inside YouTube's 1546x423 safe area (visible on every device).
    img = spoon(img, W / 2 - 520, H / 2 + 40, 0.55)
    d = ImageDraw.Draw(img)
    x = W / 2 - 360
    d.text((x, H / 2 - 95), "UNIVERSE IN A", font=font(92), fill="white", anchor="lm")
    d.text((x, H / 2 + 5), "TEASPOON", font=font(118), fill=YELLOW, anchor="lm")
    d.text((x, H / 2 + 105), "Space facts, made human-sized  ·  New Shorts daily", font=font(40),
           fill=(200, 205, 230), anchor="lm")
    return img


def avatar():
    img = space(800, 800, seed=11)
    img = spoon(img, 400, 470, 1.15, angle=-30)
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    banner().save(OUT / "banner.png")
    avatar().save(OUT / "profile.png")
    print(f"saved {OUT / 'banner.png'} and {OUT / 'profile.png'}")
