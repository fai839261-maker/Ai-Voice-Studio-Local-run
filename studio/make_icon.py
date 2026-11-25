"""Draws the Unzip Spark logo (letters U and S with a spark) and writes resources/avs.ico, logo.png and logo_small.png.

Run:  python -m studio.make_icon        (needs Pillow)
The artwork is drawn from shapes, not fonts, so it looks the same on every PC. No white background:
the badge is a deep violet-to-teal gradient with a soft rim light, easy to spot on light and dark taskbars.
"""
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter

N = 1024


def gradient(c1, c2):
    small = Image.new("RGB", (256, 256))
    px = small.load()
    for y in range(256):
        for x in range(256):
            t = (x * 0.45 + y * 0.55) / 255
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))
    return small.resize((N, N), Image.Resampling.BICUBIC)


def star(draw, cx, cy, r, fill):
    k = r * 0.22
    draw.polygon([(cx, cy - r), (cx + k, cy - k), (cx + r, cy), (cx + k, cy + k), (cx, cy + r), (cx - k, cy + k), (cx - r, cy), (cx - k, cy - k)], fill=fill)


def stamp(draw, points, w, fill):
    """Thick rounded stroke made by stamping circles along a path (no gaps, round ends)."""
    for x, y in points:
        draw.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=fill)


def line_points(x1, y1, x2, y2, step=2.0):
    import math
    n = max(1, int(math.hypot(x2 - x1, y2 - y1) / step))
    return [(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(n + 1)]


def arc_points(cx, cy, r, a1, a2, step=2.0):
    import math
    n = max(2, int(abs(math.radians(a2 - a1)) * r / step))
    return [(cx + r * math.cos(math.radians(a1 + (a2 - a1) * i / n)), cy + r * math.sin(math.radians(a1 + (a2 - a1) * i / n))) for i in range(n + 1)]


def letters_layer(color_u, color_s):
    w = 92
    layer = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # U: down the left side, around the bottom, up the right side
    ux1, ux2, top, cy, r = 234, 470, 292, 590, 118
    cx = (ux1 + ux2) / 2
    stamp(d, line_points(ux1, top, ux1, cy) + arc_points(cx, cy, r, 180, 0) + line_points(ux2, cy, ux2, top), w, color_u)
    # S: top bowl then bottom bowl, joined in the middle
    sx, r2, c1 = 684, 104, 396
    c2 = c1 + 2 * r2
    stamp(d, arc_points(sx, c1, r2, -30, -270) + arc_points(sx, c2, r2, -90, 150), w, color_s)
    return layer


def build() -> Image.Image:
    base = gradient((92, 44, 226), (16, 168, 160)).convert("RGBA")
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle([20, 20, N - 20, N - 20], radius=230, fill=255)
    # soft light from the top-left corner
    glow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([-260, -320, 720, 520], fill=(255, 255, 255, 52))
    base = Image.alpha_composite(base, glow.filter(ImageFilter.GaussianBlur(60)))
    letters = letters_layer((255, 255, 255, 255), (255, 200, 61, 255))
    shadow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    shadow.paste((10, 6, 50, 150), (0, 26), letters.split()[3])
    base = Image.alpha_composite(base, shadow.filter(ImageFilter.GaussianBlur(22)))
    base = Image.alpha_composite(base, letters)
    sparkle = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    star(ImageDraw.Draw(sparkle), 836, 236, 80, (255, 255, 255, 255))
    star(ImageDraw.Draw(sparkle), 914, 336, 30, (255, 236, 160, 255))
    base = Image.alpha_composite(base, sparkle.filter(ImageFilter.GaussianBlur(1.2)))
    rim = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(rim).rounded_rectangle([24, 24, N - 24, N - 24], radius=226, outline=(255, 255, 255, 70), width=8)
    base = Image.alpha_composite(base, rim)
    out = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    out.paste(base, (0, 0), ImageChops.multiply(mask, base.split()[3]))
    return out


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "resources"
    root.mkdir(exist_ok=True)
    art = build()
    art.resize((512, 512), Image.Resampling.LANCZOS).save(root / "logo.png")
    art.resize((96, 96), Image.Resampling.LANCZOS).save(root / "logo_small.png")
    ico = art.resize((256, 256), Image.Resampling.LANCZOS)
    ico.save(root / "avs.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])
    print("written", root)
