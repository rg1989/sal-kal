"""Run: .venv/bin/python tests/test_admin.py
Usage statistics (app.stats) and the owner's /admin page, through the real ASGI app (middleware, cookies, headers), on
scratch databases. Only the owner's verified Google sign-in opens /admin; everyone else gets "not found"."""
import asyncio
import hashlib
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="sal-admin-")
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
os.environ["SAL_AUTH"] = os.path.join(TMP, "auth.json")
os.environ["SAL_STATS_SYNC"] = "1"  # no writer thread: the test flushes
os.environ.pop("SAL_DEV_LOGIN", None)
with open(os.environ["SAL_AUTH"], "w") as f:
    json.dump({"google": {"client_id": "id", "client_secret": "s"}}, f)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import auth, db as dbmod, server, stats  # noqa: E402

c = dbmod.connect()
server.con = lambda: c
c.execute("INSERT INTO products(key, code, name, scope, store_count, chain_count) VALUES('7290004131074', '7290004131074', 'חלב 3%', 'global', 5, 3)")
c.commit()
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
FB_APP = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/22B83 [FBAN/FBIOS;FBAV/480.0]"


def call(method, path, headers=None, body=b"", cookie=None):
    """One request through the whole app: (status, headers, body)."""
    hdrs = {"host": "sal-kal.com", "user-agent": IPHONE, **(headers or {})}
    if cookie:
        hdrs["cookie"] = cookie
    if body:
        hdrs["content-length"] = str(len(body))
    p, _, q = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "https",
             "path": p, "raw_path": p.encode(), "query_string": q.encode(), "root_path": "",
             "headers": [(k.lower().encode(), v.encode()) for k, v in hdrs.items()],
             "client": ("203.0.113.9", 5555), "server": ("sal-kal.com", 443)}
    out, sent = [], [False]

    async def receive():
        if not sent[0]:
            sent[0] = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()  # a real client stays connected

    async def send(m):
        out.append(m)

    try:
        asyncio.run(server.app(scope, receive, send))
    except Exception:  # a crashing route: the 500 was still sent
        pass
    start = next(m for m in out if m["type"] == "http.response.start")
    h = {}
    for k, v in start["headers"]:
        h.setdefault(k.decode(), []).append(v.decode())
    return start["status"], {k: v[-1] for k, v in h.items()} | {"set-cookie-all": h.get("set-cookie", [])}, \
        b"".join(m.get("body", b"") for m in out if m["type"] == "http.response.body")


def ux(vid, sid, events, cookie=None, ctx=None, ua=IPHONE, ip="198.51.100.7"):
    events = [{**e, "t": i * 100} if isinstance(e, dict) else e for i, e in enumerate(events)]  # 0.1 s apart, sent at the end
    body = json.dumps({"vid": vid, "sid": sid, "now": len(events) * 100, "ev": events, **({"ctx": ctx} if ctx else {})}).encode()
    return call("POST", "/api/ux", {"content-type": "application/json", "cf-connecting-ip": ip, "cf-ipcountry": "IL",
                                    "cf-ipcity": "Ramat Gan", "user-agent": ua}, body, cookie)


# ---------------------------------------------------------------- who is the owner
# auth.py holds only a hash of the real owner's address (the repo is public); the test swaps in a made-up owner
assert len(auth.OWNER_EMAIL_SHA256) == 64 and "@" not in open(auth.__file__).read().split("OWNER_EMAIL_SHA256 =")[1].split("\n")[0]
OWNER = "Owner.Person@Example.com"
auth.OWNER_EMAIL_SHA256 = hashlib.sha256(OWNER.lower().encode()).hexdigest()
assert auth.owner_email(OWNER) and auth.owner_email(" owner.person@example.com ") and not auth.owner_email("someone@gmail.com")
owner = auth.sign_in(c, "google", "g-owner", OWNER, True, "Roman", None)
alice = auth.sign_in(c, "google", "g-alice", "alice@example.com", True, "Alice", "https://lh3.example/a.jpg")
fake = auth.sign_in(c, "facebook", "f-fake", "owner.person@example.com", False, "Not the owner", None)    # unverified
fake2 = auth.sign_in(c, "google", "g-fake", "owner.person@example.com", False, "Not the owner 2", None)  # unverified
assert len({owner, alice, fake, fake2}) == 4
assert auth.is_owner(c, {"id": owner}) and not any(auth.is_owner(c, {"id": u}) for u in (alice, fake, fake2, None))
c.execute("UPDATE users SET admin=1 WHERE id=?", (alice,))  # the old admin flag (reindex) doesn't open /admin
c.commit()
cookie = {u: f"{auth.SESSION_COOKIE}={auth.new_session(c, u)}" for u in (owner, alice, fake, fake2)}

# ---------------------------------------------------------------- /admin is the owner's only; the rest get "not found"
for path in ("/admin", "/admin/admin.js", "/admin/admin.css"):
    for who in (None, alice, fake, fake2):
        st, h, b = call("GET", path, cookie=cookie.get(who))
        assert st == 404, (path, who, st)
    st, h, b = call("GET", path, cookie=cookie[owner])
    assert st == 200, (path, st)
    assert h["cache-control"] == "no-store" and "noindex" in h["x-robots-tag"]
    assert "script-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
st, h, b = call("GET", "/admin", cookie=cookie[owner])
assert b"Sal-Kal" in b and b"/admin/admin.js" in b
assert call("GET", "/admin/server.py", cookie=cookie[owner])[0] == 404
assert call("GET", "/admin/..%2Fserver.py", cookie=cookie[owner])[0] == 404
for name in ("overview", "stuck", "usage", "audience", "users", "server", "data", "live", "users/1"):
    assert call("GET", f"/api/admin/{name}")[0] == 401                       # not signed in: like every /api call
    assert call("GET", f"/api/admin/{name}", cookie=cookie[alice])[0] == 404, name
    assert call("GET", f"/api/admin/{name}", cookie=cookie[fake])[0] == 404, name

# ---------------------------------------------------------------- what browsers send
V1, S1 = "vidAnonAlice01", "sidAliceVisit1"
ctx = {"lang": "he", "theme": "dark", "screen": "390x844", "vw": 390, "touch": True, "ref": "L.Facebook.com", "pwa": False,
       "tz": "Asia/Jerusalem", "utm_source": "fb", "landing": "#/"}
st, _, _ = ux(V1, S1, [{"k": "view", "p": "landing"}, {"k": "view", "p": "signin"}, {"k": "signin_click", "a": "google"}], ctx=ctx)
assert st == 204
# the sign-in round trip carries the browser's ids through the provider
st, h, _ = call("GET", f"/auth/google?lang=he&v={V1}.{S1}")
assert st == 303 and "accounts.google.com" in h["location"]
state = next(x.split(";")[0] for x in h["set-cookie-all"] if x.startswith(auth.STATE_COOKIE))
assert call("GET", "/auth/google/callback?error=access_denied&state=x", cookie=state)[0] == 303
assert call("GET", "/auth/google/callback?code=x&state=x")[0] == 303     # no state cookie at all
# signed in now (as Alice): searches, a product added, trouble
evs = [{"k": "view", "p": "home", "t": 0},
       {"k": "search", "p": "browse", "a": "חלב סויה ללא סוכר", "b": "none", "n": 0, "t": 0},
       {"k": "search", "p": "browse", "a": "חלב סויה", "b": "add", "n": 12, "d": {"prev": "חלב סויה ללא סוכר"}, "t": 0},
       {"k": "add", "p": "browse", "a": "7290004131074", "n": 2, "t": 0},
       {"k": "view", "p": "product", "a": "7290004131074", "t": 0},
       {"k": "dwell", "p": "home", "n": 5400, "d": {"depth": 80}, "t": 0},
       {"k": "rage", "p": "browse", "a": "img", "b": "b-cards", "n": 3, "t": 0},
       {"k": "dead", "p": "browse", "a": "img", "b": "b-cards", "t": 0},
       {"k": "error", "p": "home", "a": "<img src=x onerror=alert(1)>" + "x" * 500, "b": "/static/app.js:1:2", "t": 0},
       {"k": "api_fail", "p": "basket", "a": "/api/compare", "n": 502, "t": 0},
       {"k": "onboard", "a": "1", "t": 0},
       {"k": "evil_kind", "a": "dropped", "t": 0},
       {"k": "click", "p": "../../etc", "a": {"nested": "dropped"}, "d": {"x" * 50: "y" * 500}, "t": 0},
       "not even an object"]
assert ux(V1, S1, evs, cookie=cookie[alice])[0] == 204
assert ux("short", S1, evs, cookie=cookie[alice])[0] == 204                # bad ids: nothing kept
assert ux("vidFacebook001", "sidFacebook001", [{"k": "view"}], ua=FB_APP, ip="198.51.100.8")[0] == 204
assert call("POST", "/api/ux", {"content-type": "application/json"}, b"{not json")[0] == 204
assert call("POST", "/api/ux", {"content-type": "application/json", "content-length": "999999"}, b"{}")[0] == 204
# server events join the visit through the X-Sal-V header
assert call("PUT", "/api/settings", {"content-type": "application/json", "x-sal-v": f"{V1}.{S1}"},
            json.dumps({"prefs": {"max_km": 3}}).encode(), cookie[alice])[0] == 200
stats.flush()

w = stats._writer()
kinds = [r[0] for r in w.execute("SELECT kind FROM events ORDER BY id")]
assert "evil_kind" not in kinds and kinds.count("search") == 2 and "signin_start" in kinds and "distance" in kinds, kinds
assert w.execute("SELECT count(*) FROM events WHERE kind='signin_fail'").fetchone()[0] == 2
assert w.execute("SELECT vid FROM events WHERE kind='signin_start'").fetchone()[0] == V1
assert w.execute("SELECT vid, sid, uid FROM events WHERE kind='distance'").fetchone() == (V1, S1, alice)
assert {r[0] for r in w.execute("SELECT b FROM events WHERE kind='signin_fail'")} == {"cancelled (access_denied)", "no state cookie"}
assert w.execute("SELECT count(*) FROM events WHERE vid='short'").fetchone()[0] == 0
err = w.execute("SELECT a FROM events WHERE kind='error'").fetchone()[0]
assert len(err) == 160                                                      # cut to size
page, a_, data = w.execute("SELECT page, a, data FROM events WHERE kind='click'").fetchone()
assert page is None and a_ is None and json.loads(data) == {"x" * 24: "y" * 100}  # junk dropped, the rest cut to size
v = dict(zip(("device", "os", "browser", "inapp", "country", "city", "ref", "utm", "uid", "via", "screen"),
             w.execute("SELECT device, os, browser, inapp, country, city, ref, utm, uid, via, screen FROM visits WHERE sid=?", (S1,)).fetchone()))
assert v == {"device": "Phone", "os": "iOS", "browser": "Safari", "inapp": None, "country": "IL", "city": "Ramat Gan",
             "ref": "l.facebook.com", "utm": "fb", "uid": alice, "via": "internet", "screen": "390x844"}, v
raw = open(stats.path(), "rb").read() + (open(stats.path() + "-wal", "rb").read() if os.path.exists(stats.path() + "-wal") else b"")
assert b"198.51.100" not in raw and b"203.0.113" not in raw and b"AppleWebKit" not in raw  # no IPs, no User-Agent strings
assert stats.parse_ua(FB_APP)[2:] == ("Facebook", "Facebook")
assert stats.parse_ua("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.1 Safari/605.1.15", touch=True)[:2] == ("Tablet", "iPadOS")
assert stats.is_bot("Googlebot/2.1") and stats.is_bot("python-requests/2.32") and not stats.is_bot(IPHONE)

# rate limit per visit: 600 events in 10 minutes
for i in range(12):
    ux("vidFlood000001", "sidFlood000001", [{"k": "click", "a": f"b{i}"}] * 60)
stats.flush()
assert w.execute("SELECT count(*) FROM events WHERE sid='sidFlood000001'").fetchone()[0] == 600

# ---------------------------------------------------------------- the owner's own visits stay out unless asked for
VO, SO = "vidOwnerPhone1", "sidOwnerVisit1"
ux(VO, SO, [{"k": "view", "p": "signin"}])                                  # before signing in: anonymous
stats.flush()
assert w.execute("SELECT admin FROM visits WHERE sid=?", (SO,)).fetchone()[0] == 0
ux(VO, SO, [{"k": "view", "p": "home"}, {"k": "search", "a": "owner-only-search", "n": 3, "b": "none"}], cookie=cookie[owner])
stats.flush()
assert w.execute("SELECT count(*) FROM events WHERE vid=? AND admin=0", (VO,)).fetchone()[0] == 0  # marked, back to the start
get = lambda name, who=owner: json.loads(call("GET", f"/api/admin/{name}", cookie=cookie[who])[2])
o = get("overview?days=7")
assert o["kpi"]["visitors"] == 3 and o["kpi"]["searches"] == 2 and o["kpi"]["zero"] == 1, o["kpi"]  # Alice's, the FB one, the flood
assert get("overview?days=7&mine=1")["kpi"]["visitors"] == 4
assert len(o["daily"]) == 7 and sum(map(sum, o["heat"])) == o["kpi"]["visits"]
f = {s["step"]: s["n"] for s in o["funnel"]}
assert f["New browsers"] == 3 and f["Went past the landing page"] == 1 and f["Tapped sign-in"] == 1 and f["Signed in"] == 1 and f["Added a product"] == 1, f
assert {x["uid"] for x in o["live"]} == {alice, None}
st_ = get("stuck")
assert st_["zero"][0]["q"] == "חלב סויה ללא סוכר" and st_["rephrased"][0]["then_q"] == "חלב סויה"
assert st_["rage"][0]["zone"] == "b-cards" and st_["dead"][0]["n"] == 1 and st_["api"][0]["status"] == 502
assert {x["reason"] for x in st_["signin"]} == {"cancelled (access_denied)", "no state cookie"}
assert st_["onboard"] == [{"step": "1", "people": 1}]
us = get("usage")
assert us["added"][0]["name"] == "חלב 3%" and us["viewed"][0]["key"] == "7290004131074"
assert {x["q"] for x in us["searches"]} == {"חלב סויה", "חלב סויה ללא סוכר"}               # not the owner's
assert us["pages"][0]["page"] in ("home", "signin", "product") and us["prefs"]["accounts"] == 4
au = get("audience")
assert {x["k"] for x in au["device"]} == {"Phone"} and {x["k"] for x in au["browser"]} >= {"Safari", "Facebook"}
assert au["country"][0]["k"] == "IL" and au["new"] == 3
ul = get("users")
a = next(u for u in ul if u["id"] == alice)
assert a["searches"] == 2 and a["adds"] == 1 and a["device"] == "Phone" and a["visits"] == 1, a
ud = get(f"users/{alice}")
assert ud["user"]["email"] == "alice@example.com" and ud["prefs"]["km"] == 3
assert any(e["kind"] == "add" and e.get("name") == "חלב 3%" for e in ud["events"])
assert any(e["kind"] == "view" and e["page"] == "signin" for e in ud["events"])  # from before signing in, same browser
assert call("GET", "/api/admin/users/9999", cookie=cookie[owner])[0] == 404
sv = get("server")
routes = {(r["method"], r["route"]): r for r in sv["routes"]}
assert routes[("POST", "/api/ux")]["n"] >= 15 and ("GET", "/api/admin/users/{user_id}") in routes, list(routes)
assert routes[("GET", "/admin")]["e4"] >= 1                                  # the 404s for everyone else
assert ("GET", "(sign-in needed)") in routes
d = get("data")
assert d["products"] == 1 and "user.db" in d["sizes"] and d["accounts"] is True

# a crashing route is counted and noted
@server.app.get("/api/boom")
def boom():
    raise RuntimeError("kaboom")
server.app.router.routes.insert(0, server.app.router.routes.pop())  # before the catch-all routes
assert call("GET", "/api/boom", cookie=cookie[alice])[0] == 500
stats.flush()
assert w.execute("SELECT a, b FROM events WHERE kind='server_error'").fetchone() == ("/api/boom", "RuntimeError: kaboom")
assert any(r["route"] == "/api/boom" and r["e5"] == 1 for r in get("server")["routes"])

# ---------------------------------------------------------------- deleting an account deletes its statistics
assert call("DELETE", "/api/me", cookie=cookie[alice])[0] == 200
stats.flush()
assert w.execute("SELECT count(*) FROM events WHERE uid=? OR vid=?", (alice, V1)).fetchone()[0] == 0
assert w.execute("SELECT count(*) FROM visits WHERE uid=? OR vid=?", (alice, V1)).fetchone()[0] == 0
assert w.execute("SELECT uid, vid, sid FROM events WHERE kind='account_deleted'").fetchone() == (None, None, None)
assert w.execute("SELECT count(*) FROM events WHERE vid=?", (VO,)).fetchone()[0] == 3  # nobody else's

# ---------------------------------------------------------------- the privacy page says what's kept
st, _, b = call("GET", "/privacy?lang=en")
assert st == 200 and b"Usage statistics" in b and b"180 days" in b and b"no tracking" not in b.lower()
st, _, b = call("GET", "/privacy?lang=he")
assert "סטטיסטיקת שימוש".encode() in b
print("admin ok")
