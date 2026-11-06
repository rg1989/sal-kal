"""Usage statistics for the owner's admin page (/admin): who comes, what they open, search and click, where they get
stuck, and how the server holds up. First-party only: kept in stats.db next to the price DB and never sent anywhere.

What is kept
- visits: one per browser tab visit (a new one after 30 minutes idle): device class, OS and browser read from the
  User-Agent (the string itself isn't kept), in-app browser (Facebook, Instagram…), country and city from Cloudflare's
  headers, screen size, language, theme, the site that linked here (host only) and utm_* tags.
- events: a page opened and how long it was looked at, a search with its result count and what came of it, a click
  (the control's label, never what was typed), a rage or dead click, an error, load times; and from the server:
  sign-in started / done / failed, account set up, address and distance chosen, carts made and deleted.
- requests: per day, route and status: count and a latency histogram.
- ids: a random browser id (localStorage) and a random visit id (sessionStorage), and the account id when signed in.

Not kept: IP addresses (only held in memory for rate limits), the User-Agent string, anything typed into a field,
third-party scripts of any kind.

Retention: visits and events 180 days, request counts 400 days. Deleting an account deletes its visits and events,
and the anonymous ones from the same browsers (forget()).

Writes go through a queue to one background thread, so a request never waits on this database.
"""
import atexit
import json
import math
import os
import queue
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from . import db as dbmod

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Jerusalem")
except Exception:  # no tz database: Israel standard time
    from datetime import timezone
    TZ = timezone(timedelta(hours=2))

KEEP_DAYS = 180
KEEP_REQUEST_DAYS = 400
LIVE_SECONDS = 300
BUCKETS = (25, 50, 100, 250, 500, 1000, 2500)  # ms: upper bounds of b0..b6; b7 = slower


def path():
    return os.environ.get("SAL_STATS") or os.path.join(os.path.dirname(os.path.abspath(dbmod.DB_PATH)), "stats.db")


def user_db_path():
    return os.environ.get("BASKET_USER_DB") or os.path.join(os.path.dirname(os.path.abspath(dbmod.DB_PATH)), "user.db")


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS visits(
  sid TEXT PRIMARY KEY, vid TEXT, uid INTEGER, admin INTEGER DEFAULT 0, start REAL, last REAL, day INTEGER, hour INTEGER,
  views INTEGER DEFAULT 0, events INTEGER DEFAULT 0,
  device TEXT, os TEXT, browser TEXT, inapp TEXT, country TEXT, city TEXT, via TEXT,
  lang TEXT, theme TEXT, screen TEXT, vw INTEGER, pwa INTEGER, tz TEXT, ref TEXT, utm TEXT, landing TEXT);
CREATE INDEX IF NOT EXISTS visits_start ON visits(start);
CREATE INDEX IF NOT EXISTS visits_last ON visits(last);
CREATE INDEX IF NOT EXISTS visits_vid ON visits(vid, start);
CREATE INDEX IF NOT EXISTS visits_uid ON visits(uid, start);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, ts REAL, day INTEGER, sid TEXT, vid TEXT, uid INTEGER, admin INTEGER DEFAULT 0,
  src TEXT, kind TEXT, page TEXT, a TEXT, b TEXT, n REAL, data TEXT);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, ts);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_sid ON events(sid, ts);
CREATE INDEX IF NOT EXISTS events_uid ON events(uid, ts);
CREATE INDEX IF NOT EXISTS events_vid ON events(vid);
CREATE TABLE IF NOT EXISTS requests(
  day INTEGER, route TEXT, method TEXT, status INTEGER, bot INTEGER, n INTEGER, ms REAL, max_ms REAL,
  b0 INTEGER, b1 INTEGER, b2 INTEGER, b3 INTEGER, b4 INTEGER, b5 INTEGER, b6 INTEGER, b7 INTEGER,
  PRIMARY KEY(day, route, method, status, bot)) WITHOUT ROWID;
"""


def day_of(ts):
    return int(datetime.fromtimestamp(ts, TZ).strftime("%Y%m%d"))


def hour_of(ts):
    return datetime.fromtimestamp(ts, TZ).hour


def midnight(ts=None, days_back=0):
    """Unix time of local midnight `days_back` days before the day of ts."""
    d = datetime.fromtimestamp(ts or time.time(), TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return (d - timedelta(days=days_back)).timestamp()


# ---------------------------------------------------------------- reading the browser
BOT = re.compile(r"bot\b|bot/|crawl|spider|slurp|preview|facebookexternalhit|whatsapp/|telegram|curl/|wget/|python|httpx|"
                 r"go-http|java/|okhttp|headless|lighthouse|pagespeed|monitor|scan|axios|node-fetch|libwww|^$", re.I)
INAPP = (("FBAN|FBAV|FB_IAB|FBIOS", "Facebook"), ("Instagram", "Instagram"), ("musical_ly|TikTok|BytedanceWebview", "TikTok"),
         (r"\bLine/", "LINE"), (r"\bGSA/", "Google app"), (r"Snapchat", "Snapchat"), (r"LinkedInApp", "LinkedIn"),
         (r"; wv\)", "Android WebView"))


def is_bot(ua):
    return bool(BOT.search(ua or ""))


def parse_ua(ua, touch=False):
    """(device, os, browser, in-app browser or None) from a User-Agent, coarsely."""
    ua = ua or ""
    if re.search(r"iPhone|iPod", ua):
        os_ = "iOS"
    elif re.search(r"iPad", ua) or ("Macintosh" in ua and touch):  # iPadOS asks for desktop pages as a Mac
        os_ = "iPadOS"
    elif "Android" in ua:
        os_ = "Android"
    elif "CrOS" in ua:
        os_ = "ChromeOS"
    elif "Windows" in ua:
        os_ = "Windows"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        os_ = "macOS"
    elif "Linux" in ua:
        os_ = "Linux"
    else:
        os_ = "Other"
    if os_ == "iPadOS" or (os_ == "Android" and "Mobile" not in ua) or "Tablet" in ua:
        device = "Tablet"
    elif os_ in ("iOS", "Android") or "Mobi" in ua:
        device = "Phone"
    else:
        device = "Desktop"
    inapp = next((name for pat, name in INAPP if re.search(pat, ua)), None)
    for pat, name in ((r"Edg[AeiOS]*/", "Edge"), (r"SamsungBrowser/", "Samsung Internet"), (r"OPR/|Opera", "Opera"),
                      (r"Firefox/|FxiOS/", "Firefox"), (r"YaBrowser/", "Yandex"), (r"Chrome/|CriOS/", "Chrome"),
                      (r"Version/.*Safari/|Safari/", "Safari")):
        if re.search(pat, ua):
            browser = name
            break
    else:
        browser = "Other"
    if inapp and inapp != "Android WebView":
        browser = inapp
    return device, os_, browser, inapp


# ---------------------------------------------------------------- cleaning what a browser sends
ID = re.compile(r"[A-Za-z0-9_-]{10,40}")
WEB_KINDS = {"view", "dwell", "search", "filter", "add", "remove", "cart", "compare", "click", "rage", "dead", "error",
             "api_fail", "toast", "perf", "onboard", "dialog", "signin_click", "gps", "addr"}
# "Script error." with no file is a script from another origin, mostly a browser's own (Firefox for iPhone throws one
# whenever a dialog opens): it says nothing about Sal-Kal's code, so it isn't kept or counted
FOREIGN_ERROR = "Script error."
OURS = f"NOT (kind='error' AND a='{FOREIGN_ERROR}' AND coalesce(b, '')='')"
PAGE = re.compile(r"[a-z0-9_-]{1,24}")
CTRL = re.compile(r"[\x00-\x1f\x7f]+")


def town_of(p):
    """The town an account shops from, by name: the official list's name for its code, else the saved name, else the
    last part of the address (an address keeps the town's code in `town`, not its name)."""
    pl = [x for x in (p.get("places") or []) if isinstance(x, dict)]
    home = p.get("home") if isinstance(p.get("home"), dict) else (pl[0] if pl else {})
    name = home.get("town_name") or (home.get("label") or "").split(",")[-1].strip() or None
    try:  # the official spelling, so one town is one row however it was found
        from . import places
        t = places.town(home["town"]) if isinstance(home.get("town"), int) else places.town_by_name(name) if name else None
        if t:
            return t["name"]
    except Exception:
        pass
    return name


def _s(v, n=160):
    if v is None or isinstance(v, (dict, list)):
        return None
    v = CTRL.sub(" ", str(v)).strip()
    return v[:n] or None


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return max(-1e9, min(1e9, f)) if math.isfinite(f) else None


def _data(d):
    if not isinstance(d, dict):
        return None
    out = {}
    for k, v in list(d.items())[:10]:
        k = _s(k, 24)
        if not k:
            continue
        if isinstance(v, bool) or v is None:
            out[k] = v
        elif isinstance(v, (int, float)):
            out[k] = _num(v)
        else:
            out[k] = _s(v, 100)
    s = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    return s if len(s) <= 800 and out else None


# ---------------------------------------------------------------- rate limits (memory only)
_rl, _rl_lock = {}, threading.Lock()


def _allow(key, cost, n, per):
    """Up to n units a `per` seconds per key."""
    now = time.time()
    with _rl_lock:
        if len(_rl) > 20000:
            for k in [k for k, (t0, _) in _rl.items() if now - t0 > per]:
                del _rl[k]
        t0, used = _rl.get(key, (now, 0))
        if now - t0 > per:
            t0, used = now, 0
        if used + cost > n:
            _rl[key] = (t0, used)
            return False
        _rl[key] = (t0, used + cost)
        return True


# ---------------------------------------------------------------- the writer
_q = queue.Queue(maxsize=50000)
_req, _req_lock = {}, threading.Lock()
_w, _wlock = None, threading.RLock()
_thread = None
_pruned = {"day": None}
_owner_vids = set()


def _writer():
    global _w
    if _w is None:
        _w = sqlite3.connect(path(), timeout=30, check_same_thread=False)
        _w.execute("PRAGMA synchronous=NORMAL")
        _w.executescript(SCHEMA)
        _owner_vids.update(r[0] for r in _w.execute("SELECT DISTINCT vid FROM visits WHERE admin=1 AND vid IS NOT NULL"))
    return _w


def _start():
    global _thread
    if _thread is None and not os.environ.get("SAL_STATS_SYNC"):
        _thread = threading.Thread(target=_loop, name="stats-writer", daemon=True)
        _thread.start()


def _loop():
    last_req = time.time()
    while True:
        try:
            item = _q.get(timeout=2)
            _write([item] + _drain())
        except queue.Empty:
            pass
        except Exception as e:  # never let statistics take the site down
            print("stats:", repr(e))
        if time.time() - last_req > 30:
            last_req = time.time()
            try:
                _flush_requests()
                _prune()
            except Exception as e:
                print("stats:", repr(e))


def _drain():
    out = []
    while len(out) < 5000:
        try:
            out.append(_q.get_nowait())
        except queue.Empty:
            break
    return out


def _put(item):
    _start()
    try:
        _q.put_nowait(item)
    except queue.Full:
        pass


def flush():
    """Write everything queued now (tests, shutdown, before reading for the admin page)."""
    with _wlock:
        _write(_drain())
        _flush_requests()


atexit.register(lambda: flush() if _w is not None or not _q.empty() or _req else None)

EV_COLS = "ts, day, sid, vid, uid, admin, src, kind, page, a, b, n, data"


def _write(items):
    if not items:
        return
    with _wlock:
        c = _writer()
        with c:
            for it in items:
                op = it[0]
                if op == "visit":
                    v = it[1]
                    c.execute("""INSERT INTO visits(sid, vid, uid, admin, start, last, day, hour, views, events, device, os, browser,
                                   inapp, country, city, via, lang, theme, screen, vw, pwa, tz, ref, utm, landing)
                                 VALUES(:sid, :vid, :uid, :admin, :start, :last, :day, :hour, :views, :events, :device, :os, :browser,
                                   :inapp, :country, :city, :via, :lang, :theme, :screen, :vw, :pwa, :tz, :ref, :utm, :landing)
                                 ON CONFLICT(sid) DO UPDATE SET last=max(last, excluded.last), views=views+excluded.views,
                                   events=events+excluded.events, uid=coalesce(excluded.uid, uid), admin=max(admin, excluded.admin),
                                   lang=coalesce(excluded.lang, lang), theme=coalesce(excluded.theme, theme),
                                   screen=coalesce(screen, excluded.screen), vw=coalesce(excluded.vw, vw), pwa=coalesce(pwa, excluded.pwa),
                                   tz=coalesce(tz, excluded.tz), ref=coalesce(ref, excluded.ref), utm=coalesce(utm, excluded.utm),
                                   landing=coalesce(landing, excluded.landing)""", v)
                elif op == "events":
                    c.executemany(f"INSERT INTO events({EV_COLS}) VALUES({','.join('?' * 13)})", it[1])
                elif op == "owner_vid":
                    c.execute("UPDATE visits SET admin=1 WHERE vid=? AND admin=0", (it[1],))
                    c.execute("UPDATE events SET admin=1 WHERE vid=? AND admin=0", (it[1],))


def _flush_requests():
    with _req_lock:
        rows = list(_req.items())
        _req.clear()
    if not rows:
        return
    with _wlock:
        c = _writer()
        with c:
            c.executemany("""INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                             ON CONFLICT(day, route, method, status, bot) DO UPDATE SET n=n+excluded.n, ms=ms+excluded.ms,
                               max_ms=max(max_ms, excluded.max_ms), b0=b0+excluded.b0, b1=b1+excluded.b1, b2=b2+excluded.b2,
                               b3=b3+excluded.b3, b4=b4+excluded.b4, b5=b5+excluded.b5, b6=b6+excluded.b6, b7=b7+excluded.b7""",
                          [(*k, *v) for k, v in rows])


def _prune():
    today = day_of(time.time())
    if _pruned["day"] == today:
        return
    _pruned["day"] = today
    cut = time.time() - KEEP_DAYS * 86400
    with _wlock:
        c = _writer()
        with c:
            c.execute("DELETE FROM events WHERE ts < ?", (cut,))
            c.execute("DELETE FROM visits WHERE last < ?", (cut,))
            c.execute("DELETE FROM requests WHERE day < ?", (day_of(time.time() - KEEP_REQUEST_DAYS * 86400),))


# ---------------------------------------------------------------- recording
METHODS = {"GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"}
# Site-wide ceilings, whoever sends them: a flood from many addresses can't fill the disk (the machine's other
# services share it). Far above real use (a busy day is a few thousand events).
DAY_WEB, DAY_SERVER, MAX_DB_BYTES = 300000, 100000, 2 * 1024 ** 3
_size = {"t": 0.0, "big": False}


def _full():
    """stats.db has reached MAX_DB_BYTES: new events are dropped (checked once a minute)."""
    now = time.time()
    if now - _size["t"] > 60:
        try:
            _size["big"] = sum(os.path.getsize(p) for p in (path(), path() + "-wal") if os.path.exists(p)) > MAX_DB_BYTES
        except OSError:
            _size["big"] = False
        _size["t"] = now
    return _size["big"]


def hit(route, method, status, ms, bot):
    """One HTTP request, folded into today's per-route counts (written every 30 s)."""
    method = method if method in METHODS else "OTHER"  # any word can be sent as a method: one row for all of them
    k = (day_of(time.time()), route, method, int(status), int(bool(bot)))
    b = next((i for i, ub in enumerate(BUCKETS) if ms < ub), len(BUCKETS))
    with _req_lock:
        v = _req.get(k)
        if v is None:
            v = _req[k] = [0, 0.0, 0.0] + [0] * 8
        v[0] += 1
        v[1] += ms
        v[2] = max(v[2], ms)
        v[3 + b] += 1
    _start()


def event(kind, uid=None, vid=None, sid=None, admin=False, page=None, a=None, b=None, n=None, data=None, ts=None):
    """An event the server itself saw (sign-in, settings, carts, errors)."""
    ts = ts or time.time()
    if _full() or not _allow(("day", "server"), 1, DAY_SERVER, 86400):
        return
    vid = vid if vid and ID.fullmatch(vid) else None
    sid = sid if sid and ID.fullmatch(sid) else None
    _put(("events", [(ts, day_of(ts), sid, vid, uid, int(bool(admin) or vid in _owner_vids), "server", kind,
                      _s(page, 24), _s(a), _s(b), _num(n), _data(data) if isinstance(data, dict) else None)]))


def ingest(body, *, uid, admin, ua, country, city, via, ip):
    """A batch from the browser (usage.js). Returns how many events were kept."""
    if not isinstance(body, dict):
        return 0
    vid, sid = body.get("vid"), body.get("sid")
    if not (isinstance(vid, str) and ID.fullmatch(vid) and isinstance(sid, str) and ID.fullmatch(sid)):
        return 0
    evs = body.get("ev")
    if not isinstance(evs, list):
        return 0
    evs = evs[:60]
    if _full() or not (_allow(("ip", ip), len(evs), 1200, 600) and _allow(("sid", sid), len(evs), 600, 600)
                       and _allow(("day", "web"), len(evs), DAY_WEB, 86400)):
        return 0
    now = time.time()
    sent = _num(body.get("now")) or now * 1000
    rows, views, adm = [], 0, int(bool(admin) or vid in _owner_vids)
    for e in evs:
        if not isinstance(e, dict) or e.get("k") not in WEB_KINDS:
            continue
        t = _num(e.get("t")) or sent
        ts = now - max(0.0, min(900.0, (sent - t) / 1000))
        page = e.get("p") if isinstance(e.get("p"), str) and PAGE.fullmatch(e.get("p")) else None
        kind = e["k"]
        if kind == "error" and _s(e.get("a")) == FOREIGN_ERROR and not _s(e.get("b")):
            continue
        views += kind == "view"
        rows.append((ts, day_of(ts), sid, vid, uid, adm, "web", kind, page,
                     _s(e.get("a")), _s(e.get("b")), _num(e.get("n")), _data(e.get("d"))))
    if not rows:
        return 0
    ctx = body.get("ctx") if isinstance(body.get("ctx"), dict) else {}
    device, os_, browser, inapp = parse_ua(ua, bool(ctx.get("touch")))
    first = min(r[0] for r in rows)
    utm = "/".join(x for x in (_s(ctx.get("utm_source"), 40), _s(ctx.get("utm_medium"), 40), _s(ctx.get("utm_campaign"), 60)) if x) or None
    ref = _s(ctx.get("ref"), 80)
    ref = ref.lower().removeprefix("www.") if ref else None
    _put(("visit", {
        "sid": sid, "vid": vid, "uid": uid, "admin": adm, "start": first, "last": max(r[0] for r in rows),
        "day": day_of(first), "hour": hour_of(first), "views": views, "events": len(rows),
        "device": device, "os": os_, "browser": browser, "inapp": inapp,
        "country": _s(country, 4), "city": _s(city, 60), "via": via,
        "lang": _s(ctx.get("lang"), 5), "theme": _s(ctx.get("theme"), 8), "screen": _s(ctx.get("screen"), 12),
        "vw": int(_num(ctx.get("vw")) or 0) or None, "pwa": int(bool(ctx.get("pwa"))) if ctx else None,
        "tz": _s(ctx.get("tz"), 40), "ref": ref, "utm": utm, "landing": _s(ctx.get("landing"), 80)}))
    _put(("events", rows))
    if admin and vid not in _owner_vids:  # the owner's own browsers: their visits before signing in are the owner's too
        _owner_vids.add(vid)
        _put(("owner_vid", vid))
    return len(rows)


def forget(uid):
    """Everything kept about an account: its visits and events, and the anonymous ones from the same browsers."""
    flush()
    with _wlock:
        c = _writer()
        with c:
            vids = [r[0] for r in c.execute("SELECT DISTINCT vid FROM visits WHERE uid=? AND vid IS NOT NULL", (uid,))]
            vids += [r[0] for r in c.execute("SELECT DISTINCT vid FROM events WHERE uid=? AND vid IS NOT NULL", (uid,))]
            vids = sorted(set(vids))
            c.execute("DELETE FROM events WHERE uid=?", (uid,))
            c.execute("DELETE FROM visits WHERE uid=?", (uid,))
            for i in range(0, len(vids), 500):
                part = vids[i:i + 500]
                ph = ",".join("?" * len(part))
                c.execute(f"DELETE FROM events WHERE vid IN ({ph})", part)
                c.execute(f"DELETE FROM visits WHERE vid IN ({ph})", part)


# ---------------------------------------------------------------- reading (the admin page)
_rlocal = threading.local()


def reader():
    """This thread's read connection, with user.db attached (read-only) as u."""
    r = getattr(_rlocal, "c", None)
    if r is None:
        with _wlock:
            _writer()  # the tables exist before anything reads them
        r = sqlite3.connect(path(), timeout=30, check_same_thread=False)
        r.row_factory = sqlite3.Row
        if os.path.exists(user_db_path()):
            r.execute("ATTACH DATABASE ? AS u", (user_db_path(),))
        r.execute("PRAGMA query_only=1")  # the admin page only ever reads
        _rlocal.c = r
    return r


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(math.ceil(p / 100 * len(sorted_vals))) - 1))
    return sorted_vals[i]


def _hist_pct(row, p):
    """Upper bound (ms) of the bucket holding the p-th percentile; None = slower than the last bound."""
    n = sum(row[f"b{i}"] for i in range(8))
    if not n:
        return None
    want, acc = p / 100 * n, 0
    for i in range(8):
        acc += row[f"b{i}"]
        if acc >= want:
            return BUCKETS[i] if i < len(BUCKETS) else None
    return None


class Range:
    def __init__(self, days, owner):
        self.days = max(1, min(int(days or 7), 180))
        self.t1 = time.time()
        self.t0 = midnight(self.t1, self.days - 1)
        self.p0 = self.t0 - (self.t1 - self.t0)  # the period before, same length, for the change
        self.owner = bool(owner)
        self.adm = "" if owner else " AND admin=0"
        self.adm_e = "" if owner else " AND e.admin=0"
        self.day_list = [day_of(midnight(self.t1, i) + 3600) for i in range(self.days - 1, -1, -1)]


def _visit_kpis(r, R, t0, t1):
    row = r.execute(f"""SELECT count(DISTINCT vid) visitors, count(*) visits, count(DISTINCT uid) users,
                               coalesce(sum(views), 0) views
                        FROM visits WHERE start >= ? AND start < ?{R.adm}""", (t0, t1)).fetchone()
    lens = sorted(x[0] for x in r.execute(f"SELECT last - start FROM visits WHERE start >= ? AND start < ?{R.adm}", (t0, t1)))
    ev = r.execute(f"""SELECT sum(kind='search') searches, sum(kind='search' AND n=0) zero, sum(kind='add') adds,
                              count(DISTINCT CASE WHEN kind='view' AND page='basket' THEN sid END) checkouts,
                              sum(kind='error' AND {OURS}) js_errors, sum(kind IN ('rage', 'dead')) stuck
                       FROM events WHERE ts >= ? AND ts < ?{R.adm}""", (t0, t1)).fetchone()
    d0, d1 = day_of(t0), day_of(t1 - 1)
    s5 = r.execute("SELECT coalesce(sum(n), 0) FROM requests WHERE day BETWEEN ? AND ? AND status >= 500", (d0, d1)).fetchone()[0]
    new = r.execute(f"SELECT count(*) FROM u.users WHERE created >= ? AND created < ?", (t0, t1)).fetchone()[0] if _has_u(r) else 0
    out = {**dict(row), **{k: ev[k] or 0 for k in ev.keys()}, "server_errors": s5, "new_accounts": new,
           "visit_median": _pct(lens, 50), "bounce": None}
    single = r.execute(f"""SELECT count(*) FROM visits WHERE start >= ? AND start < ? AND views <= 1 AND last - start < 10{R.adm}""",
                       (t0, t1)).fetchone()[0]
    out["bounce"] = round(single / row["visits"], 3) if row["visits"] else None
    out["zero_rate"] = round(out["zero"] / out["searches"], 3) if out["searches"] else None
    return out


def _has_u(r):
    try:
        r.execute("SELECT 1 FROM u.users LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def overview(R):
    r = reader()
    kpi = _visit_kpis(r, R, R.t0, R.t1)
    prev = _visit_kpis(r, R, R.p0, R.t0)
    daily = {d: {"day": d, "visitors": 0, "signed": 0, "visits": 0, "views": 0, "new_accounts": 0, "set_up": 0} for d in R.day_list}
    for x in r.execute(f"""SELECT day, count(DISTINCT vid) visitors, count(DISTINCT CASE WHEN uid IS NOT NULL THEN vid END) signed,
                                  count(*) visits, sum(views) views
                           FROM visits WHERE start >= ? AND start < ?{R.adm} GROUP BY day""", (R.t0, R.t1)):
        if x["day"] in daily:
            daily[x["day"]].update(visitors=x["visitors"], signed=x["signed"], visits=x["visits"], views=x["views"] or 0)
    if _has_u(r):
        for created, onboarded in r.execute("SELECT created, onboarded FROM u.users WHERE created >= ? AND created < ?", (R.t0, R.t1)):
            d = day_of(created)
            if d in daily:
                daily[d]["new_accounts"] += 1
                daily[d]["set_up"] += bool(onboarded)  # of the accounts made that day, those that have finished setup
    heat = [[0] * 24 for _ in range(7)]  # [Sunday..Saturday][hour]
    for x in r.execute(f"SELECT day, hour, count(*) n FROM visits WHERE start >= ? AND start < ?{R.adm} GROUP BY day, hour",
                       (R.t0, R.t1)):
        wd = (datetime.strptime(str(x["day"]), "%Y%m%d").weekday() + 1) % 7
        heat[wd][x["hour"] or 0] += x["n"]
    return {"kpi": kpi, "prev": prev, "daily": list(daily.values()), "heat": heat, "funnel": funnel(R), "live": live(R)}


def live(R):
    r = reader()
    name = "(SELECT name FROM u.users WHERE id=v.uid)" if _has_u(r) else "NULL"
    return [dict(v) for v in r.execute(f"""
        SELECT v.uid, {name} name, v.device, v.os, v.browser, v.country, v.city, v.start, v.last, v.views, v.ref,
               (SELECT page || coalesce(':' || a, '') FROM events e WHERE e.sid=v.sid AND e.kind='view' ORDER BY ts DESC LIMIT 1) here
        FROM visits v WHERE last >= ?{R.adm} ORDER BY last DESC LIMIT 50""", (time.time() - LIVE_SECONDS,))]


def funnel(R):
    """Browsers seen for the first time in the period, and how far each got."""
    r = reader()
    adm = "" if R.owner else " AND max(admin)=0"
    first = {x[0]: (x[1], x[2]) for x in r.execute(
        f"SELECT vid, min(start), min(day) FROM visits WHERE vid IS NOT NULL GROUP BY vid HAVING min(start) >= ? AND min(start) < ?{adm}",
        (R.t0, R.t1))}
    if not first:
        return [{"step": s, "n": 0} for s in FUNNEL]
    f = (f"(SELECT vid FROM visits WHERE vid IS NOT NULL GROUP BY vid HAVING min(start) >= {float(R.t0)!r} "
         f"AND min(start) < {float(R.t1)!r}{adm})")
    has = lambda sql: {x[0] for x in r.execute(sql)} & first.keys()
    landed = has(f"SELECT DISTINCT vid FROM events WHERE vid IN {f} AND kind='view' AND page='landing'")
    opened = has(f"SELECT DISTINCT vid FROM events WHERE vid IN {f} AND kind='view' AND page != 'landing'")
    started = has(f"SELECT DISTINCT vid FROM events WHERE vid IN {f} AND kind IN ('signin_start', 'signin_click')")
    signed = has(f"SELECT DISTINCT vid FROM visits WHERE vid IN {f} AND uid IS NOT NULL") | \
        has(f"SELECT DISTINCT vid FROM events WHERE vid IN {f} AND kind='signin'")
    setup = set()
    if _has_u(r):
        setup = has(f"""SELECT DISTINCT v.vid FROM visits v JOIN u.users us ON us.id=v.uid
                         WHERE v.vid IN {f} AND us.onboarded=1""")
    added = has(f"SELECT DISTINCT vid FROM events WHERE vid IN {f} AND kind='add'")
    checkout = has(f"SELECT DISTINCT vid FROM events WHERE vid IN {f} AND kind='view' AND page='basket'")
    back = {vid for vid, d in r.execute(f"SELECT vid, day FROM visits WHERE vid IN {f}") if vid in first and d > first[vid][1]}
    accounts_mode = bool(started or signed)
    steps = [len(first), len(opened) if landed else None, len(started) if accounts_mode else None,
             len(signed) if accounts_mode else None, len(setup), len(added), len(checkout), len(back)]
    return [{"step": s, "n": n} for s, n in zip(FUNNEL, steps) if n is not None]


FUNNEL = ("New browsers", "Went past the landing page", "Tapped sign-in", "Signed in", "Finished setup", "Added a product", "Opened checkout", "Came back another day")


def usage(R, names=lambda keys: {}):
    r = reader()
    q = lambda sql, *a: [dict(x) for x in r.execute(sql, (R.t0, R.t1, *a))]
    W = f"ts >= ? AND ts < ?{R.adm}"
    pages = q(f"""SELECT page, count(*) views, count(DISTINCT vid) visitors FROM events WHERE {W} AND kind='view'
                  GROUP BY page ORDER BY views DESC""")
    dw = {}
    for x in r.execute(f"SELECT page, n, data FROM events WHERE {W} AND kind='dwell' AND n IS NOT NULL", (R.t0, R.t1)):
        d = dw.setdefault(x["page"], {"ms": [], "depth": []})
        d["ms"].append(x["n"])
        dep = (json.loads(x["data"]) if x["data"] else {}).get("depth")
        if isinstance(dep, (int, float)) and not isinstance(dep, bool):  # visitors send this: only numbers count
            d["depth"].append(dep)
    exits = {x["page"]: x["n"] for x in r.execute(f"""
        SELECT page, count(*) n FROM (SELECT sid, page, max(ts) FROM events WHERE {W} AND kind='view' GROUP BY sid) GROUP BY page""",
        (R.t0, R.t1))}
    for p in pages:
        d = dw.get(p["page"], {"ms": [], "depth": []})
        p["dwell_median"] = _pct(sorted(d["ms"]), 50)
        p["depth_avg"] = round(sum(d["depth"]) / len(d["depth"])) if d["depth"] else None
        p["exits"] = exits.get(p["page"], 0)
    searches = q(f"""SELECT a q, count(*) n, count(DISTINCT vid) people, round(avg(n)) results, sum(b='add') adds,
                            sum(b='open') opens, sum(coalesce(b, 'none')='none') AS "nothing", max(ts) last
                     FROM events WHERE {W} AND kind='search' AND a IS NOT NULL GROUP BY a ORDER BY n DESC, last DESC LIMIT 60""")
    cats = q(f"""SELECT a slug, count(*) views, count(DISTINCT vid) people FROM events WHERE {W} AND kind='view' AND page='browse'
                 AND a IS NOT NULL GROUP BY a ORDER BY views DESC LIMIT 40""")
    viewed = q(f"""SELECT a key, count(*) n, count(DISTINCT vid) people FROM events WHERE {W} AND kind='view' AND page='product'
                   AND a IS NOT NULL GROUP BY a ORDER BY n DESC LIMIT 30""")
    added = q(f"""SELECT a key, count(*) n, count(DISTINCT vid) people, sum(coalesce(n, 1)) qty FROM events WHERE {W} AND kind='add'
                  AND a IS NOT NULL GROUP BY a ORDER BY n DESC LIMIT 30""")
    added_from = q(f"SELECT page, count(*) n FROM events WHERE {W} AND kind='add' GROUP BY page ORDER BY n DESC")
    removed = q(f"""SELECT a key, count(*) n FROM events WHERE {W} AND kind='remove' AND a IS NOT NULL GROUP BY a ORDER BY n DESC LIMIT 20""")
    filters = q(f"SELECT a type, b value, count(*) n FROM events WHERE {W} AND kind='filter' GROUP BY a, b ORDER BY n DESC LIMIT 30")
    clicks = q(f"""SELECT page, b zone, a label, count(*) n, count(DISTINCT vid) people FROM events WHERE {W} AND kind='click'
                   GROUP BY page, b, a ORDER BY n DESC LIMIT 60""")
    dialogs = q(f"SELECT a dialog, count(*) n, count(DISTINCT vid) people FROM events WHERE {W} AND kind='dialog' GROUP BY a ORDER BY n DESC")
    carts = q(f"""SELECT kind || ':' || coalesce(a, '') action, count(*) n FROM events WHERE {W}
                  AND kind IN ('cart', 'cart_new', 'cart_delete') GROUP BY kind, a ORDER BY n DESC""")
    comp = r.execute(f"""SELECT count(*) n, avg(n) items, sum(json_extract(data, '$.complete') > 0) any_complete,
                                sum(coalesce(json_extract(data, '$.complete'), 0) = 0) none_complete
                         FROM events WHERE {W} AND kind='compare'""", (R.t0, R.t1)).fetchone()
    best = q(f"""SELECT json_extract(data, '$.best') chain, count(*) n FROM events WHERE {W} AND kind='compare'
                 AND json_extract(data, '$.best') IS NOT NULL GROUP BY chain ORDER BY n DESC LIMIT 12""")
    keys = {x["key"] for x in viewed + added + removed}
    nm = names(keys) if keys else {}
    for x in viewed + added + removed:
        x["name"] = nm.get(x["key"])
    return {"pages": pages, "searches": searches, "categories": cats, "viewed": viewed, "added": added, "added_from": added_from,
            "removed": removed, "filters": filters, "clicks": clicks, "dialogs": dialogs, "carts": carts,
            "compare": dict(comp) if comp else {}, "best": best, "prefs": _prefs(r)}


def _prefs(r):
    """How accounts have Sal set up: distance, in store / delivered, club prices, language, town."""
    if not _has_u(r):
        return {}
    km, mode, club, lang, towns, onboarded, total = {}, {}, {}, {}, {}, 0, 0
    for u in r.execute("SELECT prefs, lang, onboarded FROM u.users"):
        total += 1
        onboarded += bool(u["onboarded"])
        try:
            p = json.loads(u["prefs"] or "{}")
        except ValueError:
            p = {}
        k = str(p.get("max_km") or "any")
        km[k] = km.get(k, 0) + 1
        m = p.get("mode") or "store"
        mode[m] = mode.get(m, 0) + 1
        cl = "on" if p.get("club") else "off"
        club[cl] = club.get(cl, 0) + 1
        lg = u["lang"] or "he"
        lang[lg] = lang.get(lg, 0) + 1
        t = town_of(p)
        if t:
            towns[t] = towns.get(t, 0) + 1
    srt = lambda d: sorted(({"k": k, "n": n} for k, n in d.items()), key=lambda x: -x["n"])
    return {"accounts": total, "onboarded": onboarded, "km": srt(km), "mode": srt(mode), "club": srt(club), "lang": srt(lang),
            "towns": srt(towns)[:20]}


def stuck(R):
    """Where people get stuck or something breaks."""
    r = reader()
    q = lambda sql, *a: [dict(x) for x in r.execute(sql, (R.t0, R.t1, *a))]
    W = f"ts >= ? AND ts < ?{R.adm}"
    rage = q(f"""SELECT page, b zone, a label, count(*) n, count(DISTINCT vid) people, max(ts) last FROM events WHERE {W}
                 AND kind='rage' GROUP BY page, b, a ORDER BY n DESC LIMIT 30""")
    dead = q(f"""SELECT page, b zone, a label, count(*) n, count(DISTINCT vid) people, max(ts) last FROM events WHERE {W}
                 AND kind='dead' GROUP BY page, b, a ORDER BY n DESC LIMIT 30""")
    zero = q(f"""SELECT a q, count(*) n, count(DISTINCT vid) people, max(ts) last FROM events WHERE {W} AND kind='search' AND n=0
                 AND a IS NOT NULL GROUP BY a ORDER BY n DESC, last DESC LIMIT 40""")
    nothing = q(f"""SELECT a q, count(*) n, round(avg(n)) results, max(ts) last FROM events WHERE {W} AND kind='search'
                    AND coalesce(b, 'none')='none' AND n > 0 AND a IS NOT NULL GROUP BY a ORDER BY n DESC LIMIT 30""")
    # a search with no results followed within 2 minutes by another one: what people meant
    rephrased = q(f"""SELECT e.a tried, f.a then_q, f.n then_n, e.ts FROM events e
                      JOIN events f ON f.id = (SELECT g.id FROM events g WHERE g.sid=e.sid AND g.kind='search'
                                               AND (g.ts > e.ts OR (g.ts = e.ts AND g.id > e.id)) AND g.ts < e.ts + 120
                                               ORDER BY g.ts, g.id LIMIT 1)
                      WHERE e.ts >= ? AND e.ts < ?{R.adm_e} AND e.kind='search' AND e.n=0 AND e.a IS NOT NULL
                      ORDER BY e.ts DESC LIMIT 40""")
    errors = q(f"""SELECT a message, b source, page, count(*) n, count(DISTINCT vid) people, max(ts) last FROM events WHERE {W}
                   AND kind='error' AND {OURS} GROUP BY a, b ORDER BY n DESC LIMIT 30""")
    api = q(f"""SELECT a path, cast(n AS INTEGER) status, count(*) n, count(DISTINCT vid) people, max(ts) last FROM events WHERE {W}
                AND kind='api_fail' GROUP BY a, n ORDER BY n DESC LIMIT 30""")
    toasts = q(f"""SELECT a message, page, count(*) n, count(DISTINCT vid) people, max(ts) last FROM events WHERE {W}
                   AND kind='toast' GROUP BY a ORDER BY n DESC LIMIT 20""")
    signin = q(f"""SELECT a provider, b reason, count(*) n, max(ts) last FROM events WHERE {W} AND kind='signin_fail'
                   GROUP BY a, b ORDER BY n DESC""")
    # sign-in started but never finished, by in-app browser: Google refuses to sign in inside Facebook/Instagram's browser
    inapp = q(f"""SELECT coalesce(v.inapp, v.browser) browser, count(DISTINCT e.vid) started,
                         count(DISTINCT CASE WHEN v2.uid IS NOT NULL THEN e.vid END) finished
                  FROM events e JOIN visits v ON v.sid=e.sid LEFT JOIN visits v2 ON v2.vid=e.vid AND v2.uid IS NOT NULL
                  WHERE e.ts >= ? AND e.ts < ?{R.adm_e} AND e.kind IN ('signin_click', 'signin_start')
                  GROUP BY 1 ORDER BY started DESC""")
    onboard = q(f"""SELECT a step, count(DISTINCT coalesce(uid, vid)) people FROM events WHERE {W} AND kind='onboard'
                    GROUP BY a ORDER BY a""")
    exits = q(f"""SELECT page, count(*) n FROM (SELECT sid, page, max(ts) FROM events WHERE {W} AND kind='view' GROUP BY sid)
                  GROUP BY page ORDER BY n DESC""")
    bounces = q(f"""SELECT coalesce(landing, '') landing, count(*) n FROM visits WHERE start >= ? AND start < ?{R.adm}
                    AND views <= 1 AND last - start < 10 GROUP BY 1 ORDER BY n DESC LIMIT 15""")
    no_store = r.execute(f"""SELECT count(*) FROM events WHERE {W} AND kind='compare' AND n > 0
                             AND coalesce(json_extract(data, '$.complete'), 0)=0""", (R.t0, R.t1)).fetchone()[0]
    perf = {}
    for x in r.execute(f"""SELECT v.device, e.n, e.data FROM events e JOIN visits v ON v.sid=e.sid
                           WHERE e.ts >= ? AND e.ts < ?{R.adm_e} AND e.kind='perf'""", (R.t0, R.t1)):
        d = perf.setdefault(x["device"] or "?", {"load": [], "lcp": [], "ttfb": []})
        if x["n"]:
            d["load"].append(x["n"])
        extra = json.loads(x["data"]) if x["data"] else {}
        for k in ("lcp", "ttfb"):
            if extra.get(k):
                d[k].append(extra[k])
    perf = [{"device": k, "n": len(v["load"]), **{f"{m}_p75": _pct(sorted(v[m]), 75) for m in ("load", "lcp", "ttfb")}}
            for k, v in sorted(perf.items())]
    server_err = q(f"""SELECT a route, b error, count(*) n, max(ts) last FROM events WHERE {W} AND kind='server_error'
                       GROUP BY a, b ORDER BY last DESC LIMIT 20""")
    return {"rage": rage, "dead": dead, "zero": zero, "nothing": nothing, "rephrased": rephrased, "errors": errors, "api": api,
            "toasts": toasts, "signin": signin, "inapp": inapp, "onboard": onboard, "exits": exits, "bounces": bounces,
            "no_store": no_store, "perf": perf, "server_errors": server_err}


def audience(R):
    r = reader()
    W = f"start >= ? AND start < ?{R.adm}"
    by = lambda col, lim=20: [dict(x) for x in r.execute(
        f"""SELECT coalesce({col}, '') k, count(*) visits, count(DISTINCT vid) people FROM visits WHERE {W}
            GROUP BY 1 ORDER BY visits DESC LIMIT {lim}""", (R.t0, R.t1))]
    widths = [dict(x) for x in r.execute(f"""SELECT CASE WHEN vw IS NULL THEN '' WHEN vw < 600 THEN 'under 600 px'
                                                     WHEN vw < 1100 THEN '600–1099 px' WHEN vw < 1280 THEN '1100–1279 px'
                                                     ELSE '1280 px +' END k, count(*) visits, count(DISTINCT vid) people
                                              FROM visits WHERE {W} GROUP BY 1 ORDER BY visits DESC""", (R.t0, R.t1))]
    firsts = dict(r.execute("SELECT vid, min(start) FROM visits WHERE vid IS NOT NULL GROUP BY vid").fetchall())
    new = ret = 0
    for (vid,) in r.execute(f"SELECT DISTINCT vid FROM visits WHERE {W} AND vid IS NOT NULL", (R.t0, R.t1)):
        if firsts.get(vid, 0) >= R.t0:
            new += 1
        else:
            ret += 1
    return {"device": by("device"), "os": by("os"), "browser": by("browser"), "inapp": by("inapp"), "country": by("country"),
            "city": by("city"), "lang": by("lang"), "theme": by("theme"), "width": widths, "ref": by("ref", 30),
            "utm": by("utm"), "via": by("via"), "pwa": by("pwa"), "tz": by("tz"), "new": new, "returning": ret}


def users(R):
    """Every account with what it has done (all time) and in the period."""
    r = reader()
    if not _has_u(r):
        return []
    out = []
    agg = {x["uid"]: dict(x) for x in r.execute("""
        SELECT uid, count(*) visits, count(DISTINCT day) days, max(last) last, min(start) first, sum(views) views
        FROM visits WHERE uid IS NOT NULL GROUP BY uid""")}
    per = {x["uid"]: dict(x) for x in r.execute("""
        SELECT uid, count(*) visits FROM visits WHERE uid IS NOT NULL AND start >= ? AND start < ? GROUP BY uid""", (R.t0, R.t1))}
    evs = {x["uid"]: dict(x) for x in r.execute(f"""
        SELECT uid, sum(kind='search') searches, sum(kind='add') adds, sum(kind='view' AND page='basket') checkouts,
               sum(kind IN ('rage', 'dead', 'error') AND {OURS}) trouble FROM events WHERE uid IS NOT NULL GROUP BY uid""")}
    lastv = {x["uid"]: dict(x) for x in r.execute("""
        SELECT uid, device, os, browser, country, city FROM visits v WHERE uid IS NOT NULL
        AND start = (SELECT max(start) FROM visits w WHERE w.uid=v.uid)""")}
    carts = {x["user_id"]: dict(x) for x in r.execute("""
        SELECT c.user_id, count(DISTINCT c.id) carts, count(i.key) items FROM u.carts c LEFT JOIN u.cart_items i ON i.cart_id=c.id
        GROUP BY c.user_id""")}
    prov = {}
    for x in r.execute("SELECT user_id, provider, email FROM u.identities ORDER BY created"):
        prov.setdefault(x["user_id"], []).append(x["provider"])
    for u in r.execute("SELECT id, name, email, avatar, created, seen, lang, onboarded, admin, prefs FROM u.users ORDER BY seen DESC"):
        try:
            p = json.loads(u["prefs"] or "{}")
        except ValueError:
            p = {}
        home = p.get("home") or {}
        out.append({"id": u["id"], "name": u["name"], "email": u["email"], "avatar": u["avatar"], "created": u["created"],
                    "seen": max(u["seen"] or 0, (agg.get(u["id"]) or {}).get("last") or 0), "lang": u["lang"],
                    "onboarded": bool(u["onboarded"]), "providers": prov.get(u["id"], []),
                    "town": town_of(p),
                    "km": p.get("max_km"), "mode": p.get("mode") or "store",
                    **{k: (agg.get(u["id"]) or {}).get(k) or 0 for k in ("visits", "days", "views")},
                    "visits_period": (per.get(u["id"]) or {}).get("visits") or 0,
                    **{k: (evs.get(u["id"]) or {}).get(k) or 0 for k in ("searches", "adds", "checkouts", "trouble")},
                    **{k: (carts.get(u["id"]) or {}).get(k) or 0 for k in ("carts", "items")},
                    **{k: (lastv.get(u["id"]) or {}).get(k) for k in ("device", "os", "browser", "country", "city")}})
    return out


def user(uid, names=lambda keys: {}):
    r = reader()
    if not _has_u(r):
        return None
    u = r.execute("SELECT * FROM u.users WHERE id=?", (uid,)).fetchone()
    if not u:
        return None
    u = dict(u)
    try:
        prefs = json.loads(u.pop("prefs") or "{}")
    except ValueError:
        prefs = {}
    try:
        delivery = json.loads(u.pop("delivery") or "{}")
    except ValueError:
        delivery = {}
    ids = [dict(x) for x in r.execute("SELECT provider, email, verified, created FROM u.identities WHERE user_id=? ORDER BY created", (uid,))]
    carts = []
    for c in r.execute("SELECT id, name, created, updated FROM u.carts WHERE user_id=? ORDER BY updated DESC", (uid,)):
        items = [dict(x) for x in r.execute("SELECT key, qty, name FROM u.cart_items WHERE cart_id=? ORDER BY position", (c["id"],))]
        carts.append({**dict(c), "items": items})
    unnamed = {i["key"] for c in carts for i in c["items"] if not i["name"]}
    if unnamed:
        nm = names(unnamed)
        for c in carts:
            for i in c["items"]:
                i["name"] = i["name"] or nm.get(i["key"])
    sessions = r.execute("SELECT count(*) FROM u.sessions WHERE user_id=? AND expires > ?", (uid, time.time())).fetchone()[0]
    visits = [dict(x) for x in r.execute("""SELECT sid, start, last, views, events, device, os, browser, inapp, country, city, via,
                                                    ref, utm, landing, lang, theme, screen, pwa
                                             FROM visits WHERE uid=? OR vid IN (SELECT vid FROM visits WHERE uid=?)
                                             ORDER BY start DESC LIMIT 60""", (uid, uid))]
    events = [dict(x) for x in r.execute("""SELECT ts, src, kind, page, a, b, n, data FROM events
                                             WHERE uid=? OR vid IN (SELECT vid FROM visits WHERE uid=? AND vid IS NOT NULL)
                                             ORDER BY ts DESC LIMIT 400""", (uid, uid))]
    keys = {e["a"] for e in events if e["kind"] in ("add", "remove") or (e["kind"] == "view" and e["page"] == "product")} - {None}
    nm = names(keys) if keys else {}
    for e in events:
        if e["a"] in nm:
            e["name"] = nm[e["a"]]
    home = prefs.get("home") or {}
    return {"user": u, "identities": ids, "carts": carts, "sessions": sessions, "visits": visits, "events": events,
            "prefs": {"address": home.get("label"), "precision": home.get("precision"), "km": prefs.get("max_km"),
                      "mode": prefs.get("mode"), "club": prefs.get("club"),
                      "delivers_here": sum(1 for v in delivery.values() if isinstance(v, dict) and v.get("delivers_here")),
                      "fee_edits": sum(1 for v in delivery.values() if isinstance(v, dict) and set(v) - {"delivers_here"})}}


def server(R):
    r = reader()
    d0, d1 = day_of(R.t0), day_of(R.t1 - 1)
    cols = ", ".join(f"sum(b{i}) b{i}" for i in range(8))
    routes = []
    for x in r.execute(f"""SELECT route, method, sum(n) n, sum(ms) ms, max(max_ms) max_ms, sum(CASE WHEN status >= 500 THEN n END) e5,
                                  sum(CASE WHEN status >= 400 AND status < 500 AND status != 401 THEN n END) e4,
                                  sum(CASE WHEN status = 401 THEN n END) e401, sum(CASE WHEN bot THEN n END) bots, {cols}
                           FROM requests WHERE day BETWEEN ? AND ? GROUP BY route, method ORDER BY n DESC""", (d0, d1)):
        routes.append({"route": x["route"], "method": x["method"], "n": x["n"], "avg": round(x["ms"] / x["n"]) if x["n"] else None,
                       "p50": _hist_pct(x, 50), "p95": _hist_pct(x, 95), "max": round(x["max_ms"] or 0),
                       "e5": x["e5"] or 0, "e4": x["e4"] or 0, "e401": x["e401"] or 0, "bots": x["bots"] or 0})
    daily = {d: {"day": d, "n": 0, "e5": 0, "bots": 0} for d in R.day_list}
    for x in r.execute("""SELECT day, sum(n) n, sum(CASE WHEN status >= 500 THEN n END) e5, sum(CASE WHEN bot THEN n END) bots
                          FROM requests WHERE day BETWEEN ? AND ? GROUP BY day""", (d0, d1)):
        if x["day"] in daily:
            daily[x["day"]].update(n=x["n"], e5=x["e5"] or 0, bots=x["bots"] or 0)
    status = [dict(x) for x in r.execute("""SELECT status, sum(n) n FROM requests WHERE day BETWEEN ? AND ? GROUP BY status
                                            ORDER BY n DESC""", (d0, d1))]
    errs = [dict(x) for x in r.execute("""SELECT ts, a route, b error FROM events WHERE kind='server_error' AND ts >= ? AND ts < ?
                                          ORDER BY ts DESC LIMIT 30""", (R.t0, R.t1))]
    size = {}
    for name, p in (("stats.db", path()),):
        try:
            size[name] = os.path.getsize(p) + (os.path.getsize(p + "-wal") if os.path.exists(p + "-wal") else 0)
        except OSError:
            pass
    counts = r.execute("SELECT (SELECT count(*) FROM events) events, (SELECT count(*) FROM visits) visits").fetchone()
    return {"routes": routes, "daily": list(daily.values()), "status": status, "errors": errs, "stats_size": size,
            "rows": dict(counts), "keep_days": KEEP_DAYS}
