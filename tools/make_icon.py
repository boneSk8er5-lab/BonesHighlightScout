"""Draws the Bones Highlight Scout icon in the same neon style as the Caption Studio icon.

Run:  python tools/make_icon.py   ->  BonesHighlightScout/HighlightScout.ico (+ .png)
"""
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "..", "BonesHighlightScout")
GLOW = (60, 230, 120)
CORE = (194, 255, 210)
S = 1024  # draw big, scale down


def font(path, size):
    try: return ImageFont.truetype(path, size)
    except OSError: return ImageFont.load_default()


def tile():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, S - 8, S - 8), radius=120, fill=(14, 20, 18, 255), outline=(40, 52, 46, 255), width=10)
    # faint scanlines like the Caption Studio icon
    for y in range(40, S - 40, 12):
        d.line((40, y, S - 40, y), fill=(18, 26, 23, 255), width=4)
    return img


def neon(base, draw_fn, blur=18):
    """draw_fn(draw, color, width_scale) draws the shapes; gives a blurred glow plus a bright core."""
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(glow), GLOW + (255,), 1.6)
    glow = glow.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(glow)
    shape = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(shape), GLOW + (255,), 1.0)
    base.alpha_composite(shape)
    core = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(core), CORE + (255,), 0.45)
    base.alpha_composite(core)


def waveform_points(x0, x1, mid, spike_h):
    """A quiet waveform with one big spike in the middle: the 'highlight'."""
    heights = [0.12, 0.2, 0.1, 0.25, 0.15, 0.3, 0.18, 1.0, 0.55, 0.22, 0.28, 0.12, 0.2, 0.1, 0.16]
    step = (x1 - x0) / (len(heights) - 1)
    return [(x0 + i * step, mid, h * spike_h) for i, h in enumerate(heights)]


def draw_mark(d, color, w, box):
    x0, y0, x1, y1 = box
    bw = int(26 * w)
    arm = (x1 - x0) * 0.09
    # [ ] brackets
    d.line((x0 + arm, y0, x0, y0, x0, y1, x0 + arm, y1), fill=color, width=bw, joint="curve")
    d.line((x1 - arm, y0, x1, y0, x1, y1, x1 - arm, y1), fill=color, width=bw, joint="curve")
    # waveform bars
    mid = (y0 + y1) / 2
    bar_w = max(4, int(30 * w))
    for x, m, h in waveform_points(x0 + (x1 - x0) * 0.16, x1 - (x1 - x0) * 0.16, mid, (y1 - y0) * 0.42):
        d.line((x, m - h, x, m + h), fill=color, width=bar_w)


def build(size, with_text):
    img = tile()
    if with_text:
        box = (200, 150, S - 200, 520)
    else:
        box = (170, 230, S - 170, S - 230)
    neon(img, lambda d, c, w: draw_mark(d, c, w, box))
    if with_text:
        big = font(r"C:\Windows\Fonts\consolab.ttf", 210)
        small = font(r"C:\Windows\Fonts\consolab.ttf", 74)
        def text(d, c, w):
            stroke = max(0, int(6 * w) - 2)
            d.text((S / 2, 700), "BONES", font=big, fill=c, anchor="mm", stroke_width=stroke, stroke_fill=c)
            d.text((S / 2, 850), "HIGHLIGHTSCOUT", font=small, fill=c, anchor="mm", stroke_width=max(0, stroke - 2), stroke_fill=c)
        neon(img, text, blur=14)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    # Big sizes carry the full logo; small ones just the bracketed waveform so it stays readable.
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [build(s, with_text=s >= 128) for s in sizes]
    ico = os.path.join(OUT_DIR, "HighlightScout.ico")
    frames[-1].save(ico, format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    build(512, True).save(os.path.join(OUT_DIR, "HighlightScout.png"))
    print("wrote", os.path.abspath(ico))
