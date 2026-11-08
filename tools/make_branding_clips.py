"""Generates Peak Moments World's banner (2560x1440) and profile picture (800x800) into assets/branding/clips/.

    .venv/bin/python tools/make_branding_clips.py
"""
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FONT = ROOT / "assets" / "fonts" / "Poppins-Bold.ttf"
OUT = ROOT / "assets" / "branding" / "clips"
BG, PINK, CYAN, YELLOW = (10, 8, 20), (255, 46, 136), (0, 220, 255), (255, 214, 0)


def font(size):
    return ImageFont.truetype(str(FONT), size)


def backdrop(w, h, seed=5):
    """Dark base, soft neon glows and diagonal speed streaks."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), BG)
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    for color, cx, cy, r in [((120, 20, 90), 0.2, 0.3, 0.3), ((10, 70, 120), 0.8, 0.7, 0.35),
                             ((80, 20, 140), 0.55, 0.15, 0.22)]:
        g.ellipse([w * cx - w * r, h * cy - w * r, w * cx + w * r, h * cy + w * r], fill=color)
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(w // 12)), 0.7)
    streaks = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(streaks)
    for _ in range(int(w / 40)):
        x, y = rnd.randint(-w // 4, w), rnd.randint(0, h)
        length, color = rnd.randint(w // 20, w // 6), rnd.choice([PINK, CYAN, (255, 255, 255)])
        d.line([x, y, x + length, y - length * 0.35], fill=color + (rnd.randint(25, 90),), width=rnd.choice([2, 3, 5]))
    img = Image.alpha_composite(img.convert("RGBA"), streaks.filter(ImageFilter.GaussianBlur(1)))
    return img


def peak(img, cx, cy, size):
    """The mark: a neon mountain peak with a play button cut into it."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    s = size
    outer = [(cx - s, cy + s * 0.62), (cx - s * 0.18, cy - s * 0.78), (cx + s * 0.12, cy - s * 0.25),
             (cx + s * 0.34, cy - s * 0.52), (cx + s, cy + s * 0.62)]
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(outer, fill=PINK + (170,))
    layer = Image.alpha_composite(layer, glow.filter(ImageFilter.GaussianBlur(s * 0.12)))
    d = ImageDraw.Draw(layer)
    d.polygon(outer, fill=PINK + (255,))
    d.polygon([(cx - s * 0.18, cy - s * 0.78), (cx - s * 0.36, cy - s * 0.4), (cx - s * 0.06, cy - s * 0.48)],
              fill=(255, 255, 255, 235))  # snow cap
    tri = [(cx - s * 0.2, cy - s * 0.02), (cx - s * 0.2, cy + s * 0.46), (cx + s * 0.2, cy + s * 0.22)]
    d.polygon(tri, fill=BG + (255,))
    d.line([outer[0], outer[-1]], fill=CYAN + (255,), width=max(3, int(s * 0.05)))
    return Image.alpha_composite(img, layer)


def banner():
    W, H = 2560, 1440
    img = backdrop(W, H)
    # Everything important sits inside YouTube's 1546x423 safe area (visible on every device).
    img = peak(img, W / 2 - 560, H / 2 - 10, 175)
    d = ImageDraw.Draw(img)
    x = W / 2 - 330
    d.text((x, H / 2 - 92), "PEAK MOMENTS", font=font(116), fill="white", anchor="lm")
    d.text((x + 4, H / 2 + 18), "WORLD", font=font(92), fill=YELLOW, anchor="lm")
    d.text((x + 6, H / 2 + 112), "The internet's best creator & gaming moments  ·  5 Shorts daily", font=font(38),
           fill=(205, 210, 235), anchor="lm")
    return img.convert("RGB")


def avatar():
    img = backdrop(800, 800, seed=9)
    img = peak(img, 400, 430, 270)
    return img.convert("RGB")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    banner().save(OUT / "banner.png")
    avatar().save(OUT / "profile.png")
    print(f"saved {OUT / 'banner.png'} and {OUT / 'profile.png'}")
