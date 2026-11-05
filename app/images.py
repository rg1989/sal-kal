"""Product photos from the chains' own online catalogues: one photo per barcode, white studio background cut away.

Several catalogues are used at the same time (SOURCES). Each has its own worker and its own polite pace, works first
on the products its chain sells (where it is most likely to have the photo), then helps with the rest. A product
needs one photo: whichever source gets it first. A source that says "slow down" (HTTP 429/503, or a bot-check page,
which is never worked around) rests with a growing pause while the others carry on. Barcodes a source doesn't have
are remembered per source for 30 days, written as they are found. Photos are saved as WebP in PHOTO_DIR and served
by the web app as /photos/<barcode>.webp. Private household use only.
Run: python -m app.images [--minutes 50]
"""
import argparse
import io
import json
import logging
import os
import queue as queue_mod
import threading
import time
import urllib.error
import urllib.request

import base64
import hashlib
import re
import urllib.parse

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

from . import db as dbmod
from .geo import UA

log = logging.getLogger("images")
PHOTO_DIR = os.environ.get("SAL_PHOTOS") or os.path.join(os.path.dirname(os.path.abspath(dbmod.DB_PATH)), "photos")
MISSING = os.path.join(PHOTO_DIR, "missing.json")
MISS_DAYS = 30
OK, MISS, SLOW, RETRY = "ok", "miss", "slow", "retry"


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


def save_photo(code, data, white=True):
    """Decode, cut the white background away (studio shots) or just shrink, and save as WebP. False if not an image."""
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return False
    if min(im.size) < 40:  # placeholders and tracking pixels
        return False
    if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:  # already cut out: lay it on white
        rgba = im.convert("RGBA")
        if rgba.getextrema()[3][1] == 0:  # fully transparent: a blank placeholder
            return False
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, rgba).convert("RGB")
    if ImageStat.Stat(im.convert("L")).stddev[0] < 2:  # one flat colour: a blank placeholder
        return False
    if white:
        out = cutout(im)
    else:
        out = im.convert("RGBA")
        out.thumbnail((360, 360), Image.LANCZOS)
    os.makedirs(PHOTO_DIR, exist_ok=True)
    tmp = f"{photo_path(code)}.{os.getpid()}.{threading.get_ident()}.tmp"
    out.save(tmp, "WEBP", quality=86)
    os.replace(tmp, photo_path(code))
    return True


# ---------------------------------------------------------------- sources
class Blocked(Exception):
    """The site answered with a rate limit or a bot check: rest, don't retry harder."""


class Source:
    """One catalogue. Subclasses say how to find a barcode's image (image_url); this class paces the requests."""
    name = "source"
    chains = ()        # chain ids whose products this catalogue has
    interval = 2.0     # starting seconds between requests to this site
    min_interval = None  # it may speed up to this while requests succeed (None: fixed pace)
    workers = 1        # requests in flight at once (they still keep to the pace)
    fallback = False   # small photos: only for products the general sources didn't have
    api_403_is_block = False  # a site whose firewall answers 403 when it has had enough: rest at once
    white = True       # studio shots on white: cut the background away
    headers = {}

    def __init__(self):
        self.next_at = 0.0
        self.rest = 0.0
        self.good = 0
        self.lock = threading.Lock()

    def went_well(self):
        if self.min_interval is None:
            return
        with self.lock:
            self.rest = 0.0
            self.good += 1
            if self.good >= 25:  # 25 answers in a row without a complaint: 10% faster, down to the floor
                self.good = 0
                self.interval = max(self.min_interval, self.interval * 0.9)

    def wait(self):
        with self.lock:
            now = time.time()
            at = max(now, self.next_at)
            self.next_at = at + self.interval
        if at > now:
            time.sleep(at - now)

    def resting(self):
        return time.time() < self.next_at - self.interval - 1

    def slow_down(self):
        with self.lock:
            self.good = 0
            if self.min_interval is not None:
                self.interval = min(self.interval * 2, 30.0)
            self.rest = min(max(self.rest * 2, 60.0), 3600.0)
            self.next_at = time.time() + self.rest
        log.warning("%s: asked to slow down; resting %.0f s", self.name, self.rest)

    def get(self, url, timeout=20):
        self.wait()
        req = urllib.request.Request(url, headers={"User-Agent": UA, **self.headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                raise Blocked(str(e.code)) from e
            if e.code == 403:
                body = e.read(4096).decode("utf-8", "replace")
                low = body.lower()
                if any(m in low for m in ("just a moment", "cf-chl", "captcha", "unauthorized activity", "radware")):
                    raise Blocked("bot check") from e
                if self.api_403_is_block:
                    raise Blocked("403") from e
            raise

    def image_url(self, code):
        raise NotImplementedError

    def fetch(self, code):
        """(OK, bytes) | (MISS, None) | (SLOW, None) | (RETRY, None)."""
        try:
            url = self.image_url(code)
            if not url:
                return MISS, None
            data = self.get(url)
            return (OK, data) if data else (MISS, None)
        except Blocked:
            return SLOW, None
        except urllib.error.HTTPError as e:
            return (MISS, None) if e.code in (400, 403, 404, 410) else (RETRY, None)
        except Exception as e:  # network blip, timeout: another time
            log.debug("%s %s: %s", self.name, code, e)
            return RETRY, None


class Template(Source):
    """A catalogue whose image URL is computed from the barcode alone."""
    template = ""

    def image_url(self, code):
        return self.template.format(code=code)


class Pricez(Template):
    """A price-comparison site's image CDN: retailer catalogue shots, 450-1000 px on white. Best coverage."""
    name = "pricez"
    template = "https://m.pricez.co.il/ProductPictures/{code}.jpg"
    interval, min_interval, workers = 1.0, 0.34, 3


class SuperPharm(Template):
    """Super-Pharm's product image store: strong on toiletries, baby, cosmetics and branded snacks."""
    name = "super_pharm"
    chains = ("7290172900007",)
    template = "https://superpharmstorage.blob.core.windows.net/hybris/products/desktop/medium/{code}.jpg"
    interval, min_interval, workers = 0.5, 0.2, 3


class Gs1Cdn(Template):
    """GS1 Israel brand-owner studio photos (600 px on white) on the stor.ai image CDN, by barcode alone."""
    name = "gs1_cdn"
    template = "https://d226b0iufwcjmj.cloudfront.net/gs1-products/1062/large/{code}.jpg"
    interval, min_interval, workers = 0.5, 0.2, 2


class TivTaam(Source):
    """Tiv Taam's online shop (stor.ai platform): one product lookup per barcode. Its firewall blocks eager
    clients, so this keeps robots.txt's 4-second crawl delay, never faster, and rests at the first 403."""
    name = "tiv_taam"
    chains = ("7290873255550",)
    interval = 4.0
    api_403_is_block = True
    headers = {"Accept": "application/json"}

    def image_url(self, code):
        f = urllib.parse.quote(json.dumps({"must": {"term": {"barcode": code}}}, separators=(",", ":")))
        url = f"https://www.tivtaam.co.il/v2/retailers/1062/products?appId=4&from=0&size=1&filters={f}"
        for p in json.loads(self.get(url).decode("utf-8", "replace") or "{}").get("products") or []:
            u = (p.get("image") or {}).get("url")
            if u:
                return u.replace("{{size}}", "large").replace("{{extension||'jpg'}}", "jpg")
        return None


class RamiLevy(Template):
    name = "rami_levy"
    chains = ("7290058140886",)
    template = "https://img.rami-levy.co.il/product/{code}/medium.jpg"
    interval = 2.5  # behind Cloudflare, which rate-limits bursts


class Chp(Source):
    """chp.co.il's comparison results carry a small (140 px) catalogue shot inline: a fallback."""
    name = "chp"
    interval, min_interval, workers = 1.5, 1.0, 2
    fallback = True
    headers = {"X-Requested-With": "XMLHttpRequest"}
    URL = ("https://chp.co.il/main_page/compare_results?shopping_address=&shopping_address_street_id=0"
           "&shopping_address_city_id=0&product_name_or_barcode={code}&product_barcode={code}&from=0&num_results=1")
    IMG = re.compile(r'<img data-uri="data:image/(\w+);base64,([^"]+)" class="imageuri"')
    PLACEHOLDER = "5701134c5e4714ed50aba5dec33f4f3d"  # the camera icon

    def fetch(self, code):
        try:
            html = self.get(self.URL.format(code=code)).decode("utf-8", "replace")
        except Blocked:
            return SLOW, None
        except urllib.error.HTTPError as e:
            return (MISS, None) if e.code in (400, 403, 404, 410) else (RETRY, None)
        except Exception:
            return RETRY, None
        m = self.IMG.search(html)
        if not m:
            return MISS, None
        data = base64.b64decode(m.group(2))
        return (MISS, None) if hashlib.md5(data).hexdigest() == self.PLACEHOLDER else (OK, data)


class Shufersal(Source):
    """Shufersal's online shop: its search returns the product's catalogue image. Its robots.txt asks for one
    request per 10 seconds, which this keeps."""
    name = "shufersal"
    chains = ("7290027600007",)
    interval = 10.0
    headers = {"Accept": "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest"}

    @staticmethod
    def product_code(code):
        bc = code.lstrip("0")
        return str(int(bc[6:])) if len(bc) == 13 and bc.startswith("729000") else bc

    def image_url(self, code):
        pc = self.product_code(code)
        url = f"https://www.shufersal.co.il/online/he/search/results?q={urllib.parse.quote(pc)}%3Arelevance&limit=5"
        res = json.loads(self.get(url).decode("utf-8", "replace") or "{}").get("results") or []
        for r in res:
            if r.get("code") == f"P_{pc}" or str(r.get("code", "")).startswith(f"P_{pc}_"):
                for img in r.get("images") or []:
                    u = img.get("url") or ""
                    if img.get("format") == "zoom" and "res.cloudinary.com" in u and "/product_images/default/" not in u:
                        return u.replace("f_auto,q_auto", "f_png,q_auto,w_480")
        return None


# the order matters only on demand (the first that answers wins); the hourly job runs them all at once
SOURCES = [Pricez, Gs1Cdn, SuperPharm, RamiLevy, TivTaam, Shufersal, Chp]


def sources():
    return [cls() for cls in SOURCES]


# ---------------------------------------------------------------- what to fetch
def load_missing():
    """{barcode: {source: when it said it has no photo}}, entries younger than MISS_DAYS (old {barcode: ts} files are
    Rami Levy's)."""
    try:
        with open(MISSING) as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    keep, now = {}, time.time()
    for code, v in raw.items():
        v = v if isinstance(v, dict) else {"rami_levy": v}
        v = {s: t for s, t in v.items() if now - t < MISS_DAYS * 86400}
        if v:
            keep[code] = v
    return keep


def save_missing(missing):
    os.makedirs(PHOTO_DIR, exist_ok=True)
    tmp = MISSING + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(missing, fh)
    os.replace(tmp, MISSING)


def queue(con):
    """Barcodes without a photo, best first: products in your carts, then products on a deal (they fill the home
    page), then the most widely sold."""
    rows = con.execute("""SELECT code, store_count FROM products WHERE scope='global' AND store_count > 0
                          AND length(code) >= 8""").fetchall()
    in_cart = {r[0] for r in con.execute("SELECT DISTINCT key FROM cart_items")}
    on_deal = set()
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='deals'").fetchone():
        on_deal = {r[0] for r in con.execute("SELECT DISTINCT key FROM deals")}
    rows.sort(key=lambda r: (r[0] not in in_cart, r[0] not in on_deal, -(r[1] or 0)))
    return [r[0] for r in rows if not os.path.exists(photo_path(r[0]))]


def sold_at(con, chains):
    """Barcodes these chains name on their shelves (they sell them): their catalogue likely has the photo."""
    if not chains:
        return set()
    ph = ",".join("?" * len(chains))
    return {r[0] for r in con.execute(f"SELECT DISTINCT key FROM item_names WHERE chain_id IN ({ph})", list(chains))}


# ---------------------------------------------------------------- the fetcher
def run(con, minutes=50, srcs=None):
    """Fetch photos with every source at once for up to `minutes` (the timer starts a run hourly)."""
    srcs = srcs if srcs is not None else sources()
    missing = load_missing()
    todo = queue(con)
    deadline = time.time() + minutes * 60
    lock = threading.Lock()
    done, busy = set(), set()
    stats = {s.name: {"saved": 0, "miss": 0, "slow": 0, "retry": 0} for s in srcs}
    dirty = [0]
    sold = {s.name: sold_at(con, s.chains) for s in srcs}  # read here: sqlite connections stay in this thread
    general = [s.name for s in srcs if not s.chains and not s.fallback]  # sources that try every product
    log.info("%d products without a photo; sources: %s", len(todo), ", ".join(s.name for s in srcs))

    orders = {}
    for s in srcs:
        mine = sold[s.name]
        orders[s.name] = [c for c in todo if c in mine] + [c for c in todo if c not in mine]

    def work(src):
        """Go through this source's list; products another worker holds, or (for a fallback) that a general source
        hasn't tried yet, come round again until the run ends."""
        pending, errors = orders[src.name], 0
        while pending and time.time() < deadline:
            later, fetched = [], False
            for code in pending:
                if time.time() > deadline:
                    return
                with lock:
                    tried = missing.get(code, ())
                    if code in done or src.name in tried:
                        continue
                    if code in busy or (src.fallback and not all(g in tried for g in general)):
                        later.append(code)
                        continue
                    busy.add(code)
                fetched = True
                try:
                    res, data = src.fetch(code)
                    saved = res == OK and save_photo(code, data, src.white)
                except Exception:  # never let one product stop this source's worker
                    log.exception("%s %s", src.name, code)
                    res, saved = RETRY, False
                finally:
                    with lock:
                        busy.discard(code)  # only now: another worker must not fetch it while it's being saved
                if res in (OK, MISS):
                    src.went_well()
                if saved:
                    errors = 0
                    with lock:
                        done.add(code)
                        stats[src.name]["saved"] += 1
                elif res in (OK, MISS):
                    with lock:
                        missing.setdefault(code, {})[src.name] = int(time.time())
                        stats[src.name]["miss"] += 1
                        dirty[0] += 1
                        if dirty[0] >= 50:
                            save_missing(missing)
                            dirty[0] = 0
                elif res == SLOW:
                    stats[src.name]["slow"] += 1
                    src.slow_down()
                else:
                    stats[src.name]["retry"] += 1
                    errors += 1
                    if errors >= 20:  # the site seems down: rest instead of hammering it
                        src.slow_down()
                        errors = 0
            pending = later
            if not fetched:
                time.sleep(2)  # waiting on other sources

    threads = [threading.Thread(target=work, args=(s,), name=f"photos-{s.name}-{i}", daemon=True)
               for s in srcs for i in range(s.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(max(0.0, deadline - time.time()) + 30)
    with lock:
        save_missing(missing)
    for name, st in stats.items():
        log.info("%s: saved %d, not there %d, slowed down %d, errors %d", name, st["saved"], st["miss"], st["slow"],
                 st["retry"])
    return stats


# ---------------------------------------------------------------- on demand (inside the web server)
_wanted = queue_mod.Queue()
_asked = set()
_thread = None
_lock = threading.Lock()
_srcs = None


def want(codes):
    """Fetch these photos soon, in a background thread: products you add to a cart or open shouldn't wait for the
    hourly job. Already fetched, known missing everywhere and already asked are skipped."""
    global _thread
    with _lock:
        for code in codes:
            if not code or not code.isdigit() or len(code) < 8 or code in _asked or os.path.exists(photo_path(code)):
                continue
            _asked.add(code)
            _wanted.put(code)
        if not _wanted.empty() and (_thread is None or not _thread.is_alive()):
            _thread = threading.Thread(target=_work, name="photos-on-demand", daemon=True)
            _thread.start()


def _work():
    global _srcs
    _srcs = _srcs or sources()
    while True:
        try:
            code = _wanted.get(timeout=60)
        except queue_mod.Empty:
            return
        if os.path.exists(photo_path(code)):
            continue
        tried = load_missing().get(code, {})
        for src in sorted(_srcs, key=lambda s: s.resting()):
            if src.name in tried or src.resting():
                continue
            res, data = src.fetch(code)
            if res == OK and save_photo(code, data, src.white):
                break
            if res == SLOW:
                src.slow_down()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=50, help="how long this run may fetch (default 50)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(dbmod.connect(), a.minutes)


if __name__ == "__main__":
    main()
