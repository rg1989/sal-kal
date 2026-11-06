"""Price history from before Sal-Kal started recording it, from the chains' own past price files.

Source: OpenIsraeliSupermarkets (Sefi Erlich) publishes every chain's files, parsed to CSV, as one Kaggle version per
day (erlichsefi/israeli-supermarkets-2024, from 2024-09-22; every chain from about 2024-10-22). The files are the
chains' own: checked on 2026-11-06 against Sal-Kal's downloads of the same files (8 chains, ~4M prices, plus 6 older
files from 2024-12 and 2026-01), every price and name was the same. License CC BY-NC-SA 3.0 IGO (credit, not for
profit, share alike): pages that show history from before the live history (settings history_live_since) say where it
comes from (source()). No Kaggle account is needed.

Three steps, each resumable, all files under --work (default <data>/backfill):
  fetch  read each day's PriceFull CSVs out of that version's zip with range requests (~0.5-1 GB a day, ~450 GB for
         every day; ~5 MB/s from here) and keep, per branch and day, its last file: barcode prices and the names the
         chain used (work/parts/v<N>.gz + v<N>.json, ~100 MB a day).
  build  replay the days in order in work/stage.db with the live rules (reads the live DB, never writes it):
         a branch counts on a day when its newest file is under trust.FRESH_HOURS old; a chain's name for a barcode
         counts for trust.NAMES_DAYS and must agree with the other chains' (names.odd_names, as build_name_flags);
         prices far from the product's typical price that day are left out (indexer.judge_prices, as
         build_price_flags). History = each chain's cheapest and dearest counted price, a row on days it changes and
         an end row (NULL) when it stops being counted (indexer.record_history). Branch price changes = a branch's
         price for a barcode moving from one file to the next, under board.collect's rules.
         When a chain has no current file at all (a day missing from the archive, or a chain the archive lost for
         a while) for up to BRIDGE_DAYS days, its series are held, as the Price Board takes a change when the old
         price is up to RAW_GAP_DAYS old; a longer gap ends them.
  apply  write the result into the live DB under the indexer's lock: price_history rows only before the day the live
         history began, price_moves rows only before the day the live board began, never a row the indexer wrote.
         Running it again replaces what the last apply wrote; `remove` takes it all out again.

Checked on real data (2026-11-03..05, 6 chains): where both have a barcode's price on 2026-11-05, the cheapest and
dearest per chain are the same as the live history's for 98-100% of pairs (the rest: changes later that day, which
the live history also takes from the chains' delta files).

Run as the sal user (the work folder needs ~70 GB for every day):
  sudo -u sal env BASKET_DB=/srv/sal/data/basket.db /srv/sal/venv/bin/python -m app.backfill fetch
  ... -m app.backfill build; ... -m app.backfill apply; ... -m app.backfill status
"""
import argparse
import contextlib
import datetime
import gzip
import io
import json
import logging
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ProcessPoolExecutor

from . import db as dbmod
from . import indexer, names as namesmod, trust

log = logging.getLogger("backfill")

DATASET = "erlichsefi/israeli-supermarkets-2024"
API = "https://www.kaggle.com/api/v1/datasets"
FIRST_VERSION = 15     # 2024-10-22: the first version with every chain
SOURCE = {"by": "Sefi Erlich, OpenIsraeliSupermarkets",
          "url": "https://www.kaggle.com/datasets/" + DATASET,
          "license": "CC BY-NC-SA 3.0 IGO",
          "license_url": "https://creativecommons.org/licenses/by-nc-sa/3.0/igo/"}
RAW_GAP_DAYS = 4       # as app.board: a change is dated only when the old price is at most this old
BRIDGE_DAYS = RAW_GAP_DAYS  # a chain with no current file for this long keeps its series; longer ends them
SAMPLE_GAP = 9         # weekly samples further apart than this (a gap in the archive) end every series in between
STALE_DAYS = 3         # a file dated this long before its version was published is an old one still on the portal
UA = "sal-kal-backfill/1 (+https://sal-kal.com)"
PRICE_MOVES = """CREATE TABLE IF NOT EXISTS price_moves(key TEXT, chain_id TEXT, day INTEGER, was REAL, now REAL,
  stores INTEGER, ts INTEGER, PRIMARY KEY(key, chain_id, day, was, now)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS price_moves_day ON price_moves(day);"""


def work_dir(path=None):
    return path or os.environ.get("BACKFILL_DIR") or os.path.join(os.path.dirname(os.path.abspath(dbmod.DB_PATH)),
                                                                  "backfill")


# ---------------------------------------------------------------- days and times
def _date(day):
    return datetime.date(day // 10000, day // 100 % 100, day % 100)


def next_day(day, n=1):
    return int((_date(day) + datetime.timedelta(days=n)).strftime("%Y%m%d"))


def _day_end(day):
    return time.mktime(time.strptime(f"{day}2359", "%Y%m%d%H%M"))


def _epoch(ts):
    try:
        return time.mktime(time.strptime(str(ts), "%Y%m%d%H%M"))
    except (ValueError, OverflowError):
        return 0


# ---------------------------------------------------------------- 1. reading one Kaggle price CSV
def read_csv(text):
    """Rows of a Kaggle price CSV, one source file at a time: (file_name, [(code, price, name, weighted)]).
    The publisher writes a cell equal to the one above it as an empty cell, starting again in each file (the first
    row of a file is whole); since 2026-05 a really empty value is written as ''. Before that, an empty cell could be
    either, but price files always carry a code and a price, so the repeat is the only reading for those."""
    import csv
    csv.field_size_limit(1 << 30)
    r = csv.reader(text)
    try:
        head = [h.strip().lower() for h in next(r)]
    except StopIteration:
        return
    ix = {h: i for i, h in enumerate(head)}
    need = ("file_name", "itemcode", "itemprice")
    if any(n not in ix for n in need):  # e.g. a chain with no files that day: only a header, and not this one
        log.warning("skipped a CSV without %s: %s", "/".join(n for n in need if n not in ix), head[:8])
        return
    i_fn, i_code, i_price = ix["file_name"], ix["itemcode"], ix["itemprice"]
    i_name = ix.get("itemname", ix.get("itemnm"))
    i_w = ix.get("bisweighted", ix.get("blsweighted"))
    width = len(head)
    cur, rows, prev = None, [], None
    for row in r:
        if len(row) < width:
            row = row + [""] * (width - len(row))
        fn = row[i_fn]
        if fn and fn != "''" and fn != cur:
            if cur is not None:
                yield cur, rows
            cur, rows, prev = fn, [], [""] * width
        if cur is None:
            continue
        vals = [prev[i] if c == "" else ("" if c == "''" else c) for i, c in enumerate(row[:width])]
        prev = vals
        name = vals[i_name].strip() if i_name is not None else ""
        if name.startswith("{"):  # a value with a line break was written as {} (parsers issue #67): no name
            name = ""
        w = (vals[i_w].strip() if i_w is not None else "") in ("1", "true", "True")
        rows.append((vals[i_code].strip(), vals[i_price].strip(), name, w))
    if cur is not None:
        yield cur, rows


_KEYS = {}  # (item code, weighted) -> barcode key, or None for codes that aren't one


def reduce_file(file_name, rows):
    """One source file -> (chain, store, ts, {barcode key: price}, {key: name}) or None when it isn't a full price
    file. Only manufacturer barcodes (indexer.comparable): chains' own codes are never compared or charted here."""
    m = indexer.parse_filename(file_name)
    if not m or m["kind"] != "pricefull" or not m["ts"]:
        return None
    chain = m["chain"]
    prices, names = {}, {}
    if len(_KEYS) > 2_000_000:
        _KEYS.clear()
    for code, price, name, weighted in rows:
        key = _KEYS.get((code, weighted), 0)
        if key == 0:  # the barcode rules are the same for every chain: worked out once per code
            k, scope = indexer.product_key(chain, code, weighted)
            key = _KEYS[(code, weighted)] = k if scope == "global" and indexer.comparable(k) else None
        if key is None:
            continue
        try:
            p = round(float(price), 2)
        except ValueError:
            continue
        if p <= 0:
            continue
        prices[key] = p
        if name:
            names[key] = name
    return chain, m["store"], m["ts"], prices, names


# ---------------------------------------------------------------- 2. fetch: Kaggle versions -> parts
def _get(url, timeout=120):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout)


def current_version():
    with _get(f"{API}/view/{DATASET}", 60) as r:
        return int(json.load(r)["currentVersionNumber"])


def version_files(v):
    """[(path, bytes, created)] of one version's PriceFull CSVs."""
    out, tok = [], ""
    while True:
        q = {"pageSize": 200, "datasetVersionNumber": v}
        if tok:
            q["pageToken"] = tok
        with _get(f"{API}/list/{DATASET}?" + urllib.parse.urlencode(q), 60) as r:
            d = json.load(r)
        files = d.get("datasetFiles") or []
        out += [(f["name"], f.get("totalBytes") or 0, f.get("creationDate") or "") for f in files
                if "price_full_file_" in f["name"] and f["name"].endswith(".csv")]
        tok = d.get("nextPageToken")
        if not tok or not files:
            return out


class RangeFile(io.RawIOBase):
    """A remote file read with HTTP range requests: a version's whole-dataset zip (2-3 GB, every file of the day) is
    opened where it lies and only its PriceFull members are read, compressed (~0.5 GB a day instead of 2-5 GB of
    plain CSV). Reading on from where the last read stopped keeps one request open."""

    def __init__(self, url, size):
        self.url, self.size, self.pos, self._resp, self._at = url, size, 0, None, None

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def readinto(self, b):
        if self.pos >= self.size:
            return 0
        for attempt in range(4):
            try:
                if self._resp is None or self._at != self.pos:
                    if self._resp is not None:
                        self._resp.close()
                    req = urllib.request.Request(self.url, headers={"User-Agent": UA,
                                                                    "Range": f"bytes={self.pos}-{self.size - 1}"})
                    self._resp, self._at = urllib.request.urlopen(req, timeout=120), self.pos
                    if self._resp.status != 206:
                        raise OSError(f"no range support (HTTP {self._resp.status})")
                n = self._resp.readinto(b)
                if not n and self.pos < self.size:
                    raise OSError("connection closed early")
                self.pos += n
                self._at = self.pos
                return n
            except (OSError, urllib.error.URLError) as e:
                if self._resp is not None:
                    self._resp.close()
                self._resp = None
                if attempt == 3:
                    raise
                log.warning("range read at %d: %s (try %d)", self.pos, e, attempt + 1)
                time.sleep(5 * (attempt + 1))

    def close(self):
        if self._resp is not None:
            self._resp.close()
            self._resp = None
        super().close()


def bundle(v):
    """The version's zip: a ZipFile over range requests (the signed address it redirects to lasts 3 days)."""
    with _get(f"{API}/download/{DATASET}?datasetVersionNumber={v}", 60) as r:
        url = r.geturl()
        size = int(r.headers["Content-Length"])
    return zipfile.ZipFile(io.BufferedReader(RangeFile(url, size), 4 << 20))


@contextlib.contextmanager
def open_text(v, path, spool):
    """One CSV of a version as text, streamed (gzip or zip answers are unpacked; a zip goes through `spool`)."""
    url = f"{API}/download/{DATASET}/{urllib.parse.quote(path, safe='')}?datasetVersionNumber={v}"
    with _get(url, 300) as resp:
        raw = io.BufferedReader(resp, 1 << 20)
        head = raw.peek(4)[:4]
        if head[:2] == b"\x1f\x8b":
            yield io.TextIOWrapper(gzip.GzipFile(fileobj=raw), encoding="utf-8", newline="")
        elif head == b"PK\x03\x04":
            with open(spool, "wb") as f:
                while True:
                    b = raw.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
            with zipfile.ZipFile(spool) as z, z.open(z.namelist()[0]) as member:
                yield io.TextIOWrapper(member, encoding="utf-8", newline="")
        else:
            yield io.TextIOWrapper(raw, encoding="utf-8", newline="")


class PartWriter:
    """Collects a version's files and writes, per (chain, branch, day), only its last full file."""

    def __init__(self, tmp, min_day=0):
        self.tmp = open(tmp, "w+", encoding="utf-8", newline="")
        self.min_day = min_day  # older files are a closed branch's last file, still on the portal: not that day's
        self.blocks = {}  # (chain, store, day) -> (ts, offset, length)
        self.names = {}   # (chain, key, name) -> newest ts
        self.files = 0

    def add(self, red):
        chain, store, ts, prices, names = red
        self.files += 1
        if ts // 10000 < self.min_day:
            return
        k = (chain, store, ts // 10000)
        if k in self.blocks and self.blocks[k][0] >= ts:
            pass
        else:
            text = "".join(f"{key}\t{p}\n" for key, p in prices.items())
            off = self.tmp.seek(0, 2)
            self.tmp.write(text)
            self.blocks[k] = (ts, off, len(text))
        for key, name in names.items():
            nk = (chain, key, name)
            if self.names.get(nk, 0) < ts:
                self.names[nk] = ts

    def write(self, path):
        """work/parts/v<N>.gz: 'F chain store ts' then 'key price' lines per branch-day, then 'N chain key ts name'."""
        tmp = path + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as out:
            for (chain, store, _day), (ts, off, ln) in sorted(self.blocks.items(), key=lambda x: (x[1][0], x[0])):
                self.tmp.seek(off)
                out.write(f"F\t{chain}\t{store}\t{ts}\n")
                out.write(self.tmp.read(ln))
            for (chain, key, name), ts in self.names.items():
                out.write(f"N\t{chain}\t{key}\t{ts}\t{name.replace(chr(9), ' ').replace(chr(10), ' ')}\n")
        os.replace(tmp, path)
        return [[c, s, ts] for (c, s, _d), (ts, _o, _l) in sorted(self.blocks.items(), key=lambda x: x[1][0])]

    def close(self):
        self.tmp.close()


class BundleOpener:
    """Opens a version's CSVs from inside its zip (bundle()), each file falling back to a plain download."""

    def __init__(self, v):
        self.v, self.z, self.read = v, None, 0
        try:
            self.z = bundle(v)
        except (urllib.error.URLError, OSError, KeyError, ValueError, zipfile.BadZipFile) as e:
            log.warning("v%d: no zip (%s), downloading each file", v, e)

    @contextlib.contextmanager
    def __call__(self, v, path, spool):
        if self.z is not None and path in self.z.NameToInfo:
            self.read += self.z.getinfo(path).compress_size
            with self.z.open(path) as member:
                yield io.TextIOWrapper(member, encoding="utf-8", newline="")
        else:
            with open_text(v, path, spool) as text:
                yield text

    def close(self):
        if self.z is not None:
            self.z.close()


def fetch_version(v, parts, files=None, opener=None, sample=False):
    """Download one version's PriceFull CSVs into parts/v<N>.gz and its index parts/v<N>.json. Returns the index."""
    os.makedirs(parts, exist_ok=True)
    meta_path = os.path.join(parts, f"v{v}.json")
    if os.path.exists(meta_path):  # fetched meanwhile (by another run, or copied in from another machine)
        with open(meta_path) as f:
            return json.load(f)
    t0 = time.time()
    if files is None:
        try:
            files = version_files(v)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            files = []  # no such version: an empty part, so the days after it aren't held up
    created = min((f[2] for f in files), default="")[:10]
    fd, tmp = tempfile.mkstemp(dir=parts, prefix=f".v{v}.", suffix=".txt")
    os.close(fd)
    pw = PartWriter(tmp, next_day(int(created.replace("-", "")), -STALE_DAYS) if created else 0)
    got, rows = 0, 0
    own = opener is None and files
    if own:
        opener = BundleOpener(v)
    try:
        for path, size, _ in files:
            for attempt in range(3):
                mark = (pw.tmp.seek(0, 2), dict(pw.blocks), dict(pw.names), pw.files)
                try:
                    with opener(v, path, tmp + ".zip") as text:
                        for fn, rs in read_csv(text):
                            red = reduce_file(fn, rs)
                            if red:
                                rows += len(red[3])
                                pw.add(red)
                    got += size
                    break
                except (urllib.error.URLError, OSError, EOFError, zipfile.BadZipFile) as e:
                    pw.tmp.truncate(mark[0])
                    pw.blocks, pw.names, pw.files = mark[1], mark[2], mark[3]
                    if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                        log.warning("v%d %s: not found", v, path)
                        break
                    log.warning("v%d %s: %s (try %d)", v, path, e, attempt + 1)
                    time.sleep(10 * (attempt + 1))
            else:
                raise RuntimeError(f"v{v} {path}: download failed 3 times")
        blocks = pw.write(os.path.join(parts, f"v{v}.gz"))
    finally:
        pw.close()
        if own:
            opener.close()
        for p in (tmp, tmp + ".zip"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)
    meta = {"version": v, "created": created, "sample": sample, "csv_files": len(files), "bytes": got,
            "downloaded": opener.read if own else got, "source_files": pw.files,
            "blocks": blocks, "days": sorted({ts // 10000 for _, _, ts in blocks}), "rows": rows,
            "seconds": int(time.time() - t0)}
    with open(meta_path + ".tmp", "w") as f:
        json.dump(meta, f)
    os.replace(meta_path + ".tmp", meta_path)
    log.info("v%d (%s): %d CSVs, %.1f GB (%.2f GB downloaded), %d branch-days, %d prices in %ds", v, created,
             len(files), got / 1e9, meta["downloaded"] / 1e9, len(blocks), rows, meta["seconds"])
    return meta


def version_day(v, cache):
    """The day a version was published (YYYYMMDD, from its files' creation time), None when there is no such version."""
    k = str(v)
    if k not in cache:
        try:
            files = version_files(v)
            cache[k] = int(min(f[2] for f in files)[:10].replace("-", "")) if files else None
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            cache[k] = None
    return cache[k]


def has_zip(v):
    try:
        with _get(f"{API}/download/{DATASET}?datasetVersionNumber={v}", 60):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def plan_weekly(work, start, weeks):
    """One version a week: published on `start`, then 7 days earlier each time (the nearest day with a zip when that
    day has none). Saved oldest first in work/plan.json: [[day, version], ...]."""
    path = os.path.join(work, "versions.json")
    cache = json.load(open(path)) if os.path.exists(path) else {}
    v_ref = current_version()
    d_ref = version_day(v_ref, cache)
    out = []
    for k in range(weeks):
        target = next_day(start, -7 * k)
        v = v_ref - (_date(d_ref) - _date(target)).days  # about one version a day
        found = None
        for _ in range(40):  # walk to the version published that day (some days have none, some two)
            d = version_day(v, cache)
            if d is None:
                v -= 1
                continue
            gap = (_date(d) - _date(target)).days
            if gap == 0:
                found = v
                break
            if abs(gap) == 1 and found is None and d < target:
                found = v
            v -= gap if abs(gap) > 1 else (1 if gap > 0 else -1)
            if found and gap < 0:
                break
        for cand in [found, found and found - 1, found and found + 1]:
            if cand and version_day(cand, cache) and has_zip(cand):
                out.append([version_day(cand, cache), cand])
                break
        else:
            log.warning("no version with a zip near %d", target)
        with open(path, "w") as f:
            json.dump(cache, f)
    out = sorted({tuple(x) for x in out})
    with open(os.path.join(work, "plan.json"), "w") as f:
        json.dump({"samples": [list(x) for x in out]}, f)
    log.info("weekly plan: %d samples, %s .. %s", len(out), out[0] if out else None, out[-1] if out else None)
    return out


def fetch_weekly(work, start, weeks, jobs=2, newest_first=False):
    """fetch for the weekly plan, oldest first (so build can follow it), or newest first (a second machine helping
    from the other end: a version already there when its turn comes is skipped)."""
    parts = os.path.join(work, "parts")
    os.makedirs(parts, exist_ok=True)
    plan = plan_weekly(work, start, weeks)
    todo = [v for _, v in plan if not os.path.exists(os.path.join(parts, f"v{v}.json"))]
    if newest_first:
        todo.reverse()
    with ProcessPoolExecutor(max(1, jobs)) as ex:
        jobs_ = {ex.submit(fetch_version, v, parts, None, None, True): v for v in todo}
        for f, v in jobs_.items():
            try:
                f.result()
            except Exception as e:  # noqa: BLE001
                log.error("v%d: %s", v, e)


def fetch(work, first=FIRST_VERSION, last=None, jobs=2):
    parts = os.path.join(work, "parts")
    os.makedirs(parts, exist_ok=True)
    lock = open(os.path.join(work, "fetch.lock"), "a")
    try:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        pass
    except OSError:
        raise SystemExit("another fetch is running")
    for f in os.listdir(parts):  # a stopped run's half-written versions
        if f.startswith(".v"):
            os.remove(os.path.join(parts, f))
    last = last or current_version()
    todo = [v for v in range(first, last + 1) if not os.path.exists(os.path.join(parts, f"v{v}.json"))]
    log.info("fetch: %d versions to get (%d..%d)", len(todo), first, last)
    with ProcessPoolExecutor(max(1, jobs)) as ex:  # parsing takes a core per ~20 MB/s: one process per version
        jobs = {ex.submit(fetch_version, v, parts): v for v in todo}
        failed = []
        for f, v in jobs.items():
            try:
                f.result()
            except Exception as e:  # noqa: BLE001 - one bad version is retried on the next run, not the end
                log.error("v%d: %s", v, e)
                failed.append(v)
    if failed:
        log.warning("fetch: %d versions failed (run fetch again): %s", len(failed), failed[:20])


# ---------------------------------------------------------------- 3. build: replay the days in stage.db
STAGE = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;
PRAGMA cache_size=-CACHE_KB;
PRAGMA mmap_size=MMAP_BYTES;
-- every branch's newest barcode prices (its last full file so far) and when that file was published
CREATE TABLE IF NOT EXISTS last(key TEXT, chain_id TEXT, store_id INTEGER, price REAL,
  PRIMARY KEY(key, chain_id, store_id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS last_store ON last(chain_id, store_id);
CREATE TABLE IF NOT EXISTS sts(chain_id TEXT, store_id INTEGER, ts INTEGER, PRIMARY KEY(chain_id, store_id));
-- every name a chain gave a barcode, with the newest file it was in (as item_names)
CREATE TABLE IF NOT EXISTS nm(key TEXT, chain_id TEXT, name TEXT, ts INTEGER, PRIMARY KEY(key, chain_id, name))
  WITHOUT ROWID;
-- the open history series (as price_last) and the last day each was counted
CREATE TABLE IF NOT EXISTS hl(key TEXT, chain_id TEXT, mn REAL, mx REAL, seen INTEGER,
  PRIMARY KEY(key, chain_id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS chain_seen(chain_id TEXT PRIMARY KEY, day INTEGER);
-- the result
CREATE TABLE IF NOT EXISTS out_h(key TEXT, chain_id TEXT, day INTEGER, mn REAL, mx REAL, n INTEGER,
  PRIMARY KEY(key, chain_id, day)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS out_m(key TEXT, chain_id TEXT, day INTEGER, was REAL, now REAL, stores INTEGER, ts INTEGER,
  PRIMARY KEY(key, chain_id, day, was, now)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS done(day INTEGER PRIMARY KEY, stores INTEGER, pairs INTEGER, rows INTEGER, moves INTEGER);
-- the days that were weekly samples (history only), not whole days
CREATE TABLE IF NOT EXISTS samples(day INTEGER PRIMARY KEY, version INTEGER);
"""


def stage(work):
    """stage.db; its page cache is BACKFILL_CACHE_MB (default 1500; less on a small server, slower)."""
    mb = int(os.environ.get("BACKFILL_CACHE_MB", "1500"))
    c = sqlite3.connect(os.path.join(work, "stage.db"), timeout=60)
    c.executescript(STAGE.replace("CACHE_KB", str(mb * 1000)).replace("MMAP_BYTES", str(mb * 2_700_000)))
    return c


def metas(work):
    parts = os.path.join(work, "parts")
    out = []
    for f in os.listdir(parts) if os.path.isdir(parts) else []:
        if f.startswith("v") and f.endswith(".json"):
            with open(os.path.join(parts, f)) as fh:
                out.append(json.load(fh))
    return sorted(out, key=lambda m: m["version"])


def ready_until(ms):
    """The last day every fetched version has finished: up to the first version not fetched yet, and not the newest
    day there (the next version can still bring that day's late files)."""
    ms = [m for m in ms if not m.get("sample")]
    if not ms:
        return None
    vs = [m["version"] for m in ms]
    k = 0
    while k + 1 < len(vs) and vs[k + 1] == vs[k] + 1:
        k += 1
    days = [d for m in ms[:k + 1] for d in m["days"]]
    return next_day(max(days), -1) if days else None


def read_part(path, day_end):
    """Blocks and names in a part: yields ('F', chain, store, ts, {key: price}) and ('N', chain, key, ts, name)."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        cur, prices = None, None
        for line in f:
            if line[0] == "F" and line[1] == "\t":
                if cur:
                    yield ("F", *cur, prices)
                _, ch, st, ts = line.rstrip("\n").split("\t")
                cur, prices = (ch, int(st), int(ts)), {}
            elif line[0] == "N" and line[1] == "\t":
                if cur:
                    yield ("F", *cur, prices)
                    cur = None
                _, ch, key, ts, name = line.rstrip("\n").split("\t", 4)
                if int(ts) <= day_end:
                    yield ("N", ch, key, int(ts), name)
            elif cur:
                key, p = line.rstrip("\n").split("\t")
                prices[key] = float(p)
        if cur:
            yield ("F", *cur, prices)


class Rules:
    """What the live DB knows that a past day is judged by: which chain ids are one retailer (one vote), the
    product's card name, and the words too common to tell products apart (all read once, read-only)."""

    def __init__(self, live):
        self.live = live
        self.voter = indexer.retailers(live) if _has(live, "chains") else {}
        gw = getattr(namesmod, "generic_words", None)
        rows = live.execute("SELECT key, name FROM item_names WHERE ts >= ?", (trust.names_cutoff(),)) \
            if _has(live, "item_names") else []
        self.generic = gw(rows) if gw else frozenset()
        self.has_products = _has(live, "products")
        self._card, self._odd = {}, {}

    def card(self, key):
        v = self._card.get(key)
        if v is None:
            r = self.live.execute("SELECT name FROM products WHERE key=?", (key,)).fetchone() \
                if self.has_products else None
            v = self._card[key] = (r[0] if r else "") or ""
            if len(self._card) > 400_000:
                self._card.clear()
        return v

    def odd(self, key, multi):
        """Chains whose names for this barcode disagree with the other chains' (as build_name_flags)."""
        card = self.card(key)
        sig = (card, tuple(sorted((c, tuple(ns)) for c, ns in multi.items())))
        v = self._odd.get(sig)
        if v is None:
            votes = {c: self.voter.get(c, c) for c in multi}
            v = self._odd[sig] = frozenset(indexer._odd(multi, card, self.generic, votes)[0])
            if len(self._odd) > 500_000:
                self._odd.clear()
        return v


def _has(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None


def _by_key(cur):
    """Group rows (key, ...) of a cursor ordered by key."""
    key, rows = None, []
    for r in cur:
        if r[0] != key:
            if rows:
                yield key, rows
            key, rows = r[0], []
        rows.append(r)
    if rows:
        yield key, rows


def load_block(s, chain, store, ts, prices):
    """A branch's new full file replaces its prices. Returns its price changes [(key, was, now, was_ts)]: only
    from the file before it, and only when that one is at most RAW_GAP_DAYS older (as board.collect)."""
    old = s.execute("SELECT ts FROM sts WHERE chain_id=? AND store_id=?", (chain, store)).fetchone()
    if old and old[0] >= ts:
        return []
    s.execute("DROP TABLE IF EXISTS temp.inc")
    s.execute("CREATE TEMP TABLE inc(key TEXT PRIMARY KEY, price REAL) WITHOUT ROWID")
    s.executemany("INSERT INTO inc VALUES(?,?)", prices.items())
    moves = []
    if old and _epoch(ts) - _epoch(old[0]) <= RAW_GAP_DAYS * 86400:
        moves = [(k, was, now, old[0]) for k, was, now in s.execute(
            """SELECT i.key, l.price, i.price FROM inc i JOIN last l ON l.key=i.key AND l.chain_id=? AND l.store_id=?
               WHERE abs(l.price - i.price) >= 0.005""", (chain, store))]
    s.execute("""DELETE FROM last WHERE chain_id=? AND store_id=? AND NOT EXISTS
                 (SELECT 1 FROM inc WHERE inc.key=last.key)""", (chain, store))
    s.execute("""INSERT INTO last(key, chain_id, store_id, price) SELECT key, ?, ?, price FROM inc WHERE true
                 ON CONFLICT(key, chain_id, store_id) DO UPDATE SET price=excluded.price
                 WHERE abs(last.price - excluded.price) >= 0.0005""", (chain, store))
    s.execute("INSERT INTO sts VALUES(?,?,?) ON CONFLICT(chain_id, store_id) DO UPDATE SET ts=excluded.ts",
              (chain, store, ts))
    return moves


def build_day(s, rules, day, blocks, names, at=None, moves_on=True, bridge_from=None):
    """One day: load the branches' files, then judge every barcode with the live rules and write history rows and
    branch price changes. blocks: [(chain, store, ts, prices)] (each branch's last file that day); names:
    [(chain, key, ts, name)]."""
    raw = {}  # key -> [(chain, store, was, now, ts)]
    for chain, store, ts, prices in sorted(blocks, key=lambda b: b[2]):
        for key, was, now, _was_ts in load_block(s, chain, store, ts, prices):
            if moves_on:  # a weekly sample can't date a change: no price changes from it
                raw.setdefault(key, []).append((chain, store, was, now, ts))
    s.executemany("""INSERT INTO nm VALUES(?,?,?,?) ON CONFLICT(key, chain_id, name) DO UPDATE SET ts=excluded.ts
                     WHERE excluded.ts > nm.ts""", [(k, c, n, ts) for c, k, ts, n in names])
    end = at or _day_end(day)  # a weekly sample is judged at its version's newest file, as the indexer would then
    cutoff, ncut = trust.fresh_cutoff(end), trust.names_cutoff(end)
    fresh = {(c, st) for c, st, ts in s.execute("SELECT chain_id, store_id, ts FROM sts") if ts >= cutoff}
    observed = {c for c, _ in fresh}
    s.executemany("INSERT INTO chain_seen VALUES(?,?) ON CONFLICT(chain_id) DO UPDATE SET day=excluded.day",
                  [(c, day) for c in observed])
    lo, hi = trust.OUTLIER
    hist, seen, moves = [], [], {}
    hl = s.cursor()
    names_cur = _by_key(s.execute("SELECT key, chain_id, name, ts FROM nm WHERE ts >= ? ORDER BY key", (ncut,)))
    nk, nrows = next(names_cur, (None, None))
    for key, rows in _by_key(s.execute("SELECT key, chain_id, store_id, price FROM last ORDER BY key")):
        while nk is not None and nk < key:
            nk, nrows = next(names_cur, (None, None))
        rows = [r for r in rows if (r[1], r[2]) in fresh]
        if not rows:
            continue
        multi = {}
        if nk == key:
            for _, ch, name, ts in sorted(nrows, key=lambda r: -r[3]):
                multi.setdefault(ch, []).append(name)
        odd = rules.odd(key, multi) if len(multi) >= 2 else frozenset()
        use = [r for r in rows if r[1] in multi and r[1] not in odd]  # named by its chain, and the same product
        flags, typical = set(), None
        if use:
            ps = [r[3] for r in use]
            if max(ps) > 3 * min(ps) or key in raw:  # within 3x of each other nothing can be an outlier
                typical, fl = indexer.judge_prices([(rules.voter.get(r[1], r[1]), r[1], r[2], r[3]) for r in use])
                flags = {(ch, st) for ch, st, _ in fl}
        per = {}
        for _, ch, st, p in use:
            if (ch, st) in flags:
                continue
            v = per.get(ch)
            per[ch] = [p, p, 1] if v is None else [min(v[0], p), max(v[1], p), v[2] + 1]
        if per:
            last = {r[0]: (r[1], r[2]) for r in hl.execute(
                "SELECT chain_id, mn, mx FROM hl WHERE key=?", (key,))}
            for ch, (mn, mx, n) in per.items():
                was = last.get(ch)
                if was is None or abs(was[0] - mn) >= 0.005 or abs(was[1] - mx) >= 0.005:
                    hist.append((key, ch, day, mn, mx, n))
                seen.append((key, ch, mn, mx, day))
        for ch, st, was, now, ts in raw.get(key, ()):  # board.collect's rules for a branch's price change
            if ch in odd or (ch, st) in flags:
                continue
            if typical and not (lo * typical <= was <= hi * typical and lo * typical <= now <= hi * typical):
                continue
            g = moves.setdefault((key, ch, day, round(was, 2), round(now, 2)), [0, ts])
            g[0] += 1
            g[1] = min(g[1], ts)
    s.executemany("INSERT OR REPLACE INTO out_h VALUES(?,?,?,?,?,?)", hist)
    s.executemany("""INSERT INTO hl VALUES(?,?,?,?,?) ON CONFLICT(key, chain_id) DO UPDATE SET mn=excluded.mn,
                     mx=excluded.mx, seen=excluded.seen""", seen)
    # series that stopped being counted end today (a NULL row), unless their chain has no current file at all and
    # had one within BRIDGE_DAYS
    # (dated the chain's first day without one)
    last_seen = dict(s.execute("SELECT chain_id, day FROM chain_seen"))
    since = next_day(day, -BRIDGE_DAYS) if bridge_from is None else bridge_from
    held = {c for c, d in last_seen.items() if c not in observed and d >= since}
    ended = [(k, c) for k, c in s.execute("SELECT key, chain_id FROM hl WHERE seen < ?", (day,)) if c not in held]
    s.executemany("INSERT OR REPLACE INTO out_h VALUES(?,?,?,NULL,NULL,0)",
                  [(k, c, day if c in observed or not moves_on else min(day, next_day(last_seen.get(c, day))))
                   for k, c in ended])
    s.executemany("DELETE FROM hl WHERE key=? AND chain_id=?", ended)
    s.executemany("""INSERT INTO out_m VALUES(?,?,?,?,?,?,?) ON CONFLICT(key, chain_id, day, was, now)
                     DO UPDATE SET stores=stores+excluded.stores, ts=min(ts, excluded.ts)""",
                  [(*k, *v) for k, v in moves.items()])
    s.execute("INSERT OR REPLACE INTO done VALUES(?,?,?,?,?)", (day, len(fresh), len(seen), len(hist) + len(ended),
                                                                 len(moves)))
    s.commit()
    return len(fresh), len(hist), len(ended), len(moves)


def build(work, live_path=None, until=None):
    """Replay every fetched day not built yet, in order (a day with nothing fetched still ends or holds series)."""
    s = stage(work)
    live = sqlite3.connect(f"file:{os.path.abspath(live_path or dbmod.DB_PATH)}?mode=ro", uri=True, timeout=60)
    rules = Rules(live)
    ms = metas(work)
    stop = ready_until(ms)
    if until:
        stop = min(stop, until) if stop else until
    if not stop:
        log.info("build: nothing fetched yet")
        return 0
    # the first day is the first version's own: the earlier late files it carries are loaded into that day (a day
    # built from them alone would have a few branches, and the next day would look like a wave of changes)
    daily = [m for m in ms if not m.get("sample")]
    created = [int(m["created"].replace("-", "")) for m in daily if m.get("created")]
    first = min(created) if created else min(d for m in daily for d in m["days"])
    parts = os.path.join(work, "parts")
    n = build_samples(s, rules, work, ms, first)
    if n < 0:  # a weekly sample isn't fetched yet: the days after it wait
        return 0
    done = s.execute("SELECT max(day) FROM done").fetchone()[0]
    day = next_day(done) if done and done >= first else first
    ms = daily
    while day <= stop:
        t0 = time.time()
        on = (lambda d: d <= day) if day == first else (lambda d: d == day)
        with_day = [m for m in ms if any(on(d) for d in m["days"])]
        best = {}  # (chain, store) -> (ts, version): the branch's last file that day across versions
        for m in with_day:
            for ch, st, ts in m["blocks"]:
                if on(ts // 10000) and best.get((ch, st), (0,))[0] < ts:
                    best[(ch, st)] = (ts, m["version"])
        blocks, names = [], []
        end_ts = day * 10000 + 2359
        for m in with_day:
            for rec in read_part(os.path.join(parts, f"v{m['version']}.gz"), end_ts):
                if rec[0] == "F":
                    _, ch, st, ts, prices = rec
                    if best.get((ch, st)) == (ts, m["version"]):
                        blocks.append((ch, st, ts, prices))
                else:
                    names.append(rec[1:])
        fresh, rows, ended, moves = build_day(s, rules, day, blocks, names)
        log.info("%d: %d branch files, %d current branches, %d history rows, %d ended, %d price changes (%.0fs)",
                 day, len(blocks), fresh, rows, ended, moves, time.time() - t0)
        day = next_day(day)
        n += 1
    return n


def build_samples(s, rules, work, ms, first_daily):
    """The weekly samples in work/plan.json before the first daily day, oldest first: each is one version's last
    file per branch, judged at that version's newest file; history rows only. A series not in a sample ends there;
    a gap of more than SAMPLE_GAP days ends every series. Returns the samples built, or -1 when one isn't fetched."""
    path = os.path.join(work, "plan.json")
    if not os.path.exists(path):
        return 0
    plan = [(d, v) for d, v in json.load(open(path))["samples"] if d < first_daily]
    by_v = {m["version"]: m for m in ms}
    done = {r[0] for r in s.execute("SELECT day FROM done")}
    prev, n = None, 0
    for day, v in plan:
        if day in done:
            prev = day
            continue
        m = by_v.get(v)
        if m is None:
            log.info("build: waiting for weekly sample v%d (%d)", v, day)
            return -1
        t0 = time.time()
        if prev and (_date(day) - _date(prev)).days > SAMPLE_GAP:
            # the archive has nothing for weeks (its publishing stopped): every series ends where the next sample
            # would have been, and starts again here, rather than being drawn across the gap
            end = next_day(prev, 7)
            s.execute("INSERT OR REPLACE INTO out_h SELECT key, chain_id, ?, NULL, NULL, 0 FROM hl", (end,))
            s.execute("DELETE FROM hl")
        best = {}
        for ch, st, ts in m["blocks"]:
            if best.get((ch, st), 0) < ts:
                best[(ch, st)] = ts
        newest = max(best.values(), default=day * 10000 + 2359)
        blocks, names = [], []
        for rec in read_part(os.path.join(work, "parts", f"v{v}.gz"), newest):
            if rec[0] == "F":
                if best.get((rec[1], rec[2])) == rec[3]:
                    blocks.append(rec[1:])
            else:
                names.append(rec[1:])
        # a chain missing from a sample ends there (nothing is held between weekly samples)
        fresh, rows, ended, _ = build_day(s, rules, day, blocks, names, at=_epoch(newest), moves_on=False,
                                          bridge_from=next_day(day))
        s.execute("INSERT OR REPLACE INTO samples VALUES(?,?)", (day, v))
        s.commit()
        log.info("%d (weekly, v%d): %d branch files, %d current branches, %d history rows, %d ended (%.0fs)",
                 day, v, len(blocks), fresh, rows, ended, time.time() - t0)
        prev, n = day, n + 1
    return n


# ---------------------------------------------------------------- 4. apply / remove / source
def get_setting(c, key, default=None):
    r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def put_setting(c, key, value):
    c.execute("INSERT INTO settings VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def live_bounds(c, today=None):
    """(history_live_since, moves_live_since): the first day the live indexer recorded each; backfilled rows go only
    before them. Kept in settings the first time, so a later apply finds the same days."""
    today = today or int(time.strftime("%Y%m%d"))
    h = get_setting(c, "history_live_since") or get_setting(c, "history_since") or today
    m = get_setting(c, "moves_live_since") or get_setting(c, "moves_since") or today
    return int(h), int(m)


def _drop(c, h_since, m_since):
    """Take out what an earlier apply wrote: every history row before the live history began, the end rows it put
    on that first live day (the live indexer never ends a series on its first day: nothing came before it), and
    every branch price change before the live board began."""
    a = c.execute("DELETE FROM price_history WHERE day < ? OR (day = ? AND mn IS NULL)", (h_since, h_since)).rowcount
    b = c.execute("DELETE FROM price_moves WHERE day < ?", (m_since,)).rowcount if _has(c, "price_moves") else 0
    return a, b


def apply(work, db_path=None, wait=1800, moves=True):
    path = os.path.abspath(db_path or dbmod.DB_PATH)
    c = dbmod.connect(path)  # settings are in user.db, which connect() attaches as u
    c.row_factory = None
    c.executescript(PRICE_MOVES)
    deadline = time.time() + wait
    while True:
        with indexer.run_lock(path) as ok:
            if ok:
                return _apply(c, work, moves)
        if time.time() > deadline:
            raise SystemExit("the indexer kept its lock: try again later")
        log.info("waiting for the indexer to finish")
        time.sleep(30)


def _apply(c, work, moves=True):
    t0 = time.time()
    h_since, m_since = live_bounds(c)
    c.execute("ATTACH DATABASE ? AS bf", (os.path.join(work, "stage.db"),))
    with c:
        old = _drop(c, h_since, m_since)
        h = c.execute("""INSERT OR IGNORE INTO price_history(key, chain_id, day, mn, mx, n)
                         SELECT key, chain_id, day, mn, mx, n FROM bf.out_h WHERE day < ?""", (h_since,)).rowcount
        # a series still open when the live history began ends that day unless the indexer has a row for it then
        e = c.execute("""INSERT OR IGNORE INTO price_history(key, chain_id, day, mn, mx, n)
                         SELECT o.key, o.chain_id, ?, NULL, NULL, 0 FROM bf.out_h o
                         WHERE o.mn IS NOT NULL AND o.day = (SELECT max(x.day) FROM bf.out_h x
                               WHERE x.key=o.key AND x.chain_id=o.chain_id AND x.day < ?)""",
                      (h_since, h_since)).rowcount
        # branch price changes only once the Price Board credits the source (moves=False keeps them out)
        m = c.execute("""INSERT OR IGNORE INTO price_moves(key, chain_id, day, was, now, stores, ts)
                         SELECT key, chain_id, day, was, now, stores, ts FROM bf.out_m WHERE day < ?""",
                      (m_since,)).rowcount if moves else 0
        hf, ht = c.execute("SELECT min(day), max(day) FROM bf.out_h WHERE day < ?", (h_since,)).fetchone()
        mf, mt = c.execute("SELECT min(day), max(day) FROM bf.out_m WHERE day < ?", (m_since,)).fetchone() \
            if moves else (None, None)
        put_setting(c, "history_live_since", str(h_since))
        put_setting(c, "moves_live_since", str(m_since))
        if hf:
            put_setting(c, "history_since", str(min(hf, h_since)))
        if mf:
            put_setting(c, "moves_since", str(min(mf, m_since)))
        wf, wl = c.execute("SELECT min(day), max(day) FROM bf.samples").fetchone() \
            if c.execute("SELECT 1 FROM bf.sqlite_master WHERE name='samples'").fetchone() else (None, None)
        daily_from = c.execute("SELECT min(day) FROM bf.done WHERE day NOT IN (SELECT day FROM bf.samples)").fetchone()[0] \
            if wf else hf
        put_setting(c, "backfill", json.dumps({**SOURCE, "history": [hf, ht], "moves": [mf, mt],
                                               "weekly": [wf, wl] if wf else None, "daily_from": daily_from,
                                               "until": h_since, "applied": int(time.time()),
                                               "rows": h + e, "moves_rows": m}))
    c.execute("DETACH DATABASE bf")
    log.info("apply: replaced %d history rows and %d price changes; wrote %d history rows (%s..%s) + %d end rows "
             "on %d, %d price changes (%s..%s) in %.0fs", old[0], old[1], h, hf, ht, e, h_since, m, mf, mt,
             time.time() - t0)
    return {"history": h, "ends": e, "moves": m}


def remove(db_path=None):
    """Undo apply: the backfilled rows go and the live start days come back."""
    path = os.path.abspath(db_path or dbmod.DB_PATH)
    c = dbmod.connect(path)
    c.row_factory = None
    with indexer.run_lock(path) as ok:
        if not ok:
            raise SystemExit("the indexer holds its lock: try again in a few minutes")
        if not get_setting(c, "backfill"):
            return (0, 0)
        h_since, m_since = live_bounds(c)
        with c:
            out = _drop(c, h_since, m_since)
            put_setting(c, "history_since", str(h_since))
            put_setting(c, "moves_since", str(m_since))
            c.execute("DELETE FROM settings WHERE key IN ('backfill', 'history_live_since', 'moves_live_since')")
        log.info("remove: %d history rows and %d price changes taken out", *out)
        return out


def source(c):
    """Where history before the live history comes from, for pages that show it: {by, url, license, license_url,
    until (the first live day), history: [first, last day]} or None."""
    v = get_setting(c, "backfill")
    if not v:
        return None
    try:
        d = json.loads(v)
    except ValueError:
        return None
    out = {k: d.get(k) for k in ("by", "url", "license", "license_url", "until", "history", "moves", "weekly",
                                 "daily_from")}
    # the last day archive data is shown for: charts (history) and the Price Board (history and price changes)
    h, m = (d.get("history") or [None, None]), (d.get("moves") or [None, None])
    out["history_last"] = next_day(d["until"], -1) if d.get("until") and h[0] else h[1]
    out["last"] = max(x for x in (out["history_last"], m[1], 0) if x) or None
    return out


def status(work):
    ms = metas(work)
    out = {"versions": len(ms), "first": ms[0]["version"] if ms else None, "last": ms[-1]["version"] if ms else None,
           "gb_downloaded": round(sum(m.get("downloaded", m["bytes"]) for m in ms) / 1e9, 1), "ready_until": ready_until(ms)}
    sp = os.path.join(work, "stage.db")
    if os.path.exists(sp):
        s = sqlite3.connect(sp)
        out["built"] = s.execute("SELECT min(day), max(day), count(*) FROM done").fetchone()
        out["history_rows"] = s.execute("SELECT count(*) FROM out_h").fetchone()[0]
        out["price_changes"] = s.execute("SELECT count(*) FROM out_m").fetchone()[0]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=("fetch", "build", "apply", "remove", "status"))
    ap.add_argument("--work", default=None, help="folder for downloads and stage.db (default <data>/backfill)")
    ap.add_argument("--db", default=None, help="the live DB (default $BASKET_DB)")
    ap.add_argument("--from", dest="first", type=int, default=FIRST_VERSION, help="first Kaggle version")
    ap.add_argument("--to", dest="last", type=int, default=None, help="last Kaggle version (default the newest)")
    ap.add_argument("--jobs", type=int, default=2, help="versions downloaded at once (one process each)")
    ap.add_argument("--until", type=int, default=None, help="build: last day (YYYYMMDD)")
    ap.add_argument("--weekly-from", type=int, default=None,
                    help="fetch: one version a week, published this day (YYYYMMDD) and every 7 days before it")
    ap.add_argument("--weeks", type=int, default=52, help="fetch --weekly-from: how many")
    ap.add_argument("--newest-first", action="store_true", help="fetch --weekly-from: from the newest end")
    ap.add_argument("--no-moves", action="store_true", help="apply: history only, no branch price changes")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    work = work_dir(a.work)
    os.makedirs(work, exist_ok=True)
    if a.step == "fetch" and a.weekly_from:
        fetch_weekly(work, a.weekly_from, a.weeks, a.jobs, a.newest_first)
    elif a.step == "fetch":
        fetch(work, a.first, a.last, a.jobs)
    elif a.step == "build":
        build(work, a.db, a.until)
    elif a.step == "apply":
        print(json.dumps(apply(work, a.db, moves=not a.no_moves)))
    elif a.step == "remove":
        print(remove(a.db))
    else:
        print(json.dumps(status(work), indent=1))


if __name__ == "__main__":
    main()
