"""Basket web app: JSON API + static UI.  Run: uvicorn app.server:app --host 0.0.0.0 --port 8080

Accounts (app.auth): with Google or Facebook sign-in set up, every /api call except a few open ones needs a session,
and carts, address, distance and delivery settings are each account's own. Without it ("local" mode) everything
belongs to the household account, user 1, as before accounts existed."""
import asyncio
import base64
import contextvars
import hashlib
import hmac
import html
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict, deque

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db as dbmod
from . import auth, catalog, commute, engine, geo, guard, places, reports, stats, trust
from .images import PHOTO_DIR, photo_path, want as want_photos
from .chains import BRAND_HE, chain_name

# no /docs, /redoc or /openapi.json: nobody outside needs a map of the API
app = FastAPI(title="Basket", docs_url=None, redoc_url=None, openapi_url=None)
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


# ---------------------------------------------------------------- who is asking
_user = contextvars.ContextVar("user", default=None)
_public = contextvars.ContextVar("public", default=False)
_visit = contextvars.ContextVar("visit", default=(None, None))  # (browser id, visit id) from usage.js, for server events
OPEN = {"/api/me", "/api/status", "/api/logout", "/api/ux"}  # answered without signing in


def _from_internet(request):
    """Came in through the Cloudflare tunnel (Cloudflare sets this header on every request and overwrites any sent to
    it); the LAN and Tailscale reach Sal without it. Sending it yourself only locks you out."""
    return "cf-connecting-ip" in request.headers


def me():
    """The account this request is for: the signed-in one, or the household's in local mode. None = not signed in."""
    return _user.get()


def uid():
    u = me()
    if not u:
        raise HTTPException(401, "sign in")
    return u["id"]


def _json(v, default):
    try:
        return json.loads(v) if v else default
    except ValueError:
        return default


def prefs():
    """This account's shopping preferences: its places, around one or on the way between two, distance, club prices,
    in store / delivered. `home` and `work` are worked out from the places (effective)."""
    u = me()
    return effective({**engine.DEFAULT_PREFS, **(_json(u.get("prefs"), {}) if u else {})})


# ---------------------------------------------------------------- named places: around one, or on the way between two
PLACE_ID = re.compile(r"[a-z0-9]{1,16}")
MAX_PLACES = 12
ADDR_KEYS = ("label", "lat", "lon", "town", "street", "number", "town_name", "street_name", "precision")


def places_of(p):
    """The account's named places (home, work, a parent's, the gym...). Accounts from before named places have only
    `home` (and `work`): those are its places."""
    pl = p.get("places")
    if isinstance(pl, list) and pl:
        return [x for x in pl if isinstance(x, dict) and "lat" in x and "id" in x]
    return [{"id": k, "name": None, **p[k]} for k in ("home", "work") if isinstance(p.get(k), dict) and "lat" in p[k]]


def effective(p):
    """Prefs with `home` = the place stores are found around (around a place) or where the trip starts (on the way),
    and `work` = where the trip ends (None around a place), from the places picked by `at`, `from` and `to`."""
    pl = places_of(p)
    if not pl:
        return {**p, "places": [], "home": None, "work": None}
    by = {x["id"]: x for x in pl}
    at = by.get(p.get("at")) or pl[0]
    frm = by.get(p.get("from")) or pl[0]
    to = by.get(p.get("to"))
    if to is None or to is frm:
        to = next((x for x in pl if x is not frm), None)
    route = p.get("area") == "route" and to is not None
    addr = lambda x: {k: x[k] for k in ADDR_KEYS if k in x}
    return {**p, "places": pl, "at": at["id"], "from": frm["id"], "to": to["id"] if to else None,
            "home": addr(frm if route else at), "work": addr(to) if route else None}


def overrides():
    """This account's own delivery-fee edits (and the chains it confirmed deliver to it)."""
    u = me()
    return _json(u.get("delivery"), {}) if u else {}


def _owner():
    """This request is the site owner's (auth.OWNER_EMAIL, signed in with Google)."""
    u = me()
    if not u:
        return False
    if "_owner" not in u:
        u["_owner"] = auth.is_owner(con(), u)
    return u["_owner"]


def _ev(kind, **f):
    """Note something the server saw for the usage statistics (app.stats): who, from which visit. Never fails a request."""
    try:
        vid, sid = _visit.get()
        f.setdefault("uid", (me() or {}).get("id"))
        stats.event(kind, vid=f.pop("vid", vid), sid=f.pop("sid", sid), admin=f.pop("admin", None) or _owner(), **f)
    except Exception as e:
        print("stats:", repr(e), file=sys.stderr)


def _visit_ids(v):
    vid, _, sid = (v or "").partition(".")
    return (vid, sid) if stats.ID.fullmatch(vid) and stats.ID.fullmatch(sid) else (None, None)


def _route_of(request, status):
    """The route pattern (/api/carts/{cid}), so the request counts don't split by id."""
    p = request.url.path
    if p.startswith("/static/"):
        return "/static/*"
    if p.startswith("/photos/"):
        return "/photos/*"
    r = request.scope.get("route")
    if getattr(r, "path", None):
        return r.path
    if status == 401 and p.startswith("/api/"):
        return "(sign-in needed)"
    return "(unmatched)"


def _save_user(c, **cols):
    c.execute(f"UPDATE users SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", [*cols.values(), uid()])
    c.commit()
    me().update(cols)


@app.middleware("http")
async def who(request: Request, call_next):
    path = request.url.path
    u = None
    public = _from_internet(request)
    base = auth.config().get("base_url")
    if base:  # www.sal-kal.com and any other name -> the one address sign-in returns to (its cookies live there)
        want, host = urllib.parse.urlsplit(base).netloc, request.headers.get("host", "")
        to = base + path + (f"?{request.url.query}" if request.url.query else "")
        if public and host and host != want:
            return RedirectResponse(to, status_code=301)
        # the home network (omarchy:8080, Tailscale) once accounts are on: a session can only live on the base address
        # (the provider sends the browser back there), so pages and sign-in move there. Temporary: turning accounts
        # off brings local mode back. /api calls stay, for scripts and status checks.
        if (host and host != want and request.method == "GET" and auth.accounts_on()
                and (path in ("/", "/app", "/about", "/admin") or path.startswith("/auth/"))):
            return RedirectResponse(to, status_code=307)
    t0 = time.perf_counter()
    if path.startswith(("/api/", "/auth/", "/admin")):
        c = con()
        if auth.accounts_on():
            u = auth.session_user(c, request.cookies.get(auth.SESSION_COOKIE))
        elif not public:  # local mode: the household's own network and Tailscale
            u = auth.user_row(c, 1)
        # from the internet without sign-in set up: nobody, so the household's carts and address never leave home
    token, ptoken = _user.set(u), _public.set(public)
    vtoken = _visit.set(_visit_ids(request.headers.get("x-sal-v")))
    status = 500
    try:
        resp = _refuse(request, path, u, public)
        # metered: from the internet, any API call but the open, cheap ones (the page must always be able to start)
        # and the owner's own (the admin page's reports take a while)
        if resp is None and public and guard.metered(path) and path not in OPEN and not (u or {}).get("admin"):
            resp = await _metered(request, call_next, u, guard.heavy(path))
        elif resp is None:
            resp = await call_next(request)
        status = resp.status_code
    except Exception as e:
        _ev("server_error", a=_route_of(request, 500), b=f"{type(e).__name__}: {e}"[:160])
        raise
    finally:
        try:
            stats.hit(_route_of(request, status), request.method, status, (time.perf_counter() - t0) * 1000,
                      stats.is_bot(request.headers.get("user-agent")))
        except Exception as e:
            print("stats:", repr(e), file=sys.stderr)
        _user.reset(token)
        _public.reset(ptoken)
        _visit.reset(vtoken)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), payment=(), usb=(), geolocation=(self)")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if public:  # through Cloudflare, always https: browsers never try plain http again
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def _busy(status, text, retry):
    return JSONResponse({"detail": text}, status_code=status, headers={"Retry-After": str(int(retry))})


def _same_origin(request):
    """A write (POST/PUT/DELETE) from a page on this site: browsers send Origin with every cross-site write, and
    the session cookie is SameSite=Lax as well; scripts and curl send none."""
    origin = request.headers.get("origin")
    if not origin or request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    return urllib.parse.urlsplit(origin).netloc == request.headers.get("host", "")


def _refuse(request, path, u, public):
    """The answer when this request may not go on (not signed in, from another site, too many), else None."""
    if path.startswith(("/api/", "/auth/")) and not _same_origin(request):
        return JSONResponse({"detail": "cross-site request"}, status_code=403)
    if path.startswith("/api/") and u is None and path not in OPEN:
        return JSONResponse({"detail": "sign in"}, status_code=401)
    if public and u and path.startswith("/api/"):
        wait = guard.allow("user", u["id"])
        if wait:
            return _busy(429, "too many requests, wait a moment", wait)
    return None


async def _metered(request, call_next, u, heavy):
    """An API call from the internet: within this visitor's and account's compute budget, and the seconds it took
    are charged to both. An expensive page also waits its turn: one at a time per account (guard.turn), at most
    guard.HEAVY_SLOTS at once site-wide."""
    keys = [("ip", request.headers.get("cf-connecting-ip", "")[:64])] + ([("user", u["id"])] if u else [])
    wait = guard.budget_left(keys)
    if wait:
        _ev("throttled", a=_route_of(request, 429), n=wait)
        return _busy(429, "too many requests, wait a moment", wait)

    async def run():
        t0 = time.monotonic()
        try:
            return await call_next(request)
        finally:
            guard.charge(keys, time.monotonic() - t0)
    if not heavy:
        return await run()
    try:
        async with guard.turn(keys, guard.HEAVY_WAIT):
            return await run()
    except asyncio.TimeoutError:
        return _busy(503, "busy, try again in a moment", 5)


app.add_middleware(guard.Door)  # outermost: counts and reads the request before anything else runs


_limits, _limits_lock = {}, threading.Lock()


def _under(name, n, per):
    """Counts one call to `name` for this account: False once it made n in the last `per` seconds."""
    k, now = (name, (me() or {}).get("id")), time.time()
    with _limits_lock:
        if len(_limits) > 20000:  # accounts that went quiet
            for old in [x for x, q in _limits.items() if not q or now - q[-1] > 3600]:
                del _limits[old]
        q = _limits.setdefault(k, deque())
        while q and now - q[0] > per:
            q.popleft()
        if len(q) >= n:
            return False
        q.append(now)
        return True


def limit(name, n, per):
    """At most n calls a `per` seconds per account to an endpoint that calls an outside service."""
    if not _under(name, n, per):
        raise HTTPException(429, "too many requests, wait a moment")


def _admin():
    if not (me() or {}).get("admin"):
        raise HTTPException(403, "admin only")


_pages = {}


def _page(name):
    """A page with each script and stylesheet URL carrying its file's change time, so a deploy is picked up at once."""
    p = os.path.join(STATIC, name)
    m = max(e.stat().st_mtime for d in (STATIC, os.path.join(STATIC, "landing")) if os.path.isdir(d)
            for e in os.scandir(d) if e.name.endswith((".html", ".js", ".css")))
    hit = _pages.get(name)
    if not hit or hit[0] != m:
        page = open(p, encoding="utf-8").read()
        def stamp(mt):
            f = os.path.join(STATIC, mt.group(2))
            return f'{mt.group(1)}/static/{mt.group(2)}?v={int(os.stat(f).st_mtime) if os.path.exists(f) else 0}"'
        page = re.sub(r'(src="|href=")/static/([\w./-]+\.(?:js|css))"', stamp, page)
        hit = _pages[name] = (m, page, _csp(page))
    return HTMLResponse(hit[1], headers={"Cache-Control": "no-cache", "Content-Security-Policy": hit[2]})


# Scripts only from this server: the pages' own small inline scripts by their hash, and the two inline handlers the
# page code writes (a photo that fails removes itself; the offline page's "Try again"). Inline styles stay allowed:
# the page builds them. Avatars come from Google or Facebook; the map's tiles, fonts and icons from OpenFreeMap.
HANDLERS = ("this.remove()", "location.reload()")


def _sha(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def _csp(page):
    inline = re.findall(r"<script>(.*?)</script>", page, re.S)
    return ("default-src 'self'; "
            f"script-src 'self' {' '.join(_sha(s) for s in inline)} 'unsafe-hashes' {' '.join(_sha(h) for h in HANDLERS)}; "
            "worker-src 'self' blob:; child-src blob:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob: https://*.googleusercontent.com https://platform-lookaside.fbsbx.com https://*.fbcdn.net; "
            "connect-src 'self' https://tiles.openfreemap.org; media-src 'self'; manifest-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


@app.get("/")
def index(request: Request):
    """Visitors who aren't signed in get the landing page; signed-in people (and the home network in local mode) the app."""
    if auth.accounts_on() and not auth.session_user(con(), request.cookies.get(auth.SESSION_COOKIE)):
        return _page("landing.html")
    return _page("index.html")


@app.get("/app")
def app_page():
    return _page("index.html")


@app.get("/about")
def about():
    return _page("landing.html")


@app.get("/sw.js")
def service_worker():
    """The service worker (installable app, offline start) sits at the root so that it covers the whole site."""
    return FileResponse(os.path.join(STATIC, "sw.js"), media_type="text/javascript", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
os.makedirs(PHOTO_DIR, exist_ok=True)
app.mount("/photos", StaticFiles(directory=PHOTO_DIR), name="photos")  # filled by `python -m app.images`


def _img(code):
    return f"/photos/{code}.webp" if code and code.isdigit() and os.path.exists(photo_path(code)) else None


# ---------------------------------------------------------------- status
_status = {"t": 0.0, "v": None}


@app.get("/api/status")
def status():
    """Open to everyone (the landing page's live counts): worked out at most every 30 seconds."""
    if _status["v"] is not None and time.monotonic() - _status["t"] < 30:
        return _status["v"]
    _status["v"], _status["t"] = _status_now(), time.monotonic()
    return _status["v"]


def _status_now():
    c = con()
    st = engine.get_setting(c, "index_status", {})
    counts = c.execute("""SELECT (SELECT count(*) FROM products) products,
                                 (SELECT count(*) FROM stores WHERE price_full_ts > 0) stores,
                                 (SELECT count(DISTINCT chain_id) FROM stores WHERE price_full_ts > 0) chains,
                                 (SELECT max(last_price_ts) FROM stores) data_ts""").fetchone()
    return {"index": st, **dict(counts), "located": len(geo.store_coords(c))}


@app.post("/api/reindex")
def reindex():
    _admin()
    st = engine.get_setting(con(), "index_status", {})
    if st.get("running") and time.time() - st.get("started", 0) < 3 * 3600:
        return {"started": False, "reason": "already running"}
    root = os.path.dirname(os.path.dirname(__file__))
    subprocess.Popen([sys.executable, "-m", "app.indexer"], cwd=root,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"started": True}


# ---------------------------------------------------------------- products
MAX_WORDS, MAX_QUERY = 12, 200  # a search is a few words; more would only make the full-text query slow


def _tokens(q):
    return [t for t in re.split(r"[\s\"'״׳()*:^-]+", q[:MAX_QUERY]) if t][:MAX_WORDS]


def _cap(n, hi, lo=1):
    """A count from the query string, within what the page ever asks for."""
    return max(lo, min(hi, int(n)))


@app.get("/api/search")
def search(q: str, limit: int = 30):
    c = con()
    q, limit = q.strip()[:MAX_QUERY], _cap(limit, 200)
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


# ---------------------------------------------------------------- on my way: the branches along home -> work
_way_cache = {}


def _commuting(p):
    """On my way is chosen and both ends are set (else: around home, as before)."""
    return p.get("area") == "route" and "lat" in (p.get("home") or {}) and "lat" in (p.get("work") or {})


def _detour(p):
    """How far off the route a branch may be, km: one of the choices for the way you travel (on foot 200 m at most)."""
    return commute.detour_km(p.get("travel") or "car", p.get("detour_km"))


def _way(c, p=None):
    """On my way: (the route home -> work, {(chain, store): (km off the route, km along it)} for every located branch
    within the detour), or (None, None) around home. Cached for 10 minutes per trip and detour; a straight line
    (the routing service was down) isn't kept, so the next request tries the road route again."""
    p = p or prefs()
    if not _commuting(p):
        return None, None
    h, w, travel, width = p["home"], p["work"], p.get("travel") or "car", _detour(p)
    k = (h["lat"], h["lon"], w["lat"], w["lon"], travel, width, int(time.time() // 600))
    hit = _way_cache.get(k)  # read once: another request may clear the cache
    if hit is None:
        path = commute.path(h, w, travel)
        exact = geo.exact_stores(c)  # a branch placed at its town's centre can't be said to be along a route
        hit = (path, commute.corridor(path["coords"], {sk: ll for sk, ll in geo.store_coords(c).items() if sk in exact}, width))
        if not path["straight"]:
            if len(_way_cache) > 100:
                _way_cache.clear()
            _way_cache[k] = hit
    return hit


# ---------------------------------------------------------------- per card: cheapest near you + price per 100 g / litre
_near_cache = {}


def _near_stores(c):
    """Branches you'd walk into, with their distance: inside your distance limit (15 km without one), like the map;
    on my way, the branches along the route with their distance off it. None when no home address is set.
    Cached for 10 minutes per address + limit."""
    p = prefs()
    home, max_km = p.get("home"), p.get("max_km")
    if not home:
        return None
    _, way = _way(c, p)
    if way is not None:
        kind = {(r[0], r[1]): r[2] for r in c.execute("SELECT chain_id, store_id, store_type FROM stores WHERE price_full_ts > 0")}
        return {sk: round(v[0], 1) for sk, v in way.items() if sk in kind and kind[sk] != 2}
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
        if len(_near_cache) > 200:  # one entry per address + distance in use
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
    if me():  # the page shows the categories next: have their lists ready for where this account shops
        _warm_lists()
    # duotone line icons: two masks the page paints in the theme's ink and the brand yellow
    have = lambda slug: os.path.exists(os.path.join(STATIC, "cat", f"{slug}-line.png"))
    return [{**catalog.public(cat), **({"line": f"/static/cat/{cat['slug']}-line.png",
                                        "accent": f"/static/cat/{cat['slug']}-accent.png"} if have(cat["slug"]) else {})}
            for cat in catalog.CATEGORIES]


@app.get("/api/category/{slug}")
def category(slug: str, limit: int = 60):
    limit = _cap(limit, 300)
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


def _live_deals(c, alias="d", stores=True, upcoming=False):
    """Which stored deals can be shown right now, checked on every request (the deals table is rebuilt only after
    an index run): the deal has started and not ended, and (unless the caller checks stores itself) its store's
    prices are current. Returns (SQL condition on `alias`, its args, a signature of the stores part for caches).
    Out-of-date stores are named in the condition: on two million deal rows that beats joining stores.
    upcoming: deals that haven't started yet count too (the caller checks start times itself, as LiveDeals does)."""
    now = int(time.strftime("%Y%m%d%H%M"))
    sql, args = (f"({alias}.end_ts IS NULL OR {alias}.end_ts >= ?)", [now]) if upcoming else \
        (f"({alias}.end_ts IS NULL OR {alias}.end_ts >= ?) AND ({alias}.start_ts IS NULL OR {alias}.start_ts <= ?)", [now, now])
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


class LiveDeals:
    """Each product's deals with their saving and when they run, best saving first, read once per deals table (each
    index run). Which one is live is decided when asked, so a deal ending somewhere doesn't mean reading the ~2
    million deal rows again (seconds; up to 17 s from a cold disk), as it did when only today's best was kept."""
    def __init__(self, table):
        self.table = table  # key -> [(save_pct, start_ts or None, end_ts or None), ...]

    def get(self, key, default=None, now=None):
        now = now or int(time.strftime("%Y%m%d%H%M"))
        for save, start, end in self.table.get(key, ()):
            if (start is None or start <= now) and (end is None or end >= now):
                return save
        return default

    def live(self, now=None):
        """{product key: best saving %} for every deal live now."""
        now = now or int(time.strftime("%Y%m%d%H%M"))
        return {k: v for k in self.table if (v := self.get(k, now=now)) is not None}

    def next_change(self, keys, now=None):
        """The last minute (YYYYMMDDHHMM) through which these products' live savings stay as they are now: the first
        of their deals to end or to start."""
        now, until = now or int(time.strftime("%Y%m%d%H%M")), 999912312359
        for k in keys:
            for _, start, end in self.table.get(k, ()):
                if end is not None and end >= now:
                    until = min(until, end)
                if start is not None and start > now:
                    until = min(until, start - 1)  # the minute before it starts (an ordinal: always below `start`)
        return until


_deal_pct, _deal_pct_lock = {}, threading.Lock()


def deal_pct(c, club):
    """product key -> best saving % among deals live now (LiveDeals.get), for the "On deal" filter and sorting."""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='deals'").fetchone():
        return LiveDeals({})
    live, args, sig = _live_deals(c, upcoming=True)
    k = (engine.get_setting(c, "deals_version", 0), bool(club), sig)
    hit = _deal_pct.get(k)
    if hit is None:
        with _deal_pct_lock:  # read once: the other requests (and the warm-up) wait for it rather than read it again
            hit = _deal_pct.get(k)
            if hit is None:
                where = live + ("" if club else " AND d.description NOT LIKE '%מועדון%'")
                table = {}
                for key, save, start, end in c.execute(
                        f"SELECT key, max(save_pct), start_ts, end_ts FROM deals d WHERE {where} GROUP BY key, start_ts, end_ts", args):
                    if comparable(key):
                        table.setdefault(key, []).append((save, start, end))
                for v in table.values():
                    v.sort(key=lambda x: -(x[0] or 0))
                hit = LiveDeals(table)
                for old in [x for x in _deal_pct if (x[0], x[2]) != (k[0], k[2])]:  # an older deals table
                    del _deal_pct[old]
                _deal_pct[k] = hit
    return hit


def _deal_warmer():
    """Reads the new deal tables as soon as an index run writes them (checked every 30 s), with and without club
    deals, so no visitor waits for that read. Its own connection; off the request path (not charged to anyone)."""
    c = None
    while True:
        try:
            c = c or dbmod.connect()
            for club in (False, True):
                deal_pct(c, club)
        except Exception as e:
            print("deal warm-up:", repr(e), file=sys.stderr)
            c = None
        time.sleep(30)


@app.on_event("startup")
def _warm_deals():
    threading.Thread(target=_deal_warmer, name="deal-warmer", daemon=True).start()


_lists, _lists_lock = OrderedDict(), threading.Lock()  # query + where you shop + data version -> (until, expires, result)
_lists_busy = {}  # key -> Event, while one thread reads that list
LIST_TTL, MAX_LISTS = 600, 400


def _list_key(c, p, q):
    """What a list depends on: the query, where you shop (prices near you; on the way, the route), club deals, and the
    data (new prices, a new check of them, new deals: each makes a new key)."""
    way = _commuting(p)
    where = [p.get("home"), p.get("max_km"), bool(p.get("club")), p.get("work") if way else None,
             [p.get("travel"), _detour(p)] if way else None]
    ver = [engine.get_setting(c, "deals_version", 0), _confirmed_through(c),
           c.execute("SELECT max(last_price_ts) FROM stores").fetchone()[0]]
    return json.dumps([q, where, ver], sort_keys=True, ensure_ascii=False, default=str)


@app.get("/api/browse")
def browse(terms: str = "", cat: str = "", size: str = "", deal: int = 0, sort: str = "", limit: int = 120):
    """Results for stacked filters: every word in `terms` (|-separated) AND the category must match. Kept for
    LIST_TTL per query and place, or until a deal among them starts or ends, whichever is first; new data is a new key."""
    c = con()
    limit = _cap(limit, 200)
    k = _list_key(c, prefs(), [terms[:MAX_QUERY], cat, size[:MAX_QUERY], int(bool(deal)), sort, limit])
    now = int(time.strftime("%Y%m%d%H%M"))
    with _lists_lock:
        hit = _lists.get(k)
        if hit and hit[1] > time.time() and now <= hit[0]:
            _lists.move_to_end(k)
            return hit[2]
        busy = _lists_busy.get(k)  # being read already (the warm-up, or another tab): wait for it, don't read it twice
        if busy is None:
            _lists_busy[k] = threading.Event()
    if busy is not None:
        busy.wait(30)
        with _lists_lock:
            hit = _lists.get(k)
        if hit and hit[1] > time.time() and now <= hit[0]:
            return hit[2]
    try:
        until, out = _browse(c, terms, cat, size, deal, sort, limit)
        with _lists_lock:
            _lists[k] = (until, time.time() + LIST_TTL, out)
            while len(_lists) > MAX_LISTS:
                _lists.popitem(last=False)
    finally:
        if busy is None:
            with _lists_lock:
                _lists_busy.pop(k).set()
    return out


_warm_q = queue.Queue(maxsize=240)
_warm_thread = None


def _warm_lists():
    """Every category's list for where this account shops, read in the background (one worker, one list at a time,
    off the request path) when its home page asks for the categories, so the first tap on one is instant."""
    global _warm_thread
    ctx = contextvars.copy_context()  # who is asking: their places and settings
    for cat in catalog.CATEGORIES:
        try:
            _warm_q.put_nowait((ctx, cat["slug"]))
        except queue.Full:
            return
    if _warm_thread is None or not _warm_thread.is_alive():
        _warm_thread = threading.Thread(target=_warm_worker, name="list-warmer", daemon=True)
        _warm_thread.start()


def _warm_worker():
    while True:
        ctx, slug = _warm_q.get()
        try:
            ctx.run(browse, cat=slug, limit=120)  # the same query the page sends: a cache hit costs nothing
        except Exception as e:
            print("list warm-up:", repr(e), file=sys.stderr)
        time.sleep(0.05)  # let requests in between


def _browse(c, terms, cat, size, deal, sort, limit):
    """browse() without the cache: (the minute through which it stays right, the result)."""
    words = [t.strip() for t in terms[:MAX_QUERY].split("|") if t.strip()][:MAX_WORDS]
    toks = [tok for w in words for tok in _tokens(w)][:MAX_WORDS]
    cdef = next((x for x in catalog.CATEGORIES if x["slug"] == cat), None)
    empty = {"total": 0, "products": [], "refine": [], "sizes": [], "deal_n": 0}
    if not toks and not cdef:
        return 999912312359, empty
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
    pct = deal_pct(c, prefs().get("club"))
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
    return pct.next_change([d["key"] for d in items]), {
        "total": len(shown), "more": len(rows) >= 1500, "products": _enrich(c, shown[:limit]),
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
    """What this account adds to its carts most (and most recently)."""
    c, limit = con(), _cap(limit, 48)
    rows = c.execute("""SELECT p.*, count(*) n, max(ca.updated) last FROM cart_items i
                         JOIN carts ca ON ca.id=i.cart_id JOIN products p ON p.key=i.key
                         WHERE p.store_count > 0 AND ca.user_id=? GROUP BY i.key ORDER BY n DESC, last DESC LIMIT ?""",
                     (uid(), limit)).fetchall()
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
    c, limit = con(), _cap(limit, 48)
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='deals'").fetchone():
        return []
    p = prefs()
    home, max_km = p.get("home"), p.get("max_km")
    want = [k for k in keys.split(",") if k][:100]
    mine = lambda: [r[0] for r in c.execute("""SELECT DISTINCT i.key FROM cart_items i JOIN carts ca ON ca.id=i.cart_id
                                                 WHERE ca.user_id=?""", (uid(),))]
    if kind == "basket":  # the basket = every product in any of this account's carts
        want = mine()[:300]
        kind = "mine"
    basket = tuple(sorted(mine()))[:500] if skip_basket else ()
    _, way = _way(c, p)
    nearby = bool(home and max_km) or way is not None  # the nearby stores are joined below, current ones only
    live, live_args, sig = _live_deals(c, stores=not nearby)
    checked, cutoff = _confirmed_through(c), trust.fresh_cutoff()
    fresh = {(r[0], r[1]) for r in c.execute("SELECT chain_id, store_id FROM stores WHERE last_price_ts >= ? AND promo_full_ts >= ?",
                                              (cutoff, cutoff))}
    ck = (engine.get_setting(c, "deals_version", 0), kind, json.dumps(home), max_km, tuple(want), limit, sig, checked,
          hash(frozenset(fresh)) if nearby else 0, basket,
          json.dumps([p.get("work"), p.get("travel"), _detour(p)]) if way is not None else None)
    hit = _deals_cache.get(ck)  # (deals stay right until, cards); read once: other threads may clear the cache
    if _cache_ok(hit):
        return hit[1]
    coords = geo.store_coords(c) if home else {}
    dist = lambda sk: geo.km(home["lat"], home["lon"], *coords[sk]) if sk in coords else None
    if way is not None:  # on my way: how far off the route
        dist = lambda sk: way[sk][0] if sk in way else None
    join, where, params = "", [live], list(live_args)
    if nearby:
        c.execute("CREATE TEMP TABLE IF NOT EXISTS near(chain_id TEXT, store_id INTEGER, PRIMARY KEY(chain_id, store_id))")
        c.execute("DELETE FROM near")
        c.executemany("INSERT INTO near VALUES(?,?)", [sk for sk in coords if sk in fresh and (
            sk in way if way is not None else dist(sk) <= float(max_km))])
        join = "JOIN near n ON n.chain_id=d.chain_id AND n.store_id=d.store_id"
    if not p.get("club"):
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
        where.append(f"d.key NOT IN ({','.join('?' * len(basket))})")
        params += list(basket)
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
    c, limit = con(), _cap(limit, 1500)
    p = prefs()
    home, max_km = p.get("home"), p.get("max_km")
    if not home:
        return {"home": None, "max_km": None, "stores": []}
    names = {r["chain_id"]: r["name_he"] for r in c.execute("SELECT chain_id, name_he FROM chains")}
    info = {(r["chain_id"], r["store_id"]): r for r in c.execute(
        "SELECT chain_id, store_id, name, city, store_type FROM stores WHERE price_full_ts > 0")}
    path, way = _way(c, p)
    near = []
    for sk, (lat, lon) in geo.store_coords(c).items():
        if sk not in info or info[sk]["store_type"] == 2:  # online-only shops have no door to walk into
            continue
        if way is not None:  # on my way: along the route, nearest to it first
            if sk in way:
                near.append((way[sk][0], sk, lat, lon))
            continue
        d = geo.km(home["lat"], home["lon"], lat, lon)
        if d <= float(max_km or 15):
            near.append((d, sk, lat, lon))
    near.sort()
    out = {"home": home, "max_km": max_km, "total": len(near),
           "stores": [{"lat": lat, "lon": lon, "km": round(d, 1), "chain": chain_name(sk[0], names.get(sk[0])),
                       "name": info[sk]["name"] or "", "city": info[sk]["city"] or "",
                       **({"at": way[sk][1]} if way is not None else {})}
                      for d, sk, lat, lon in near[:limit]]}
    if way is not None:
        out.update(max_km=None, work=p["work"], travel=p.get("travel") or "car", detour_km=_detour(p),
                   route={k: path[k] for k in ("coords", "minutes", "km", "straight")})
    return out


@app.post("/api/way/trip")
def way_trip(body: dict = Body(...)):
    """On the way with stops: from -> each stop in the order given -> to, by the account's way of travel. {"coords", "minutes", "km", "legs", "extra_minutes", "extra_km"}, extra = over going
    straight; {"route": None} when the routing service can't be reached or on my way is off."""
    c, p = con(), prefs()
    if not _commuting(p):
        return {"route": None}
    coords = geo.store_coords(c)
    stops = []
    for x in (body.get("stops") or [])[:commute.MAX_STOPS]:
        try:
            ll = coords.get((str(x[0]), int(x[1])))
        except (TypeError, ValueError, IndexError):
            ll = None
        if ll:
            stops.append(ll)
    a, b = (p["home"]["lat"], p["home"]["lon"]), (p["work"]["lat"], p["work"]["lon"])
    travel = p.get("travel") or "car"
    limit("way", 40, 60)
    try:
        direct = commute.route([a, b], travel)
        trip = commute.route([a, *stops, b], travel) if stops else direct
    except Exception as e:
        print("trip route unavailable:", e, file=sys.stderr)
        return {"route": None}
    return {"route": True, "coords": trip["coords"], "minutes": trip["minutes"], "km": trip["km"], "legs": trip["legs"],
            "extra_minutes": max(0, trip["minutes"] - direct["minutes"]), "extra_km": max(0.0, round(trip["km"] - direct["km"], 1)),
            "travel": travel}


# ---------------------------------------------------------------- your address: picked from the official lists
@app.on_event("startup")
def _places_lists():
    """The official town and street lists, downloaded once in the background (a few seconds) when they're missing."""
    threading.Thread(target=places.ready, daemon=True).start()


@app.get("/api/places/towns")
def places_towns(q: str = ""):
    return places.towns(q[:60])


@app.get("/api/places/streets")
def places_streets(town: int, q: str = ""):
    return places.streets(town, q[:60])


@app.get("/api/places/locate")
def places_locate(town: int, street: int = 0, number: int = 0):
    """Where an address picked from the lists is: {label, lat, lon, precision, town, street, number}."""
    if number and not 0 < number < 10000:
        raise HTTPException(422, "house number out of range")
    limit("locate", 30, 60)
    try:
        with guard.outside("nominatim"):
            return places.locate(town, street or None, number or None)
    except guard.Busy:
        raise HTTPException(503, "busy, try again in a moment")
    except ValueError:
        raise HTTPException(404, "no such town")
    except LookupError:
        raise HTTPException(404, "not on the map")
    except Exception as e:
        print("locate failed:", e, file=sys.stderr)
        raise HTTPException(502, "map lookup unavailable")


@app.get("/api/places/reverse")
def places_reverse(lat: float, lon: float):
    """The address at your phone's location, when it's in Israel."""
    if not (29.3 <= lat <= 33.5 and 34.2 <= lon <= 35.95):
        raise HTTPException(422, "outside Israel")
    limit("locate", 30, 60)
    try:
        with guard.outside("nominatim"):
            return places.reverse(lat, lon)
    except guard.Busy:
        raise HTTPException(503, "busy, try again in a moment")
    except LookupError:
        raise HTTPException(404, "no address here")
    except Exception as e:
        print("reverse failed:", e, file=sys.stderr)
        raise HTTPException(502, "map lookup unavailable")


@app.post("/api/drive")
def drive(body: dict = Body(...)):
    """Drive minutes from body.home to the listed stores: {"chain:store": minutes}."""
    home = body.get("home") or {}
    if not isinstance(home, dict) or "lat" not in home:
        return {"times": {}, "live": False}
    home = _clean_home(home)
    limit("drive", 40, 60)
    c = con()
    coords = geo.store_coords(c)
    want = set()
    for x in (body.get("stores") if isinstance(body.get("stores"), list) else [])[:60]:
        try:
            want.add((str(x[0]), int(x[1])))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    pts = {k: coords[k] for k in want if k in coords}
    key = engine.get_setting(c, "maps_key", None)
    mins = geo.drive_minutes((home["lat"], home["lon"]), list(pts.values()), key)
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
    qty = {i["key"]: i["qty"] for i in _items(body.get("items"), MAX_ITEMS)}
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
_areas, _area_retry, _area_lock, _area_busy = None, 0, threading.Lock(), set()
MAX_AREAS = 300  # one per address + distance in use; the oldest go first
HOURS_KM = 30    # the widest area asked for in one query; branches farther from home are looked up around themselves


def _osm_area(home, max_km):
    """Every food shop with opening hours around an address (OpenStreetMap), fetched in one query and kept in
    hours.json for two weeks, per address and distance. After a failure, waits 10 minutes. The query runs outside
    the lock (it can take half a minute): meanwhile other areas answer from what's kept, and the same area isn't
    asked for twice. An account asks for at most a few new areas an hour."""
    global _areas, _area_retry
    key = json.dumps([round(home["lat"], 3), round(home["lon"], 3), max_km])
    with _area_lock:
        if _areas is None:
            try:
                with open(HOURS_FILE) as f:
                    saved = json.load(f)
                _areas = saved.get("areas") or ({json.dumps(saved["key"]): saved} if saved.get("key") else {})
            except (OSError, ValueError, KeyError):
                _areas = {}
        area = _areas.get(key) or {}
        if area.get("until", 0) > time.time() or time.time() <= _area_retry or key in _area_busy \
                or not _under("hours-area", 6, 3600):
            return area.get("shops") or [], bool(area)
        _area_busy.add(key)
    try:
        shops = geo.osm_shops(home["lat"], home["lon"], (min(float(max_km or 15), HOURS_KM) + 1) * 1000)
    except Exception as e:
        print("opening hours unavailable:", e, file=sys.stderr)
        with _area_lock:
            _area_busy.discard(key)
            if not isinstance(e, guard.Busy):  # the service failed (not our own limit): give it 10 minutes
                _area_retry = time.time() + 600
        return area.get("shops") or [], bool(area)
    with _area_lock:
        _area_busy.discard(key)
        _areas[key] = {"until": time.time() + 14 * 86400, "shops": shops}
        for k in sorted(_areas, key=lambda k: _areas[k].get("until", 0))[:max(0, len(_areas) - MAX_AREAS)]:
            del _areas[k]
        tmp = HOURS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"areas": _areas}, f, ensure_ascii=False)
        os.replace(tmp, HOURS_FILE)
    return shops, True


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
        p = prefs()
        home = p.get("home")
        if home:
            limit("route", 60, 60)
            try:
                if _commuting(p):  # on my way: home -> this store -> work, and what the stop adds
                    travel, a, b = p.get("travel") or "car", (home["lat"], home["lon"]), (p["work"]["lat"], p["work"]["lon"])
                    direct, trip = commute.route([a, b], travel), commute.route([a, ll, b], travel)
                    out["route"] = {"coords": trip["coords"], "minutes": trip["minutes"], "km": trip["km"], "way": True,
                                    "travel": travel, "extra_minutes": max(0, trip["minutes"] - direct["minutes"]),
                                    "extra_km": max(0.0, round(trip["km"] - direct["km"], 1))}
                else:
                    out["route"] = geo.route((home["lat"], home["lon"]), ll)
            except Exception as e:
                print("route unavailable:", e, file=sys.stderr)
    return out


@app.get("/api/store/{chain_id}/{store_id}/hours")
def store_hours(chain_id: str, store_id: int):
    """Opening hours from OpenStreetMap, when someone has mapped them for this branch."""
    c = con()
    _, chain, he = _store_row(c, chain_id, store_id)
    p = prefs()
    ll = geo.store_coords(c).get((chain_id, store_id))
    if not ll or not p.get("home"):
        return {"hours": None, "week": None, "checked": False}
    # on my way the branch can be far from home: the shops around the branch itself (cells of about a kilometre);
    # so is a branch beyond HOURS_KM of home
    home, max_km = p["home"], p.get("max_km")
    far = max_km is not None and float(max_km) > HOURS_KM and geo.km(home["lat"], home["lon"], *ll) > HOURS_KM
    shops, ok = _osm_area({"lat": round(ll[0], 2), "lon": round(ll[1], 2)}, 1) if _commuting(p) or far \
        else _osm_area(home, min(float(max_km), HOURS_KM) if max_km is not None else None)
    hours, name = geo.match_hours(shops, ll[0], ll[1], [chain, BRAND_HE.get(chain, ""), he])
    return {"hours": hours, "week": geo.parse_hours(hours), "name": name, "checked": ok}


# ---------------------------------------------------------------- compare
@app.post("/api/compare")
def compare(body: dict = Body(...)):
    c = con()
    try:
        p = effective({**prefs(), **_request_prefs(body.get("prefs"))})
        _, way = _way(c, p)
    except (KeyError, TypeError, ValueError, AttributeError):  # malformed places in the request: the saved ones
        p, way = prefs(), None
    res = engine.compare(c, _items(body.get("items"), MAX_ITEMS), p, overrides=overrides(), way=way)
    for it in res["items"]:
        it["img"] = _img(it["key"])
    return res


def _items(raw, n):
    """Cart lines from a request: [{"key", "qty"}], at most n, keys of a sane length, quantities as numbers."""
    out = []
    for i in (raw if isinstance(raw, list) else [])[:n]:
        if isinstance(i, dict) and isinstance(i.get("key"), str) and 0 < len(i["key"]) <= 40:
            try:
                q = float(i.get("qty") or 1)
            except (TypeError, ValueError):
                continue
            if q == q:  # not NaN
                out.append({**i, "qty": max(0.01, min(999.0, q))})
    return out


def _request_prefs(raw):
    """Prefs a request may try without saving them (the comparison page sends its own): the same checks as saving,
    and anything that fails them is simply left out (then the saved value counts). Never the places themselves: a
    page opened before an address was fixed elsewhere still holds the old point, and its stops would follow a route
    the map doesn't show. A request only picks among the saved places (at, from, to)."""
    out = {}
    for k, v in (raw.items() if isinstance(raw, dict) else ()):
        if k not in PREF_KEYS or k in ("home", "work", "places"):
            continue
        try:
            v = _clean_pref(k, v)
        except HTTPException:
            continue
        out[k] = v
    return out


# ---------------------------------------------------------------- settings
@app.get("/api/settings")
def get_settings():
    c = con()
    chains = c.execute("""SELECT DISTINCT s.chain_id, ch.name_he FROM stores s LEFT JOIN chains ch
                          ON ch.chain_id=s.chain_id WHERE s.price_full_ts > 0""").fetchall()
    table = engine.delivery_table(c, overrides())
    seen, rows = set(), []
    for r in chains:
        name = chain_name(r["chain_id"], r["name_he"])
        if name in seen:
            continue
        seen.add(name)
        rows.append({"chain_id": r["chain_id"], "name": name, **table.get(r["chain_id"], {})})
    rows.sort(key=lambda r: (r.get("fee") is None, r["name"]))
    return {"prefs": prefs(), "delivery": rows, "traffic": bool(engine.get_setting(c, "maps_key", None))}


PREF_KEYS = {"home", "max_km", "club", "mode", "work", "area", "travel", "detour_km", "places", "at", "from", "to"}


def _clean_pref(k, v):
    """One of the simple prefs as it may be kept: a known value, a number in range, or a place id."""
    try:
        if k == "max_km":
            return None if v is None else max(0.5, min(100.0, float(v)))
        if k == "detour_km":
            return None if v is None else max(0.1, min(5.0, float(v)))
    except (TypeError, ValueError):
        raise HTTPException(422, f"bad {k}")
    if k == "club":
        return bool(v)
    if k == "mode":
        return "delivery" if v == "delivery" else "store"
    if k == "area":
        return "route" if v == "route" else "home"
    if k == "travel":
        return v if v in commute.TRAVEL else "car"
    if k in ("at", "from", "to"):
        if v is None or (isinstance(v, str) and PLACE_ID.fullmatch(v)):
            return v
        raise HTTPException(422, f"bad {k}")
    raise HTTPException(422, f"bad {k}")


CHAIN_ID, MAX_DELIVERY = re.compile(r"\d{13}"), 80  # chain ids are GS1 location numbers; ~35 chains publish
DELIVERY_NUM = ("fee", "reduced_fee", "reduced_above", "free_above", "min_order")
DELIVERY_FLAG = ("delivers", "delivers_here")


def _clean_delivery(rule):
    """A delivery-fee edit: amounts in shekels (or none), and yes/no flags."""
    if not isinstance(rule, dict):
        raise HTTPException(422, "bad delivery rule")
    out = {}
    for k in DELIVERY_NUM:
        if k in rule:
            v = rule[k]
            if v is None or v == "":
                out[k] = None
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(422, f"bad {k}")
            if not 0 <= v <= 10000:
                raise HTTPException(422, f"bad {k}")
            out[k] = v
    for k in DELIVERY_FLAG:
        if k in rule:
            out[k] = None if rule[k] is None else bool(rule[k])
    return out


def _clean_home(h):
    """An address as the picker returns it; nothing else is kept."""
    if not h:
        return None
    try:
        lat, lon = float(h["lat"]), float(h["lon"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "bad address")
    if not (29.3 <= lat <= 33.5 and 34.2 <= lon <= 35.95):
        raise HTTPException(422, "outside Israel")
    out = {"label": str(h.get("label") or "")[:120], "lat": lat, "lon": lon}
    for k in ("town", "street", "number"):
        if isinstance(h.get(k), int):
            out[k] = h[k]
    for k in ("town_name", "street_name", "precision"):
        if isinstance(h.get(k), str):
            out[k] = h[k][:80]
    return out


def _clean_places(lst):
    """Named places as the places sheet sends them: [{id, name, address...}], at most MAX_PLACES."""
    if not isinstance(lst, list):
        raise HTTPException(422, "bad places")
    out, seen = [], set()
    for x in lst[:MAX_PLACES]:
        pid = str((x or {}).get("id") or "") if isinstance(x, dict) else ""
        if not PLACE_ID.fullmatch(pid) or pid in seen:
            raise HTTPException(422, "bad place")
        seen.add(pid)
        name = str(x.get("name") or "").strip()[:40] or None
        out.append({"id": pid, "name": name, **_clean_home(x)})
    return out


def _set_place(pl, pid, addr):
    """Put an address on the place `pid` (keeping its name), or add it."""
    for i, x in enumerate(pl):
        if x["id"] == pid:
            pl[i] = {"id": pid, "name": x.get("name"), **addr}
            return
    pl.append({"id": pid, "name": None, **addr})


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    c = con()
    if "prefs" in body:
        p = {k: v for k, v in (body["prefs"] or {}).items() if k in PREF_KEYS}
        old = _json(me().get("prefs"), {})
        for k in ("home", "work"):
            if k in p:
                p[k] = _clean_home(p[k])
        if "places" in p:
            p["places"] = _clean_places(p["places"])
        for k in ("at", "from", "to"):
            if k in p and not (isinstance(p[k], str) and PLACE_ID.fullmatch(p[k])):
                del p[k]
        # an address without a place (the first-run setup, older pages): `home` is the place you're around,
        # `work` the place called work, which becomes where the trip ends
        if p.get("home") or p.get("work"):
            pl = [dict(x) for x in places_of({**old, **({"places": p["places"]} if "places" in p else {})})]
            if p.get("home"):
                _set_place(pl, effective({**old, "places": pl})["at"] if pl else "home", p["home"])
            if p.get("work"):
                _set_place(pl, "work", p["work"])
                p["to"] = "work"
            p["places"] = pl
        p.pop("home", None)
        p.pop("work", None)
        for k in ("max_km", "club", "mode", "area", "travel", "detour_km"):  # around home, or on my way (home -> work)
            if k in p:
                p[k] = _clean_pref(k, p[k])
        was, new = effective(old), effective({**old, **p})
        # home and work are kept in the saved prefs too, for what reads them directly (the usage statistics)
        _save_user(c, prefs=json.dumps({**old, **p, "home": new["home"], "work": new["work"]}, ensure_ascii=False))
        h = new["home"]
        if (h or {}).get("label") != (was.get("home") or {}).get("label"):
            _ev("address", a=(h or {}).get("town_name") or ((h or {}).get("label") or "").split(",")[-1].strip() or None,
                b=(h or {}).get("precision"))
        if len(new["places"]) != len(was["places"]):
            _ev("places", n=len(new["places"]))
        if "max_km" in p and p["max_km"] != old.get("max_km"):
            _ev("distance", n=p["max_km"])
        for k in ("mode", "club", "area", "travel"):
            if k in p and p[k] != old.get(k):
                _ev(k, a=str(p[k]))
        if "detour_km" in p and p["detour_km"] != old.get("detour_km"):
            _ev("detour", n=p["detour_km"])
    if "maps_key" in body:  # write-only: GET only says whether one is set. Billed per use: the admin's call
        _admin()
        engine.put_setting(c, "maps_key", (body["maps_key"] or "").strip() or None)
    if "delivery" in body:
        if not isinstance(body["delivery"], dict):
            raise HTTPException(422, "bad delivery")
        cur = overrides()
        for cid, rule in body["delivery"].items():
            if not CHAIN_ID.fullmatch(cid) or (cid not in cur and len(cur) >= MAX_DELIVERY):  # bounded: chains only
                continue
            if isinstance(rule, dict) and rule.get("reset"):
                cur.pop(cid, None)
            else:
                cur[cid] = _clean_delivery(rule)
        _save_user(c, delivery=json.dumps(cur))
        _ev("delivery_edit", n=len(body["delivery"]))
    return get_settings()


# ---------------------------------------------------------------- carts
def _cart(c, cid):
    """One of this account's carts (someone else's is "not found")."""
    row = c.execute("SELECT * FROM carts WHERE id=? AND user_id=?", (cid, uid())).fetchone()
    if not row:
        raise HTTPException(404, "cart not found")
    return {**dict(row), "items": engine.cart_items(c, cid)}


@app.get("/api/carts")
def list_carts():
    c = con()
    rows = c.execute("""SELECT c.*, count(i.key) n FROM carts c LEFT JOIN cart_items i ON i.cart_id=c.id
                        WHERE c.user_id=? GROUP BY c.id ORDER BY c.updated DESC""", (uid(),)).fetchall()
    return [dict(r) for r in rows]


MAX_CARTS, MAX_ITEMS = 50, 400


@app.post("/api/carts")
def create_cart(body: dict = Body(default={})):
    c = con()
    if c.execute("SELECT count(*) FROM carts WHERE user_id=?", (uid(),)).fetchone()[0] >= MAX_CARTS:
        raise HTTPException(409, "too many carts")
    now = int(time.time())
    name = body.get("name") if isinstance(body.get("name"), str) else ""
    cur = c.execute("INSERT INTO carts(name, created, updated, user_id) VALUES(?,?,?,?)",
                    (name.strip()[:80] or "New basket", now, now, uid()))
    cid = cur.lastrowid
    _write_items(c, cid, body.get("items") or [])
    c.commit()
    _ev("cart_new", n=len(body.get("items") or []))
    return _cart(c, cid)


def _write_items(c, cid, items):
    items = _items(items, MAX_ITEMS)
    codes = [i["key"] for i in items if ":" not in i["key"]]
    if codes:  # cart products' photos first: real products only, so a cart can't send the photo fetcher anywhere
        want_photos([r[0] for r in c.execute(f"SELECT code FROM products WHERE scope='global' AND key IN ({','.join('?' * len(codes))})", codes)])
    c.execute("DELETE FROM cart_items WHERE cart_id=?", (cid,))
    c.executemany("INSERT OR REPLACE INTO cart_items(cart_id, key, qty, name, position) VALUES(?,?,?,?,?)",
                  [(cid, i["key"], i["qty"], str(i.get("name") or "")[:200], n) for n, i in enumerate(items)])


@app.get("/api/carts/{cid}")
def get_cart(cid: int):
    return _cart(con(), cid)


@app.put("/api/carts/{cid}")
def update_cart(cid: int, body: dict = Body(...)):
    c = con()
    _cart(c, cid)
    if "name" in body:
        name = body["name"] if isinstance(body["name"], str) else ""
        c.execute("UPDATE carts SET name=? WHERE id=?", (name.strip()[:80] or "Untitled", cid))
    if "items" in body:
        _write_items(c, cid, body["items"])
    c.execute("UPDATE carts SET updated=? WHERE id=?", (int(time.time()), cid))
    c.commit()
    return _cart(c, cid)


@app.delete("/api/carts/{cid}")
def delete_cart(cid: int):
    c = con()
    _cart(c, cid)
    for t, col in (("cart_items", "cart_id"), ("carts", "id")):
        c.execute(f"DELETE FROM {t} WHERE {col}=?", (cid,))
    c.commit()
    _ev("cart_delete")
    return {"ok": True}


@app.post("/api/carts/{cid}/duplicate")
def duplicate_cart(cid: int):
    c = con()
    src = _cart(c, cid)
    return create_cart({"name": f"{src['name']} (copy)", "items": src["items"]})


# ---------------------------------------------------------------- accounts: sign in with Google / Facebook
def _base(request):
    """This site's address as the browser sees it (auth.json base_url when set: providers need it exact)."""
    b = auth.config().get("base_url")
    if b:
        return b.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto.split(',')[0].strip()}://{host.split(',')[0].strip()}"


def _cookie(resp, request, name, value, max_age, path="/"):
    resp.set_cookie(name, value, max_age=max_age, httponly=True, samesite="lax", path=path,
                    secure=_base(request).startswith("https://"))


def _back(why=None):
    # /app, not /: signed out, / is the landing page, which wouldn't show why sign-in failed
    resp = RedirectResponse("/app#/signin" + (f"?signin={why}" if why else ""), status_code=303)
    resp.delete_cookie(auth.STATE_COOKIE, path="/auth")
    return resp


@app.get("/auth/{provider}")
def auth_start(provider: str, request: Request, lang: str = "", who: str = "", v: str = ""):
    if provider not in auth.providers():
        raise HTTPException(404, "sign-in not set up")
    url, state = auth.start(provider, f"{_base(request)}/auth/{provider}/callback", who=who if provider == "dev" else None)
    state["l"] = lang if lang in ("he", "en") else request.cookies.get("lang") if request.cookies.get("lang") in ("he", "en") else None
    vid, sid = _visit_ids(v)  # usage.js's ids, so the statistics can follow a visit through the provider and back
    if vid:
        state["x"] = f"{vid}.{sid}"
    _ev("signin_start", a=provider, vid=vid, sid=sid)
    resp = RedirectResponse(url, status_code=303)
    _cookie(resp, request, auth.STATE_COOKIE, auth.sign(state), 900, path="/auth")
    return resp


@app.get("/auth/{provider}/callback")
def auth_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = ""):
    st = auth.unsign(request.cookies.get(auth.STATE_COOKIE, ""), 900)
    vid, sid = _visit_ids((st or {}).get("x"))
    fail = lambda why, back: (_ev("signin_fail", a=provider, b=why, vid=vid, sid=sid), _back(back))[1]
    if error:
        return fail(f"cancelled ({error[:40]})", "cancelled")
    if not st:  # no sign-in cookie: often an in-app browser that opened the provider in another browser
        return fail("no state cookie", "failed")
    if st.get("p") != provider or not code or not hmac.compare_digest(str(st.get("s", "")), state):
        return fail("state mismatch", "failed")
    try:
        sub, email, verified, name, avatar = auth.finish(provider, code, f"{_base(request)}/auth/{provider}/callback", st.get("v"))
    except Exception as e:
        print(f"{provider} sign-in failed:", e, file=sys.stderr)
        return fail(f"provider: {str(e)[:100]}", "failed")
    c = con()
    before = c.execute("SELECT coalesce(max(id), 0) FROM users").fetchone()[0]
    user_id = auth.sign_in(c, provider, str(sub), email, verified, (name or "")[:80], avatar, lang=st.get("l"))
    _ev("signin", a=provider, b="new" if user_id > before else "back", uid=user_id, vid=vid, sid=sid,
        admin=auth.is_owner(c, {"id": user_id}))
    resp = RedirectResponse("/#/", status_code=303)
    _cookie(resp, request, auth.SESSION_COOKIE, auth.new_session(c, user_id), auth.SESSION_DAYS * 86400)
    resp.delete_cookie(auth.STATE_COOKIE, path="/auth")
    return resp


@app.get("/api/me")
def get_me():
    """Who you are, whether sign-in is on (and with what), and your shopping preferences."""
    u, c = me(), con()
    out = {"mode": "accounts" if auth.accounts_on() or _public.get() else "local", "providers": auth.providers(),
           "user": None, "prefs": None}
    if u:
        u["providers"] = [r[0] for r in c.execute("SELECT provider FROM identities WHERE user_id=? ORDER BY created", (u["id"],))]
        out.update(user=auth.public(u), prefs=prefs())
    return out


@app.put("/api/me")
def put_me(body: dict = Body(...)):
    c = con()
    cols = {}
    if body.get("lang") in ("he", "en"):
        cols["lang"] = body["lang"]
    if "onboarded" in body:
        cols["onboarded"] = int(bool(body["onboarded"]))
        if cols["onboarded"] and not me().get("onboarded"):
            _ev("onboarded")
    if isinstance(body.get("name"), str) and body["name"].strip():
        cols["name"] = body["name"].strip()[:80]
    if cols:
        _save_user(c, **cols)
    return get_me()


@app.delete("/api/me")
def delete_me(request: Request):
    """Erase this account: carts, address, settings, sign-ins and sessions."""
    if not auth.accounts_on():
        raise HTTPException(400, "no accounts in local mode")
    gone = uid()
    auth.delete_user(con(), gone)
    stats.forget(gone)  # its usage statistics too, and the anonymous ones from its browsers
    stats.event("account_deleted")  # counted, with nothing to say whose
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


@app.post("/api/logout")
def logout(request: Request):
    _ev("logout")
    auth.end_session(con(), request.cookies.get(auth.SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


# ---------------------------------------------------------------- usage statistics (app.stats) and the owner's /admin page
@app.post("/api/ux")
async def ux(request: Request):
    """A batch of usage events from usage.js (sendBeacon or fetch). Open to visitors who haven't signed in, so the
    sign-in page counts too; capped in size and rate. Answers 204 whatever happens."""
    if int(request.headers.get("content-length") or 0) <= 65536:
        raw = await request.body()
        if len(raw) <= 65536:
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
            u = me()
            ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")
            stats.ingest(body, uid=(u or {}).get("id"), admin=_owner(), ua=request.headers.get("user-agent"),
                         country=request.headers.get("cf-ipcountry"), city=request.headers.get("cf-ipcity"),
                         via="internet" if _from_internet(request) else "home", ip=ip)
    return Response(status_code=204)


ADMIN_DIR = os.path.join(os.path.dirname(__file__), "admin")
ADMIN_HEADERS = {
    "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow",
    # the page shows what visitors typed and sent: scripts only from this server, never inline
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                               "font-src https://fonts.gstatic.com; img-src 'self' https: data:; connect-src 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"}


def _owner_only():
    """Anyone but the owner gets the same answer as for a page that doesn't exist."""
    if not _owner():
        raise HTTPException(404, "Not Found")


@app.get("/admin")
def admin_page():
    _owner_only()
    return FileResponse(os.path.join(ADMIN_DIR, "admin.html"), headers=ADMIN_HEADERS)


@app.get("/admin/{name}")
def admin_asset(name: str):
    _owner_only()
    if name not in ("admin.js", "admin.css", "reports.js", "reports.css"):
        raise HTTPException(404, "Not Found")
    return FileResponse(os.path.join(ADMIN_DIR, name), headers=ADMIN_HEADERS)


def _names(keys):
    """Product names for keys in the statistics."""
    keys = [k for k in keys if isinstance(k, str)][:600]
    if not keys:
        return {}
    return {r[0]: r[1] for r in con().execute(f"SELECT key, name FROM products WHERE key IN ({','.join('?' * len(keys))})", keys)}


def _report(days, mine):
    _owner_only()
    stats.flush()  # what's still queued shows at once
    return stats.Range(days, mine)


@app.get("/api/admin/overview")
def admin_overview(days: int = 7, mine: int = 0):
    return stats.overview(_report(days, mine))


@app.get("/api/admin/live")
def admin_live(days: int = 1, mine: int = 0):
    return stats.live(_report(days, mine))


@app.get("/api/admin/stuck")
def admin_stuck(days: int = 7, mine: int = 0):
    return stats.stuck(_report(days, mine))


@app.get("/api/admin/usage")
def admin_usage(days: int = 7, mine: int = 0):
    return stats.usage(_report(days, mine), _names)


@app.get("/api/admin/audience")
def admin_audience(days: int = 7, mine: int = 0):
    return stats.audience(_report(days, mine))


@app.get("/api/admin/users")
def admin_users(days: int = 7, mine: int = 0):
    return stats.users(_report(days, mine))


@app.get("/api/admin/users/{user_id}")
def admin_user(user_id: int):
    _report(7, 1)
    u = stats.user(user_id, _names)
    if not u:
        raise HTTPException(404, "no such account")
    return u


@app.get("/api/admin/server")
def admin_server(days: int = 7, mine: int = 0):
    return stats.server(_report(days, mine))


@app.get("/api/admin/data")
def admin_data():
    """The price data's health: each chain's stores and how fresh their prices are, sizes, the last index run."""
    _owner_only()
    c = con()
    fresh_after = trust.fresh_cutoff()
    names = {r[0]: r[1] for r in c.execute("SELECT chain_id, name_he FROM chains")}
    chains = {}
    for r in c.execute("""SELECT chain_id, count(*) stores, sum(last_price_ts >= ?) fresh, max(last_price_ts) newest,
                                 sum(store_type = 2) online FROM stores WHERE price_full_ts > 0 GROUP BY chain_id""", (fresh_after,)):
        name = chain_name(r["chain_id"], names.get(r["chain_id"]))
        x = chains.setdefault(name, {"chain": name, "stores": 0, "fresh": 0, "online": 0, "newest": 0})
        x["stores"] += r["stores"]
        x["fresh"] += r["fresh"] or 0
        x["online"] += r["online"] or 0
        x["newest"] = max(x["newest"], r["newest"] or 0)
    deals = "(SELECT count(*) FROM deals)" if c.execute("SELECT 1 FROM sqlite_master WHERE name='deals'").fetchone() else "NULL"
    counts = c.execute(f"""SELECT (SELECT count(*) FROM products WHERE store_count > 0) products,
                                  (SELECT count(*) FROM products WHERE store_count > 0 AND scope='global') barcodes,
                                  (SELECT count(*) FROM stores WHERE price_full_ts > 0) stores, {deals} deals""").fetchone()
    data_dir = os.path.dirname(os.path.abspath(dbmod.DB_PATH))
    sizes = {}
    for f in ("basket.db", "user.db", "stats.db", "places.db"):
        p = os.path.join(data_dir, f)
        if os.path.exists(p):
            sizes[f] = os.path.getsize(p) + (os.path.getsize(p + "-wal") if os.path.exists(p + "-wal") else 0)
    try:
        photos = sum(1 for e in os.scandir(PHOTO_DIR) if e.name.endswith(".webp"))
    except OSError:
        photos = None
    try:
        du = shutil.disk_usage(data_dir)
        disk = {"free": du.free, "total": du.total}
    except OSError:
        disk = None
    return {"chains": sorted(chains.values(), key=lambda x: (-x["fresh"], x["chain"])), **dict(counts), "photos": photos,
            "sizes": sizes, "disk": disk, "index": engine.get_setting(c, "index_status", {}),
            "confirmed_through": engine.get_setting(c, "confirmed_through", None),
            "history_since": engine.get_setting(c, "history_since", None), "fresh_hours": trust.FRESH_HOURS,
            "accounts": auth.accounts_on(), "providers": [p for p in auth.providers() if p != "dev"]}


# ---------------------------------------------------------------- reports to sell (app.reports): public price data only
@app.get("/api/admin/reports")
def admin_reports():
    """What the price data holds, for the Reports tab's header."""
    _owner_only()
    return {"holdings": reports.holdings(con()),
            "categories": [{"slug": x["slug"], "name": x["name"]} for x in catalog.CATEGORIES]}


@app.get("/api/admin/reports/{name}")
def admin_report(name: str, request: Request):
    """One report as JSON, or ?format=csv (&part=<table>) as a download."""
    _owner_only()
    q = request.query_params
    try:
        rep = reports.run(con(), name, q)
    except KeyError:
        raise HTTPException(404, "Not Found")
    if q.get("format") != "csv":
        return rep
    part = q.get("part") or ""
    stamp = time.strftime("%Y%m%d")
    fname = f"sal-kal-{name}{'-' + re.sub(r'[^a-z]', '', part) if part else ''}-{stamp}.csv"
    return Response(reports.csv_text(rep, part), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store"})


# ---------------------------------------------------------------- privacy and data deletion (Facebook requires both pages)
LEGAL = {
    "he": {
        "privacy": ("פרטיות", [
            ("מה נשמר", "כשנכנסים עם Google או Facebook נשמרים השם, כתובת הדוא״ל ותמונת הפרופיל שהם מעבירים, ומזהה החשבון אצלם. "
                        "בנוסף נשמרים הסלים שיצרת, הכתובת שבחרת (רחוב, מספר, יישוב ונקודה במפה), המרחק, השפה והגדרות דמי המשלוח."),
            ("למה", "כדי להציג לך את הסלים שלך ואת המחירים בחנויות שליד הכתובת שבחרת, וכדי לדעת מה לשפר באתר. "
                    "אין פרסומות, אין כלי מעקב של אחרים ואין מכירה או מסירה של מידע."),
            ("סטטיסטיקת שימוש", "השרת של סל קל עצמו שומר אילו עמודים נפתחו ולכמה זמן, חיפושים וכמה תוצאות נמצאו, לחיצות "
                                "(על איזה כפתור, אף פעם לא מה שהוקלד), שגיאות וזמני טעינה, וסוג המכשיר, הדפדפן, המדינה והעיר "
                                "כפי ש־Cloudflare מזהה אותם. כתובת ה־IP לא נשמרת. המידע נשמר 180 יום, רק מפעיל האתר רואה אותו, "
                                "הוא לא נמסר לאף אחד ונמחק יחד עם החשבון."),
            ("שירותים חיצוניים", "הכתובת שבחרת נשלחת פעם אחת ל־OpenStreetMap (Nominatim) כדי לסמן אותה במפה, בלי שם או דוא״ל. "
                                 "זמני נסיעה ומסלולים מחושבים ב־OSRM לפי נקודות במפה. Google ו־Facebook מעורבים רק ברגע הכניסה."),
            ("עוגיות", "עוגיית כניסה אחת (HttpOnly) שומרת אותך מחובר/ת עד 180 יום, ועוגייה של בחירת השפה. "
                       "מזהה אקראי באחסון הדפדפן מבדיל בין ביקורים לצורך הסטטיסטיקה."),
            ("מחיקה", "אפשר למחוק את החשבון ואת כל המידע בכל רגע: חשבון ← מחיקת החשבון שלי. המחיקה מיידית ומלאה, כולל סטטיסטיקת השימוש."),
        ]),
        "data-deletion": ("מחיקת המידע שלך", [
            ("איך מוחקים", "נכנסים לסל קל, לוחצים על תמונת הפרופיל בכותרת, ובוחרים ״מחיקת החשבון שלי״. "
                           "הסלים, הכתובת, ההגדרות, פרטי הכניסה, סטטיסטיקת השימוש וכל החיבורים הפעילים נמחקים מיד ולצמיתות."),
            ("גם ב־Facebook", "אפשר להסיר את סל קל מהאפליקציות המחוברות בהגדרות Facebook (הגדרות ופרטיות ← אפליקציות ואתרים)."),
        ]),
    },
    "en": {
        "privacy": ("Privacy", [
            ("What is kept", "When you sign in with Google or Facebook, Sal-Kal keeps the name, email address and profile photo they pass on, "
                             "and your account id there. It also keeps the carts you make, the address you choose (street, number, town "
                             "and a point on the map), your distance, language and delivery-fee settings."),
            ("Why", "To show you your carts and their prices at the stores near the address you chose, and to see what to "
                    "improve on the site. No ads, no outside trackers, and nothing is sold or passed on."),
            ("Usage statistics", "Sal-Kal's own server keeps which pages were opened and for how long, searches and how many "
                                 "results they found, clicks (which button, never what was typed), errors and load times, and "
                                 "the device type, browser, country and city as Cloudflare reports them. IP addresses are not "
                                 "kept. It is kept for 180 days, seen only by the site's operator, never passed on, and deleted "
                                 "with your account."),
            ("Outside services", "The address you choose is sent once to OpenStreetMap (Nominatim) to put it on the map, without your "
                                 "name or email. Drive times and routes are worked out by OSRM from map points. Google and Facebook "
                                 "are involved only when you sign in."),
            ("Cookies", "One sign-in cookie (HttpOnly) keeps you signed in for up to 180 days, and one remembers your language. "
                        "A random id in the browser's storage tells visits apart for the usage statistics."),
            ("Deleting", "You can delete your account and everything in it at any time: Account → Delete my account. It is "
                         "immediate and complete, usage statistics included."),
        ]),
        "data-deletion": ("Deleting your data", [
            ("How", "Open Sal-Kal, tap your profile picture in the header and choose “Delete my account”. Your carts, address, settings, "
                    "sign-ins, usage statistics and every open session are erased at once and for good."),
            ("On Facebook too", "You can also remove Sal-Kal from your connected apps in Facebook’s settings (Settings & privacy → Apps and websites)."),
        ]),
    },
}


def _legal_page(kind, request, lang):
    lang = lang if lang in ("he", "en") else request.cookies.get("lang") if request.cookies.get("lang") in ("he", "en") else "he"
    title, parts = LEGAL[lang][kind]
    contact = auth.config().get("contact_email")
    other = "en" if lang == "he" else "he"
    body = "".join(f"<h2>{html.escape(h)}</h2><p>{html.escape(p)}</p>" for h, p in parts)
    if contact:
        body += f"<h2>{'יצירת קשר' if lang == 'he' else 'Contact'}</h2><p><a href=\"mailto:{html.escape(contact)}\">{html.escape(contact)}</a></p>"
    page = f"""<!doctype html><html lang="{lang}" dir="{'rtl' if lang == 'he' else 'ltr'}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)} · {'סל קל' if lang == 'he' else 'Sal-Kal'}</title>
<link rel="stylesheet" href="/static/styles.css"></head><body class="legal"><main class="legal-page">
<p class="legal-top"><a href="/">{'← סל קל' if lang == 'he' else '← Sal-Kal'}</a><a href="?lang={other}" lang="{other}">{'English' if lang == 'he' else 'עברית'}</a></p>
<h1>{html.escape(title)}</h1>{body}</main></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-cache", "Content-Security-Policy":
                                       "default-src 'self'; script-src 'none'; style-src 'self' 'unsafe-inline' "
                                       "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
                                       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})


@app.get("/privacy")
def privacy(request: Request, lang: str = ""):
    return _legal_page("privacy", request, lang)


@app.get("/data-deletion")
def data_deletion(request: Request, lang: str = ""):
    return _legal_page("data-deletion", request, lang)
