"""Run: .venv/bin/python tests/test_signin.py
Sign-in redirects through the real ASGI app: sign-in only works on the base address (the provider sends the browser
back there, and the one-time state cookie lives there), so pages and /auth from any other address move there first;
a failed sign-in comes back to the app's sign-in page (which says why), not to the landing page."""
import asyncio
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="sal-signin-")
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
os.environ["SAL_AUTH"] = os.path.join(TMP, "auth.json")
os.environ["SAL_STATS_SYNC"] = "1"
os.environ.pop("SAL_DEV_LOGIN", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import auth, server  # noqa: E402


def setup(conf):
    with open(os.environ["SAL_AUTH"], "w") as f:
        json.dump(conf, f)
    os.utime(os.environ["SAL_AUTH"], (0, os.stat(os.environ["SAL_AUTH"]).st_mtime + 1))  # re-read even within a second
    auth.config()


def call(path, host="sal-kal.com", internet=False, cookie=None, method="GET"):
    hdrs = {"host": host, "user-agent": "test"}
    if internet:
        hdrs["cf-connecting-ip"] = "198.51.100.7"
    if cookie:
        hdrs["cookie"] = cookie
    p, _, q = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": p, "raw_path": p.encode(), "query_string": q.encode(), "root_path": "",
             "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()], "client": ("10.0.0.5", 5555),
             "server": (host, 80)}
    out, sent = [], [False]

    async def receive():
        if not sent[0]:
            sent[0] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()

    async def send(m):
        out.append(m)

    asyncio.run(server.app(scope, receive, send))
    start = next(m for m in out if m["type"] == "http.response.start")
    h = {k.decode(): v.decode() for k, v in start["headers"]}
    cookies = [v.decode() for k, v in start["headers"] if k == b"set-cookie"]
    return start["status"], h, cookies


BASE = "https://sal-kal.com"
setup({"google": {"client_id": "id", "client_secret": "s"}, "base_url": BASE})

# the home network (no Cloudflare header) with accounts on: pages and sign-in move to the base address, temporarily
for path in ("/", "/app", "/admin", "/auth/google?lang=he&v=x.y"):
    st, h, _ = call(path, host="omarchy:8080")
    assert st == 307 and h["location"] == BASE + path, (path, st, h.get("location"))
st, h, cookies = call("/auth/google?lang=he", host="omarchy:8080")
assert not any(c.startswith(auth.STATE_COOKIE) for c in cookies)  # no state cookie left on the wrong address
assert call("/api/status", host="omarchy:8080")[0] == 200           # API calls stay (status checks, scripts)
assert call("/static/sw.js", host="omarchy:8080")[0] in (200, 404) and call("/static/sw.js", host="omarchy:8080")[0] != 307

# on the base address: sign-in starts there and sets the state cookie for /auth
st, h, cookies = call("/auth/google?lang=he", host="sal-kal.com", internet=True)
assert st == 303 and h["location"].startswith("https://accounts.google.com/") and "redirect_uri=https%3A%2F%2Fsal-kal.com" in h["location"]
assert any(c.startswith(auth.STATE_COOKIE + "=") and "Path=/auth" in c for c in cookies), cookies

# from the internet under another name: a permanent move, as before
st, h, _ = call("/app", host="www.sal-kal.com", internet=True)
assert st == 301 and h["location"] == BASE + "/app"

# coming back without the state cookie (another browser, or an old link): the app's sign-in page says it failed
st, h, _ = call("/auth/google/callback?code=c&state=s", internet=True)
assert st == 303 and h["location"] == "/app#/signin?signin=failed", h["location"]
st, h, _ = call("/auth/google/callback?error=access_denied", internet=True)
assert h["location"] == "/app#/signin?signin=cancelled"

# without a base address (local test servers) nothing moves
setup({"google": {"client_id": "id", "client_secret": "s"}})
assert call("/app", host="127.0.0.1:8766")[0] == 200
# accounts off (local mode): the home network keeps the app where it is
setup({"base_url": BASE})
assert call("/app", host="omarchy:8080")[0] == 200
print("ok")
