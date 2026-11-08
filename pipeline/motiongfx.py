"""Original, deterministic procedural motion graphics for educational space scenes.

These are explicitly illustrations, not documentary imagery. Each scene supplies a
named style and seed; the renderer creates a short seamless vertical MP4 with no
text, logos, external assets, or network access.
"""
from pathlib import Path
import math
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 540, 960
FPS = 24
DURATION = 4
FRAMES = FPS * DURATION
TAU = math.tau


def _rng(spec):
    return np.random.default_rng(int(spec.get("seed", 1)))


def _background(rng):
    y = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    top = np.array([2, 4, 16], dtype=np.float32)[None, None, :]
    bottom = np.array([10, 7, 30], dtype=np.float32)[None, None, :]
    base = np.broadcast_to(top * (1 - y) + bottom * y, (H, W, 3)).copy()
    stars = []
    for _ in range(105):
        stars.append((float(rng.uniform(0, W)), float(rng.uniform(0, H)),
                      float(rng.uniform(0.5, 1.8)), float(rng.uniform(0, TAU)),
                      int(rng.integers(130, 235))))
    return base, stars


def _add_stars(image, stars, phase):
    draw = ImageDraw.Draw(image)
    for x, y, radius, offset, brightness in stars:
        pulse = 0.65 + 0.35 * math.sin(phase + offset)
        value = int(brightness * pulse)
        r = radius * (0.85 + 0.15 * pulse)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(value, value, min(255, value + 20)))


def _glow(image, box, color, blur=35, alpha=150):
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(box, fill=(*color, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    image.alpha_composite(layer)


def _texture(rng, style):
    tw, th = 512, 256
    palettes = {
        "titan": (190, 123, 45), "rhea": (160, 157, 153), "iapetus": (152, 147, 135),
        "enceladus": (190, 211, 218), "mimas": (155, 157, 158), "miranda": (145, 150, 153),
        "ariel": (156, 163, 165), "umbriel": (94, 96, 99), "titania": (145, 139, 132),
        "oberon": (132, 119, 113), "ceres": (126, 125, 121), "pluto": (180, 143, 110),
    }
    base = np.array(palettes.get(style, (150, 150, 150)), dtype=np.float32)
    noise = rng.normal(0, 11, (th, tw, 1)).astype(np.float32)
    arr = np.clip(base[None, None, :] + noise, 0, 255).astype(np.uint8)
    image = Image.fromarray(arr, "RGB")
    draw = ImageDraw.Draw(image)
    for _ in range(0 if style == "titan" else 95):
        x, y = int(rng.integers(0, tw)), int(rng.integers(0, th))
        radius = int(rng.integers(2, 17))
        shade = int(rng.integers(-48, 28))
        color = tuple(int(np.clip(c + shade, 0, 255)) for c in base)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
        draw.arc((x - radius, y - radius, x + radius, y + radius), 200, 345,
                 fill=tuple(min(255, int(c) + 30) for c in color), width=1)
    if style == "iapetus":
        draw.rectangle((0, 0, tw // 2, th), fill=(55, 49, 43))
    if style in {"miranda", "ariel", "titania"}:
        for _ in range(20):
            x = int(rng.integers(0, tw))
            draw.line((x, 0, x + int(rng.integers(-100, 100)), th),
                      fill=(75, 82, 87), width=int(rng.integers(1, 4)))
    if style == "enceladus":
        for _ in range(12):
            x = int(rng.integers(0, tw))
            draw.line((x, th // 2, x + 55, th), fill=(70, 125, 154), width=2)
    if style == "pluto":
        draw.ellipse((270, 65, 345, 150), fill=(218, 193, 157))
        draw.ellipse((325, 65, 400, 150), fill=(218, 193, 157))
        draw.polygon(((270, 110), (400, 110), (335, 205)), fill=(218, 193, 157))
    return np.asarray(image, dtype=np.float32)


def _moon_frame(spec, rng, texture, stars, phase, frame):
    image = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    style = spec.get("style", "moon")
    size = 350
    yy, xx = np.mgrid[-1:1:complex(size), -1:1:complex(size)].astype(np.float32)
    rr = xx * xx + yy * yy
    mask = rr <= 1
    z = np.sqrt(np.clip(1 - rr, 0, 1))
    longitude = np.arctan2(xx, z) + 0.55 * math.sin(phase)
    latitude = np.arcsin(np.clip(yy, -1, 1))
    th, tw = texture.shape[:2]
    tx = ((longitude / TAU + 0.5) * tw).astype(int) % tw
    ty = np.clip(((0.5 - latitude / math.pi) * th).astype(int), 0, th - 1)
    sampled = texture[ty, tx]
    light = np.clip(0.22 + 0.90 * np.clip(0.72 * z - 0.55 * xx + 0.20 * yy, 0, 1), 0, 1.12)
    sphere = np.clip(sampled * light[..., None], 0, 255).astype(np.uint8)
    sphere[~mask] = 0
    alpha = np.where(mask, 255, 0).astype(np.uint8)
    sprite = Image.fromarray(np.dstack((sphere, alpha)), "RGBA")
    cx = W // 2 + int(10 * math.sin(phase))
    cy = 435 + int(12 * math.cos(phase))
    color = (205, 139, 52) if style == "titan" else (110, 155, 205)
    _glow(image, (cx - 185, cy - 185, cx + 185, cy + 185), color, 38, 90)
    image.alpha_composite(sprite, (cx - size // 2, cy - size // 2))
    draw = ImageDraw.Draw(image)
    if style == "mimas":
        draw.ellipse((cx + 25, cy - 70, cx + 125, cy + 30), fill=(65, 68, 72, 210),
                     outline=(195, 198, 200, 230), width=7)
        draw.ellipse((cx + 48, cy - 48, cx + 102, cy + 6), outline=(105, 108, 112, 255), width=5)
    if style == "enceladus":
        for k in range(35):
            p = (k / 35 + frame / FRAMES) % 1
            x = cx - 50 + 100 * math.sin(k * 2.3) * p
            y = cy - 160 - 210 * p
            r = 1 + 3 * (1 - p)
            draw.ellipse((x - r, y - r, x + r, y + r), fill=(165, 220, 255, int(220 * (1 - p))))
    return image


def _galaxy_frame(spec, rng, stars, phase):
    base, _ = _background(rng)
    image = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    style = spec.get("style", "spiral")
    count = 2400
    r = np.sqrt(rng.uniform(0.006, 1, count))
    arm = rng.integers(0, 2, count) * math.pi
    jitter = rng.normal(0, 0.16, count)
    inclination = {"andromeda": 0.38, "triangulum": 0.82, "whirlpool": 0.72,
                   "sombrero": 0.12, "cartwheel": 0.72}.get(style, 0.65)
    rotation = 0.16 * math.sin(phase)
    gx = (np.arange(W, dtype=np.float32)[None, :] - W / 2) / 205
    gy = (np.arange(H, dtype=np.float32)[:, None] - 440) / (205 * inclination)
    gr = np.sqrt(gx * gx + gy * gy)
    ga = np.arctan2(gy, gx)
    if style == "cartwheel":
        pattern = np.exp(-((gr - 0.82) / 0.09) ** 2)
    else:
        pattern = (0.5 + 0.5 * np.cos(2 * (ga - gr * 5.4 - rotation))) ** 8 * np.exp(-(gr / 0.9) ** 4)
    base += pattern[..., None] * np.array([65, 85, 145], dtype=np.float32)[None, None, :]
    image = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    if style == "cartwheel":
        r = np.clip(rng.normal(0.82, 0.10, count), 0.2, 1.05)
        theta = rng.uniform(0, TAU, count) + rotation
    else:
        theta = arm + r * 5.4 + jitter + rotation * (1.25 - r)
    x = np.cos(theta) * r * 205
    y = np.sin(theta) * r * 205 * inclination
    cx, cy = W / 2, 440
    px = (cx + x).astype(int)
    py = (cy + y).astype(int)
    good = (px >= 1) & (px < W - 1) & (py >= 1) & (py < H - 1)
    layer = np.zeros((H, W, 3), dtype=np.float32)
    brightness = np.clip(1.15 - r, 0.15, 1)
    colors = np.ones((count, 3), dtype=np.float32) * np.array([175, 195, 235])
    colors[r < 0.35] = [255, 220, 160]
    for c in range(3):
        np.add.at(layer[..., c], (py[good], px[good]), colors[good, c] * brightness[good])
    cloud = Image.fromarray(np.clip(layer, 0, 255).astype(np.uint8), "RGB").filter(ImageFilter.GaussianBlur(0.65))
    combined = np.asarray(image.convert("RGB"), dtype=np.float32) + np.asarray(cloud, dtype=np.float32)
    image = Image.fromarray(np.clip(combined, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    core_w, core_h = (100, 36) if style == "sombrero" else (90, 90 * max(0.25, inclination))
    _glow(image, (cx - core_w, cy - core_h, cx + core_w, cy + core_h), (255, 205, 140), 26, 230)
    if style == "sombrero":
        draw = ImageDraw.Draw(image)
        draw.line((cx - 210, cy + 4, cx + 210, cy - 4), fill=(40, 27, 35, 255), width=10)
    if style == "whirlpool":
        _glow(image, (cx + 130, cy - 150, cx + 205, cy - 75), (210, 185, 255), 18, 180)
    return image


def _nebula_frame(spec, rng, stars, phase, frame):
    base, _ = _background(rng)
    style = spec.get("style", "orion")
    palettes = {"orion": ((25, 45, 110), (240, 85, 35)), "crab": ((20, 80, 115), (205, 110, 55)),
                "ring": ((30, 60, 145), (235, 65, 85)), "helix": ((20, 100, 105), (215, 120, 65)),
                "eagle": ((20, 90, 90), (225, 125, 45))}
    c1, c2 = palettes.get(style, palettes["orion"])
    small = rng.random((60, 34), dtype=np.float32)
    noise = np.asarray(Image.fromarray((small * 255).astype(np.uint8)).resize((W, H), Image.Resampling.BICUBIC),
                       dtype=np.float32) / 255
    noise = np.roll(noise, int(frame * W / FRAMES), axis=1)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dx, dy = (xx - W / 2) / 230, (yy - 440) / 290
    radius = np.sqrt(dx * dx + dy * dy)
    if style in {"ring", "helix"}:
        cloud = np.exp(-((radius - (0.52 if style == "ring" else 0.65)) / 0.15) ** 2)
    else:
        cloud = np.exp(-radius * radius * 1.25)
    density = np.clip((noise * 1.45 - 0.30) * cloud, 0, 1)
    mix = np.clip(noise * 1.4 - 0.2, 0, 1)[..., None]
    colors = np.array(c1)[None, None, :] * (1 - mix) + np.array(c2)[None, None, :] * mix
    arr = base + colors * density[..., None] * 1.25
    image = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    if style == "crab":
        draw = ImageDraw.Draw(image)
        for k in range(42):
            angle = k * TAU / 42 + 0.06 * math.sin(phase)
            length = 80 + 120 * (0.5 + 0.5 * math.sin(k * 4.7))
            x2, y2 = W / 2 + math.cos(angle) * length, 440 + math.sin(angle) * length
            draw.line((W / 2, 440, x2, y2), fill=(130, 205, 230, 100), width=2)
    if style == "eagle":
        draw = ImageDraw.Draw(image)
        for x, width, height in ((190, 65, 280), (275, 85, 360), (365, 55, 240)):
            draw.polygon(((x - width // 2, 700), (x - width // 3, 700 - height),
                          (x, 665 - height), (x + width // 3, 700 - height), (x + width // 2, 700)),
                         fill=(7, 15, 26, 220))
    _glow(image, (W / 2 - 45, 395, W / 2 + 45, 485), (235, 240, 255), 25, 200)
    return image


def _comet_frame(spec, rng, stars, phase):
    base, _ = _background(rng)
    style = spec.get("style", "halley")
    cx = 205 + 25 * math.sin(phase)
    cy = 470 + 18 * math.cos(phase)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dx, dy = xx - cx, yy - cy
    along = (dx - dy * 0.45) / math.sqrt(1 + 0.45 ** 2)
    across = (dy + dx * 0.45) / math.sqrt(1 + 0.45 ** 2)
    positive = np.clip(along, 0, None)
    width = 12 + positive * 0.11
    tail = np.exp(-(across / width) ** 2) * np.exp(-positive / 330) * (along > -15)
    color = np.array([95, 155, 255], dtype=np.float32)
    arr = base + tail[..., None] * color[None, None, :] * 0.95
    if style == "hale-bopp":
        dust_width = 25 + positive * 0.16
        dust = np.exp(-((across + positive * 0.18) / dust_width) ** 2) * np.exp(-positive / 350) * (along > 0)
        arr += dust[..., None] * np.array([255, 205, 135])[None, None, :] * 0.75
    image = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    _glow(image, (cx - 45, cy - 45, cx + 45, cy + 45), (215, 235, 255), 18, 230)
    draw = ImageDraw.Draw(image)
    rotation = phase * 0.18
    if style == "67p":
        for ox, oy, radius in ((-25, 15, 37), (20, -18, 30)):
            draw.ellipse((cx + ox - radius, cy + oy - radius, cx + ox + radius, cy + oy + radius),
                         fill=(105, 104, 101, 255), outline=(175, 174, 166, 255), width=3)
    else:
        points = []
        for k in range(14):
            angle = k * TAU / 14 + rotation
            radius = 37 * (1 + 0.16 * math.sin(k * 3.1))
            points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
        draw.polygon(points, fill=(100, 96, 91, 255), outline=(188, 181, 165, 255))
    return image


def _terrain_frame(spec, rng, stars, phase):
    base, _ = _background(rng)
    image = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    style = spec.get("style", "pluto-terrain")
    draw = ImageDraw.Draw(image)
    shift = 18 * math.sin(phase)
    if style == "occator":
        draw.rectangle((0, 220, W, H), fill=(91, 91, 89, 255))
        for k in range(45):
            x = (k * 137) % (W + 100) - 50 + shift
            y = 260 + (k * 83) % 600
            r = 8 + (k * 11) % 35
            draw.ellipse((x - r, y - r * 0.45, x + r, y + r * 0.45), outline=(55, 56, 56, 255), width=4)
        _glow(image, (W / 2 - 105 + shift, 450, W / 2 + 105 + shift, 590), (190, 192, 187), 22, 120)
        draw = ImageDraw.Draw(image)
        for x, y, r in ((250, 505, 16), (290, 520, 12), (270, 480, 9), (320, 490, 7)):
            draw.ellipse((x - r + shift, y - r, x + r + shift, y + r), fill=(245, 246, 232, 255))
    else:
        palettes = [(54, 47, 54), (90, 70, 66), (135, 104, 84), (181, 151, 119)]
        for layer, color in enumerate(palettes):
            baseline = 470 + layer * 115
            points = [(-80, H)]
            for k in range(12):
                x = -80 + k * 65 + shift * (layer + 1) / 4
                peak = baseline - (55 + 75 * (0.5 + 0.5 * math.sin(k * 2.2 + layer)))
                points.extend(((x, baseline), (x + 30, peak), (x + 65, baseline)))
            points.append((W + 80, H))
            draw.polygon(points, fill=(*color, 255))
        haze = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(haze).rectangle((0, 280, W, 520), fill=(175, 155, 160, 40))
        image.alpha_composite(haze.filter(ImageFilter.GaussianBlur(35)))
    return image


def _photo_frame(photo, spec, base, stars, phase):
    """Animate a documented texture with depth, parallax, light and particles."""
    from PIL import ImageEnhance, ImageOps
    kind = spec.get("kind", "moon")
    backdrop = ImageOps.fit(photo.convert("RGB"), (W, H), Image.Resampling.LANCZOS)
    backdrop = backdrop.filter(ImageFilter.GaussianBlur(32))
    backdrop = ImageEnhance.Brightness(backdrop).enhance(0.20).convert("RGBA")
    image = Image.blend(Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB").convert("RGBA"),
                        backdrop, 0.42)
    rgba = photo.convert("RGBA")
    luminance = np.asarray(photo.convert("L"), dtype=np.float32)
    alpha = np.clip((luminance - 7) * 15, 0, 255).astype(np.float32)
    h, w = alpha.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    if kind == "moon":
        radius = np.sqrt(((xx - w / 2) / (w * 0.52)) ** 2 + ((yy - h / 2) / (h * 0.52)) ** 2)
        feather = np.clip((1 - radius) / 0.14, 0, 1)
    else:
        edge = np.minimum(np.minimum(xx, w - 1 - xx), np.minimum(yy, h - 1 - yy))
        feather = np.clip(edge / 38, 0, 1)
    alpha = np.minimum(alpha, feather * 255).astype(np.uint8)
    alpha_image = Image.fromarray(alpha, "L").filter(ImageFilter.GaussianBlur(1.2))
    rgba.putalpha(alpha_image)
    bbox = alpha_image.getbbox()
    if bbox:
        rgba = rgba.crop(bbox)
    rgba.thumbnail((505, 570), Image.Resampling.LANCZOS)
    pulse = 1.035 + 0.025 * math.sin(phase)
    rgba = rgba.resize((max(1, int(rgba.width * pulse)), max(1, int(rgba.height * pulse))),
                       Image.Resampling.LANCZOS)
    angle = {"moon": 1.8, "galaxy": 2.4, "nebula": 1.2, "comet": -2.0, "terrain": 0.4}.get(kind, 1.0)
    rgba = rgba.rotate(angle * math.sin(phase), resample=Image.Resampling.BICUBIC, expand=True)
    cx = W // 2 + int(12 * math.sin(phase))
    cy = 455 + int(10 * math.cos(phase))
    colors = {"moon": (110, 155, 220), "galaxy": (120, 115, 235), "nebula": (210, 80, 145),
              "comet": (100, 165, 255), "terrain": (160, 125, 110)}
    glow = colors.get(kind, (120, 140, 220))
    _glow(image, (cx - 210, cy - 210, cx + 210, cy + 210), glow, 55, 85)
    image.alpha_composite(rgba, (cx - rgba.width // 2, cy - rgba.height // 2))
    _add_stars(image, stars, phase)
    draw = ImageDraw.Draw(image)
    if kind == "comet":
        for k in range(28):
            p = (k / 28 + phase / TAU) % 1
            x = cx + 30 + p * 260
            y = cy - 15 - p * 130 + 12 * math.sin(k * 2.1)
            radius = 1.5 * (1 - p) + 0.4
            draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                         fill=(160, 205, 255, int(190 * (1 - p))))
    if kind == "nebula":
        for k in range(16):
            angle = k * TAU / 16 + phase * 0.15
            x = cx + math.cos(angle) * (120 + 20 * math.sin(k))
            y = cy + math.sin(angle) * (90 + 15 * math.cos(k))
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(255, 210, 235, 150))
    return image


def render(spec, out_path):
    """Render one seamless four-second motion graphic and return its path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rng = _rng(spec)
    base, stars = _background(rng)
    kind = spec.get("kind", "moon")
    photo = Image.open(spec["image"]).convert("RGB") if spec.get("image") else None
    texture = _texture(rng, spec.get("style", "")) if kind == "moon" and photo is None else None
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
               "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(out_path)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for frame in range(FRAMES):
            phase = TAU * frame / FRAMES
            if photo is not None:
                image = _photo_frame(photo, spec, base, stars, phase)
            elif kind == "moon":
                image = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
                _add_stars(image, stars, phase)
                # Use a private generator so adding the moon never changes the star field.
                moon = _moon_frame(spec, np.random.default_rng(0), texture, [], phase, frame)
                image.alpha_composite(moon)
            elif kind == "galaxy":
                image = _galaxy_frame(spec, rng, stars, phase)
                _add_stars(image, stars, phase)
            elif kind == "nebula":
                image = _nebula_frame(spec, rng, stars, phase, frame)
                _add_stars(image, stars, phase)
            elif kind == "comet":
                image = _comet_frame(spec, rng, stars, phase)
                _add_stars(image, stars, phase)
            elif kind == "terrain":
                image = _terrain_frame(spec, rng, stars, phase)
                _add_stars(image, stars, phase)
            else:
                raise ValueError(f"unknown motion kind: {kind}")
            process.stdin.write(np.asarray(image.convert("RGB"), dtype=np.uint8).tobytes())
    finally:
        if process.stdin:
            process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError(f"ffmpeg failed rendering {out_path}")
    return out_path
