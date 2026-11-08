"""Generates the true-stories channel's banner (2560x1440) and profile picture (800x800) into assets/branding/clips/.

    .venv/bin/python tools/make_branding_stories.py
"""
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FONT = ROOT / "assets" / "fonts" / "Poppins-Bold.ttf"
OUT = ROOT / "assets" / "branding" / "clips"
INK, GOLD, PAPER, RED = (22, 16, 12), (232, 186, 92), (236, 222, 196), (196, 52, 40)


def font(size):
    return ImageFont.truetype(str(FONT), size)


def archive(w, h, seed=3):
    """Dark sepia base with a warm vignette and film grain: the look of an old photo archive."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), INK)
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([w * 0.15, -h * 0.2, w * 0.85, h * 1.2], fill=(92, 62, 34))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(w // 7)), 0.6)
    grain = Image.effect_noise((w, h), 22).convert("RGB")
    img = Image.blend(img, grain, 0.07)
    d = ImageDraw.Draw(img)
    for _ in range(int(w / 90)):  # faint film scratches
        x = rnd.randint(0, w)
        d.line([x, 0, x + rnd.randint(-30, 30), h], fill=(70, 55, 40), width=1)
    return img


def stamp(img, cx, cy, r):
    """The mark: a red rubber 'TRUE' stamp, slightly rotated, like a verified case file."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w = max(4, int(r * 0.07))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=RED + (235,), width=w)
    d.ellipse([cx - r * 0.82, cy - r * 0.82, cx + r * 0.82, cy + r * 0.82], outline=RED + (235,), width=max(2, w // 2))
    f = font(int(r * 0.52))
    d.text((cx, cy + r * 0.02), "TRUE", font=f, fill=RED + (240,), anchor="mm")
    for k in range(10):  # small stars around the ring
        a = k * math.tau / 10
        x, y = cx + math.cos(a) * r * 0.91, cy + math.sin(a) * r * 0.91
        if abs(math.sin(a)) > 0.45:
            d.ellipse([x - w * 0.5, y - w * 0.5, x + w * 0.5, y + w * 0.5], fill=RED + (235,))
    layer = layer.rotate(-12, resample=Image.BICUBIC, center=(cx, cy))
    return Image.alpha_composite(img.convert("RGBA"), layer)


def banner():
    W, H = 2560, 1440
    img = archive(W, H)
    # Everything important sits inside YouTube's 1546x423 safe area (visible on every device).
    img = stamp(img, W / 2 - 560, H / 2, 165)
    d = ImageDraw.Draw(img)
    x = W / 2 - 330
    d.text((x, H / 2 - 88), "UNBELIEVABLY", font=font(108), fill=PAPER, anchor="lm")
    d.text((x + 4, H / 2 + 22), "TRUE", font=font(116), fill=GOLD, anchor="lm")
    d.text((x + 6, H / 2 + 118), "Real stories that sound made up  ·  New true story every day", font=font(38),
           fill=(200, 180, 150), anchor="lm")
    return img.convert("RGB")


def avatar():
    img = archive(800, 800, seed=8)
    img = stamp(img, 400, 400, 300)
    return img.convert("RGB")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    banner().save(OUT / "banner_stories.png")
    avatar().save(OUT / "profile_stories.png")
    print(f"saved {OUT / 'banner_stories.png'} and {OUT / 'profile_stories.png'}")
