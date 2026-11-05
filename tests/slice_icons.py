"""Cut a generated 4x3 grid of duotone line icons (charcoal lines + yellow fills on white) into masks.

Run: python3 tests/slice_icons.py grid.png app/static/cat
For each category (row by row, catalog order) writes <slug>-line.png and <slug>-accent.png: white images
whose alpha is the charcoal strokes / the yellow fills. The page paints them with CSS masks in the theme's
ink colour and the brand yellow, so the icons follow light/dark mode like the rest of the UI.
"""
import os
import sys

from PIL import Image, ImageChops, ImageOps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.catalog import CATEGORIES  # noqa: E402


def masks(cell):
    rgb = cell.convert("RGB")
    r, g, b = rgb.split()
    dark = ImageOps.invert(rgb.convert("L"))                         # 0 on white, ~230 on charcoal
    yellow = ImageChops.subtract(ImageChops.darker(r, g), b)         # min(R,G) - B: high only for yellow
    accent = yellow.point(lambda v: 0 if v < 30 else min(255, int(v * 1.9)))
    line = ImageChops.subtract(dark.point(lambda v: min(255, int(v * 1.3))), yellow.point(lambda v: min(255, int(v * 1.8))))
    line = line.point(lambda v: 0 if v < 28 else min(255, int((v - 28) * 1.15)))
    return line, accent


def to_png(mask, box, size):
    m = mask.crop(box)
    side = max(m.size)
    sq = Image.new("L", (side, side), 0)
    sq.paste(m, ((side - m.width) // 2, (side - m.height) // 2))
    sq = sq.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    out.putalpha(sq)
    return out


def main(src, dest, cols=4, rows=3, size=192):
    grid = Image.open(src)
    W, H = grid.size
    os.makedirs(dest, exist_ok=True)
    for i, cat in enumerate(CATEGORIES[: cols * rows]):
        cx, cy = i % cols, i // cols
        cell = grid.crop((cx * W // cols, cy * H // rows, (cx + 1) * W // cols, (cy + 1) * H // rows))
        line, accent = masks(cell)
        box = ImageChops.lighter(line, accent).point(lambda v: 255 if v > 40 else 0).getbbox()
        pad = 6
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(cell.width, box[2] + pad), min(cell.height, box[3] + pad))
        to_png(line, box, size).save(os.path.join(dest, f"{cat['slug']}-line.png"), optimize=True)
        to_png(accent, box, size).save(os.path.join(dest, f"{cat['slug']}-accent.png"), optimize=True)
        print(cat["slug"], box)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
