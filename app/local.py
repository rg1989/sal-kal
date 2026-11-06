"""Sal-Kal on your own computer.

One program serves the app on http://localhost:<port>, runs the setup wizard in the browser (/setup) and keeps the
prices current. It runs the downloader, the indexer, store locations and deals for the stores near your own places
only: the full national set is ~70 GB of files a day, the stores around one address a few hundred MB.
The installers (deploy/local/install.sh for macOS and Linux, install.ps1 for Windows) put everything in one folder:
  macOS    ~/Library/Application Support/Sal-Kal
  Windows  %LOCALAPPDATA%\\Sal-Kal
  Linux    ~/.local/share/sal-kal
which holds bin/ (uv), python/, venv/, app/ (this repository), data/ (prices, carts, settings, log) and browsers/
(Chromium, which some chains' portals need). Nothing is written outside it except the shortcuts and the
start-at-login entry, and `uninstall` removes those too.
Commands: python -m app.local serve | open | install | stop | uninstall [--keep-data] | fetch <types> [regex]
"""
import json
import logging
import logging.handlers
import math
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent          # the copy of the repository (ROOT/app)
ROOT = Path(os.environ.get("SAL_HOME") or APP_DIR.parent)
DATA = ROOT / "data"
DUMPS = DATA / "dumps"
BROWSERS = ROOT / "browsers"
CONFIG = DATA / "local.json"
PIDFILE = DATA / "runner.pid"
LOGFILE = DATA / "sal-kal.log"
SYSTEM = {"Darwin": "mac", "Windows": "windows"}.get(platform.system(), "linux")
NAME = "Sal-Kal"
LABEL = "com.sal-kal.local"          # launchd label / systemd unit stem
PORTS = range(8080, 8100)
EVERY_H = 6                          # how often prices are refreshed (the chains publish full price files daily)
STORES_EVERY_H = 24                  # the store lists
KEEP_FILES_H = 30                    # a price file the indexer never got to is deleted this long after it arrived
MAX_KM = 40                          # the largest area downloaded around a place, whatever the app's distance says
MARGIN_KM = 2                        # beyond your distance: a store placed at its town centre may be a little off
NO_WINDOW = 0x08000000 if SYSTEM == "windows" else 0   # CREATE_NO_WINDOW: no console flashing up on Windows

os.environ.setdefault("BASKET_DB", str(DATA / "basket.db"))
os.environ.setdefault("DUMPS_PATH", str(DUMPS))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS))
os.environ["SAL_LOCAL"] = "1"

log = logging.getLogger("sal-kal")
_cfg_lock = threading.Lock()
DEFAULTS = {"port": 8080, "lan": False, "autostart": True, "phase": "welcome", "last_run": 0, "last_stores": 0,
            "browser": False, "covered": [], "version": 1}


# ---------------------------------------------------------------- settings (data/local.json)
def load():
    try:
        return {**DEFAULTS, **json.loads(CONFIG.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save(**kw):
    with _cfg_lock:
        cfg = {**load(), **kw}
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, CONFIG)
        return cfg


def url(cfg=None, path=""):
    return f"http://localhost:{(cfg or load())['port']}{path}"


def lan_url(cfg=None):
    cfg = cfg or load()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))   # nothing is sent: this only asks which address the LAN route uses
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        return None
    return f"http://{ip}:{cfg['port']}"


def _logging():
    DATA.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(LOGFILE, maxBytes=5_000_000, backupCount=1, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(h)
    root.setLevel(logging.INFO)


# ---------------------------------------------------------------- the update pipeline
def _py(*args, timeout, on_line=None):
    """Runs `python <args>` from the app folder, its output into the log. Returns (exit code, last lines)."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    p = subprocess.Popen([sys.executable, *args], cwd=APP_DIR, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                         errors="replace", creationflags=NO_WINDOW)
    tail, killer = [], threading.Timer(timeout, p.kill)
    killer.start()
    try:
        for line in p.stdout:
            line = line.rstrip()
            if line and " DEBUG " not in line:   # the downloader logs every request at DEBUG
                log.info("%s: %s", args[1] if args[0] == "-m" else args[0], line[:400])
                tail = (tail + [line])[-20:]
                if on_line:
                    on_line(line)
        return p.wait(), tail
    finally:
        killer.cancel()


_tl = threading.local()


def _con():
    """One connection per thread (connect() checks the schema each time: not something to repeat every 30 s)."""
    c = getattr(_tl, "con", None)
    if c is None:
        from . import db as dbmod
        c = _tl.con = dbmod.connect()
    return c


def _household():
    """The household's prefs (local mode: everyone is account 1) and whether it finished the first-run setup."""
    try:
        r = _con().execute("SELECT prefs, onboarded FROM u.users WHERE id=1").fetchone()
    except Exception:
        return {}, False
    if not r:
        return {}, False
    try:
        p = json.loads(r[0] or "{}")
    except ValueError:
        p = {}
    return p, bool(r[1])


def _places(p):
    pl = p.get("places")
    if isinstance(pl, list) and pl:
        return [x for x in pl if isinstance(x, dict) and "lat" in x]
    return [p[k] for k in ("home", "work") if isinstance(p.get(k), dict) and "lat" in p[k]]


def _seg_km(lat, lon, a, b):
    """Distance from a point to the straight segment a-b, in km (flat-earth: fine at city scale)."""
    k = 111.32 * math.cos(math.radians(lat))
    px, py = lon * k, lat * 110.57
    ax, ay, bx, by = a["lon"] * k, a["lat"] * 110.57, b["lon"] * k, b["lat"] * 110.57
    dx, dy = bx - ax, by - ay
    t = 0 if dx == dy == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def wanted_stores(con=None):
    """Every branch the app may price for you: within your distance (+ a margin) of any of your places, and along the
    way between two of them. [(chain_id, store_id)]."""
    from . import geo
    con = con or _con()
    p, _ = _household()
    places = _places(p)
    if not places:
        return []
    km = p.get("max_km")
    r = min(float(km) if isinstance(km, (int, float)) and km > 0 else MAX_KM, MAX_KM) + MARGIN_KM
    by = {x.get("id"): x for x in places}
    a, b = by.get(p.get("from")) or places[0], by.get(p.get("to"))
    route = p.get("area") == "route" and b is not None and b is not a
    detour = float(p.get("detour_km") or 1) + MARGIN_KM
    out = []
    for sk, (lat, lon) in geo.store_coords(con).items():
        if any(geo.km(lat, lon, x["lat"], x["lon"]) <= r for x in places) or (route and _seg_km(lat, lon, a, b) <= detour):
            out.append(sk)
    return sorted(out)


def _regex(stores):
    """The downloader's file-name filter for these stores' full price and deal files."""
    by = {}
    for chain, store in stores:
        by.setdefault(chain, set()).add(int(store))
    alts = [f"{c}-(?:\\d+-)?0*(?:{'|'.join(str(s) for s in sorted(ids))})-" for c, ids in sorted(by.items())]
    return f"(?i)(?:price|promo)full(?:{'|'.join(alts)})"


class Pipeline:
    """The one background thread: the first-run steps, then a price refresh every EVERY_H hours, and at once when
    your places or distance bring in stores that were never downloaded."""

    def __init__(self):
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.progress = {}
        self.running = False

    def state(self):
        with self.lock:
            return dict(self.progress)

    def _set(self, **kw):
        with self.lock:
            self.progress.update(kw)

    def start(self):
        threading.Thread(target=self._loop, name="sal-kal-updates", daemon=True).start()

    def _loop(self):
        time.sleep(2)
        while True:
            try:
                self._tick()
            except Exception as e:  # never let the thread die: try again later
                log.exception("update failed")
                self._set(step=None, error=str(e)[:300])
                self.running = False
            self.wake.wait(timeout=30)
            self.wake.clear()

    def _tick(self):
        cfg = load()
        ph = cfg["phase"]
        if ph == "installing":
            self.first_steps()
        elif ph == "area":
            p, onboarded = _household()
            if onboarded and _places(p):
                save(phase="prices")
                self.wake.set()
        elif ph == "prices":
            self.update(first=True)
        elif ph == "ready":
            due = time.time() - cfg["last_run"] > EVERY_H * 3600
            covered = {tuple(x) for x in cfg["covered"]}
            if due or set(wanted_stores()) - covered:
                self.update()

    # -- steps
    def _step(self, name, total=None):
        self._set(step=name, done=0, total=total, error=None, started=int(time.time()))

    def first_steps(self):
        self.running = True
        self._set(error=None, warning=None)
        cfg = load()
        if not cfg["browser"]:
            self._step("browser")
            code, tail = _py("-m", "playwright", "install", "--only-shell", "chromium", timeout=1800)
            if code == 0:
                save(browser=True)
            else:  # only a few chains' portals need it: carry on without them
                log.warning("Chromium install failed: %s", tail[-3:])
                self._set(warning="browser")
        self.stores()
        self._step("map")
        save(phase="area" if not _household()[1] else "prices")
        self.running = False
        self._set(step=None)
        self.wake.set()

    def stores(self):
        from . import geo
        self._prune()
        self._step("stores", total=self._chains())
        t0 = time.time()
        stop = threading.Event()
        threading.Thread(target=self._count_store_files, args=(t0, stop), daemon=True).start()
        try:
            _py("-m", "app.local", "fetch", "STORE_FILE", timeout=45 * 60)
        finally:
            stop.set()
        self._index()
        con = _con()
        n = con.execute("SELECT count(*) FROM stores").fetchone()[0]
        if not n:
            raise RuntimeError("no-stores")
        self._step("map")
        seed(con)
        _py("-m", "app.geo", "--budget", "150", timeout=15 * 60)   # stores the shipped list doesn't have
        save(last_stores=int(time.time()))

    def update(self, first=False):
        self.running = True
        try:
            if time.time() - load()["last_stores"] > STORES_EVERY_H * 3600:
                self.stores()
            want = wanted_stores()
            if not want:
                if first:
                    save(phase="area")
                return
            self._step("prices", total=2 * len(want))
            self._prune()
            t0 = time.time()
            stop = threading.Event()
            threading.Thread(target=self._count_price_files, args=(t0, set(want), stop), daemon=True).start()
            try:
                _py("-m", "app.local", "fetch", "PRICE_FULL_FILE,PROMO_FULL_FILE", _regex(want), timeout=2 * 3600)
            finally:
                stop.set()
            self._index()
            self._step("deals")
            _py("-m", "app.deals", timeout=30 * 60)
            _py("-m", "app.geo", "--budget", "100", timeout=10 * 60)
            save(last_run=int(time.time()), covered=[list(x) for x in want], **({"phase": "ready"} if first else {}))
            self._set(step=None, finished=int(time.time()), stores=len(want))
        finally:
            self.running = False

    def _index(self):
        self._step("index")
        stop = threading.Event()

        def watch():
            while not stop.wait(2):
                try:
                    r = _con().execute("SELECT value FROM settings WHERE key='index_status'").fetchone()
                    s = json.loads(r[0]) if r else {}
                    self._set(done=s.get("processed") or 0, total=s.get("total"))
                except Exception:
                    pass
        threading.Thread(target=watch, daemon=True).start()
        t0 = time.time()
        try:
            code, _ = _py("-m", "app.indexer", "--dumps", str(DUMPS), timeout=3 * 3600)
        finally:
            stop.set()
        if code == 0:   # read into the database: the files themselves (GBs of XML) aren't needed again
            self._delete_files(before=t0)

    def _chains(self):
        code, tail = _py("-m", "app.local", "chains", timeout=120)
        try:
            return int(tail[-1])
        except (IndexError, ValueError):
            return None

    def _files(self, since):
        from .indexer import parse_filename
        for dirpath, dirs, files in os.walk(DUMPS):
            dirs[:] = [d for d in dirs if d != "status"]
            for f in files:
                try:
                    if os.path.getmtime(os.path.join(dirpath, f)) >= since - 1:
                        yield dirpath, parse_filename(f)
                except OSError:
                    pass

    def _count_store_files(self, since, stop):
        while not stop.wait(3):
            self._set(done=len({d for d, f in self._files(since) if f and f["kind"] == "stores"}))

    def _count_price_files(self, since, want, stop):
        while not stop.wait(3):
            got = {(f["kind"], f["chain"], f["store"]) for _, f in self._files(since)
                   if f and f["kind"] in ("pricefull", "promofull") and (f["chain"], f["store"]) in want}
            self._set(done=len(got))

    def _prune(self):
        """Price files left over from a run that stopped before indexing go after a day (the downloader's own status
        files say what it already fetched, so nothing is fetched twice); those status files are kept small
        (deploy/trim-status.py)."""
        self._delete_files(before=time.time() - KEEP_FILES_H * 3600)
        trim = APP_DIR / "deploy" / "trim-status.py"
        if trim.exists():
            _py(str(trim), str(DATA), timeout=600)

    def _delete_files(self, before):
        for dirpath, dirs, files in os.walk(DUMPS):
            dirs[:] = [d for d in dirs if d != "status"]
            for f in files:
                p = os.path.join(dirpath, f)
                try:
                    if os.path.getmtime(p) < before:
                        os.remove(p)
                except OSError:
                    pass


def seed(con):
    """Store locations shipped with the app (app/data/store-points.json, from OpenStreetMap via Nominatim): a new
    install places nearly every branch at once instead of an hour of lookups at Nominatim's 1 a second."""
    f = APP_DIR / "app" / "data" / "store-points.json"
    try:
        rows = json.loads(f.read_text(encoding="utf-8"))["points"]
    except (OSError, ValueError, KeyError):
        return 0
    now = int(time.time())
    con.executemany("INSERT OR IGNORE INTO geocache VALUES(?,?,?,?)", [(q, lat, lon, now) for q, lat, lon in rows])
    con.commit()
    return len(rows)


pipeline = Pipeline()


# ---------------------------------------------------------------- the setup page's API
CHECK = ["https://prices.shufersal.co.il/", "https://url.publishedprices.co.il/login", "https://laibcatalog.co.il/",
         "https://prices.super-pharm.co.il/", "https://shop.hazi-hinam.co.il/"]


def _reach(u):
    req = urllib.request.Request(u, method="GET", headers={"User-Agent": "Mozilla/5.0 (Sal-Kal setup check)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status < 400
    except urllib.error.HTTPError as e:
        return e.code not in (403, 451) and e.code < 500
    except Exception:
        return False


def check():
    """Can this computer reach the chains' price portals? Some only answer Israeli addresses."""
    res = {}
    threads = [threading.Thread(target=lambda u=u: res.__setitem__(u, _reach(u))) for u in CHECK]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    ok = sum(1 for v in res.values() if v)
    return {"ok": ok, "of": len(CHECK)}


def _disk():
    total = 0
    for dirpath, _, files in os.walk(DATA):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    free = shutil.disk_usage(DATA).free if DATA.exists() else None
    return {"used": total, "free": free}


def public_state():
    cfg = load()
    return {"phase": cfg["phase"]}


def state():
    cfg = load()
    p, onboarded = _household()
    try:
        c = _con()
        stores = c.execute("SELECT count(*) FROM stores").fetchone()[0]
        products = c.execute("SELECT count(*) FROM products").fetchone()[0]
    except Exception:
        stores = products = 0
    return {"phase": cfg["phase"], "progress": pipeline.state(), "running": pipeline.running,
            "url": url(cfg), "lan": cfg["lan"], "lan_url": lan_url(cfg) if cfg["lan"] else None,
            "autostart": cfg["autostart"], "os": SYSTEM, "folder": str(ROOT), "onboarded": onboarded,
            "places": len(_places(p)), "covered": len(cfg["covered"]), "stores": stores, "products": products,
            "last_run": cfg["last_run"], "next_run": cfg["last_run"] + EVERY_H * 3600 if cfg["last_run"] else None,
            "disk": _disk()}


def attach(app):
    """The setup page, its API and the local-only checks, on the app (app.server) before it starts."""
    from fastapi import HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
    from . import server

    @app.middleware("http")
    async def local_gate(request: Request, call_next):
        # Only this computer's own names (and, when you allow it, this network's addresses): a web page you visit
        # elsewhere can't reach Sal-Kal by pointing a domain of its own at 127.0.0.1 (DNS rebinding).
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
        ok = host in ("localhost", "127.0.0.1", "::1") or (load()["lan"] and (
            re.fullmatch(r"[\d.]+", host) or host.endswith(".local") or host == socket.gethostname().lower()))
        if not ok:
            return JSONResponse({"detail": "unknown host"}, status_code=421)
        if request.method == "GET" and request.url.path in ("/", "/app"):
            ph = load()["phase"]
            if ph in ("welcome", "installing", "prices"):
                return RedirectResponse("/setup", status_code=307)
            if ph == "area":  # the app's own first run asks for your address; then back to setup (local-app.js)
                page = server._page("index.html")
                body = page.body.replace(b"</body>", b'<script src="/static/local-app.js"></script>\n</body>', 1)
                headers = {k: v for k, v in page.headers.items() if k.lower() != "content-length"}
                return HTMLResponse(body, headers=headers)
        return await call_next(request)

    @app.get("/setup")
    def setup_page():
        return server._page("setup.html")

    @app.get("/api/local/state")
    def get_state():
        return state()

    @app.get("/api/local/check")
    def get_check():
        return check()

    @app.post("/api/local/start")
    def start_setup():
        cfg = load()
        if cfg["phase"] in ("welcome", "installing") and not pipeline.running:
            save(phase="installing")
        elif cfg["phase"] == "ready" and not pipeline.running:
            save(last_run=0)   # "update now"
        pipeline.wake.set()
        return state()

    @app.put("/api/local/settings")
    async def put_settings(request: Request):
        body = await request.json()
        out = {}
        if isinstance(body.get("autostart"), bool):
            out["autostart"] = body["autostart"]
            set_autostart(body["autostart"])
        if isinstance(body.get("lan"), bool):
            out["lan"] = body["lan"]
        if out:
            save(**out)
        if "lan" in out:   # listening on another address needs a restart: the new runner takes over
            threading.Timer(0.5, restart).start()
        return state()

    @app.post("/api/local/uninstall")
    async def post_uninstall(request: Request):
        body = await request.json()
        if body.get("confirm") != "uninstall":
            raise HTTPException(400, "confirm")
        threading.Timer(1.0, uninstall, kwargs={"keep_data": bool(body.get("keep_data"))}).start()
        return {"ok": True}


# ---------------------------------------------------------------- running it
def _alive(port, timeout=1.5):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/local/state", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _free(port, host):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def serve():
    if sys.stdout is None or sys.stderr is None:  # pythonw (Windows) has no console: uvicorn's logging needs one
        DATA.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(DATA / "console.log", "a", encoding="utf-8", buffering=1)
    _logging()
    cfg = load()
    if _alive(cfg["port"]):
        log.info("already running on %s", cfg["port"])
        return
    host = "0.0.0.0" if cfg["lan"] else "127.0.0.1"
    port = next((p for p in [cfg["port"], *PORTS] if _free(p, host)), None)
    if port is None:
        raise SystemExit("no free port in 8080-8099")
    if port != cfg["port"]:
        cfg = save(port=port)
    PIDFILE.write_text(str(os.getpid()))
    import uvicorn
    from . import server
    attach(server.app)
    pipeline.start()
    log.info("Sal-Kal on %s", url(cfg))
    uvicorn.run(server.app, host=host, port=port, log_level="warning", server_header=False)


def _detached(*args):
    """Starts `python -m app.local <args>` with no window and not tied to this terminal."""
    py = Path(sys.executable)
    if SYSTEM == "windows" and (py.parent / "pythonw.exe").exists():
        py = py.parent / "pythonw.exe"
    kw = dict(cwd=APP_DIR, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if SYSTEM == "windows":
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen([str(py), "-m", "app.local", *args], **kw)


def ensure_running():
    cfg = load()
    if _alive(cfg["port"]):
        return True
    if not _service_start():
        _detached("serve")
    for _ in range(60):
        time.sleep(0.5)
        if _alive(load()["port"]):
            return True
    return False


def open_app():
    if ensure_running():
        webbrowser.open(url(path="/setup" if load()["phase"] != "ready" else "/"))
    else:
        print(f"Sal-Kal didn't start. Its log: {LOGFILE}")


def restart():
    """The runner replaces itself (after a settings change it can't take while running)."""
    if _service_name():
        _service("restart")
    else:
        _detached("serve-after", str(os.getpid()))
    os._exit(0)


def _serve_after(pid):
    for _ in range(40):   # wait for the old runner to let go of the port
        if not _pid_alive(int(pid)):
            break
        time.sleep(0.25)
    serve()


def _pid_alive(pid):
    if SYSTEM == "windows":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                           creationflags=NO_WINDOW)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop():
    _service("stop")
    try:
        pid = int(PIDFILE.read_text())
    except (OSError, ValueError):
        return
    if pid != os.getpid() and _pid_alive(pid):
        if SYSTEM == "windows":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=NO_WINDOW)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            if not _pid_alive(pid):
                break
            time.sleep(0.25)
    PIDFILE.unlink(missing_ok=True)


# ---------------------------------------------------------------- this computer: shortcuts, start at login
def _py_gui():
    py = Path(sys.executable)
    return py.parent / "pythonw.exe" if SYSTEM == "windows" and (py.parent / "pythonw.exe").exists() else py


def _icon_png():
    return APP_DIR / "app" / "static" / "pwa-512.png"


def _home():
    return Path.home()


def _mac_app():
    return _home() / "Applications" / f"{NAME}.app"


def _mac_agent():
    return _home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _linux_unit():
    return Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config") / "systemd" / "user" / "sal-kal.service"


def _linux_desktop():
    return Path(os.environ.get("XDG_DATA_HOME") or _home() / ".local" / "share") / "applications" / "sal-kal.desktop"


def _linux_autostart():
    return Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config") / "autostart" / "sal-kal.desktop"


def _win_dirs():
    appdata = Path(os.environ.get("APPDATA") or _home() / "AppData" / "Roaming")
    progs = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return progs, progs / "Startup"


def _win_desktop():
    try:   # the real Desktop folder (OneDrive moves it)
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                           capture_output=True, text=True, creationflags=NO_WINDOW, timeout=30)
        if r.stdout.strip():
            return Path(r.stdout.strip())
    except Exception:
        pass
    return _home() / "Desktop"


def _win_shortcut(path, args, icon):
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:SK_PATH);$s.TargetPath=$env:SK_TARGET;"
          "$s.Arguments=$env:SK_ARGS;$s.WorkingDirectory=$env:SK_DIR;$s.IconLocation=$env:SK_ICON;$s.Save()")
    env = {**os.environ, "SK_PATH": str(path), "SK_TARGET": str(_py_gui()), "SK_ARGS": args, "SK_DIR": str(APP_DIR),
           "SK_ICON": str(icon)}
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], env=env,
                   capture_output=True, creationflags=NO_WINDOW, timeout=60)


def _service_name():
    if SYSTEM == "mac" and _mac_agent().exists():
        return LABEL
    if SYSTEM == "linux" and _linux_unit().exists():
        return "sal-kal.service"
    return None


def _service(action):
    name = _service_name()
    if not name:
        return False
    try:
        if SYSTEM == "mac":
            gui = f"gui/{os.getuid()}"
            if action == "start":
                subprocess.run(["launchctl", "bootstrap", gui, str(_mac_agent())], capture_output=True)
                r = subprocess.run(["launchctl", "kickstart", f"{gui}/{LABEL}"], capture_output=True)
            elif action == "restart":
                r = subprocess.run(["launchctl", "kickstart", "-k", f"{gui}/{LABEL}"], capture_output=True)
            else:
                r = subprocess.run(["launchctl", "bootout", f"{gui}/{LABEL}"], capture_output=True)
        else:
            r = subprocess.run(["systemctl", "--user", action, name], capture_output=True)
        return r.returncode == 0
    except OSError:
        return False


def _service_start():
    return _service("start")


def _systemd_user():
    try:
        return subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def set_autostart(on):
    """Start Sal-Kal when you sign in to this computer (so prices stay current), or not."""
    py, app_dir = str(sys.executable), str(APP_DIR)
    if SYSTEM == "mac":
        f = _mac_agent()
        if on:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{_x(py)}</string><string>-m</string><string>app.local</string><string>serve</string></array>
  <key>WorkingDirectory</key><string>{_x(app_dir)}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key><string>Background</string>
  <key>StandardErrorPath</key><string>{_x(str(DATA / "launchd.log"))}</string>
</dict></plist>
""", encoding="utf-8")
        elif f.exists():
            _service("stop")
            f.unlink()
    elif SYSTEM == "linux":
        unit, xdg = _linux_unit(), _linux_autostart()
        if on and _systemd_user():
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_text(f"""[Unit]
Description=Sal-Kal (grocery prices near you), on this computer
After=network-online.target

[Service]
WorkingDirectory={app_dir}
ExecStart="{py}" -m app.local serve
Restart=on-failure
RestartSec=10
Nice=10

[Install]
WantedBy=default.target
""", encoding="utf-8")
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            subprocess.run(["systemctl", "--user", "enable", "sal-kal.service"], capture_output=True)
        elif on:
            xdg.parent.mkdir(parents=True, exist_ok=True)
            xdg.write_text(_desktop_entry("serve", hidden=True), encoding="utf-8")
        else:
            if unit.exists():
                subprocess.run(["systemctl", "--user", "disable", "sal-kal.service"], capture_output=True)
                unit.unlink()
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            xdg.unlink(missing_ok=True)
    else:
        startup = _win_dirs()[1] / f"{NAME}.lnk"
        if on:
            startup.parent.mkdir(parents=True, exist_ok=True)
            _win_shortcut(startup, "-m app.local serve", _win_icon())
        else:
            startup.unlink(missing_ok=True)


def _x(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _desktop_entry(cmd, hidden=False):
    return (f"[Desktop Entry]\nType=Application\nName={NAME}\nName[he]=סל קל\nComment=Grocery prices near you\n"
            f"Exec=\"{sys.executable}\" -m app.local {cmd}\nPath={APP_DIR}\nIcon={_icon_png()}\nTerminal=false\n"
            f"Categories=Utility;\n" + ("NoDisplay=true\nX-GNOME-Autostart-enabled=true\n" if hidden else ""))


def _win_icon():
    ico = DATA / "sal-kal.ico"
    if not ico.exists():
        try:
            from PIL import Image
            Image.open(_icon_png()).save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        except Exception:
            return str(_py_gui())
    return str(ico)


def shortcuts():
    """The Sal-Kal icon: an app in ~/Applications (macOS), Start menu and Desktop (Windows), the apps menu (Linux).
    Opening it starts Sal-Kal if it isn't running and opens it in your browser."""
    if SYSTEM == "mac":
        app = _mac_app()
        shutil.rmtree(app, ignore_errors=True)
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Resources").mkdir()
        exe = app / "Contents" / "MacOS" / "sal-kal"
        exe.write_text(f'#!/bin/sh\ncd "{APP_DIR}" && exec "{sys.executable}" -m app.local open\n', encoding="utf-8")
        exe.chmod(0o755)
        icon = ""
        try:  # .icns from the app icon with macOS's own tools
            iconset = DATA / "sal-kal.iconset"
            shutil.rmtree(iconset, ignore_errors=True)
            iconset.mkdir()
            for s in (16, 32, 128, 256, 512):
                for scale in (1, 2):
                    px = s * scale
                    if px <= 1024:
                        subprocess.run(["sips", "-z", str(px), str(px), str(_icon_png()), "--out",
                                        str(iconset / f"icon_{s}x{s}{'@2x' if scale == 2 else ''}.png")],
                                       capture_output=True, check=True)
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(app / "Contents" / "Resources" / "icon.icns")],
                           capture_output=True, check=True)
            shutil.rmtree(iconset, ignore_errors=True)
            icon = "<key>CFBundleIconFile</key><string>icon</string>"
        except (OSError, subprocess.CalledProcessError):
            pass
        (app / "Contents" / "Info.plist").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>{NAME}</string>
  <key>CFBundleDisplayName</key><string>{NAME}</string>
  <key>CFBundleIdentifier</key><string>{LABEL}.open</string>
  <key>CFBundleExecutable</key><string>sal-kal</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  {icon}
</dict></plist>
""", encoding="utf-8")
    elif SYSTEM == "linux":
        f = _linux_desktop()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_desktop_entry("open"), encoding="utf-8")
        f.chmod(0o755)
    else:
        progs, _ = _win_dirs()
        icon = _win_icon()
        for p in (progs / f"{NAME}.lnk", _win_desktop() / f"{NAME}.lnk"):
            p.parent.mkdir(parents=True, exist_ok=True)
            _win_shortcut(p, "-m app.local open", icon)
        _win_uninstall_entry(True)


def _win_uninstall_entry(on):
    """Sal-Kal in Settings > Apps, with its own Uninstall button."""
    try:
        import winreg
    except ImportError:
        return
    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Sal-Kal"
    if not on:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        for name, val in {"DisplayName": NAME, "Publisher": "Sal-Kal (open source)", "DisplayIcon": _win_icon(),
                          "InstallLocation": str(ROOT), "URLInfoAbout": "https://sal-kal.com",
                          "UninstallString": f'"{_py_gui()}" -m app.local uninstall'}.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def install():
    """After the installer has put the files in place: the icon, start at login, and Sal-Kal started and opened."""
    DATA.mkdir(parents=True, exist_ok=True)
    cfg = load()
    stop()
    shortcuts()
    set_autostart(cfg["autostart"])
    if ensure_running():
        u = url(path="/setup" if load()["phase"] != "ready" else "/")
        webbrowser.open(u)
        print(f"\n  Sal-Kal is running: {url()}\n")
    else:
        print(f"\n  Sal-Kal didn't start. Its log: {LOGFILE}\n")
        sys.exit(1)


def uninstall(keep_data=False):
    """Removes the icon, start at login and the whole Sal-Kal folder (keep_data: everything but data/). Stopping the
    start-at-login service comes last: when Sal-Kal runs under it, that stops this very process."""
    svc = _service_name()
    if SYSTEM == "mac":
        shutil.rmtree(_mac_app(), ignore_errors=True)
        _mac_agent().unlink(missing_ok=True)
    elif SYSTEM == "linux":
        _linux_desktop().unlink(missing_ok=True)
        _linux_autostart().unlink(missing_ok=True)
        if _linux_unit().exists():
            subprocess.run(["systemctl", "--user", "disable", "sal-kal.service"], capture_output=True)
            _linux_unit().unlink()
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    else:
        progs, startup = _win_dirs()
        for p in (progs / f"{NAME}.lnk", _win_desktop() / f"{NAME}.lnk", startup / f"{NAME}.lnk"):
            p.unlink(missing_ok=True)
        _win_uninstall_entry(False)
    keep = {"data"} if keep_data else set()
    if SYSTEM == "windows":   # a running program can't delete its own files there: cmd does it once we've exited
        parts = [f'rmdir /s /q "{ROOT / d}"' for d in ("bin", "python", "venv", "app", "browsers", "cache", "data")
                 if d not in keep]
        if not keep:
            parts.append(f'rmdir /s /q "{ROOT}"')
        subprocess.Popen(["cmd", "/c", "timeout /t 3 /nobreak >nul & " + " & ".join(parts)],
                         creationflags=0x00000008 | NO_WINDOW, cwd=str(ROOT.parent))
    else:
        for p in ROOT.iterdir() if ROOT.exists() else []:
            if p.name not in keep:
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        if not keep:
            shutil.rmtree(ROOT, ignore_errors=True)
    if svc == LABEL:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    elif svc:
        subprocess.run(["systemctl", "--user", "stop", svc], capture_output=True)
    os._exit(0)


# ---------------------------------------------------------------- the downloader (run as its own process)
def fetch(types, regex=None):
    from app import scraper_fixes  # the server's two fixes: Shufersal skipped once fetched, lean status files
    DUMPS.mkdir(parents=True, exist_ok=True)
    os.chdir(DATA)  # it opens its log (and keeps cookies) in the working folder, at import
    scraper_fixes.apply()
    scraper_fixes.quiet()
    from il_supermarket_scarper import ScarpingTask
    kw = {"files_types": types.split(","), "multiprocessing": max(1, min(4, (os.cpu_count() or 2) // 2)),
          "output_configuration": {"output_mode": "disk", "base_storage_path": str(DUMPS)},
          "status_configuration": {"database_type": "json", "base_path": str(DUMPS / "status")},
          "timeout_in_seconds": 40 * 60}
    if regex:
        kw["file_name_regex"] = regex
    task = ScarpingTask(**kw)
    task.start(single_pass=True)
    task.join()


def chains():
    from il_supermarket_scarper import ScraperFactory
    print(len(ScraperFactory.all_scrapers_name()))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "open"
    if cmd == "serve":
        serve()
    elif cmd == "serve-after":
        _serve_after(sys.argv[2])
    elif cmd == "open":
        open_app()
    elif cmd == "install":
        install()
    elif cmd == "stop":
        stop()
    elif cmd == "uninstall":
        stop()
        uninstall(keep_data="--keep-data" in sys.argv)
    elif cmd == "fetch":
        fetch(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == "chains":
        chains()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
