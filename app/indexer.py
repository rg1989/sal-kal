"""Index the downloaded Price Transparency files into SQLite.

Reads every chain's Stores / PriceFull / Price / PromoFull / Promo files from
the downloader's dumps folder. The XML dialects differ per portal (Shufersal,
Cerberus, Bina, Super-Pharm "Line" files, SAP-style uppercase store files), so
parsing is tag-name driven and case-insensitive instead of per-chain.

Run:  python -m app.indexer [--dumps ~/il-prices/dumps]
"""
import argparse
import gzip
import io
import json
import logging
import os
import re
import sys
import time
import zipfile
from collections import defaultdict

from lxml import etree

from . import db as dbmod
from .chains import chain_name

log = logging.getLogger("indexer")

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
    if len(code.lstrip("0")) < 8:
        return False
    if len(code) == 13 and code[0] == "2":  # in-store / weighed barcode range
        return False
    return True


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


def index_prices(con, path, meta, scoped_cache):
    chain, store, ts = meta["chain"], meta["store"], meta["ts"]
    prices, products = [], []
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
    return len(prices)


def _promo_fields(el):
    d = descendants(el)
    codes = []
    clubs = []
    for c in el.iter():
        t = local(c.tag)
        if t == "clubid":
            clubs.append((c.text or "").strip())
        elif t == "item" or (t == "itemcode" and local(c.getparent().tag) != "item"):
            if t == "item":
                f = children(c)
                code, gift = f.get("itemcode"), f.get("isgiftitem", "0")
            else:
                code, gift = (c.text or "").strip(), d.get("isgiftitem", "0")
            if code and gift.strip() != "1":
                codes.append(code)
    return d, codes, clubs


def index_promos(con, path, meta, scoped_cache, now_int):
    chain, store, ts = meta["chain"], meta["store"], meta["ts"]
    promos, items = {}, []
    for el, _ in iter_records(path, {"promotion", "sale", "line"}, "promotionid"):
        d, codes, clubs = _promo_fields(el)
        pid = d.get("promotionid")
        if not pid or not codes:
            continue
        if d.get("additionaliscoupon", "0") == "1" or d.get("additionalisactive", "1") == "0":
            continue
        # "spend ₪X in total" deals aren't per-product prices (field is spelled both ways across chains)
        if any((num(d.get(k), 0) or 0) > 0 for k in ("minpurchaseamnt", "minpurchaseamount", "additionalminbasketamount")):
            continue
        if len(codes) > 2000:  # ponytail: store-wide conditions list every item; real product deals are far smaller
            continue
        end = date_int(d.get("promotionenddate"), d.get("promotionendhour"), end=True)
        if end and end < now_int:
            continue
        start = date_int(d.get("promotionstartdate"), d.get("promotionstarthour"))
        dp = num(d.get("discountedprice"))
        rate = num(d.get("discountrate"))
        if not dp and not rate:
            continue
        club_ids = [re.match(r"\d*", c).group(0) for c in clubs if c]
        club_only = 1 if club_ids and any(c not in ("", "0") for c in club_ids) else 0
        promos[pid] = (chain, store, pid, (d.get("promotiondescription") or "")[:200], start, end,
                       num(d.get("minqty"), 1) or 1, dp, rate, club_only, ts)
        for code in codes:
            weighted = code in scoped_cache[chain]
            key, _ = product_key(chain, code, weighted)
            items.append((key, chain, store, pid, ts))
    con.executemany(
        """INSERT INTO promos VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(chain_id, store_id, promo_id) DO UPDATE SET description=excluded.description,
             start_ts=excluded.start_ts, end_ts=excluded.end_ts, min_qty=excluded.min_qty,
             discounted_price=excluded.discounted_price, discount_rate=excluded.discount_rate,
             club_only=excluded.club_only, ts=excluded.ts""", list(promos.values()))
    con.executemany(
        """INSERT INTO promo_items VALUES(?,?,?,?,?)
           ON CONFLICT DO UPDATE SET ts=excluded.ts""", items)
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


def run(dumps, db_path=None, only=None):
    con = dbmod.connect(db_path)
    started = time.time()
    now_int = int(time.strftime("%Y%m%d%H%M"))
    set_status(con, running=True, started=int(started), error=None)
    done = {r["path"]: r["size"] for r in con.execute("SELECT path, size FROM files")}
    files = [f for f in scan(dumps) if done.get(f["path"]) != f["size"]]
    if only:  # partial pass (e.g. promos first); filtered before `skipped`, so other kinds stay unindexed
        files = [f for f in files if f["kind"] in only]
    state = {(r["chain_id"], r["store_id"]): (r["price_full_ts"], r["promo_full_ts"])
             for r in con.execute("SELECT chain_id, store_id, price_full_ts, promo_full_ts FROM stores")}
    scoped_cache = defaultdict(set)
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

    plan = list(stores_files.values())
    plan += sorted([f for f in latest_full.values() if f["kind"] == "pricefull"], key=lambda f: f["ts"])
    plan += sorted([f for f in files if f["kind"] == "price"], key=lambda f: f["ts"])
    plan += sorted([f for f in latest_full.values() if f["kind"] == "promofull"], key=lambda f: f["ts"])
    plan += sorted([f for f in files if f["kind"] == "promo"], key=lambda f: f["ts"])
    skipped = [f for f in files if f not in plan]

    log.info("%d new files, %d to index", len(files), len(plan))
    set_status(con, total=len(plan), processed=0)
    n_rows = 0
    for i, f in enumerate(plan):
        key = (f["chain"], f["store"])
        pf, mf = state.get(key, (0, 0))
        kind = f["kind"]
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
                    rows = index_prices(con, f["path"], f, scoped_cache)
                    con.execute("UPDATE stores SET price_full_ts=?, last_price_ts=max(last_price_ts, ?) WHERE chain_id=? AND store_id=?",
                                (f["ts"], f["ts"], *key))
                    state[key] = (f["ts"], mf)
            elif kind == "price":
                if f["ts"] <= pf:
                    rows = 0
                else:
                    rows = index_prices(con, f["path"], f, scoped_cache)
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
    if plan:
        log.info("updating product availability stats")
        con.execute("DROP TABLE IF EXISTS temp.agg")
        con.execute("""CREATE TEMP TABLE agg AS SELECT p.key, count(*) n, count(DISTINCT p.chain_id) c,
                       min(p.price) mn, max(p.price) mx FROM prices p JOIN stores s
                       ON s.chain_id=p.chain_id AND s.store_id=p.store_id WHERE p.ts >= s.price_full_ts
                       GROUP BY p.key""")
        con.execute("CREATE INDEX temp.agg_key ON agg(key)")
        con.execute("""UPDATE products SET store_count=coalesce((SELECT n FROM agg WHERE agg.key=products.key),0),
                       chain_count=coalesce((SELECT c FROM agg WHERE agg.key=products.key),0),
                       min_price=(SELECT mn FROM agg WHERE agg.key=products.key),
                       max_price=(SELECT mx FROM agg WHERE agg.key=products.key)""")
        con.commit()
    try:
        record_history(con)
    except Exception:
        log.exception("price history failed")
    set_status(con, running=False, finished=int(time.time()), processed=len(plan),
               rows=n_rows, seconds=int(time.time() - started))
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # fold the write-ahead log back in; it otherwise grows by GBs
    con.close()
    log.info("done: %d files, %d rows in %ds", len(plan), n_rows, time.time() - started)


def record_history(con, day=None):
    """Price history for every product: its cheapest and dearest current shelf price at each chain, written as a row
    only on days it changes (today's row is rewritten until midnight). price_last holds the latest row per pair."""
    day = day or int(time.strftime("%Y%m%d"))
    t = time.time()
    con.execute("DROP TABLE IF EXISTS temp.cur")
    con.execute("""CREATE TEMP TABLE cur AS SELECT p.key, p.chain_id, min(p.price) mn, max(p.price) mx, count(*) n
                   FROM prices p JOIN stores s ON s.chain_id=p.chain_id AND s.store_id=p.store_id
                   WHERE p.price > 0 AND p.ts >= s.price_full_ts GROUP BY p.key, p.chain_id""")
    n = con.execute("""INSERT OR REPLACE INTO price_history(key, chain_id, day, mn, mx, n)
                       SELECT c.key, c.chain_id, ?, c.mn, c.mx, c.n FROM cur c
                       LEFT JOIN price_last l ON l.key=c.key AND l.chain_id=c.chain_id
                       WHERE l.key IS NULL OR l.day = ? OR abs(l.mn - c.mn) >= 0.005 OR abs(l.mx - c.mx) >= 0.005""",
                    (day, day)).rowcount
    con.execute("""INSERT OR REPLACE INTO price_last(key, chain_id, mn, mx, day)
                   SELECT key, chain_id, mn, mx, day FROM price_history WHERE day = ?""", (day,))
    con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('history_since', ?)", (str(day),))
    con.commit()
    log.info("price history: %d rows for %d in %.0fs", n, day, time.time() - t)


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
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    run(a.dumps, a.db, only=set(a.only.split(",")) if a.only else None)


if __name__ == "__main__":
    main()
