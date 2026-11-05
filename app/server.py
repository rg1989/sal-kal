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
from .images import PHOTO_DIR, photo_path
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


def _present(c, rows, limit, toks=None):
    """Search-style result list: one entry per weighed product across chains, best matches first."""
    names = {r["chain_id"]: r["name_he"] for r in c.execute("SELECT chain_id, name_he FROM chains")}
    out, groups = [], {}
    for r in rows:
        d = dict(r)
        if d["scope"] == "chain":
            # weighed produce has a different code at every chain: show one entry, priced across all of them
            if d["norm"] and len(d["norm"]) >= 2:
                if d["norm"] in groups:
                    continue
                groups[d["norm"]] = d
            cid = d["key"].split(":", 1)[0]
            d["chain"] = chain_name(cid, names.get(cid))
        d["img"] = _img(d["code"]) if d["scope"] == "global" else None
        out.append(d)
    if groups:
        ph = ",".join("?" * len(groups))
        for g in c.execute(f"""SELECT norm, count(DISTINCT substr(key, 1, instr(key, ':')-1)) chains, sum(store_count) stores,
                                      min(min_price) mn, max(max_price) mx FROM products
                               WHERE scope='chain' AND store_count > 0 AND norm IN ({ph}) GROUP BY norm""", list(groups)):
            groups[g["norm"]].update(chain_count=g["chains"], store_count=g["stores"], min_price=g["mn"], max_price=g["mx"])
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
    if k not in _near_cache:
        _near_cache.clear()
        kind = {(r[0], r[1]): r[2] for r in c.execute("SELECT chain_id, store_id, store_type FROM stores WHERE price_full_ts > 0")}
        near = {}
        for sk, (lat, lon) in geo.store_coords(c).items():
            if sk in kind and kind[sk] != 2:  # online-only shops have no door to walk into
                d = geo.km(home["lat"], home["lon"], lat, lon)
                if d <= float(max_km or 15):
                    near[sk] = round(d, 1)
        _near_cache[k] = near
    return _near_cache[k]


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
    """Adds to each product card: `near` = the cheapest shelf price at a branch near you (chain + distance; the nearest
    of equally cheap ones), `pack` = its size, and `per` = its price per 100 g / litre."""
    near = _near_stores(c)
    if near and items:
        names = {r[0]: r[1] for r in c.execute("SELECT chain_id, name_he FROM chains")}
        keys = {d["key"]: d["key"] for d in items}  # price key -> the card it belongs to
        # produce sold by weight has its own code at every chain: price the card at all of them
        norms = {d["norm"]: d["key"] for d in items if d.get("scope") == "chain" and d.get("norm")}
        if norms:
            for r in c.execute(f"""SELECT key, norm FROM products WHERE scope='chain' AND store_count > 0
                                   AND norm IN ({','.join('?' * len(norms))})""", list(norms)):
                keys.setdefault(r[0], norms[r[1]])
        best = {}
        # confirmed shelf prices only: current store files, no outliers, no chain that names the barcode differently
        for k, chain, store, price in c.execute(
                f"""SELECT p.key, p.chain_id, p.store_id, p.price FROM prices p
                    JOIN stores s ON s.chain_id=p.chain_id AND s.store_id=p.store_id
                    WHERE p.key IN ({','.join('?' * len(keys))}) AND p.ts >= s.price_full_ts AND s.last_price_ts >= ?
                      AND NOT EXISTS (SELECT 1 FROM price_flags f WHERE f.key=p.key AND f.chain_id=p.chain_id AND f.store_id=p.store_id)
                      AND NOT EXISTS (SELECT 1 FROM name_flags nf WHERE nf.key=p.key AND nf.chain_id=p.chain_id)""",
                [*keys, trust.fresh_cutoff()]):
            km = near.get((chain, store))
            if km is None or not price:
                continue
            card, b = keys[k], best.get(keys[k])
            if b is None or (price, km) < (b[0], b[2]):
                best[card] = (price, chain, km)
        for d in items:
            b = best.get(d["key"])
            d["near"] = {"price": b[0], "chain": chain_name(b[1], names.get(b[1])), "km": b[2]} if b else None
    for d in items:
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


_deal_pct = {}


def deal_pct(c, club):
    """product key -> best saving %, for the "On deal" filter and sorting."""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='deals'").fetchone():
        return {}
    k = (engine.get_setting(c, "deals_version", 0), bool(club))
    if k not in _deal_pct:
        where = "" if club else " WHERE description NOT LIKE '%מועדון%'"
        _deal_pct.clear()
        _deal_pct[k] = dict(c.execute("SELECT key, max(save_pct) FROM deals" + where + " GROUP BY key").fetchall())
    return _deal_pct[k]


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


_deals_cache = {}


@app.get("/api/deals")
def deals(kind: str = "biggest", keys: str = "", limit: int = 12):
    """Real savings vs the product's typical price, at stores within the user's distance.
    kind: biggest | new | mine (mine = only `keys`, e.g. the cart and recently viewed)."""
    c = con()
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='deals'").fetchone():
        return []
    prefs = engine.get_prefs(c)
    home, max_km = prefs.get("home"), prefs.get("max_km")
    want = [k for k in keys.split(",") if k][:100]
    if kind == "basket":  # the basket = every product in any of the household's carts
        want = [r[0] for r in c.execute("SELECT DISTINCT key FROM cart_items")][:300]
        kind = "mine"
    ck = (engine.get_setting(c, "deals_version", 0), kind, json.dumps(home), max_km, tuple(want), limit)
    if ck in _deals_cache:
        return _deals_cache[ck]
    coords = geo.store_coords(c) if home else {}
    dist = lambda sk: geo.km(home["lat"], home["lon"], *coords[sk]) if sk in coords else None
    join, where, params = "", [], []
    if home and max_km:
        c.execute("CREATE TEMP TABLE IF NOT EXISTS near(chain_id TEXT, store_id INTEGER, PRIMARY KEY(chain_id, store_id))")
        c.execute("DELETE FROM near")
        c.executemany("INSERT INTO near VALUES(?,?)", [sk for sk in coords if dist(sk) <= float(max_km)])
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
    # everyday products first: savings weighted by how widely the product is sold
    order = ("g.start_ts DESC, g.save_pct DESC" if kind == "new"
             else "g.save_pct * (0.5 + 0.5 * min(p.store_count, 100) / 100.0) DESC")
    rows = c.execute(f"""SELECT g.*, p.name, p.manufacturer, p.quantity, p.unit, p.is_weighted, p.chain_count,
                                p.store_count, p.min_price, p.max_price, p.scope, p.code FROM (
                           SELECT d.key, min(d.unit_price) unit_price, d.chain_id, d.store_id, d.price, d.typical,
                                  d.save_pct, d.min_qty, d.description, d.start_ts FROM deals d {join}
                           {"WHERE " + " AND ".join(where) if where else ""} GROUP BY d.key) g
                         JOIN products p ON p.key=g.key {"" if kind == "mine" else "WHERE p.chain_count >= 3"}
                         ORDER BY {order} LIMIT ?""", params + [limit * 4]).fetchall()
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
                               "min_qty": r["min_qty"], "description": r["description"], "start_ts": r["start_ts"]}})
    _enrich(c, out)
    if len(_deals_cache) > 64:
        _deals_cache.clear()
    _deals_cache[ck] = out
    return out


@app.get("/api/product")
def product(key: str):
    row = con().execute("SELECT * FROM products WHERE key=?", (key,)).fetchone()
    if not row:
        raise HTTPException(404, "product not found")
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


@app.get("/api/history")
def product_history(key: str):
    """A product's price over time at each chain: [day, cheapest branch, dearest branch], on the days it changed."""
    c = con()
    names = {r[0]: r[1] for r in c.execute("SELECT chain_id, name_he FROM chains")}
    series = {}
    for cid, day, mn, mx in c.execute("SELECT chain_id, day, mn, mx FROM price_history WHERE key=? ORDER BY day", (key,)):
        series.setdefault(cid, []).append([day, mn, mx])
    by_name = {}  # one line per brand (a few brands publish under two chain ids)
    for cid, pts in series.items():
        name = chain_name(cid, names.get(cid))
        if name not in by_name or pts[-1][1] < by_name[name]["points"][-1][1]:
            by_name[name] = {"chain_id": cid, "chain": name, "points": pts}
    return {"since": _since(c), "today": int(time.strftime("%Y%m%d")), "chains": list(by_name.values())}


def _at(points, day):
    """The price on `day` from change-only points: the last change on or before it (the first one before history)."""
    v = points[0][1]
    for d, p in points:
        if d > day:
            break
        v = p
    return v


@app.post("/api/history/cart")
def cart_history(body: dict = Body(...)):
    """What a cart cost at each chain over time (every product at the chain's cheapest branch that day), and which
    products changed price there in the last 30 days."""
    c = con()
    qty = {i["key"]: float(i.get("qty") or 1) for i in body.get("items") or []}
    chains = [str(x) for x in (body.get("chains") or [])][:8]
    if not qty or not chains:
        return {"since": _since(c), "series": {}, "changes": {}}
    pts = {}
    for k, cid, day, mn in c.execute(
            f"""SELECT key, chain_id, day, mn FROM price_history WHERE key IN ({','.join('?' * len(qty))})
                AND chain_id IN ({','.join('?' * len(chains))}) ORDER BY day""", [*qty, *chains]):
        pts.setdefault(cid, {}).setdefault(k, []).append([day, mn])
    month_ago = int(time.strftime("%Y%m%d", time.localtime(time.time() - 30 * 86400)))
    series, changes = {}, {}
    for cid, items in pts.items():
        days = sorted({d for p in items.values() for d, _ in p})
        series[cid] = [[d, round(sum(qty[k] * _at(p, d) for k, p in items.items()), 2)] for d in days]
        moved = {k: [_at(p, month_ago), p[-1][1]] for k, p in items.items()}
        changes[cid] = {k: v for k, v in moved.items() if abs(v[1] - v[0]) >= 0.005}
    return {"since": _since(c), "series": series, "changes": changes}


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
            clean = {k: rule.get(k) for k in ("fee", "reduced_fee", "reduced_above", "free_above", "min_order", "delivers")
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
