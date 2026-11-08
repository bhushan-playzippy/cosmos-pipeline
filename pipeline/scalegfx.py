"""Original to-scale comparison graphics: the channel's signature visual ("Cosmic Scale").

A scene may carry  "compare": {"kind": "diameter"|"length", "unit": "km",
                               "items": [{"label": "Earth", "value": 12742}, {"label": "Sun", "value": 1391000}]}
Values are fact-checked with the rest of the script. Sizes are drawn honestly to scale; when the smaller object
would be under a few pixels, it's drawn as a marked dot and the ratio is printed instead of faking its size.
"""
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .util import ASSETS

W, H = 1080, 1920
COLORS = [(255, 212, 0), (90, 170, 255), (255, 120, 90)]


def valid(spec):
    try:
        items = spec["items"]
        return 2 <= len(items) <= 3 and all(float(i["value"]) > 0 and str(i["label"]).strip() for i in items)
    except (KeyError, TypeError, ValueError):
        return False


def _font(size):
    try:
        return ImageFont.truetype(str(ASSETS / "fonts" / "Poppins-Bold.ttf"), size)
    except OSError:
        return ImageFont.load_default()


def _fmt(v):
    v = float(v)
    if v >= 1e9:
        return f"{v / 1e9:,.1f} billion"
    if v >= 1e6:
        return f"{v / 1e6:,.1f} million"
    return f"{v:,.0f}" if v >= 10 else f"{v:g}"


def render(spec, out_path):
    items = sorted(spec["items"], key=lambda i: float(i["value"]))
    unit, kind = spec.get("unit", ""), spec.get("kind", "diameter")
    big = float(items[-1]["value"])
    img = Image.new("RGB", (W, H), (6, 8, 20))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([-200, 300, W + 200, 1500], fill=(30, 30, 90))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(180)), 0.6)
    d = ImageDraw.Draw(img)
    # Layout keeps clear of the burned-in captions (~1180–1340 px) and the 12% Ken Burns crop at the edges.
    d.text((W // 2, 140), "TO SCALE", font=_font(64), fill=(255, 212, 0), anchor="mm")
    label_font, small = _font(46), _font(34)

    if kind == "length":
        max_len = W - 200
        for n, it in enumerate(items):
            y = 330 + n * 250
            L = max(6, max_len * float(it["value"]) / big)
            d.rounded_rectangle([100, y, 100 + L, y + 60], radius=12, fill=COLORS[n % 3])
            d.text((100, y - 60), f"{it['label']}", font=label_font, fill="white")
            d.text((100, y + 75), f"{_fmt(it['value'])} {unit}", font=small, fill=(200, 200, 220))
    else:
        max_d = 600
        x = W // 2
        cy = 480 + max_d // 2
        for n, it in enumerate(reversed(items)):  # biggest first, smaller ones drawn on top, bottom-aligned
            dia = max_d * float(it["value"]) / big
            shown = max(dia, 8)
            top = cy + max_d / 2 - shown
            d.ellipse([x - shown / 2, top, x + shown / 2, top + shown], fill=COLORS[(len(items) - 1 - n) % 3],
                      outline=(255, 255, 255) if dia < 8 else None, width=2)
        legend_y = 215
        for n, it in enumerate(reversed(items)):
            ratio = big / float(it["value"])
            extra = "" if ratio < 1.5 else f"  ·  {ratio:,.0f}× smaller" if ratio < 1e6 else ""
            d.ellipse([110, legend_y + n * 70 + 8, 146, legend_y + n * 70 + 44],
                      fill=COLORS[(len(items) - 1 - n) % 3])
            d.text((165, legend_y + n * 70), f"{it['label']}: {_fmt(it['value'])} {unit}{extra}",
                   font=small, fill="white")
    img.save(out_path)
    return out_path
