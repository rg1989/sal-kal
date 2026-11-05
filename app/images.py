"""Product photos from the stores' own catalogue, with the white studio background cut away.

Rami Levy's online shop publishes clean catalogue shots addressable by barcode, which covers
nearly every widely sold product. Each photo is fetched once, its white background (only the white
connected to the edges, so white packaging stays) made transparent, and saved as WebP in PHOTO_DIR;
the web app serves them as /photos/<barcode>.webp on Sal's own tiles. Barcodes the catalogue doesn't
have are remembered for 30 days so they aren't asked for again every night.
Run: python -m app.images [--limit N]
"""
import argparse
import io
import json
import logging
import os
import time
import urllib.error
import urllib.request

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import db as dbmod
from .geo import UA

log = logging.getLogger("images")
SOURCE = "https://img.rami-levy.co.il/product/{code}/medium.jpg"
PHOTO_DIR = os.environ.get("SAL_PHOTOS") or os.path.join(os.path.dirname(os.path.abspath(dbmod.DB_PATH)), "photos")
MISSING = os.path.join(PHOTO_DIR, "missing.json")


def photo_path(code):
    return os.path.join(PHOTO_DIR, f"{code}.webp")


def cutout(im, cutoff=250, size=360):
    """Make the white background transparent: near-white pixels connected to the image border."""
    im = im.convert("RGB")
    im.thumbnail((size, size), Image.LANCZOS)  # work at display size: the flood fill is per pixel
    w, h = im.size
    r, g, b = im.split()
    lo = ImageChops.darker(ImageChops.darker(r, g), b)  # a pixel is white only if its darkest channel is
    m = lo.point(lambda v: 255 if v >= cutoff else 0)
    step = max(1, min(w, h) // 60)
    seeds = [(x, y) for x in range(0, w, step) for y in (0, h - 1)] + [(x, y) for y in range(0, h, step) for x in (0, w - 1)]
    for p in seeds:
        if m.getpixel(p) == 255:
            ImageDraw.floodfill(m, p, 128)
    bg = m.point(lambda v: 255 if v == 128 else 0)
    alpha = ImageChops.invert(bg.filter(ImageFilter.MaxFilter(3))).filter(ImageFilter.GaussianBlur(0.7))
    out = im.convert("RGBA")
    out.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 24 else 0).getbbox()
    return out.crop(box) if box else out


SLOW = "slow"  # the source asked us to back off (HTTP 429/503)


def fetch_one(code):
    """True = saved, False = the catalogue has no photo, SLOW = back off, None = try again another time."""
    try:
        req = urllib.request.Request(SOURCE.format(code=code), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        img = cutout(Image.open(io.BytesIO(data)))
        tmp = photo_path(code) + ".tmp"
        img.save(tmp, "WEBP", quality=86)
        os.replace(tmp, photo_path(code))
        return True
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            return SLOW
        return False if e.code in (403, 404) else None
    except Exception as e:  # network blip or a broken image: retry next run
        log.debug("photo %s: %s", code, e)
        return None


def run(con, limit=1200, pause=2.5):
    """One gentle pass: a photo every `pause` seconds, at most `limit` per run (the timer runs it hourly).
    The source sits behind Cloudflare and rate-limits bursts; the first "slow down" ends the run."""
    os.makedirs(PHOTO_DIR, exist_ok=True)
    try:
        with open(MISSING) as fh:
            missing = {k: v for k, v in json.load(fh).items() if time.time() - v < 30 * 86400}
    except (OSError, ValueError):
        missing = {}
    codes = [r[0] for r in con.execute("""SELECT code FROM products WHERE scope='global' AND store_count > 0
                                           AND length(code) >= 8 ORDER BY store_count DESC""")]
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='deals'").fetchone():
        on_deal = {r[0] for r in con.execute("SELECT DISTINCT key FROM deals")}
        codes.sort(key=lambda c: c not in on_deal)  # deal products first (they fill the home page), then by popularity
    todo = [c for c in codes if c not in missing and not os.path.exists(photo_path(c))][:limit]
    log.info("%d photos to fetch (%d already here, %d known missing)", len(todo),
             sum(os.path.exists(photo_path(c)) for c in codes), len(missing))
    saved = none = errors = 0
    for code in todo:
        res = fetch_one(code)
        if res == SLOW:
            log.warning("photo source asked us to slow down; stopping until the next run")
            break
        if res is True:
            saved += 1
        elif res is False:
            none += 1
            missing[code] = int(time.time())
        else:
            errors += 1
            if errors >= 30:
                log.warning("photo source keeps failing; stopping until the next run")
                break
        time.sleep(pause)
    with open(MISSING, "w") as fh:
        json.dump(missing, fh)
    log.info("saved %d photos; %d not in the catalogue; %d errors", saved, none, errors)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    con = dbmod.connect()
    run(con, a.limit)


if __name__ == "__main__":
    main()
