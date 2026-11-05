"""Basket web app: JSON API + static UI.  Run: uvicorn app.server:app --host 0.0.0.0 --port 8080"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db as dbmod
from . import catalog, engine, geo, trust
from .images import PHOTO_DIR, photo_path, want as want_photos
from .chains import BRAND_HE, chain_name

app = FastAPI(title="Basket")
dbmod.connect().close()  # create/migrate the schema once at startup, before requests race to do it
STATIC = os.path.join(os.path.dirname(__file__), "static")
_local = threading.local()


def con():
    c = getattr(_local, "con", None)
    if c is None:
        c = _local.con = dbmod.connect()
    elif c.in_transaction:  # a failed request left a transaction open; it would block every later write
        c.rollback()
    return c


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
os.makedirs(PHOTO_DIR, exist_ok=True)
app.mount("/photos", StaticFiles(directory=PHOTO_DIR), name="photos")  # filled by `python -m app.images`


def _img(code):
    return f"/photos/{code}.webp" if code and code.isdigit() and os.path.exists(photo_path(code)) else None


# ---------------------------------------------------------------- status
@app.get("/api/status")
def status():
    c = con()
    st = engine.get_setting(c, "index_status", {})
    counts = c.execute("""SELECT (SELECT count(*) FROM products) products,
                                 (SELECT count(*) FROM stores WHERE price_full_ts > 0) stores,
                                 (SELECT count(DISTINCT chain_id) FROM stores WHERE price_full_ts > 0) chains,
                                 (SELECT max(last_price_ts) FROM stores) data_ts""").fetchone()
    return {"index": st, **dict(counts), "located": len(geo.store_coords(c))}


@app.post("/api/reindex")
def reindex():
    st = engine.get_setting(con(), "index_status", {})
    if st.get("running") and time.time() - st.get("started", 0) < 3 * 3600:
        return {"started": False, "reason": "already running"}
    root = os.path.dirname(os.path.dirname(__file__))
    subprocess.Popen([sys.executable, "-m", "app.indexer"], cwd=root,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"started": True}


# ---------------------------------------------------------------- products
def _tokens(q):
    return [t for t in re.split(r"[\s\"'״׳()*:^-]+", q) if t]


@app.get("/api/search")
def search(q: str, limit: int = 30):
    c = con()
    q = q.strip()
    if not q:
        return []
    if q.isdigit() and len(q) >= 4:
        rows = c.execute("""SELECT * FROM products WHERE code LIKE ? AND store_count > 0
                            ORDER BY chain_count DESC, store_count DESC LIMIT 200""", (q + "%",)).fetchall()
    else:
        toks = _tokens(q)
        if not toks:
            return []
        fts = lambda op: c.execute("""SELECT p.* FROM (SELECT key FROM products_fts WHERE products_fts MATCH ?
                              ORDER BY rank LIMIT 600) f JOIN products p ON p.key=f.key
                            WHERE p.store_count > 0
                            ORDER BY p.chain_count DESC, p.store_count DESC LIMIT 200""",
                                   (f" {op} ".join(f'"{t}"*' for t in toks),)).fetchall()
        # chain data often lacks the brand ("במבה אסם 80" → "במבה קלאסי 80 גר"): if nothing has
        # every word, fall back to whatever matches the most words
        rows = fts("AND") or (fts("OR") if len(toks) > 1 else [])
    return _present(c, rows, limit, _tokens(q) or [q])


def _comparable(key):
    from .indexer import is_global_code
    return bool(key) and ":" not in key and not re.fullmatch(r"729000000\d{4}", key) and is_global_code(key)


# A manufacturer barcode: the same product at every chain, so its prices can be set side by side. Chain codes
# (produce, deli, "<chain>:<code>") and bare codes that aren't real barcodes are each one chain's own item.
comparable = getattr(engine, "comparable", None) or _comparable


def _confirmed_through(c):
    """Newest price file time (YYYYMMDDHHMM) the indexer's confirm() has checked; None while it isn't tracked (then
    every price counts as checked)."""
    try:
        return int(engine.get_setting(c, "confirmed_through", 0) or 0) or None
    except (TypeError, ValueError):
        return None


def _named_sql(c):
    """SQL condition on prices `p`: a barcode's price counts only at a chain whose own name for it was checked against
    the other chains' (no name from that chain: nothing to check). Empty on an old database without names."""
    if not c.execute("SELECT 1 FROM item_names LIMIT 1").fetchone():
        return ""
    return ("AND (instr(p.key, ':') > 0 OR EXISTS (SELECT 1 FROM item_names n WHERE n.key=p.key AND n.chain_id=p.chain_id"
            f" AND n.ts >= {int(trust.names_cutoff())}))")


def _present(c, rows, limit, toks=None):
    """Search-style result list: one entry per weighed product (its chain's own item), best matches first."""
    names = {r["chain_id"]: r["name_he"] for r in c.execute("SELECT chain_id, name_he FROM chains")}
    out, groups = [], {}
    for r in rows:
        d = dict(r)
        if d["scope"] == "chain":
            # weighed produce has a different code at every chain: one card per name, priced at its own chain only;
            # chain_count says how many chains have an item of the same name (never priced together)
            if d["norm"] and len(d["norm"]) >= 2:
                if d["norm"] in groups:
                    continue
                groups[d["norm"]] = d
            cid = d["key"].split(":", 1)[0]
            d["chain"] = chain_name(cid, names.get(cid))
        elif not comparable(d["key"]):  # a code several chains use for different things: no price across chains
            d.update(min_price=None, max_price=None)
        d["img"] = _img(d["code"]) if d["scope"] == "global" else None
        out.append(d)
    if groups:
        ph = ",".join("?" * len(groups))
        brands = {}  # one brand can publish under two chain ids
        for norm, cid in c.execute(f"""SELECT DISTINCT norm, substr(key, 1, instr(key, ':')-1) FROM products
                                       WHERE scope='chain' AND store_count > 0 AND norm IN ({ph})""", list(groups)):
            brands.setdefault(norm, set()).add(chain_name(cid, names.get(cid)))
        for norm, d in groups.items():
            d["chain_count"] = max(len(brands.get(norm, ())), 1)
    # "ביצים" should find eggs before chocolate eggs: names that start with the first word win,
    # ignoring a leading pack size ("12 ביצים L"); then the most widely sold
    if toks:
        starts = lambda d: re.sub(r"^[\d\s.,%xX*]+", "", d["name"] or "").startswith(toks[0])
        hits = lambda d: sum(t in f'{d["name"]} {d["manufacturer"]} {d["code"]}' for t in toks)
        out.sort(key=lambda d: (-hits(d), not starts(d), -d["chain_count"], -d["store_count"]))
    else:
        out.sort(key=lambda d: (-d["chain_count"], -d["store_count"]))
    return out[:limit]


# ---------------------------------------------------------------- per card: cheapest near you + price per 100 g / litre
_near_cache = {}


def _near_stores(c):
    """Branches you'd walk into, with their distance: inside your distance limit (15 km without one), like the map.
    None when no home address is set. Cached for 10 minutes per address + limit."""
    prefs = engine.get_prefs(c)
    home, max_km = prefs.get("home"), prefs.get("max_km")
    if not home:
        return None
    k = (home["lat"], home["lon"], max_km, int(time.time() // 600))
    near = _near_cache.get(k)  # read once: another request may clear the cache
    if near is None:
        kind = {(r[0], r[1]): r[2] for r in c.execute("SELECT chain_id, store_id, store_type FROM stores WHERE price_full_ts > 0")}
        near = {}
        for sk, (lat, lon) in geo.store_coords(c).items():
            if sk in kind and kind[sk] != 2:  # online-only shops have no door to walk into
                d = geo.km(home["lat"], home["lon"], lat, lon)
                if d <= float(max_km or 15):
                    near[sk] = round(d, 1)
        _near_cache.clear()
        _near_cache[k] = near
    return near


def _per_measure(price, size):
    """Shelf-label style unit price: per 100 g for weight, per litre for liquids."""
    if not price or not size:
        return None
    n, u = size.split(" ")
    n = float(n)
    if u in ("g", "kg"):
        return {"v": round(price / (n * (1000 if u == "kg" else 1)) * 100, 2), "per": "100 g"}
    if u in ("ml", "L"):
        return {"v": round(price / (n * (1000 if u == "L" else 1)) * 1000, 2), "per": "L"}
    return None


def _pack(d):
    """The pack size to price per 100 g / litre: the chain's own fields first, since names carry other numbers
    ("PRO 20 גרם" is 20 g of protein in a 200 g pot). When the chain gave a number but no unit, the name's size
    counts only if it agrees with that number; a wrong unit price is worse than none."""
    fields = catalog.size_of("", d.get("quantity"), d.get("unit"))
    if fields or not d.get("size"):
        return fields
    try:
        q = float(d.get("quantity") or 0)
    except ValueError:
        q = 0
    n, u = d["size"].split(" ")
    n = float(n) * (1000 if u in ("kg", "L") else 1)
    return d["size"] if q <= 0 or any(abs(n - q * f) <= 0.01 * n + 0.5 for f in (1, 1000)) else None


def _enrich(c, items):
    """Adds to each product card, from confirmed shelf prices now (current store files, checked by the indexer, no
    outliers, no chain that names the barcode differently or not at all): `near` = the cheapest at a branch near you
    (chain + distance; the nearest of equally cheap ones), `min_price` / `max_price` = the range at every store
    (stats stored by the indexer can be out of date), `pack` = its size and `per` = its price per 100 g / litre.
    A chain's own item (produce, deli) is priced only at that chain, under its own code."""
    near = _near_stores(c) or {}
    keys = [d["key"] for d in items if ":" in d["key"] or comparable(d["key"])]
    if keys:
        names = {r[0]: r[1] for r in c.execute("SELECT chain_id, name_he FROM chains")}
        best, span, checked = {}, {}, _confirmed_through(c)
        ph = ",".join("?" * len(keys))
        named = {(r[0], r[1]) for r in c.execute(f"SELECT DISTINCT key, chain_id FROM item_names WHERE key IN ({ph})"
                                                 f" AND ts >= {int(trust.names_cutoff())}", keys)}
        check_names = bool(_named_sql(c))  # an old database without names: nothing to check against
        # prices newer than the last full check count when in line with the typical price from that check
        typical = dict(c.execute(f"SELECT key, median FROM products WHERE key IN ({ph})", keys))
        for k, chain, store, price, ts in c.execute(
                f"""SELECT p.key, p.chain_id, p.store_id, p.price, p.ts FROM prices p
                    JOIN stores s ON s.chain_id=p.chain_id AND s.store_id=p.store_id
                    WHERE p.key IN ({ph}) AND p.ts >= s.price_full_ts AND s.last_price_ts >= ? AND p.price > 0
                      AND NOT EXISTS (SELECT 1 FROM price_flags f WHERE f.key=p.key AND f.chain_id=p.chain_id AND f.store_id=p.store_id)
                      AND NOT EXISTS (SELECT 1 FROM name_flags nf WHERE nf.key=p.key AND nf.chain_id=p.chain_id)""",
                [*keys, trust.fresh_cutoff()]):
            if (checked and ts > checked and not engine._within(price, typical.get(k))) \
                    or (":" in k and chain != k.split(":", 1)[0]) \
                    or (check_names and ":" not in k and (k, chain) not in named):
                continue
            lo, hi = span.get(k, (price, price))
            span[k] = (min(lo, price), max(hi, price))
            km = near.get((chain, store))
            if km is not None and (k not in best or (price, km) < (best[k][0], best[k][2])):
                best[k] = (price, chain, km)
        for d in items:
            if d["key"] in keys:
                d["min_price"], d["max_price"] = span.get(d["key"], (None, None))
            b = best.get(d["key"])
            d["near"] = {"price": b[0], "chain": chain_name(b[1], names.get(b[1])), "km": b[2]} if b else None
    for d in items:
        d.setdefault("near", None)
        d["size"] = d.get("size") or catalog.size_of(d["name"], d.get("quantity"), d.get("unit"))
        d["pack"] = _pack(d)
        deal = d.get("deal")
        price = deal["unit_price"] if deal else (d.get("near") or {}).get("price") or d.get("min_price")
        d["per"] = None if d.get("is_weighted") else _per_measure(price, d["pack"])
    return items


# ---------------------------------------------------------------- shop: categories, deals, picks, photos
@app.get("/api/categories")
def categories():
    # duotone line icons: two masks the page paints in the theme's ink and the brand yellow
    have = lambda slug: os.path.exists(os.path.join(STATIC, "cat", f"{slug}-line.png"))
    return [{**catalog.public(cat), **({"line": f"/static/cat/{cat['slug']}-line.png",
                                        "accent": f"/static/cat/{cat['slug']}-accent.png"} if have(cat["slug"]) else {})}
            for cat in catalog.CATEGORIES]


@app.get("/api/category/{slug}")
def category(slug: str, limit: int = 60):
    cat = next((x for x in catalog.CATEGORIES if x["slug"] == slug), None)
    if not cat:
        raise HTTPException(404, "no such category")
    c = con()
    rows = c.execute("""SELECT p.* FROM (SELECT key FROM products_fts WHERE products_fts MATCH ? LIMIT 5000) f
                         JOIN products p ON p.key=f.key WHERE p.store_count > 0
                         ORDER BY p.chain_count DESC, p.store_count DESC LIMIT 300""", (catalog.fts_query(cat),)).fetchall()
    return {**catalog.public(cat), "products": _present(c, rows, limit)}


_deal_stores = {}


def _stale_deal_stores(c):
    """Stores with deals in the table whose price or deal files have gone out of date since it was built (it is built
    from current stores only, so this is usually empty while the index timer runs)."""
    v = engine.get_setting(c, "deals_version", 0)
    have = _deal_stores.get(v)
    if have is None:
        have = {(r[0], r[1]) for r in c.execute("SELECT DISTINCT chain_id, store_id FROM deals")}
        _deal_stores.clear()
        _deal_stores[v] = have
    cutoff = trust.fresh_cutoff()
    old = c.execute("SELECT chain_id, store_id FROM stores WHERE last_price_ts < ? OR promo_full_ts < ?", (cutoff, cutoff))
    return sorted(sk for sk in ((r[0], r[1]) for r in old) if sk in have)


def _live_deals(c, alias="d", stores=True):
    """Which stored deals can be shown right now, checked on every request (the deals table is rebuilt only after
    an index run): the deal has started and not ended, and (unless the caller checks stores itself) its store's
    prices are current. Returns (SQL condition on `alias`, its args, a signature of the stores part for caches).
    Out-of-date stores are named in the condition: on two million deal rows that beats joining stores."""
    now = int(time.strftime("%Y%m%d%H%M"))
    sql, args = (f"({alias}.end_ts IS NULL OR {alias}.end_ts >= ?) AND ({alias}.start_ts IS NULL OR {alias}.start_ts <= ?)",
                 [now, now])
    stale = _stale_deal_stores(c) if stores else []
    by_chain = {}
    for cid, sid in stale:
        by_chain.setdefault(cid, []).append(sid)
    conds = []
    for cid, ids in by_chain.items():
        conds.append(f"({alias}.chain_id=? AND {alias}.store_id IN ({','.join('?' * len(ids))}))")
        args += [cid, *ids]
    if conds:
        sql += f" AND NOT ({' OR '.join(conds)})"
    return sql, args, tuple(map(tuple, stale))


def _cache_ok(entry):
    """A cached deal list stays right until the first of its deals ends."""
    return entry is not None and int(time.strftime("%Y%m%d%H%M")) <= entry[0]


_deal_pct = {}


def deal_pct(c, club):
    """product key -> best saving % among deals live now, for the "On deal" filter and sorting."""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='deals'").fetchone():
        return {}
    live, args, sig = _live_deals(c)
    k = (engine.get_setting(c, "deals_version", 0), bool(club), sig)
    hit = _deal_pct.get(k)
    if not _cache_ok(hit):
        where = live + ("" if club else " AND d.description NOT LIKE '%מועדון%'")
        pct, until = {}, 999912312359
        for key, save, end in c.execute(f"SELECT key, max(save_pct), end_ts FROM deals d WHERE {where} GROUP BY key", args):
            if comparable(key):
                pct[key] = save
                until = min(until, end or until)
        hit = (until, pct)
        _deal_pct.clear()
        _deal_pct[k] = hit
    return hit[1]


@app.get("/api/browse")
def browse(terms: str = "", cat: str = "", size: str = "", deal: int = 0, sort: str = "", limit: int = 120):
    """Results for stacked filters: every word in `terms` (|-separated) AND the category must match."""
    c = con()
    words = [t.strip() for t in terms.split("|") if t.strip()]
    toks = [tok for w in words for tok in _tokens(w)]
    cdef = next((x for x in catalog.CATEGORIES if x["slug"] == cat), None)
    empty = {"total": 0, "products": [], "refine": [], "sizes": [], "deal_n": 0}
    if not toks and not cdef:
        return empty
    parts = (["(" + catalog.fts_query(cdef) + ")"] if cdef else []) + \
            (["(" + " AND ".join('"' + t.replace('"', '""') + '"*' for t in toks) + ")"] if toks else [])
    sql = """SELECT p.* FROM products_fts f JOIN products p ON p.key=f.key WHERE products_fts MATCH ? AND p.store_count > 0
             ORDER BY p.chain_count DESC, p.store_count DESC LIMIT 1500"""
    if not cdef and len(words) == 1 and words[0].isdigit() and len(words[0]) >= 4:  # a barcode (or its start)
        rows = c.execute("""SELECT * FROM products WHERE code LIKE ? AND store_count > 0
                            ORDER BY chain_count DESC, store_count DESC LIMIT 200""", (words[0] + "%",)).fetchall()
    else:
        rows = c.execute(sql, (" AND ".join(parts),)).fetchall()
    if not rows and len(words) == 1 and len(toks) > 1 and not cdef:  # one typed phrase: best partial match
        rows = c.execute(sql, (" OR ".join('"' + t.replace('"', '""') + '"*' for t in toks),)).fetchall()
    items = _present(c, rows, 1500, toks or None)
    pct = deal_pct(c, engine.get_prefs(c).get("club"))
    for d in items:
        d["size"] = catalog.size_of(d["name"], d["quantity"], d["unit"])
        d["save"] = pct.get(d["key"])
    sizes = [x for x in size.split("|") if x]
    size_ok = lambda d: not sizes or d["size"] in sizes
    deal_ok = lambda d: not deal or d["save"]
    shown = [d for d in items if size_ok(d) and deal_ok(d)]
    size_counts = Counter(d["size"] for d in items if deal_ok(d) and d["size"])
    if sort == "price":
        shown.sort(key=lambda d: (d["min_price"] is None, d["min_price"] or 0))
    elif sort == "stores":
        shown.sort(key=lambda d: -d["store_count"])
    elif sort == "saving":
        shown.sort(key=lambda d: -(d["save"] or 0))
    return {"total": len(shown), "more": len(rows) >= 1500, "products": _enrich(c, shown[:limit]),
            "refine": catalog.refine_words(shown, toks),
            "sizes": [{"size": k, "n": n} for k, n in size_counts.most_common(14)] if len(size_counts) > 1 or sizes else [],
            "deal_n": sum(1 for d in items if d["save"] and size_ok(d))}


_classics = {}


@app.get("/api/classics")
def classics():
    c = con()
    ver = c.execute("SELECT max(last_price_ts) FROM stores").fetchone()[0]
    if ver not in _classics:
        out = []
        for q in catalog.CLASSICS:
            hit = search(q, limit=1)
            if hit and hit[0]["key"] not in {o["key"] for o in out}:
                out.append(hit[0])
        _classics.clear()
        _classics[ver] = out
    return _classics[ver]


@app.get("/api/picks")
def picks(limit: int = 12):
    """What this household adds to its carts most (and most recently)."""
    c = con()
    rows = c.execute("""SELECT p.*, count(*) n, max(ca.updated) last FROM cart_items i
                         JOIN carts ca ON ca.id=i.cart_id JOIN products p ON p.key=i.key
                         WHERE p.store_count > 0 GROUP BY i.key ORDER BY n DESC, last DESC LIMIT ?""", (limit,)).fetchall()
    return _enrich(c, _present(c, rows, limit))


@app.get("/api/cards")
def cards(keys: str = ""):
    """Cards for these products, in this order, with today's confirmed prices (recently viewed: the browser keeps
    only which products, never their prices)."""
    want = [k for k in dict.fromkeys(keys.split(",")) if k][:24]
    if not want:
        return []
    c = con()
    rows = c.execute(f"SELECT * FROM products WHERE key IN ({','.join('?' * len(want))}) AND store_count > 0", want).fetchall()
    order = {k: i for i, k in enumerate(want)}
    return _enrich(c, sorted(_present(c, rows, len(want)), key=lambda d: order.get(d["key"], len(want))))


_deals_cache = {}


@app.get("/api/deals")
def deals(kind: str = "biggest", keys: str = "", limit: int = 12, skip_basket: int = 0):
    """Real savings vs the product's typical price, at stores within the user's distance.
    kind: biggest | new | ending | mine (mine = only `keys`, e.g. the cart and recently viewed).
    skip_basket: leave out products already in a cart (the home page is for finding new things)."""
    c = con()
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='deals'").fetchone():
        return []
    prefs = engine.get_prefs(c)
    home, max_km = prefs.get("home"), prefs.get("max_km")
    want = [k for k in keys.split(",") if k][:100]
    if kind == "basket":  # the basket = every product in any of the household's carts
        want = [r[0] for r in c.execute("SELECT DISTINCT key FROM cart_items")][:300]
        kind = "mine"
    basket = tuple(sorted(r[0] for r in c.execute("SELECT DISTINCT key FROM cart_items"))) if skip_basket else ()
    nearby = bool(home and max_km)  # the nearby stores are joined below, current ones only
    live, live_args, sig = _live_deals(c, stores=not nearby)
    checked, cutoff = _confirmed_through(c), trust.fresh_cutoff()
    fresh = {(r[0], r[1]) for r in c.execute("SELECT chain_id, store_id FROM stores WHERE last_price_ts >= ? AND promo_full_ts >= ?",
                                              (cutoff, cutoff))}
    ck = (engine.get_setting(c, "deals_version", 0), kind, json.dumps(home), max_km, tuple(want), limit, sig, checked,
          hash(frozenset(fresh)) if nearby else 0, basket)
    hit = _deals_cache.get(ck)  # (deals stay right until, cards); read once: other threads may clear the cache
    if _cache_ok(hit):
        return hit[1]
    coords = geo.store_coords(c) if home else {}
    dist = lambda sk: geo.km(home["lat"], home["lon"], *coords[sk]) if sk in coords else None
    join, where, params = "", [live], list(live_args)
    if nearby:
        c.execute("CREATE TEMP TABLE IF NOT EXISTS near(chain_id TEXT, store_id INTEGER, PRIMARY KEY(chain_id, store_id))")
        c.execute("DELETE FROM near")
        c.executemany("INSERT INTO near VALUES(?,?)", [sk for sk in coords if sk in fresh and dist(sk) <= float(max_km)])
        join = "JOIN near n ON n.chain_id=d.chain_id AND n.store_id=d.store_id"
    if not prefs.get("club"):
        where.append("d.description NOT LIKE '%מועדון%'")
    if kind == "mine":
        if not want:
            return []
        where.append(f"d.key IN ({','.join('?' * len(want))})")
        params += want
    if kind == "new":  # started in the last week
        where.append("d.start_ts >= ?")
        params.append(int(time.strftime("%Y%m%d0000", time.localtime(time.time() - 7 * 86400))))
    if kind == "ending":  # last days: ends today, tomorrow or the day after
        where.append("d.end_ts IS NOT NULL AND d.end_ts <= ?")
        params.append(int(time.strftime("%Y%m%d2359", time.localtime(time.time() + 2 * 86400))))
    if basket:
        where.append("d.key NOT IN (SELECT key FROM cart_items)")
    # everyday products first: savings weighted by how widely the product is sold
    weighted = "g.save_pct * (0.5 + 0.5 * min(p.store_count, 100) / 100.0) DESC"
    order = ("g.start_ts DESC, g.save_pct DESC" if kind == "new"
             else f"g.end_ts / 10000, {weighted}" if kind == "ending"  # the day it ends, then the saving
             else weighted)
    rows = c.execute(f"""SELECT g.*, p.name, p.manufacturer, p.quantity, p.unit, p.is_weighted, p.chain_count,
                                p.store_count, p.min_price, p.max_price, p.scope, p.code FROM (
                           SELECT d.key, min(d.unit_price) unit_price, d.chain_id, d.store_id, d.price, d.typical,
                                  d.save_pct, d.min_qty, d.description, d.start_ts, d.end_ts FROM deals d {join}
                           WHERE {" AND ".join(where)} GROUP BY d.key) g
                         JOIN products p ON p.key=g.key {"" if kind == "mine" else "WHERE p.chain_count >= 3"}
                         ORDER BY {order} LIMIT ?""", params + [limit * 4]).fetchall()
    rows = [r for r in rows if comparable(r["key"])]
    # the shelf price the deal was worked out from must still be the store's confirmed price now
    now_price = {}
    if rows:
        ph = ",".join("(?,?,?)" for _ in rows)
        for k, cid, sid, price, ts in c.execute(
                f"""WITH want(key, chain_id, store_id) AS (VALUES {ph})
                    SELECT p.key, p.chain_id, p.store_id, p.price, p.ts FROM want w
                    JOIN prices p ON p.key=w.key AND p.chain_id=w.chain_id AND p.store_id=w.store_id
                    JOIN stores s ON s.chain_id=p.chain_id AND s.store_id=p.store_id
                    WHERE p.ts >= s.price_full_ts AND s.last_price_ts >= ? {_named_sql(c)}
                      AND NOT EXISTS (SELECT 1 FROM price_flags f WHERE f.key=p.key AND f.chain_id=p.chain_id AND f.store_id=p.store_id)
                      AND NOT EXISTS (SELECT 1 FROM name_flags nf WHERE nf.key=p.key AND nf.chain_id=p.chain_id)""",
                [x for r in rows for x in (r["key"], r["chain_id"], r["store_id"])] + [cutoff]):
            if not checked or ts <= checked:
                now_price[(k, cid, sid)] = price
    rows = [r for r in rows if abs((now_price.get((r["key"], r["chain_id"], r["store_id"])) or -1) - r["price"]) < 0.005]
    seen, uniq = set(), []
    for r in rows:  # one card per deal: "3 for ₪10.90" covering five crisp flavours shows once
        k = (r["chain_id"], r["description"]) if kind != "mine" else r["key"]
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    rows = uniq[:limit]
    names = {r["chain_id"]: r["name_he"] for r in c.execute("SELECT chain_id, name_he FROM chains")}
    out = []
    for r in rows:
        st = c.execute("SELECT name, city FROM stores WHERE chain_id=? AND store_id=?", (r["chain_id"], r["store_id"])).fetchone()
        d = dist((r["chain_id"], r["store_id"])) if home else None
        out.append({k: r[k] for k in ("key", "name", "manufacturer", "quantity", "unit", "is_weighted", "chain_count",
                                      "store_count", "min_price", "max_price", "scope", "code")}
                   | {"img": _img(r["code"])}
                   | {"deal": {"chain": chain_name(r["chain_id"], names.get(r["chain_id"])), "chain_id": r["chain_id"],
                               "store_id": r["store_id"], "store": st["name"] if st else "", "city": st["city"] if st else "",
                               "distance_km": round(d, 1) if d is not None else None, "price": r["price"],
                               "unit_price": r["unit_price"], "typical": r["typical"], "save_pct": r["save_pct"],
                               "min_qty": r["min_qty"], "description": r["description"], "start_ts": r["start_ts"],
                               "end_ts": r["end_ts"]}})
    _enrich(c, out)
    if len(_deals_cache) > 64:
        _deals_cache.clear()
    _deals_cache[ck] = (min([r["end_ts"] for r in rows if r["end_ts"]] or [999912312359]), out)
    return out


@app.get("/api/product")
def product(key: str):
    row = con().execute("SELECT * FROM products WHERE key=?", (key,)).fetchone()
    if not row:
        raise HTTPException(404, "product not found")
    if row["scope"] == "global" and not _img(row["code"]):
        want_photos([row["code"]])  # fetched in the background; it shows on the next look
    return {**dict(row), "img": _img(row["code"]) if row["scope"] == "global" else None}


@app.get("/api/stores/near")
def stores_near(limit: int = 600):
    """Branches with prices around the user's home, for the map: inside the distance limit,
    or the nearest ones when there is no limit."""
    c = con()
    prefs = engine.get_prefs(c)
    home, max_km = prefs.get("home"), prefs.get("max_km")
    if not home:
        return {"home": None, "max_km": None, "stores": []}
    names = {r["chain_id"]: r["name_he"] for r in c.execute("SELECT chain_id, name_he FROM chains")}
    info = {(r["chain_id"], r["store_id"]): r for r in c.execute(
        "SELECT chain_id, store_id, name, city, store_type FROM stores WHERE price_full_ts > 0")}
    near = []
    for sk, (lat, lon) in geo.store_coords(c).items():
        if sk not in info or info[sk]["store_type"] == 2:  # online-only shops have no door to walk into
            continue
        d = geo.km(home["lat"], home["lon"], lat, lon)
        if d <= float(max_km or 15):
            near.append((d, sk, lat, lon))
    near.sort()
    return {"home": home, "max_km": max_km, "total": len(near),
            "stores": [{"lat": lat, "lon": lon, "km": round(d, 1), "chain": chain_name(sk[0], names.get(sk[0])),
                        "name": info[sk]["name"] or "", "city": info[sk]["city"] or ""}
                       for d, sk, lat, lon in near[:limit]]}


@app.get("/api/geocode")
def geocode(q: str):
    """Look up the user's address (OpenStreetMap). Only the chosen result is stored, in prefs."""
    q = q.strip()
    if len(q) < 3:
        return []
    try:
        hits = geo.lookup(q, limit=5)
    except Exception:
        raise HTTPException(502, "Address lookup is unavailable right now")
    return [{"label": geo.label(h), "lat": float(h["lat"]), "lon": float(h["lon"])}
            for h in hits]


@app.post("/api/drive")
def drive(body: dict = Body(...)):
    """Drive minutes from body.home to the listed stores: {"chain:store": minutes}."""
    home = body.get("home") or {}
    if "lat" not in home:
        return {"times": {}, "live": False}
    c = con()
    coords = geo.store_coords(c)
    want = {(str(cid), int(sid)) for cid, sid in (body.get("stores") or [])[:60]}
    pts = {k: coords[k] for k in want if k in coords}
    key = engine.get_setting(c, "maps_key", None)
    mins = geo.drive_minutes((float(home["lat"]), float(home["lon"])), list(pts.values()), key)
    return {"times": {f"{k[0]}:{k[1]}": mins.get(p) for k, p in pts.items()}, "live": bool(key)}


# ---------------------------------------------------------------- price history (every product, every chain, daily)
def _since(c):
    r = c.execute("SELECT value FROM settings WHERE key='history_since'").fetchone()
    return int(r[0]) if r else None


def _flagged(c, keys, chains=None):
    """(key, chain) pairs a chain currently names differently: another product under the same barcode."""
    if not keys:
        return set()
    sql = f"SELECT DISTINCT key, chain_id FROM name_flags WHERE key IN ({','.join('?' * len(keys))})"
    return {(k, cid) for k, cid in c.execute(sql, list(keys)) if chains is None or cid in chains}


def _series(rows):
    """Change-only history rows [[day, mn, mx], ...] -> (points with a price, days a series ended). An end-marker row
    (mn and mx NULL) means the chain had no confirmed price from that day on, until its next row."""
    pts, ends = [], []
    for day, mn, mx in rows:
        if mn is None:
            if pts and (not ends or ends[-1] < pts[-1][0]):
                ends.append(day)
        else:
            pts.append([day, mn, mx])
    return pts, ends


@app.get("/api/history")
def product_history(key: str):
    """A product's price over time at each chain: points [day, cheapest branch, dearest branch] on the days it changed,
    and `ends`, the days a chain stopped having a confirmed price (a gap until its next point; `ended` when it hasn't
    come back). Only for a product that is the same everywhere (a barcode) or one chain's own item."""
    c = con()
    out = {"since": _since(c), "today": int(time.strftime("%Y%m%d")), "chains": []}
    if ":" not in key and not comparable(key):
        return out
    names = {r[0]: r[1] for r in c.execute("SELECT chain_id, name_he FROM chains")}
    rows, flagged = {}, _flagged(c, [key])
    for cid, day, mn, mx in c.execute("SELECT chain_id, day, mn, mx FROM price_history WHERE key=? ORDER BY day", (key,)):
        if (key, cid) not in flagged and (":" not in key or cid == key.split(":", 1)[0]):
            rows.setdefault(cid, []).append((day, mn, mx))
    by_name = {}  # one line per brand (a few brands publish under two chain ids): a current one, else the cheapest
    for cid, r in rows.items():
        pts, ends = _series(r)
        if not pts:
            continue
        s = {"chain_id": cid, "chain": chain_name(cid, names.get(cid)), "points": pts, "ends": ends,
             "ended": bool(ends) and ends[-1] > pts[-1][0]}
        b = by_name.get(s["chain"])
        if b is None or (s["ended"], pts[-1][1]) < (b["ended"], b["points"][-1][1]):
            by_name[s["chain"]] = s
    out["chains"] = list(by_name.values())
    return out


def _at(points, day):
    """The price on `day` from change-only points: the last change on or before it (the first one before history).
    Not used for anything shown: it back-fills days before the first price. See _on."""
    v = points[0][1]
    for d, p in points:
        if d > day:
            break
        v = p
    return v


def _on(points, day):
    """The price on `day` from change-only points [[day, price or None], ...]: the last change on or before it.
    None before the first one and after an end marker: there was no confirmed price that day. Never a later price."""
    v = None
    for d, p in points:
        if d > day:
            break
        v = p
    return v


@app.post("/api/history/cart")
def cart_history(body: dict = Body(...)):
    """What a cart's barcode products cost at each chain over time (each at the chain's cheapest branch that day), and
    which products changed price there in the last 30 days. A chain has a point on a day only when every one of them
    had a confirmed price there that day; `ends` lists the days its cart stopped being fully priced."""
    c = con()
    qty = {i["key"]: float(i.get("qty") or 1) for i in body.get("items") or []}
    chains = [str(x) for x in (body.get("chains") or [])][:8]
    if not qty or not chains:
        return {"since": _since(c), "series": {}, "ends": {}, "changes": {}}
    flagged = _flagged(c, list(qty), set(chains))
    pts = {}
    for k, cid, day, mn in c.execute(
            f"""SELECT key, chain_id, day, mn FROM price_history WHERE key IN ({','.join('?' * len(qty))})
                AND chain_id IN ({','.join('?' * len(chains))}) ORDER BY day""", [*qty, *chains]):
        if (k, cid) not in flagged and (":" in k or comparable(k)) and (":" not in k or cid == k.split(":", 1)[0]):
            pts.setdefault(cid, {}).setdefault(k, []).append([day, mn])
    today = int(time.strftime("%Y%m%d"))
    month_ago = int(time.strftime("%Y%m%d", time.localtime(time.time() - 30 * 86400)))
    need = [k for k in qty if comparable(k)]
    series, ends, changes = {}, {}, {}
    for cid, items in pts.items():
        moved = {}
        for k, p in items.items():
            then, now = _on(p, month_ago), _on(p, today)
            if then is not None and now is not None and abs(now - then) >= 0.005:
                moved[k] = [then, now]
        if moved:
            changes[cid] = moved
        # the total covers what the comparison totals: barcode products (produce is never compared), all of them
        if not need or any(k not in items for k in need):  # one never had a confirmed price here: no cart total
            continue
        line, gaps = [], []
        for d in sorted({d for k in need for d, _ in items[k]}):
            vals = [_on(items[k], d) for k in need]
            if any(v is None for v in vals):
                if line and (not gaps or gaps[-1] < line[-1][0]):
                    gaps.append(d)
            else:
                total = round(sum(qty[k] * v for k, v in zip(need, vals)), 2)
                if not line or gaps and gaps[-1] > line[-1][0] or abs(line[-1][1] - total) >= 0.005:
                    line.append([d, total])
        if line:
            series[cid] = line
            if gaps:
                ends[cid] = gaps
    return {"since": _since(c), "series": series, "ends": ends, "changes": changes}


# ---------------------------------------------------------------- one store: address, opening hours, route from home
HOURS_FILE = os.path.join(os.path.dirname(PHOTO_DIR), "hours.json")
_area, _area_retry, _area_lock = None, 0, threading.Lock()


def _osm_area(home, max_km):
    """Every food shop with opening hours around home (OpenStreetMap), fetched in one query and kept in hours.json
    for two weeks; refetched when the address or distance changes. After a failure, waits 10 minutes."""
    global _area, _area_retry
    with _area_lock:
        if _area is None:
            try:
                with open(HOURS_FILE) as f:
                    _area = json.load(f)
            except (OSError, ValueError):
                _area = {}
        key = [round(home["lat"], 3), round(home["lon"], 3), max_km]
        fresh = _area.get("key") == key and _area.get("until", 0) > time.time()
        if not fresh and time.time() > _area_retry:
            try:
                shops = geo.osm_shops(home["lat"], home["lon"], (float(max_km or 15) + 1) * 1000)
                _area = {"key": key, "until": time.time() + 14 * 86400, "shops": shops}
                tmp = HOURS_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(_area, f, ensure_ascii=False)
                os.replace(tmp, HOURS_FILE)
            except Exception as e:
                print("opening hours unavailable:", e, file=sys.stderr)
                _area_retry = time.time() + 600
        return _area.get("shops") or [], _area.get("key") == key


def _store_row(c, chain_id, store_id):
    s = c.execute("SELECT name, address, city FROM stores WHERE chain_id=? AND store_id=?", (chain_id, store_id)).fetchone()
    if not s:
        raise HTTPException(404, "store not found")
    he = c.execute("SELECT name_he FROM chains WHERE chain_id=?", (chain_id,)).fetchone()
    return s, chain_name(chain_id, he[0] if he else None), (he[0] if he else "")


@app.get("/api/store/{chain_id}/{store_id}")
def store_info(chain_id: str, store_id: int):
    """Address and the driving route from home, for the store card under the map."""
    c = con()
    s, chain, he = _store_row(c, chain_id, store_id)
    city = (s["city"] or "").strip()
    if city.isdigit():  # often a CBS town code
        t = c.execute("SELECT name FROM towns WHERE code=?", (int(city),)).fetchone() if geo._has_towns(c) else None
        city = t[0] if t else ""
    out = {"chain": chain, "brand_he": BRAND_HE.get(chain) or he, "name": s["name"] or "",
           "address": (s["address"] or "").strip(), "city": city, "lat": None, "lon": None, "route": None}
    ll = geo.store_coords(c).get((chain_id, store_id))
    if ll:
        out["lat"], out["lon"] = ll
        home = engine.get_prefs(c).get("home")
        if home:
            try:
                out["route"] = geo.route((home["lat"], home["lon"]), ll)
            except Exception as e:
                print("route unavailable:", e, file=sys.stderr)
    return out


@app.get("/api/store/{chain_id}/{store_id}/hours")
def store_hours(chain_id: str, store_id: int):
    """Opening hours from OpenStreetMap, when someone has mapped them for this branch."""
    c = con()
    _, chain, he = _store_row(c, chain_id, store_id)
    prefs = engine.get_prefs(c)
    ll = geo.store_coords(c).get((chain_id, store_id))
    if not ll or not prefs.get("home"):
        return {"hours": None, "week": None, "checked": False}
    shops, ok = _osm_area(prefs["home"], prefs.get("max_km"))
    hours, name = geo.match_hours(shops, ll[0], ll[1], [chain, BRAND_HE.get(chain, ""), he])
    return {"hours": hours, "week": geo.parse_hours(hours), "name": name, "checked": ok}


# ---------------------------------------------------------------- compare
@app.post("/api/compare")
def compare(body: dict = Body(...)):
    c = con()
    prefs = {**engine.get_prefs(c), **(body.get("prefs") or {})}
    res = engine.compare(c, body.get("items") or [], prefs)
    for it in res["items"]:
        it["img"] = _img(it["key"])
    return res


# ---------------------------------------------------------------- settings
@app.get("/api/settings")
def get_settings():
    c = con()
    chains = c.execute("""SELECT DISTINCT s.chain_id, ch.name_he FROM stores s LEFT JOIN chains ch
                          ON ch.chain_id=s.chain_id WHERE s.price_full_ts > 0""").fetchall()
    table = engine.delivery_table(c)
    seen, rows = set(), []
    for r in chains:
        name = chain_name(r["chain_id"], r["name_he"])
        if name in seen:
            continue
        seen.add(name)
        rows.append({"chain_id": r["chain_id"], "name": name, **table.get(r["chain_id"], {})})
    rows.sort(key=lambda r: (r.get("fee") is None, r["name"]))
    return {"prefs": engine.get_prefs(c), "delivery": rows, "traffic": bool(engine.get_setting(c, "maps_key", None))}


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    c = con()
    if "prefs" in body:
        engine.put_setting(c, "prefs", {**engine.get_prefs(c), **body["prefs"]})
    if "maps_key" in body:  # write-only: GET only says whether one is set
        engine.put_setting(c, "maps_key", (body["maps_key"] or "").strip() or None)
    if "delivery" in body:
        cur = engine.get_setting(c, "delivery", {})
        for cid, rule in body["delivery"].items():
            clean = {k: rule.get(k) for k in ("fee", "reduced_fee", "reduced_above", "free_above", "min_order", "delivers",
                                                    "delivers_here")
                     if k in rule}
            if rule.get("reset"):
                cur.pop(cid, None)
            else:
                cur[cid] = clean
        engine.put_setting(c, "delivery", cur)
    return get_settings()


# ---------------------------------------------------------------- carts
def _cart(c, cid):
    row = c.execute("SELECT * FROM carts WHERE id=?", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "cart not found")
    return {**dict(row), "items": engine.cart_items(c, cid)}


@app.get("/api/carts")
def list_carts():
    c = con()
    rows = c.execute("""SELECT c.*, count(i.key) n FROM carts c LEFT JOIN cart_items i ON i.cart_id=c.id
                        GROUP BY c.id ORDER BY c.updated DESC""").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/carts")
def create_cart(body: dict = Body(default={})):
    c = con()
    now = int(time.time())
    cur = c.execute("INSERT INTO carts(name, created, updated) VALUES(?,?,?)",
                    (body.get("name") or "New basket", now, now))
    cid = cur.lastrowid
    _write_items(c, cid, body.get("items") or [])
    c.commit()
    return _cart(c, cid)


def _write_items(c, cid, items):
    want_photos([i["key"] for i in items if ":" not in (i.get("key") or "")])  # cart products' photos first
    c.execute("DELETE FROM cart_items WHERE cart_id=?", (cid,))
    c.executemany("INSERT OR REPLACE INTO cart_items(cart_id, key, qty, name, position) VALUES(?,?,?,?,?)",
                  [(cid, i["key"], float(i.get("qty") or 1), i.get("name") or "", n) for n, i in enumerate(items)])


@app.get("/api/carts/{cid}")
def get_cart(cid: int):
    return _cart(con(), cid)


@app.put("/api/carts/{cid}")
def update_cart(cid: int, body: dict = Body(...)):
    c = con()
    _cart(c, cid)
    if "name" in body:
        c.execute("UPDATE carts SET name=? WHERE id=?", ((body["name"] or "Untitled").strip()[:80], cid))
    if "items" in body:
        _write_items(c, cid, body["items"])
    c.execute("UPDATE carts SET updated=? WHERE id=?", (int(time.time()), cid))
    c.commit()
    return _cart(c, cid)


@app.delete("/api/carts/{cid}")
def delete_cart(cid: int):
    c = con()
    for t, col in (("cart_items", "cart_id"), ("carts", "id")):
        c.execute(f"DELETE FROM {t} WHERE {col}=?", (cid,))
    c.commit()
    return {"ok": True}


@app.post("/api/carts/{cid}/duplicate")
def duplicate_cart(cid: int):
    c = con()
    src = _cart(c, cid)
    return create_cart({"name": f"{src['name']} (copy)", "items": src["items"]})
