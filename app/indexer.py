"""Index the downloaded Price Transparency files into SQLite.

Reads every chain's Stores / PriceFull / Price / PromoFull / Promo files from
the downloader's dumps folder. The XML dialects differ per portal (Shufersal,
Cerberus, Bina, Super-Pharm "Line" files, SAP-style uppercase store files), so
parsing is tag-name driven and case-insensitive instead of per-chain.

Run:  python -m app.indexer [--dumps ~/il-prices/dumps]
One-offs (each also runs confirm()): --rekey, --names, --promos-again, --rebuild-history [DAY]; see main().
"""
import argparse
import contextlib
import fcntl
import gzip
import io
import json
import logging
import os
import re
import statistics
import sys
import time
import zipfile
from collections import defaultdict
from concurrent import futures
from concurrent.futures import ProcessPoolExecutor

from lxml import etree

from . import db as dbmod
from . import names as namesmod
from . import trust
from .chains import chain_name  # noqa: F401

log = logging.getLogger("indexer")

NAMES_DAYS = trust.NAMES_DAYS  # a chain's name for a barcode votes in the name check this long after its newest file
CONFIRM_EVERY = 2 * 3600  # confirm() also runs with no new files when the last one is this old (stores go stale)
NAME_REWRITE = 300        # in one run a name is written again only from a file ~3 h newer (YYYYMMDDHHMM units)
UNTRACKED = 999999999999  # settings confirmed_through missing: not tracked yet, every price row counts as checked
SHARED_PRODUCE = re.compile(r"^729000000\d{4}$")  # 729-0000000-xxxx: chains number their own produce, bags, fees
SPEND_TAGS = ("minpurchaseamnt", "minpurchaseamount", "additionalminbasketamount")

KINDS = ("pricefull", "price", "promofull", "promo", "storesfull", "stores")
NAME_RE = re.compile(r"^(?:null)?(pricefull|price|promofull|promo|storesfull|stores)(\d{7,14})[-_](.*)$", re.I)


# ---------------------------------------------------------------- helpers
def parse_filename(name):
    base = os.path.basename(name)
    base = re.sub(r"(\.xml|\.gz|\.zip)+$", "", base, flags=re.I)
    m = NAME_RE.match(base)
    if not m:
        return None
    kind = m.group(1).lower()
    if kind == "storesfull":
        kind = "stores"
    chain = m.group(2)
    parts = [p for p in re.split(r"[-_]", m.group(3)) if p.isdigit()]
    ts, store = None, None
    for i, p in enumerate(parts):
        if len(p) == 12 and p.startswith("20"):
            ts, store = int(p), (parts[i - 1] if i else None)
            break
        if len(p) == 8 and p.startswith("20") and i + 1 < len(parts) and len(parts[i + 1]) in (4, 6):
            ts, store = int(p + parts[i + 1][:4]), (parts[i - 1] if i else None)
            break
    if store is None and parts:
        store = parts[0]
    try:
        store = int(store) if store is not None else 0
    except ValueError:
        store = 0
    return {"kind": kind, "chain": chain, "store": store, "ts": ts}


def open_xml(path):
    with open(path, "rb") as f:
        head = f.read(4)
    if head[:2] == b"\x1f\x8b":
        return gzip.open(path, "rb")
    if head[:2] == b"PK":
        z = zipfile.ZipFile(path)
        return io.BytesIO(z.read(z.namelist()[0]))
    return open(path, "rb")


def local(tag):
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def children(el):
    d = {}
    for c in el:
        t = local(c.tag)
        if t and t not in d:
            d[t] = (c.text or "").strip()
    return d


def descendants(el):
    d = {}
    for c in el.iter():
        if c is el:
            continue
        t = local(c.tag)
        if t and t not in d and len(c) == 0:
            d[t] = (c.text or "").strip()
    return d


def num(v, default=None):
    if v is None:
        return default
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return default


DATE_RES = [
    re.compile(r"(20\d\d)[-/.](\d\d?)[-/.](\d\d?)(?:[ T](\d\d?):(\d\d))?"),
    re.compile(r"(\d\d?)[-/.](\d\d?)[-/.](20\d\d)(?:[ T](\d\d?):(\d\d))?"),
]


def date_int(value, hour=None, end=False):
    if not value:
        return None
    s = value.strip()
    for i, rx in enumerate(DATE_RES):
        m = rx.search(s)
        if m:
            if i == 0:
                y, mo, d, hh, mm = m.groups()
            else:
                d, mo, y, hh, mm = m.groups()
            if hh is None and hour:
                hm = re.match(r"(\d\d?):(\d\d)", hour.strip())
                if hm:
                    hh, mm = hm.groups()
            if hh is None:
                hh, mm = ("23", "59") if end else ("00", "00")
            return int(f"{int(y):04d}{int(mo):02d}{int(d):02d}{int(hh):02d}{int(mm):02d}")
    return None


def is_global_code(code):
    """True when the item code is a real manufacturer barcode shared across chains."""
    if not code or not code.isdigit():
        return False
    if not 8 <= len(code) <= 14:
        return False
    short = code.lstrip("0")
    if len(short) < 8:
        return False
    if len(code) == 13 and code[0] == "2":  # in-store / weighed barcode range
        return False
    if SHARED_PRODUCE.match(short):  # valid check digits, but each chain puts its own produce/deli items there
        return False
    return trust.gs1_ok(code)  # chain-internal codes that only look like barcodes usually fail the check digit


def comparable(key):
    """A product key is compared across chains only when it is a bare manufacturer barcode. Bare keys stored before
    the barcode rules got stricter can fail this until --rekey moves them."""
    return bool(key) and ":" not in key and is_global_code(key)


def names_cutoff(now=None):
    return trust.names_cutoff(now)


def get_setting(con, key, default=None):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except ValueError:
        return row[0]


def put_setting(con, key, value):
    con.execute("INSERT INTO settings VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)))


def retailers(con):
    """chain id -> retailer: chains.folder (the dump folder) when set, else the chain id. One retailer can publish
    under two ids (City Market Shops: 0000000000000 and 7290000000003); it gets one vote and one median."""
    return {r[0]: (r[1] or r[0]) for r in con.execute("SELECT chain_id, folder FROM chains")}


def set_folders(con, found):
    """chains.folder = the dump folder holding most of a chain id's price files. Placeholder ids turn up in other
    chains' folders (0000000000000 in CityMarketShops, Keshet and Yohananof; one Carrefour file in Keshet), so the
    folder of the first file seen is not reliable."""
    count = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for f in found:
        c = count[f["chain"]][f["folder"]]
        c[0] += f["kind"] in ("pricefull", "price")
        c[1] += 1
        c[2] = max(c[2], f["ts"] or 0)
    rows = [(chain, max(by, key=lambda k: by[k])) for chain, by in count.items()]
    con.executemany("""INSERT INTO chains(chain_id, folder) VALUES(?,?) ON CONFLICT(chain_id)
                       DO UPDATE SET folder=excluded.folder WHERE chains.folder IS NOT excluded.folder""", rows)


NORM_DROP = re.compile(r'(במשקל|לק"ג|לק״ג|ק"ג|ק״ג|קג|ארוז|טרי|טריים|טריות|מובחר|מובחרות|בתפזורת|\bkg\b)', re.I)


def normalize_name(name):
    """Loose name key used to line up weighed produce across chains (their codes differ)."""
    s = NORM_DROP.sub(" ", name or "")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\d+", " ", s)
    return " ".join(s.split()).strip().lower()


def product_key(chain, code, weighted=False):
    code = (code or "").strip()
    if not weighted and is_global_code(code):
        return code, "global"
    return f"{chain}:{code}", "chain"


def iter_records(path, tags, require):
    """Stream records: elements whose local tag is in `tags` and that contain a `require` field."""
    src = open_xml(path)
    try:
        ctx = etree.iterparse(src, events=("end",), recover=True, huge_tree=True)
        header = {}
        for _, el in ctx:
            t = local(el.tag)
            if t in tags:
                yield el, header
                el.clear()
                parent = el.getparent()
                if parent is not None:
                    while el.getprevious() is not None:
                        del parent[0]
            elif len(el) == 0 and t in ("chainid", "chainname", "storeid", "subchainname"):
                header.setdefault(t, (el.text or "").strip())
    except etree.XMLSyntaxError as e:
        log.warning("XML error in %s: %s", path, e)
    finally:
        src.close()


# ---------------------------------------------------------------- parsers
def index_stores(con, path, meta):
    rows, chain_names = [], {}
    for el, header in iter_records(path, {"store", "branch"}, "storeid"):
        d = children(el)
        sid = d.get("storeid") or d.get("storenumber") or d.get("branchid")
        if not sid or not sid.strip().lstrip("-").isdigit():
            continue
        chain = d.get("chainid") or header.get("chainid") or meta["chain"]
        cname = d.get("chainname") or header.get("chainname")
        if cname:
            chain_names[chain] = cname
        stype = d.get("storetype")
        rows.append((chain, int(sid), d.get("storename") or d.get("branchname") or "",
                     d.get("address") or "", d.get("city") or "",
                     int(stype) if stype and stype.isdigit() else None,
                     d.get("subchainname") or header.get("subchainname") or ""))
    con.executemany(
        """INSERT INTO stores(chain_id, store_id, name, address, city, store_type, sub_chain)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(chain_id, store_id) DO UPDATE SET name=excluded.name, address=excluded.address,
             city=excluded.city, store_type=excluded.store_type, sub_chain=excluded.sub_chain""", rows)
    for c, n in chain_names.items():
        con.execute("INSERT INTO chains(chain_id, name_he) VALUES(?,?) ON CONFLICT(chain_id) DO UPDATE SET name_he=excluded.name_he", (c, n))
    return len(rows)


# the stored pack size is missing or "unknown" and this chain's file has a real one
SIZE_GAP = """(coalesce(products.unit, '') IN ('', 'לא ידוע') OR coalesce(CAST(products.quantity AS REAL), 0) <= 0)
              AND coalesce(excluded.unit, '') NOT IN ('', 'לא ידוע') AND coalesce(CAST(excluded.quantity AS REAL), 0) > 0"""


def index_prices(con, path, meta, scoped_cache, names_seen=None):
    """One price file. Every barcode item's name goes to item_names with the file's time (names_seen: names already
    written in this run, so each distinct (key, chain, name) is written about once per run, not once per store)."""
    chain, store, ts = meta["chain"], meta["store"], meta["ts"]
    prices, products, names = [], [], []
    names_seen = {} if names_seen is None else names_seen
    for el, _ in iter_records(path, {"item", "product", "line"}, "itemcode"):
        d = children(el)
        code = d.get("itemcode")
        price = num(d.get("itemprice"))
        if not code or price is None or price <= 0 or price > 100000:
            continue
        weighted = (d.get("bisweighted") or d.get("blsweighted") or "0").strip() in ("1", "true", "True")
        key, scope = product_key(chain, code, weighted)
        if scope == "chain":
            scoped_cache[chain].add(code)
        prices.append((key, chain, store, price, ts))
        pname = d.get("itemname") or d.get("itemnm") or ""
        if scope == "global" and pname.strip():
            _name_row(names, names_seen, key, chain, pname.strip(), ts)
        products.append((key, code, pname,
                         d.get("manufacturername") or d.get("manufactureritemdescription") or "",
                         d.get("quantity") or "", d.get("unitqty") or d.get("unitofmeasure") or "",
                         1 if weighted else 0, scope, normalize_name(pname) if scope == "chain" else None))
    con.executemany(
        """INSERT INTO prices(key, chain_id, store_id, price, ts) VALUES(?,?,?,?,?)
           ON CONFLICT(key, chain_id, store_id) DO UPDATE SET price=excluded.price, ts=excluded.ts
           WHERE excluded.ts >= prices.ts""", prices)
    con.executemany(
        """INSERT INTO products(key, code, name, manufacturer, quantity, unit, is_weighted, scope, norm)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(key) DO UPDATE SET name=CASE WHEN products.name='' THEN excluded.name ELSE products.name END,
             manufacturer=CASE WHEN products.manufacturer='' THEN excluded.manufacturer ELSE products.manufacturer END,
             -- chains differ on pack sizes: many say "unknown"; take the first chain that gives a real one
             quantity=CASE WHEN {SIZE_GAP} THEN excluded.quantity ELSE products.quantity END,
             unit=CASE WHEN {SIZE_GAP} THEN excluded.unit ELSE products.unit END""".replace("{SIZE_GAP}", SIZE_GAP),
        products)
    write_names(con, names)
    return len(prices)


def _name_row(out, seen, key, chain, name, ts):
    h = hash((key, chain, name))  # an int, not the strings: a full day of files has ~1 M distinct names
    last = seen.get(h)
    if last is None or ts - last >= NAME_REWRITE:
        seen[h] = ts
        out.append((key, chain, name, ts))


def write_names(con, names):
    """names: (key, chain_id, name, ts). Keeps every distinct name per (key, chain), with the newest file time."""
    con.executemany("""INSERT INTO item_names(key, chain_id, name, ts) VALUES(?,?,?,?)
                       ON CONFLICT(key, chain_id, name) DO UPDATE SET ts=excluded.ts WHERE excluded.ts > item_names.ts""",
                    names)


def _item_codes(el, promo_gift):
    codes = []
    for c in el.iter():
        t = local(c.tag)
        if t == "item" or (t == "itemcode" and local(c.getparent().tag) != "item"):
            if t == "item":
                f = children(c)
                code, gift = f.get("itemcode"), f.get("isgiftitem", "0")
            else:
                code, gift = (c.text or "").strip(), promo_gift
            if code and (gift or "0").strip() != "1":
                codes.append(code)
    return list(dict.fromkeys(codes))


def _promo_fields(el):
    """One promotion record -> fields (first leaf of each tag), the item codes it applies to, every item code it
    lists, and its conditions.

    Several Groups (Shufersal and most Laibcatalog/Cerberus chains): group 1 holds the items you buy, the later
    groups the reward items (1+1, second at 50%, a gift). The deal price is paid on an item only when buying it
    twice fills both roles, so only group-1 items that are also in a later group are attached (a subset of group 1;
    "buy X, get Y" across products is not modelled and stays uncounted)."""
    d, clubs, spend, caps, groups = {}, [], False, [], []
    for c in el.iter():  # one pass: first leaf of each tag (like descendants()), conditions, groups
        if c is el:
            continue
        t = local(c.tag)
        if len(c):
            if t == "group":
                groups.append(c)
            continue
        v = (c.text or "").strip()
        if t and t not in d:
            d[t] = v
        if t == "clubid":
            clubs.append(v)
        elif t in SPEND_TAGS:  # "spend ₪X in total": any group's amount (Shufersal puts it in group 2)
            spend = spend or (num(v, 0) or 0) > 0
        elif t in ("maxqty", "redemptionlimit"):
            n = num(v)
            if n and n > 0:
                caps.append(n)
    gift = d.get("isgiftitem", "0")
    if len(groups) >= 2:
        per = [_item_codes(g, gift) for g in groups]
        later = set().union(*per[1:])
        codes = [c for c in per[0] if c in later]
        listed = list(dict.fromkeys(c for p in per for c in p))
    else:
        codes = listed = _item_codes(el, gift)
    club_ids = []
    for c in clubs:
        m = re.match(r"\d+", c)
        if m and str(int(m.group(0))) not in club_ids:
            club_ids.append(str(int(m.group(0))))
    return {"d": d, "codes": codes, "listed": listed, "club_ids": club_ids, "spend": spend,
            "max_qty": min(caps) if caps else None,
            "start": date_int(d.get("promotionstartdate"), d.get("promotionstarthour"))
            or date_int(d.get("promotionstartdatetime")),
            "end": date_int(d.get("promotionenddate"), d.get("promotionendhour"), end=True)
            or date_int(d.get("promotionenddatetime"), end=True)}


def _promo_skip(p, now_int):
    """Why a promotion is not stored, or None."""
    d = p["d"]
    if not p["codes"]:
        return "no items"
    if d.get("additionaliscoupon", "0") == "1" or d.get("additionalisactive", "1") == "0":
        return "coupon or inactive"
    if p["spend"]:
        return "spend threshold"
    if len(p["listed"]) > 2000:  # ponytail: store-wide conditions list every item; real product deals are far smaller
        return "store-wide"
    if p["end"] and p["end"] < now_int:
        return "ended"
    if not num(d.get("discountedprice")) and not num(d.get("discountrate")):
        return "no price"
    return None


def index_promos(con, path, meta, scoped_cache, now_int):
    """One promo file. A promotion this file lists but that isn't stored (coupon, spend threshold, ended, ...) also
    loses the row an earlier file or an earlier version of these rules stored for it, and items a re-read no longer
    attaches (other groups) lose theirs, so re-reading a file (--promos-again) leaves only what these rules keep."""
    chain, store, ts = meta["chain"], meta["store"], meta["ts"]
    promos, items, attached, listed, dropped = {}, [], defaultdict(set), defaultdict(set), set()
    for el, _ in iter_records(path, {"promotion", "sale", "line"}, "promotionid"):
        p = _promo_fields(el)
        d = p["d"]
        pid = d.get("promotionid")
        if not pid:
            continue
        for code in p["listed"]:
            listed[pid].add(product_key(chain, code, code in scoped_cache[chain])[0])
        if _promo_skip(p, now_int):
            dropped.add(pid)
            continue
        club_only = 1 if any(c != "0" for c in p["club_ids"]) else 0
        promos[pid] = (chain, store, pid, (d.get("promotiondescription") or "")[:200], p["start"], p["end"],
                       num(d.get("minqty"), 1) or 1, num(d.get("discountedprice")), num(d.get("discountrate")),
                       club_only, ts, p["max_qty"], (d.get("remarks") or "").strip()[:200] or None,
                       ",".join(p["club_ids"]))
        for code in p["codes"]:
            key, _ = product_key(chain, code, code in scoped_cache[chain])
            attached[pid].add(key)
            items.append((key, chain, store, pid, ts))
    con.executemany("DELETE FROM promos WHERE chain_id=? AND store_id=? AND promo_id=? AND ts<=?",
                    [(chain, store, pid, ts) for pid in dropped if pid not in promos])
    con.executemany("DELETE FROM promo_items WHERE key=? AND chain_id=? AND store_id=? AND promo_id=? AND ts<=?",
                    [(k, chain, store, pid, ts) for pid in promos for k in listed[pid] - attached[pid]])
    con.executemany(
        """INSERT INTO promos(chain_id, store_id, promo_id, description, start_ts, end_ts, min_qty, discounted_price,
                              discount_rate, club_only, ts, max_qty, remarks, club_ids) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(chain_id, store_id, promo_id) DO UPDATE SET description=excluded.description,
             start_ts=excluded.start_ts, end_ts=excluded.end_ts, min_qty=excluded.min_qty,
             discounted_price=excluded.discounted_price, discount_rate=excluded.discount_rate,
             club_only=excluded.club_only, ts=excluded.ts, max_qty=excluded.max_qty, remarks=excluded.remarks,
             club_ids=excluded.club_ids
           WHERE excluded.ts >= promos.ts""",
        list(promos.values()))
    con.executemany(
        """INSERT INTO promo_items VALUES(?,?,?,?,?)
           ON CONFLICT DO UPDATE SET ts=excluded.ts WHERE excluded.ts >= promo_items.ts""", items)
    return len(promos)


# ---------------------------------------------------------------- runner
def scan(dumps):
    found = []
    for root, dirs, files in os.walk(dumps):
        dirs[:] = [d for d in dirs if d.lower() != "status"]
        for fn in files:
            meta = parse_filename(fn)
            if not meta:
                continue
            path = os.path.join(root, fn)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if meta["ts"] is None:
                meta["ts"] = int(time.strftime("%Y%m%d%H%M", time.localtime(st.st_mtime)))
            meta.update(path=path, size=st.st_size, folder=os.path.basename(root))
            found.append(meta)
    return found


def set_status(con, **kw):
    row = con.execute("SELECT value FROM settings WHERE key='index_status'").fetchone()
    cur = json.loads(row[0]) if row else {}
    cur.update(kw)
    con.execute("INSERT INTO settings VALUES('index_status', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(cur),))
    con.commit()


@contextlib.contextmanager
def run_lock(db_path=None):
    """One indexer at a time per database (the timer and POST /api/reindex can start one each). Yields False when
    another run holds the lock."""
    path = os.path.abspath(db_path or dbmod.DB_PATH) + ".indexer.lock"
    with open(path, "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def run(dumps, db_path=None, only=None, confirm_always=False, rekey_keys=False, names=False, promos_again=False,
        rebuild_since=None):
    with run_lock(db_path) as mine:
        if not mine:
            log.warning("another index run is active on this database; not starting")
            return False
        con = dbmod.connect(db_path)
        try:
            if rekey_keys:
                rekey(con)
            if names:
                backfill_names(con, dumps)
            if promos_again:
                reread_promos(con)
            _run(con, dumps, only, confirm_always or rekey_keys or names or rebuild_since is not None,
                 rebuild_since)
        finally:
            con.close()
    return True


def _run(con, dumps, only, confirm_always, rebuild_since=None):
    started = time.time()
    now_int = int(time.strftime("%Y%m%d%H%M"))
    set_status(con, running=True, started=int(started), error=None)
    done = {r["path"]: r["size"] for r in con.execute("SELECT path, size FROM files")}
    found = scan(dumps)
    set_folders(con, found)
    con.commit()
    files = [f for f in found if done.get(f["path"]) != f["size"]]
    if only:  # partial pass (e.g. promos first); filtered before `skipped`, so other kinds stay unindexed
        files = [f for f in files if f["kind"] in only]
    state = {(r["chain_id"], r["store_id"]): (r["price_full_ts"], r["promo_full_ts"])
             for r in con.execute("SELECT chain_id, store_id, price_full_ts, promo_full_ts FROM stores")}
    scoped_cache, names_seen = defaultdict(set), {}
    for r in con.execute("SELECT code, substr(key, 1, instr(key, ':')-1) AS chain FROM products WHERE scope='chain'"):
        scoped_cache[r["chain"]].add(r["code"])
    before_products = con.execute("SELECT count(*) FROM products").fetchone()[0]

    # latest full file per store; deltas after it
    latest_full = {}
    for f in files:
        if f["kind"] in ("pricefull", "promofull"):
            k = (f["kind"], f["chain"], f["store"])
            if k not in latest_full or f["ts"] > latest_full[k]["ts"]:
                latest_full[k] = f
    stores_files = {}
    for f in files:
        if f["kind"] == "stores":
            if f["chain"] not in stores_files or f["ts"] > stores_files[f["chain"]]["ts"]:
                stores_files[f["chain"]] = f

    # Price files newer than every checked row first: their rows are unchecked by their time alone. Files that
    # arrive late (older than the newest checked row: 1-5 h is common) go last, and confirmed_through is lowered just
    # before the first of them, so rows from other stores stay confirmed for most of the run. Same end state as
    # plain time order (each store's newer rows win either way).
    through0 = confirmed_through(con)
    fulls = sorted([f for f in latest_full.values() if f["kind"] == "pricefull"], key=lambda f: f["ts"])
    deltas = sorted([f for f in files if f["kind"] == "price"], key=lambda f: f["ts"])
    plan = list(stores_files.values())
    plan += [f for f in fulls if f["ts"] > through0] + [f for f in deltas if f["ts"] > through0]
    late = [f for f in fulls if f["ts"] <= through0] + [f for f in deltas if f["ts"] <= through0]
    plan += late
    plan += sorted([f for f in latest_full.values() if f["kind"] == "promofull"], key=lambda f: f["ts"])
    plan += sorted([f for f in files if f["kind"] == "promo"], key=lambda f: f["ts"])
    planned = {id(f) for f in plan}
    skipped = [f for f in files if id(f) not in planned]
    pf0 = lambda f: state.get((f["chain"], f["store"]), (0, 0))[0]  # noqa: E731
    late_ts = [f["ts"] for f in late if f["ts"] > pf0(f) or (f["kind"] == "pricefull" and f["ts"] == pf0(f))]
    if fulls or deltas:
        put_setting(con, "confirm_pending", 1)
        con.commit()
    first_late = late[0]["path"] if late_ts else None

    log.info("%d new files, %d to index", len(files), len(plan))
    set_status(con, total=len(plan), processed=0)
    n_rows = 0
    for i, f in enumerate(plan):
        key = (f["chain"], f["store"])
        pf, mf = state.get(key, (0, 0))
        kind = f["kind"]
        if f["path"] == first_late:
            mark_unchecked(con, min(late_ts))
        try:
            if kind != "stores":
                con.execute("INSERT OR IGNORE INTO stores(chain_id, store_id) VALUES(?,?)", key)
                con.execute("INSERT OR IGNORE INTO chains(chain_id, folder) VALUES(?,?)", (f["chain"], f["folder"]))
            if kind == "stores":
                rows = index_stores(con, f["path"], f)
            elif kind == "pricefull":
                if f["ts"] < pf:
                    rows = 0
                else:
                    rows = index_prices(con, f["path"], f, scoped_cache, names_seen)
                    con.execute("UPDATE stores SET price_full_ts=?, last_price_ts=max(last_price_ts, ?) WHERE chain_id=? AND store_id=?",
                                (f["ts"], f["ts"], *key))
                    state[key] = (f["ts"], mf)
            elif kind == "price":
                if f["ts"] <= pf:
                    rows = 0
                else:
                    rows = index_prices(con, f["path"], f, scoped_cache, names_seen)
                    con.execute("UPDATE stores SET last_price_ts=max(last_price_ts, ?) WHERE chain_id=? AND store_id=?", (f["ts"], *key))
            elif kind == "promofull":
                if f["ts"] < mf:
                    rows = 0
                else:
                    rows = index_promos(con, f["path"], f, scoped_cache, now_int)
                    con.execute("UPDATE stores SET promo_full_ts=? WHERE chain_id=? AND store_id=?", (f["ts"], *key))
                    state[key] = (state.get(key, (pf, 0))[0], f["ts"])
            else:  # promo delta
                rows = 0 if f["ts"] <= mf else index_promos(con, f["path"], f, scoped_cache, now_int)
            con.execute("INSERT INTO files VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET size=excluded.size, rows=excluded.rows, done_at=excluded.done_at",
                        (f["path"], f["size"], kind, rows, int(time.time())))
            con.commit()
            n_rows += rows
        except Exception as e:  # keep going: one bad file must not stop the run
            con.rollback()
            log.exception("failed on %s: %s", f["path"], e)
        if i % 25 == 0:
            set_status(con, processed=i + 1)
            log.info("[%d/%d] %s", i + 1, len(plan), os.path.basename(f["path"]))
    # older full files we skipped are recorded so they're not rescanned
    con.executemany("INSERT OR IGNORE INTO files VALUES(?,?,?,0,?)",
                    [(f["path"], f["size"], f["kind"], int(time.time())) for f in skipped])
    con.commit()

    after_products = con.execute("SELECT count(*) FROM products").fetchone()[0]
    if after_products != before_products or not con.execute("SELECT 1 FROM products_fts LIMIT 1").fetchone():
        log.info("rebuilding product search index")
        con.execute("DELETE FROM products_fts")
        con.execute("INSERT INTO products_fts(key, name, manufacturer, code) SELECT key, name, manufacturer, code FROM products")
        con.commit()

    cleanup(con)
    if plan or confirm_always or confirm_due(con):
        confirm(con, rebuild_since=rebuild_since)
    set_status(con, running=False, finished=int(time.time()), processed=len(plan),
               rows=n_rows, seconds=int(time.time() - started))
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # fold the write-ahead log back in; it otherwise grows by GBs
    log.info("done: %d files, %d rows in %ds", len(plan), n_rows, time.time() - started)


# ---------------------------------------------------------------- what counts (app.trust)
# A shelf price is confirmed when its store's file is current, confirm() has checked it (ts <= confirmed_through),
# it isn't an outlier, and for a barcode this chain has a recent name for it that agrees with the other chains'.
# Stats, history, cards and deals use confirmed prices.
# Per price row (p = prices, s = its store) ...
CONFIRMED_ROW = """p.ts >= s.price_full_ts AND s.last_price_ts >= :cutoff AND p.ts <= :through
    AND NOT EXISTS (SELECT 1 FROM price_flags f WHERE f.key=p.key AND f.chain_id=p.chain_id AND f.store_id=p.store_id)"""
# ... and per (key, chain) pair (g): checked once per pair, not once per store.
CONFIRMED_PAIR = """NOT EXISTS (SELECT 1 FROM name_flags nf WHERE nf.key=g.key AND nf.chain_id=g.chain_id)
    AND (instr(g.key, ':') > 0 OR EXISTS (SELECT 1 FROM item_names i WHERE i.key=g.key AND i.chain_id=g.chain_id
                                          AND i.ts >= :ncut))"""
CONFIRMED = CONFIRMED_ROW + " AND " + CONFIRMED_PAIR.replace("g.", "p.")


def confirmed_pairs(con, cutoff, through):
    """temp.cur(key, chain_id, mn, mx, n): each (product, chain)'s cheapest and dearest confirmed shelf price and
    its number of stores. One pass over the prices (~40 s live), shared by the stats and the history."""
    t = time.time()
    con.execute("DROP TABLE IF EXISTS temp.cur")
    con.execute(f"""CREATE TEMP TABLE cur AS SELECT g.key, g.chain_id, g.mn, g.mx, g.n FROM (
                       SELECT p.key, p.chain_id, min(p.price) mn, max(p.price) mx, count(*) n
                       FROM prices p JOIN stores s ON s.chain_id=p.chain_id AND s.store_id=p.store_id
                       WHERE p.price > 0 AND {CONFIRMED_ROW} GROUP BY p.key, p.chain_id) g
                   WHERE {CONFIRMED_PAIR}""", {"cutoff": cutoff, "through": through, "ncut": names_cutoff()})
    con.execute("CREATE INDEX temp.cur_k ON cur(key, chain_id)")
    log.info("confirmed prices in %.0fs", time.time() - t)


def confirmed_through(con):
    """Price rows with ts above this haven't been checked by confirm() yet (UNTRACKED when not tracked yet)."""
    v = get_setting(con, "confirmed_through", None)
    try:
        return UNTRACKED if v is None else int(v)
    except (TypeError, ValueError):
        return 0


def mark_unchecked(con, first_ts):
    """Before a late price file (older than the newest checked row) is written: rows from first_ts on are unchecked
    until confirm() succeeds, and the next run confirms even with no new files."""
    if first_ts - 1 < confirmed_through(con):
        put_setting(con, "confirmed_through", first_ts - 1)
    put_setting(con, "confirm_pending", 1)
    con.commit()


def confirm_due(con):
    """No new files: confirm anyway when the last one failed or didn't finish, or is CONFIRM_EVERY old (stores
    crossing the freshness line change the stats, medians and history)."""
    if get_setting(con, "confirm_pending", 0):
        return True
    return time.time() - float(get_setting(con, "last_confirm", 0) or 0) >= CONFIRM_EVERY


def confirm(con, rebuild_since=None):
    """After an index run: chains that name a barcode differently, then outlier prices (leaving those out), then
    stats and history from confirmed prices only. confirmed_through moves up only when every step succeeded; when the
    flags fail, prices newer than the last good confirm stay unconfirmed and history isn't touched."""
    cutoff = trust.fresh_cutoff()
    old = confirmed_through(con)
    t = time.time()
    through = None
    try:
        build_name_flags(con, cutoff)
        through = build_price_flags(con, cutoff)
    except Exception:
        log.exception("confirm failed: flags not rebuilt; prices newer than %s stay unconfirmed", old)
    ok = through is not None
    try:
        confirmed_pairs(con, cutoff, through if ok else old)
        update_stats(con)
        if ok:
            if rebuild_since is not None:
                rebuild_history(con, rebuild_since)
            record_history(con, cutoff=cutoff, through=through, pairs_ready=True)
    except Exception:
        log.exception("confirm failed after the flags: confirmed_through stays %s", old)
        ok = False
    if ok:
        # never 0: readers take 0 like a missing setting (not tracked: everything checked). With no fresh store
        # there is nothing to confirm, and 1 keeps every later row unchecked until the next confirm.
        through = max(through, 1)
        put_setting(con, "confirmed_through", through)
        put_setting(con, "last_confirm", int(time.time()))
        put_setting(con, "confirm_pending", 0)
        con.commit()
        log.info("confirmed through %s (%.0fs)", through, time.time() - t)
    else:
        con.rollback()
    return ok


def update_stats(con):
    """products.store_count / chain_count / min_price / max_price from confirmed prices only (temp.cur)."""
    t = time.time()
    con.execute("DROP TABLE IF EXISTS temp.agg")
    con.execute("""CREATE TEMP TABLE agg AS SELECT key, sum(n) n, count(*) c, min(mn) mn, max(mx) mx FROM temp.cur
                   GROUP BY key""")
    con.execute("CREATE INDEX temp.agg_key ON agg(key)")
    con.execute("""UPDATE products SET store_count=coalesce((SELECT n FROM agg WHERE agg.key=products.key),0),
                   chain_count=coalesce((SELECT c FROM agg WHERE agg.key=products.key),0),
                   min_price=(SELECT mn FROM agg WHERE agg.key=products.key),
                   max_price=(SELECT mx FROM agg WHERE agg.key=products.key)""")
    con.commit()
    log.info("stats in %.0fs", time.time() - t)


def judge_prices(rows, compared=True):
    """Outlier check for one product. rows: [(retailer, chain_id, store_id, price)] at fresh stores, without
    name-flagged pairs. Returns (typical price for products.median or None, [(chain_id, store_id, median shown)]).

    Each retailer counts once: its median price (a chain with most of the branches no longer sets the typical
    price by itself). Two retailers agree when their medians are within trust.OUTLIER of each other.
    - 2+ retailers (a compared barcode): the consensus is the retailers agreeing with more than half of all
      retailers (themselves included); typical = median of their medians; every store price outside
      trust.OUTLIER of it is flagged. No consensus (2 that disagree, a 2-2 split) -> every price is flagged, shown
      against the other retailers' median, and there is no typical price.
    - 1 retailer, or a code each chain uses for its own item: judged within each retailer, against its own median,
      once it has trust.OUTLIER_MIN_STORES stores; with fewer the published price stands."""
    lo, hi = trust.OUTLIER
    r0 = rows[0][0]
    if all(r[0] == r0 for r in rows):  # one retailer (most products): the common case, kept cheap
        m = statistics.median([r[3] for r in rows])
        if len(rows) < trust.OUTLIER_MIN_STORES:
            return m, []
        return m, [(ch, st, m) for _, ch, st, p in rows if p < m * lo or p > m * hi]
    by = defaultdict(list)
    for r, ch, st, p in rows:
        by[r].append((ch, st, p))
    med = {r: statistics.median(p for _, _, p in v) for r, v in by.items()}
    out = []
    if compared and len(by) >= 2:
        core = [r for r in by if 2 * sum(med[r] * lo <= med[x] <= med[r] * hi for x in by) > len(by)]
        if core:
            typical = statistics.median(med[r] for r in core)
            for v in by.values():
                out.extend((ch, st, typical) for ch, st, p in v if p < typical * lo or p > typical * hi)
            return typical, out
        for r, v in by.items():
            other = statistics.median(med[x] for x in by if x != r)
            out.extend((ch, st, other) for ch, st, _ in v)
        return None, out
    for r, v in by.items():
        if len(v) >= trust.OUTLIER_MIN_STORES:
            out.extend((ch, st, med[r]) for ch, st, p in v if p < med[r] * lo or p > med[r] * hi)
    return (next(iter(med.values())) if len(med) == 1 else None), out


def build_price_flags(con, cutoff):
    """products.median and price_flags (see judge_prices) from fresh prices, leaving out pairs the name check
    flagged and barcode pairs with no recent chain name: their prices are of something else or of something
    unknown. Runs after build_name_flags. Returns the newest current price row time at a fresh store (confirm's
    confirmed_through: rows of stale stores can't be confirmed anyway). ~60 s live."""
    t = time.time()
    retailer = retailers(con)
    nflag = {(r[0], r[1]) for r in con.execute("SELECT key, chain_id FROM name_flags")}
    named = {hash((r[0], r[1])) for r in con.execute("SELECT DISTINCT key, chain_id FROM item_names WHERE ts >= ?",
                                                      (names_cutoff(),))}
    flags, medians, cur, rows, cmp_cur, through, pair, use = [], [], None, [], False, 0, None, False

    def flush():
        if rows:
            med, fl = judge_prices(rows, cmp_cur)
            medians.append((cur, med))
            flags.extend((cur, ch, st, m) for ch, st, m in fl)

    # current rows at fresh stores, in key order (the prices primary key: no sort). Rows of other stores can't be
    # confirmed, so the newest row here bounds what this pass checked.
    for key, ch, st, price, ts in con.execute(
            """SELECT p.key, p.chain_id, p.store_id, p.price, p.ts FROM prices p
               JOIN stores s ON s.chain_id=p.chain_id AND s.store_id=p.store_id
               WHERE p.ts >= s.price_full_ts AND s.last_price_ts >= ? AND p.price > 0 ORDER BY p.key""", (cutoff,)):
        if ts > through:
            through = ts
        if key != cur:
            flush()
            cur, rows, cmp_cur = key, [], comparable(key)
        if pair != (key, ch):
            pair = (key, ch)
            use = not cmp_cur or (pair not in nflag and hash(pair) in named)
        if use:
            rows.append((retailer.get(ch, ch), ch, st, price))
    flush()
    with con:
        con.execute("DELETE FROM price_flags")
        con.executemany("INSERT OR REPLACE INTO price_flags VALUES(?,?,?,?)", flags)
        con.execute("DROP TABLE IF EXISTS temp.med")
        con.execute("CREATE TEMP TABLE med(key TEXT PRIMARY KEY, m REAL) WITHOUT ROWID")
        con.executemany("INSERT INTO med VALUES(?,?)", medians)
        con.execute("UPDATE products SET median=(SELECT round(m, 2) FROM med WHERE med.key=products.key)")
    log.info("price flags: %d prices at %d of %d products look wrong somewhere (%.0fs)",
             len(flags), len({f[0] for f in flags}), len(medians), time.time() - t)
    return through


_ODD_NEW = None  # does names.odd_names take the generic= and voter= keywords (names agent's contract)?


def _odd(multi, card, generic, voter):
    """names.odd_names per the shared contract; while names.py is still the older version, one (newest) name
    per chain and no voter map."""
    global _ODD_NEW
    if _ODD_NEW is not False:
        try:
            r = namesmod.odd_names(multi, card, generic=generic, voter=voter)
            _ODD_NEW = True
            return r
        except TypeError:
            if _ODD_NEW:
                raise
            import inspect
            if "voter" in inspect.signature(namesmod.odd_names).parameters:
                raise
            _ODD_NEW = False
            log.warning("names.odd_names has no generic=/voter=: judging one name per chain id")
    return namesmod.odd_names({c: ns[0] for c, ns in multi.items()}, card)


def _odd_name(multi, ch, card, generic, voter):
    """Which of an odd chain's names disagrees (its newest such name), for name_flags.name."""
    if len(multi[ch]) > 1:
        for n in multi[ch]:
            if ch in _odd({**multi, ch: [n]}, card, generic, voter)[0]:
                return n
    return multi[ch][0]


def build_name_flags(con, cutoff=None):
    """Barcode products a chain names differently from the other chains (names.odd_names) -> name_flags.
    Only names seen in the last NAMES_DAYS vote, every distinct name of a chain counts (a chain is odd when any of
    its names is), and each retailer votes once (voter map from chains.folder). When the product card's own name
    is the odd one, it takes a name the other chains agree on."""
    t = time.time()
    ncut = names_cutoff()
    voter = retailers(con)
    gw = getattr(namesmod, "generic_words", None)
    generic = gw((r[0], r[1]) for r in con.execute("SELECT key, name FROM item_names WHERE ts >= ?", (ncut,))) \
        if gw else frozenset()
    flags, renames, cur, card, names = [], [], None, "", {}

    def flush():
        if cur is None or len(names) < 2 or not comparable(cur):
            return
        multi = {c: [n for n, _ in sorted(v, key=lambda x: -x[1])] for c, v in names.items()}
        votes = {c: voter.get(c, c) for c in multi}
        odd, better = _odd(multi, card, generic, votes)
        flags.extend((cur, ch, _odd_name(multi, ch, card, generic, votes)) for ch in odd)
        if better:
            renames.append((better, cur))

    for key, ch, name, ts, pname in con.execute("""SELECT i.key, i.chain_id, i.name, i.ts, p.name FROM item_names i
                                                   JOIN products p ON p.key=i.key WHERE i.ts >= ? ORDER BY i.key""",
                                                (ncut,)):
        if key != cur:
            flush()
            cur, card, names = key, pname or "", defaultdict(list)
        names[ch].append((name, ts))
    flush()
    with con:
        con.execute("DELETE FROM name_flags")
        con.executemany("INSERT OR REPLACE INTO name_flags VALUES(?,?,?)", flags)
        if renames:
            con.executemany("UPDATE products SET name=? WHERE key=?", renames)
            con.execute("DROP TABLE IF EXISTS temp.renamed")
            con.execute("CREATE TEMP TABLE renamed(key TEXT PRIMARY KEY) WITHOUT ROWID")
            con.executemany("INSERT OR IGNORE INTO renamed VALUES(?)", [(k,) for _, k in renames])
            con.execute("DELETE FROM products_fts WHERE key IN (SELECT key FROM temp.renamed)")
            con.execute("""INSERT INTO products_fts(key, name, manufacturer, code) SELECT key, name, manufacturer, code
                           FROM products WHERE key IN (SELECT key FROM temp.renamed)""")
    log.info("name flags: %d chain names disagree, %d product names fixed (%.0fs)", len(flags), len(renames),
             time.time() - t)
    return len(flags)


# ---------------------------------------------------------------- one-offs
def _names_in_file(path, chain, ts):
    """(key, name) of every barcode item in one price file (runs in a worker process)."""
    out = set()
    for el, _ in iter_records(path, {"item", "product", "line"}, "itemcode"):
        d = children(el)
        weighted = (d.get("bisweighted") or d.get("blsweighted") or "0").strip() in ("1", "true", "True")
        key, scope = product_key(chain, d.get("itemcode"), weighted)
        pname = (d.get("itemname") or d.get("itemnm") or "").strip()
        if scope == "global" and pname:
            out.add((key, pname))
    return chain, ts, out


def backfill_names(con, dumps, workers=None):
    """--names: item_names from every store's latest PriceFull and the Price files after it (the files today's
    prices come from), without re-indexing prices. Files older than NAMES_DAYS are skipped (their names wouldn't
    vote). Regular runs keep it up to date from every file they index. Live data: ~2.3k full files (12 GB) and
    ~4.6k delta files (12.7 GB) at ~8.6 MB/s per worker."""
    t = time.time()
    found = scan(dumps)
    latest = {}
    for f in found:
        if f["kind"] == "pricefull":
            k = (f["chain"], f["store"])
            if k not in latest or f["ts"] > latest[k]["ts"]:
                latest[k] = f
    ncut = names_cutoff()
    todo = [f for f in latest.values() if f["ts"] >= ncut]
    todo += [f for f in found if f["kind"] == "price" and (f["chain"], f["store"]) in latest
             and f["ts"] > latest[(f["chain"], f["store"])]["ts"] and f["ts"] >= ncut]
    todo.sort(key=lambda f: -f["size"])
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    log.info("names: reading %d files (%d MB) with %d workers", len(todo), sum(f["size"] for f in todo) >> 20,
             workers)
    seen, n_rows, n_files, queue = {}, 0, 0, list(reversed(todo))
    with ProcessPoolExecutor(workers) as pool:
        running = {}
        while queue or running:
            while queue and len(running) < workers * 2:  # a bounded window: results are big
                f = queue.pop()
                running[pool.submit(_names_in_file, f["path"], f["chain"], f["ts"])] = f
            finished, _ = futures.wait(running, return_when=futures.FIRST_COMPLETED)
            for fut in finished:
                f = running.pop(fut)
                try:
                    chain, ts, pairs = fut.result()
                except Exception:
                    log.exception("names: failed on %s", f["path"])
                    continue
                rows = []
                for key, name in pairs:
                    _name_row(rows, seen, key, chain, name, ts)
                write_names(con, rows)
                n_rows += len(rows)
                n_files += 1
                if n_files % 100 == 0:
                    con.commit()
                    log.info("names: %d/%d files, %d rows written (%.0fs)", n_files, len(todo), n_rows,
                             time.time() - t)
    con.commit()
    log.info("names: %d files, %d rows written in %.0fs", len(todo), n_rows, time.time() - t)
    return n_rows


def rekey(con):
    """--rekey: bare keys stored as manufacturer barcodes that is_global_code no longer accepts (GS1 check digit,
    shared produce codes 729000000xxxx) move to per-chain keys '<chain>:<code>', as new files key them: prices and
    promo_items rows are copied (a newer row already under the new key wins) and removed, each chain gets its own
    products row (its own newest name when known), the old key's names, flags and history go, and its products row
    stays as an empty chain-scoped row (store_count 0, out of search) so carts holding it still show its name and
    the chain items with the same normalized name. u.cart_items is not touched. Live data: ~5.8k keys."""
    t = time.time()
    bad = [r[0] for r in con.execute("SELECT key FROM products WHERE scope='global'") if not is_global_code(r[0])]
    log.info("rekey: %d bare keys are not manufacturer barcodes", len(bad))
    if not bad:
        return 0
    with con:
        con.execute("DROP TABLE IF EXISTS temp.rk")
        con.execute("CREATE TEMP TABLE rk(key TEXT PRIMARY KEY) WITHOUT ROWID")
        con.executemany("INSERT INTO rk VALUES(?)", [(k,) for k in bad])
        pairs = {(r[0], r[1]) for r in con.execute(
            """SELECT DISTINCT key, chain_id FROM prices WHERE key IN (SELECT key FROM temp.rk)
               UNION SELECT DISTINCT key, chain_id FROM promo_items WHERE key IN (SELECT key FROM temp.rk)""")}
        own = {}
        for key, ch, name, ts in con.execute("""SELECT key, chain_id, name, ts FROM item_names
                                                WHERE key IN (SELECT key FROM temp.rk)"""):
            if (key, ch) not in own or ts > own[(key, ch)][1]:
                own[(key, ch)] = (name, ts)
        cols = ("code", "name", "manufacturer", "quantity", "unit", "is_weighted")
        old = {r[0]: dict(zip(cols, r[1:])) for r in con.execute(
            f"SELECT key, {', '.join(cols)} FROM products WHERE key IN (SELECT key FROM temp.rk)")}
        new_rows = []
        for key, ch in sorted(pairs):
            o = old.get(key) or {"code": key, "name": "", "manufacturer": "", "quantity": "", "unit": "",
                                 "is_weighted": 0}
            name = (own.get((key, ch)) or (o["name"] or "",))[0]
            new_rows.append((f"{ch}:{o['code'] or key}", o["code"] or key, name, o["manufacturer"] or "",
                             o["quantity"] or "", o["unit"] or "", o["is_weighted"] or 0, normalize_name(name)))
        con.executemany("""INSERT OR IGNORE INTO products(key, code, name, manufacturer, quantity, unit, is_weighted,
                           scope, norm) VALUES(?,?,?,?,?,?,?,'chain',?)""", new_rows)
        con.execute("""INSERT INTO prices(key, chain_id, store_id, price, ts)
                       SELECT chain_id || ':' || key, chain_id, store_id, price, ts FROM prices
                       WHERE key IN (SELECT key FROM temp.rk) AND true
                       ON CONFLICT(key, chain_id, store_id) DO UPDATE SET price=excluded.price, ts=excluded.ts
                       WHERE excluded.ts > prices.ts""")
        con.execute("DELETE FROM prices WHERE key IN (SELECT key FROM temp.rk)")
        con.execute("""INSERT INTO promo_items(key, chain_id, store_id, promo_id, ts)
                       SELECT chain_id || ':' || key, chain_id, store_id, promo_id, ts FROM promo_items
                       WHERE key IN (SELECT key FROM temp.rk) AND true
                       ON CONFLICT DO UPDATE SET ts=excluded.ts WHERE excluded.ts > promo_items.ts""")
        con.execute("DELETE FROM promo_items WHERE key IN (SELECT key FROM temp.rk)")
        for table in ("item_names", "name_flags", "price_flags", "price_history", "price_last"):
            con.execute(f"DELETE FROM {table} WHERE key IN (SELECT key FROM temp.rk)")
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deals'").fetchone():
            con.execute("DELETE FROM deals WHERE key IN (SELECT key FROM temp.rk)")
        con.executemany("""UPDATE products SET scope='chain', store_count=0, chain_count=0, min_price=NULL,
                           max_price=NULL, median=NULL, norm=? WHERE key=?""",
                        [(normalize_name(o["name"]), k) for k, o in old.items()])
        con.execute("DROP TABLE IF EXISTS temp.nk")
        con.execute("CREATE TEMP TABLE nk(key TEXT PRIMARY KEY) WITHOUT ROWID")
        con.executemany("INSERT OR IGNORE INTO nk VALUES(?)", [(r[0],) for r in new_rows])
        con.execute("""DELETE FROM products_fts WHERE key IN (SELECT key FROM temp.rk UNION ALL
                                                             SELECT key FROM temp.nk)""")
        con.execute("""INSERT INTO products_fts(key, name, manufacturer, code) SELECT key, name, manufacturer, code
                       FROM products WHERE key IN (SELECT key FROM temp.nk)""")
    log.info("rekey: %d keys -> %d chain keys in %.0fs", len(bad), len(pairs), time.time() - t)
    return len(bad)


def reread_promos(con):
    """--promos-again: forget which promo files were indexed, so this run re-reads every store's latest PromoFull
    and the Promo files after it (new columns and rules apply to deals already stored; index_promos drops rows the
    rules no longer keep). Live data: ~2.4k PromoFull files plus the deltas after them."""
    with con:
        n = con.execute("DELETE FROM files WHERE kind IN ('promofull', 'promo')").rowcount
    log.info("promos again: %d promo files will be read again", n)


def record_history(con, day=None, cutoff=None, through=None, pairs_ready=False):
    """Price history for every product: its cheapest and dearest confirmed shelf price at each chain, written as a
    row only on days it changes (today's row is rewritten until midnight). price_last holds the latest row per
    pair. A pair that stops being confirmed (flagged, stale store, no recent name) gets an end row (mn and mx NULL)
    and leaves price_last: readers end the series there; it starts again when the pair is confirmed again. A series
    that began today and ends today leaves no rows."""
    day = day or int(time.strftime("%Y%m%d"))
    t = time.time()
    if not pairs_ready:
        confirmed_pairs(con, trust.fresh_cutoff() if cutoff is None else cutoff,
                        confirmed_through(con) if through is None else through)
    con.execute("DROP TABLE IF EXISTS temp.chg")
    con.execute("""CREATE TEMP TABLE chg AS SELECT c.key, c.chain_id, c.mn, c.mx, c.n FROM cur c
                   LEFT JOIN price_last l ON l.key=c.key AND l.chain_id=c.chain_id
                   WHERE l.key IS NULL OR l.day = ? OR abs(l.mn - c.mn) >= 0.005 OR abs(l.mx - c.mx) >= 0.005""",
                (day,))
    n = con.execute("""INSERT OR REPLACE INTO price_history(key, chain_id, day, mn, mx, n)
                       SELECT key, chain_id, ?, mn, mx, n FROM chg""", (day,)).rowcount
    con.execute("INSERT OR REPLACE INTO price_last(key, chain_id, mn, mx, day) SELECT key, chain_id, mn, mx, ? FROM chg",
                (day,))
    ended = con.execute("""SELECT l.key, l.chain_id FROM price_last l
                           WHERE NOT EXISTS (SELECT 1 FROM cur c WHERE c.key=l.key AND c.chain_id=l.chain_id)""").fetchall()
    ended = [(r[0], r[1]) for r in ended]
    con.executemany("DELETE FROM price_history WHERE key=? AND chain_id=? AND day=?", [(k, c, day) for k, c in ended])
    marks = [(k, c, day) for k, c in ended
             if (con.execute("""SELECT mn IS NOT NULL FROM price_history WHERE key=? AND chain_id=? AND day<?
                                ORDER BY day DESC LIMIT 1""", (k, c, day)).fetchone() or (0,))[0]]
    con.executemany("INSERT INTO price_history(key, chain_id, day, mn, mx, n) VALUES(?,?,?,NULL,NULL,0)", marks)
    con.executemany("DELETE FROM price_last WHERE key=? AND chain_id=?", ended)
    con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('history_since', ?)", (str(day),))
    con.commit()
    log.info("price history: %d rows, %d series ended for %d in %.0fs", n, len(marks), day, time.time() - t)


def rebuild_history(con, since=None):
    """--rebuild-history [DAY]: drop history rows from DAY (default today) on, so the next record_history writes
    today's from confirmed prices only (rows written before the confirmed-only rules counted every price)."""
    today = int(time.strftime("%Y%m%d"))
    since = int(since or today)
    t = time.time()
    with con:
        n = con.execute("DELETE FROM price_history WHERE day >= ?", (since,)).rowcount
        con.execute("DELETE FROM price_last")
        con.execute("""INSERT INTO price_last(key, chain_id, mn, mx, day)
                       SELECT h.key, h.chain_id, h.mn, h.mx, h.day FROM price_history h
                       WHERE h.mn IS NOT NULL AND h.day = (SELECT max(x.day) FROM price_history x
                                                           WHERE x.key=h.key AND x.chain_id=h.chain_id)""")
        first = con.execute("SELECT min(day) FROM price_history").fetchone()[0]
        con.execute("INSERT INTO settings VALUES('history_since', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(first or today),))
    log.info("history: dropped %d rows from %d on (%.0fs)", n, since, time.time() - t)


def cleanup(con):
    """Drop rows superseded by a newer full file, at most once a day."""
    row = con.execute("SELECT value FROM settings WHERE key='last_cleanup'").fetchone()
    if row and time.time() - float(row[0]) < 20 * 3600:
        return
    log.info("cleaning superseded rows")
    con.execute("""DELETE FROM prices WHERE ts < (SELECT s.price_full_ts FROM stores s
                   WHERE s.chain_id=prices.chain_id AND s.store_id=prices.store_id)""")
    con.execute("""DELETE FROM promo_items WHERE ts < (SELECT s.promo_full_ts FROM stores s
                   WHERE s.chain_id=promo_items.chain_id AND s.store_id=promo_items.store_id)""")
    con.execute("""DELETE FROM promos WHERE ts < (SELECT s.promo_full_ts FROM stores s
                   WHERE s.chain_id=promos.chain_id AND s.store_id=promos.store_id)
                   OR (end_ts IS NOT NULL AND end_ts < ?)""", (int(time.strftime("%Y%m%d%H%M")),))
    # drop file records for files that no longer exist on disk
    gone = [r[0] for r in con.execute("SELECT path FROM files") if not os.path.exists(r[0])]
    con.executemany("DELETE FROM files WHERE path=?", [(p,) for p in gone])
    con.execute("INSERT INTO settings VALUES('last_cleanup', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(time.time()),))
    con.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dumps", default=os.environ.get("DUMPS_PATH", os.path.expanduser("~/il-prices/dumps")))
    ap.add_argument("--db", default=None)
    ap.add_argument("--only", help="comma-separated file kinds to index, e.g. promofull,promo")
    ap.add_argument("--confirm", action="store_true", help="rebuild flags, stats and history even with no new files")
    ap.add_argument("--rekey", action="store_true",
                    help="one-off: move bare keys that aren't manufacturer barcodes to per-chain keys")
    ap.add_argument("--names", action="store_true",
                    help="one-off: read shelf names from every store's current price files")
    ap.add_argument("--promos-again", action="store_true",
                    help="one-off: re-read every store's latest promo files")
    ap.add_argument("--rebuild-history", nargs="?", const="today", metavar="DAY",
                    help="one-off: drop price history from DAY (YYYYMMDD, default today) on and rebuild today's")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    since = None
    if a.rebuild_history:
        since = int(time.strftime("%Y%m%d")) if a.rebuild_history == "today" else int(a.rebuild_history)
    ok = run(a.dumps, a.db, only=set(a.only.split(",")) if a.only else None, confirm_always=a.confirm,
             rekey_keys=a.rekey, names=a.names, promos_again=a.promos_again, rebuild_since=since)
    if not ok:
        log.info("skipped: another index run holds the lock")


if __name__ == "__main__":
    main()
