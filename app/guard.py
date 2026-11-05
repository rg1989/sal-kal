"""The front door: what one visitor, one account or the whole site may ask of the home server.

Sal runs on a machine at home that also runs other private services, so nobody on the internet may tie it up:
- Every request through the tunnel is counted per visitor (Cloudflare's Cf-Connecting-IP) and signed-in requests per
  account too: a token bucket each, so a burst is fine and a flood gets 429 "wait a moment".
- Every API request also spends a compute budget per visitor and per account, measured in the seconds it actually
  took (a 400-item comparison is ~20 s of Python, a short search 1-3 s, most calls a few ms): one process answers
  everyone, so nobody may use more than a share of it. Expensive pages also queue one at a time per account, and at
  most HEAVY_SLOTS run at once.
- Request bodies are capped (MAX_BODY): no request can make the server hold megabytes in memory.
- Calls to outside services (OSRM, Overpass, Google Routes) go through outside(): a site-wide rate and at most a few at
  a time, so no visitor can get the home address banned there or keep the server's threads waiting on them.
The home network and Tailscale (no Cf-Connecting-IP) are the household: they get the body cap only.

Everything is kept in memory (a restart forgets it, which is fine) and old keys are dropped, so it can't grow."""
import asyncio
import contextlib
import threading
import time
import weakref

MAX_BODY = 256 * 1024  # the largest real body is a 400-item cart, ~40 KB

# (requests per second, burst) per visitor and per account; static files and photos are counted apart (a page
# with a shelf of photos makes dozens at once; Cloudflare caches most of them)
API_RATE = {"ip": (10.0, 300), "user": (5.0, 200)}
FILE_RATE = (40.0, 1200)
MAX_INFLIGHT = 12  # API requests one visitor may have open at once

# Expensive pages: (seconds of compute refilled per second, most that can be saved up). The page compares every
# cart at once on load, so a burst of several seconds is normal; a steady stream of heavy requests is not.
HEAVY = ("/api/compare", "/api/browse", "/api/category/", "/api/deals", "/api/history/cart", "/api/way/trip",
         "/api/search", "/api/cards", "/api/picks")
BUDGET = {"ip": (0.5, 120.0), "user": (0.35, 90.0)}  # an address: at most half the server, after a 2-minute burst
# Calls that mostly wait on an outside service (routes, opening hours, address lookups, sign-in) aren't charged by the
# clock: they have per-account limits of their own and site-wide budgets (outside()).
WAITS = ("/api/store/", "/api/places/locate", "/api/places/reverse", "/api/drive", "/api/way/trip")
HEAVY_SLOTS = 4      # run at once, site-wide (Python runs one at a time anyway: more would only queue inside)
HEAVY_WAIT = 20.0    # seconds a heavy request may wait for its turn before "busy" (the page compares every cart at once)

_lock = threading.Lock()
_buckets = {}  # key -> [tokens, last refill time]
MAX_KEYS = 50000


def _take(key, cost, rate, burst, now=None):
    """Spend `cost` from the bucket `key`; returns 0 when allowed, else the seconds until it would be."""
    now = now or time.monotonic()
    with _lock:
        b = _buckets.get(key)
        if b is None:
            if len(_buckets) >= MAX_KEYS:  # drop full (idle) buckets; a flood of new keys can't grow this
                for k in [k for k, (t, last) in _buckets.items() if t >= burst or now - last > 600]:
                    del _buckets[k]
                if len(_buckets) >= MAX_KEYS:
                    _buckets.clear()
            b = _buckets[key] = [float(burst), now]
        tokens = min(burst, b[0] + (now - b[1]) * rate)
        b[1] = now
        if tokens < cost:
            b[0] = tokens
            return max(1, round((cost - tokens) / rate)) if rate > 0 else 3600  # a budget of 0: never
        b[0] = tokens - cost
        return 0


def allow(kind, who, cost=1.0):
    """A request from visitor/account `who` (kind "ip" or "user"): 0 = go ahead, else seconds to wait."""
    rate, burst = API_RATE[kind]
    return _take(("rate", kind, who), cost, rate, burst)


def allow_file(ip):
    return _take(("file", ip), 1, *FILE_RATE)


def heavy(path):
    return path.startswith(HEAVY)


def metered(path):
    return path.startswith("/api/") and not path.startswith(WAITS)


def budget_left(keys):
    """0 when every (kind, who) in keys still has compute budget, else seconds until it has."""
    wait = 0
    for kind, who in keys:
        rate, cap = BUDGET[kind]
        wait = max(wait, _take(("cpu", kind, who), 0.001, rate, cap))
    return wait


def charge(keys, seconds):
    """Spend the compute a heavy request actually used (it may go below zero: the next one waits longer)."""
    now = time.monotonic()
    with _lock:
        for kind, who in keys:
            rate, cap = BUDGET[kind]
            b = _buckets.setdefault(("cpu", kind, who), [cap, now])
            b[0] = min(cap, b[0] + (now - b[1]) * rate) - seconds
            b[1] = now


_inflight = {}


def enter(ip):
    with _lock:
        n = _inflight.get(ip, 0)
        if n >= MAX_INFLIGHT:
            return False
        _inflight[ip] = n + 1
        return True


def leave(ip):
    with _lock:
        n = _inflight.get(ip, 1) - 1
        if n <= 0:
            _inflight.pop(ip, None)
        else:
            _inflight[ip] = n


# Each visitor queues for expensive pages in a line of their own (an account runs one at a time, an address two),
# and only then for one of the site's HEAVY_SLOTS: a flood from one visitor waits behind itself, never in front of
# anyone else.
OWN = {"ip": 2, "user": 1}
_lines = weakref.WeakKeyDictionary()  # event loop -> {key: [semaphore, requests holding or waiting]} (tests run several loops)


@contextlib.asynccontextmanager
async def turn(keys, timeout):
    """async with turn([("ip", ip), ("user", id)], seconds): a heavy request's turn; asyncio.TimeoutError when it
    doesn't come in time."""
    loop = asyncio.get_running_loop()
    lines = _lines.setdefault(loop, {})
    deadline = loop.time() + timeout
    held, joined = [], []
    try:
        for k in [*keys, ("site", None)]:
            e = lines.get(k)
            if e is None:
                e = lines[k] = [asyncio.Semaphore(HEAVY_SLOTS if k[0] == "site" else OWN[k[0]]), 0]
            e[1] += 1
            joined.append(k)
            await asyncio.wait_for(e[0].acquire(), max(0.01, deadline - loop.time()))
            held.append(e[0])
        yield
    finally:
        for sem in held:
            sem.release()
        for k in joined:
            e = lines[k]
            e[1] -= 1
            if e[1] <= 0 and k[0] != "site":
                del lines[k]


# ---------------------------------------------------------------- outside services
class Busy(RuntimeError):
    """An outside service's site-wide budget is used up: callers fall back as when the service is down."""


# name -> (calls, per seconds, at once)
OUTSIDE = {"osrm": (60, 60, 4), "fossgis": (30, 60, 2), "overpass": (30, 3600, 2), "google": (1500, 86400, 2),
           "nominatim": (60, 60, 2)}  # Nominatim itself waits 1 s between calls: at most 2 requests wait for it
_out = {name: threading.BoundedSemaphore(at_once) for name, (_, _, at_once) in OUTSIDE.items()}


class outside:
    """with outside("osrm"): one call to that service, within its site-wide rate and concurrency; else Busy."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        n, per, _ = OUTSIDE[self.name]
        if _take(("out", self.name), 1, n / per, n):
            raise Busy(f"{self.name}: too many lookups right now")
        if not _out[self.name].acquire(timeout=5):
            raise Busy(f"{self.name}: too many lookups at once")
        return self

    def __exit__(self, *exc):
        _out[self.name].release()
        return False


# ---------------------------------------------------------------- the ASGI layer, outside everything else
BODY_SECONDS = 30  # a body that trickles in slower than this is dropped


async def _plain(send, status, text, retry=None):
    headers = [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]
    if retry:
        headers.append((b"retry-after", str(int(retry)).encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": b'{"detail":"%s"}' % text.encode()})


class Door:
    """Counts each internet visitor's requests and reads every request body in full (at most MAX_BODY, within
    BODY_SECONDS) before the app sees it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        h = dict(scope.get("headers") or [])
        try:
            length = int(h.get(b"content-length") or 0)
        except ValueError:
            return await _plain(send, 400, "bad content-length")
        if length > MAX_BODY:
            return await _plain(send, 413, "request too large")
        ip = h.get(b"cf-connecting-ip")
        ip = ip.decode("latin-1")[:64] if ip is not None else None
        api = scope["path"].startswith(("/api/", "/auth/"))
        if ip is not None:
            wait = allow("ip", ip) if api else allow_file(ip)
            if wait:
                return await _plain(send, 429, "too many requests, wait a moment", wait)
            if api and not enter(ip):
                return await _plain(send, 429, "too many requests at once", 2)
        try:
            if length or b"transfer-encoding" in h or scope["method"] not in ("GET", "HEAD", "OPTIONS"):
                chunks, size = [], 0
                async def read():
                    nonlocal size
                    while True:
                        m = await receive()
                        if m["type"] != "http.request":
                            return False  # the client went away
                        chunks.append(m.get("body", b""))
                        size += len(chunks[-1])
                        if size > MAX_BODY:
                            return None
                        if not m.get("more_body"):
                            return True
                try:
                    ok = await asyncio.wait_for(read(), BODY_SECONDS)
                except asyncio.TimeoutError:
                    return await _plain(send, 408, "request body too slow")
                if ok is None:
                    return await _plain(send, 413, "request too large")
                if ok is False:
                    return
                body, given = b"".join(chunks), [False]

                async def replay():
                    if not given[0]:
                        given[0] = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await receive()  # then whatever comes next (the disconnect)
                return await self.app(scope, replay, send)
            return await self.app(scope, receive, send)
        finally:
            if ip is not None and api:
                leave(ip)


def reset():
    """Forget every count (tests)."""
    with _lock:
        _buckets.clear()
        _inflight.clear()
