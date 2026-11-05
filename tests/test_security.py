"""Run: .venv/bin/python tests/test_security.py
What someone on the internet could try against the home server, through the real ASGI app (app/guard.py and the
checks in app/server.py): floods, huge bodies, expensive requests, writes from other sites, junk settings, and calls
that would make the server hammer outside services."""
import asyncio
import base64
import hashlib
import json
import os
import re
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="sal-sec-")
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
os.environ["SAL_AUTH"] = os.path.join(TMP, "auth.json")
os.environ["SAL_STATS_SYNC"] = "1"
os.environ.pop("SAL_DEV_LOGIN", None)
with open(os.environ["SAL_AUTH"], "w") as f:
    json.dump({"google": {"client_id": "id", "client_secret": "s"}, "base_url": "https://sal-kal.com"}, f)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import auth, db as dbmod, guard, server, stats  # noqa: E402

c = dbmod.connect()
server.con = lambda: c
c.execute("INSERT INTO products(key, code, name, scope, store_count, chain_count) VALUES('7290004131074', '7290004131074', 'חלב 3%', 'global', 5, 3)")
c.commit()
auth.sign_in(c, "google", "g-owner", "owner@example.com", True, "Owner", None)  # the first sign-in: the household's admin
alice = auth.sign_in(c, "google", "g-alice", "alice@example.com", True, "Alice", None)
assert not auth.user_row(c, alice)["admin"]
ALICE = f"{auth.SESSION_COOKIE}={auth.new_session(c, alice)}"
bob = auth.sign_in(c, "google", "g-bob", "bob@example.com", True, "Bob", None)
BOB = f"{auth.SESSION_COOKIE}={auth.new_session(c, bob)}"


def call(method, path, body=None, cookie=ALICE, ip="198.51.100.7", headers=None, chunks=None):
    """One request through the whole app: (status, headers, body). ip=None: from the home network (no Cloudflare)."""
    raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else b""
    hdrs = {"host": "sal-kal.com", "content-type": "application/json", **(headers or {})}
    if cookie:
        hdrs["cookie"] = cookie
    if ip:
        hdrs["cf-connecting-ip"] = ip
    if chunks is None and raw:
        hdrs.setdefault("content-length", str(len(raw)))
    if chunks is not None:
        hdrs["transfer-encoding"] = "chunked"
    p, _, q = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "https",
             "path": p, "raw_path": p.encode(), "query_string": q.encode(), "root_path": "",
             "headers": [(k.lower().encode(), v.encode()) for k, v in hdrs.items()],
             "client": None, "server": ("sal-kal.com", 443)}
    parts = list(chunks) if chunks is not None else [raw]
    out = []

    async def receive():
        if parts:
            b = parts.pop(0)
            return {"type": "http.request", "body": b, "more_body": bool(parts)}
        await asyncio.Event().wait()

    async def send(m):
        out.append(m)

    try:
        asyncio.run(server.app(scope, receive, send))
    except Exception:
        pass
    start = next(m for m in out if m["type"] == "http.response.start")
    h = {k.decode(): v.decode() for k, v in start["headers"]}
    return start["status"], h, b"".join(m.get("body", b"") for m in out if m["type"] == "http.response.body")


# ---------------------------------------------------------------- nothing that maps the API is served
for path in ("/docs", "/redoc", "/openapi.json"):
    assert call("GET", path)[0] == 404, path

# ---------------------------------------------------------------- bodies: capped, in full, before the app sees them
guard.reset()
st, _, _ = call("POST", "/api/compare", b"x" * (guard.MAX_BODY + 1))
assert st == 413
st, _, _ = call("POST", "/api/compare", chunks=[b"[" + b" " * 100000] * 4)  # no length given, then too much
assert st == 413
st, _, b = call("POST", "/api/compare", chunks=[b'{"items": [{"key": "7290004131074",', b' "qty": 1}]}'])
assert st == 200 and json.loads(b)["n"] == 1, (st, b)  # a body in pieces is put back together
assert call("POST", "/api/compare", b"{}", headers={"content-length": "abc"})[0] == 400

# ---------------------------------------------------------------- writes from another site
assert call("PUT", "/api/me", {"lang": "en"}, headers={"origin": "https://evil.example"})[0] == 403
assert call("DELETE", "/api/me", headers={"origin": "https://sal-kal.com.evil.example"})[0] == 403
assert call("PUT", "/api/me", {"lang": "en"}, headers={"origin": "https://sal-kal.com"})[0] == 200
assert call("PUT", "/api/me", {"lang": "en"})[0] == 200  # no Origin: not a browser on another site
assert call("GET", "/api/me", headers={"origin": "https://evil.example"})[0] == 200  # reads don't change anything

# ---------------------------------------------------------------- floods: per visitor and per account
guard.reset()
saved = dict(guard.API_RATE)
guard.API_RATE["ip"] = (0.001, 5)
codes = [call("GET", "/api/me", ip="203.0.113.1")[0] for _ in range(6)]
assert codes == [200] * 5 + [429], codes
st, h, _ = call("GET", "/api/me", ip="203.0.113.1")
assert st == 429 and int(h["retry-after"]) >= 1
assert call("GET", "/api/me", ip="203.0.113.2")[0] == 200  # another visitor is unaffected
assert all(call("GET", "/api/me", ip=None)[0] == 200 for _ in range(8))  # the home network isn't counted
guard.API_RATE.update(saved)
guard.reset()
guard.API_RATE["user"] = (0.001, 3)
codes = [call("GET", "/api/carts", ip=f"203.0.113.{i}")[0] for i in range(10, 14)]  # one account, many addresses
assert codes == [200, 200, 200, 429], codes
assert call("GET", "/api/carts", cookie=BOB, ip="203.0.113.10")[0] == 200
guard.API_RATE.update(saved)
guard.reset()
# static files are counted apart, and more loosely
saved_file = guard.FILE_RATE
guard.FILE_RATE = (0.001, 3)
codes = [call("GET", "/static/sw.js", ip="203.0.113.20")[0] for _ in range(4)]
assert codes[-1] == 429 and call("GET", "/api/me", ip="203.0.113.20")[0] == 200, codes
guard.FILE_RATE = saved_file
guard.reset()

# ---------------------------------------------------------------- expensive pages spend a compute budget
assert call("GET", "/api/browse?terms=חלב")[0] == 200
guard.charge([("user", alice)], 10000)  # as if Alice's searches had used hours of compute
st, h, _ = call("GET", "/api/browse?terms=חלב")
assert st == 429 and int(h["retry-after"]) > 60, (st, h)
assert call("GET", "/api/carts")[0] == 429  # every call is metered, cheap ones too
assert call("GET", "/api/me")[0] == 200  # but the page can always start (and show what's going on)
assert call("GET", "/api/browse?terms=חלב", cookie=BOB)[0] == 200  # and other accounts aren't held up
assert call("GET", "/api/browse?terms=חלב", ip=None)[0] == 200  # nor the household at home
guard.reset()
guard.charge([("ip", "203.0.113.30")], 10000)  # one address, any account
assert call("GET", "/api/browse?terms=חלב", cookie=BOB, ip="203.0.113.30")[0] == 429
guard.reset()
# the time every call takes is charged, so many cheap calls add up like one expensive one
before = guard._buckets.get(("cpu", "user", bob), [guard.BUDGET["user"][1]])[0]
for _ in range(5):
    assert call("GET", "/api/settings", cookie=BOB)[0] == 200
assert guard._buckets[("cpu", "user", bob)][0] < before
guard.reset()
# the owner's admin account isn't metered (its reports take a while)
c.execute("UPDATE users SET admin=1 WHERE id=?", (bob,))
c.commit()
guard.charge([("user", bob)], 10000)
assert call("GET", "/api/carts", cookie=BOB)[0] == 200
c.execute("UPDATE users SET admin=0 WHERE id=?", (bob,))
c.commit()
guard.reset()
# the landing page's live counts are worked out at most every 30 s: calling it in a loop costs nothing
server._status["v"] = None
first = call("GET", "/api/status", cookie=None)[2]
c.execute("INSERT INTO products(key, code, name, scope, store_count, chain_count) VALUES('7290000000001', '7290000000001', 'x', 'global', 1, 1)")
c.commit()
assert call("GET", "/api/status", cookie=None)[2] == first
server._status["t"] = 0
assert call("GET", "/api/status", cookie=None)[2] != first
c.execute("DELETE FROM products WHERE key='7290000000001'")
c.commit()

# each visitor waits in a line of their own: a flood from one account never stands in front of another
async def lines():
    async def hold(keys, secs, out, name):
        try:
            async with guard.turn(keys, 0.3):
                out.append(name)
                await asyncio.sleep(secs)
        except asyncio.TimeoutError:
            out.append(name + " busy")
    out = []
    await asyncio.gather(hold([("ip", "a"), ("user", 1)], 0.6, out, "flood 1"),
                         hold([("ip", "a"), ("user", 1)], 0.6, out, "flood 2"),
                         hold([("ip", "b"), ("user", 2)], 0.1, out, "shopper"))
    return out
assert sorted(asyncio.run(lines())) == ["flood 1", "flood 2 busy", "shopper"]
assert not any(k[0] != "site" for line in guard._lines.values() for k in line)  # nothing left behind

# ---------------------------------------------------------------- huge or junk requests are cut down, never a 500
st, _, b = call("GET", "/api/browse?terms=" + "|".join(["חלב"] * 500) + "&limit=100000")
assert st == 200
st, _, b = call("GET", "/api/search?q=" + "%20".join(["א"] * 2000) + "&limit=100000")
assert st == 200 and len(json.loads(b)) <= 200
assert call("GET", "/api/deals?limit=100000")[0] == 200
assert call("GET", "/api/picks?limit=100000")[0] == 200
st, _, b = call("POST", "/api/history/cart", {"items": [{"key": str(10 ** 12 + i), "qty": "x"} for i in range(5000)],
                                              "chains": ["7290027600007"]})
assert st == 200, (st, b)
st, _, b = call("POST", "/api/compare", {"items": [{"key": "7290004131074", "qty": "NaN"}, {"key": {"a": 1}}, "x",
                                                   {"key": "7290004131074", "qty": 1e300}],
                                         "prefs": {"home": {"lat": 48.85, "lon": 2.35}, "max_km": "far", "mode": {"x": 1},
                                                   "places": "nope", "club": "yes"}})
assert st == 200, (st, b)
r = json.loads(b)
assert r["prefs"]["home"] is None and r["prefs"]["mode"] == "store" and r["prefs"]["club"] is True  # Paris is left out
assert [i["qty"] for i in r["items"]] == [999.0], r["items"]  # NaN and the junk lines are gone, the rest is capped
st, _, b = call("POST", "/api/drive", {"home": {"lat": 48.85, "lon": 2.35}, "stores": [["a", "b"], [1]]})
assert st == 422, (st, b)  # drive times only from an address in Israel
st, _, b = call("POST", "/api/drive", {"home": {"lat": 32.08, "lon": 34.78}, "stores": [["a", "b"], [1], None]})
assert st == 200 and json.loads(b)["times"] == {}, (st, b)

# settings: only known values, bounded sizes
st, _, b = call("PUT", "/api/settings", {"prefs": {"mode": "x" * 100000, "club": {"a": 1}, "travel": "rocket"},
                                         "delivery": {"7290058140886": {"fee": "29.9", "delivers_here": "yes", "evil": "x" * 1000},
                                                      "../../etc": {"fee": 1}, "x" * 5000: {"fee": 1}}})
assert st == 200, (st, b)
p = json.loads(c.execute("SELECT prefs FROM users WHERE id=?", (alice,)).fetchone()[0])
assert p["mode"] == "store" and p["club"] is True and p["travel"] == "car", p
d = json.loads(c.execute("SELECT delivery FROM users WHERE id=?", (alice,)).fetchone()[0])
assert d == {"7290058140886": {"fee": 29.9, "delivers_here": True}}, d
for bad in ({"delivery": {"7290058140886": {"fee": "x" * 10}}}, {"delivery": {"7290058140886": {"fee": -1}}},
            {"delivery": {"7290058140886": "nope"}}, {"delivery": ["x"]}, {"prefs": {"max_km": "far"}}):
    assert call("PUT", "/api/settings", bad)[0] == 422, bad
many = {str(7290000000000 + i): {"fee": 1} for i in range(500)}
assert call("PUT", "/api/settings", {"delivery": many})[0] == 200
assert len(json.loads(c.execute("SELECT delivery FROM users WHERE id=?", (alice,)).fetchone()[0])) == server.MAX_DELIVERY
# carts: names are text, quantities numbers
st, _, b = call("POST", "/api/carts", {"name": {"a": 1}, "items": [{"key": "7290004131074", "qty": "lots"}]})
assert st == 200 and json.loads(b)["name"] == "New basket", (st, b)

# ---------------------------------------------------------------- outside services: a site-wide budget
saved_out = dict(guard.OUTSIDE)
guard.OUTSIDE["osrm"] = (2, 60, 4)
guard.reset()
with guard.outside("osrm"):
    pass
with guard.outside("osrm"):
    pass
try:
    with guard.outside("osrm"):
        raise AssertionError("a third lookup went through")
except guard.Busy:
    pass
guard.OUTSIDE.update(saved_out)
guard.reset()
from app import geo  # noqa: E402
guard.OUTSIDE["google"] = (0, 86400, 2)  # the day's live-traffic lookups are used up
calls = []
geo._osrm = lambda home, dests: calls.append(len(dests)) or [7] * len(dests)
assert geo.drive_minutes((32.08, 34.78), [(32.1, 34.8)], key="billed-key") == {(32.1, 34.8): 7} and calls == [1]
guard.OUTSIDE.update(saved_out)
guard.reset()

# ---------------------------------------------------------------- headers
st, h, page = call("GET", "/app")
assert st == 200
csp = h["content-security-policy"]
assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0], csp
for inline in re.findall(r"<script>(.*?)</script>", page.decode(), re.S):  # the page's own inline script may run
    assert "'sha256-" + base64.b64encode(hashlib.sha256(inline.encode()).digest()).decode() + "'" in csp
assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
assert h["strict-transport-security"].startswith("max-age=") and h["x-frame-options"] == "DENY"
assert "camera=()" in h["permissions-policy"] and h["cross-origin-opener-policy"] == "same-origin"
assert "content-security-policy" in call("GET", "/", cookie=None)[1]  # the landing page too
assert "script-src 'none'" in call("GET", "/privacy", cookie=None)[1]["content-security-policy"]
assert "strict-transport-security" not in call("GET", "/app", ip=None, headers={"host": "sal-kal.com"})[1]

# ---------------------------------------------------------------- usage statistics can't fill the disk
stats.hit("/api/me", "X" * 5000, 200, 1.0, False)
stats.hit("/api/me", "FOO", 200, 1.0, False)
assert all(k[2] in stats.METHODS | {"OTHER"} for k in stats._req), list(stats._req)
saved_day = stats.DAY_WEB
stats.DAY_WEB = 5
stats._rl.clear()
ev = lambda n: {"vid": "a" * 16, "sid": "b" * 16, "ev": [{"k": "view", "p": "home"}] * n}
assert stats.ingest(ev(5), uid=None, admin=False, ua="", country=None, city=None, via="internet", ip="1.2.3.4") == 5
assert stats.ingest(ev(1), uid=None, admin=False, ua="", country=None, city=None, via="internet", ip="5.6.7.8") == 0
stats.DAY_WEB = saved_day
stats._rl.clear()
# a visitor's "depth" that isn't a number doesn't break the admin page's usage tab
stats.ingest({"vid": "c" * 16, "sid": "d" * 16, "ev": [{"k": "view", "p": "home"}, {"k": "dwell", "p": "home", "n": 5, "d": {"depth": "deep"}}]},
             uid=None, admin=False, ua="", country=None, city=None, via="internet", ip="9.9.9.9")
stats.flush()
stats.usage(stats.Range(7, 1), lambda keys: {})
print("security ok")
