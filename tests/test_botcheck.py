"""Run: .venv/bin/python tests/test_botcheck.py
The sign-in bot checks (app.botcheck) through the real ASGI app: Cloudflare Turnstile's token is checked before the
browser goes to the provider, each sign-in state comes back once, crawlers and scripts can't start a sign-in, and
new accounts are limited per visitor and site-wide while people signing back in never are."""
import asyncio
import json
import os
import sys
import tempfile
import urllib.parse

TMP = tempfile.mkdtemp(prefix="sal-botcheck-")
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
os.environ["SAL_AUTH"] = os.path.join(TMP, "auth.json")
os.environ["SAL_STATS_SYNC"] = "1"
os.environ["SAL_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import auth, botcheck, guard, server  # noqa: E402

BASE = "https://sal-kal.com"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1"


def setup(conf):
    with open(os.environ["SAL_AUTH"], "w") as f:
        json.dump(conf, f)
    os.utime(os.environ["SAL_AUTH"], (0, os.stat(os.environ["SAL_AUTH"]).st_mtime + 1))
    auth.config()
    guard.reset()
    botcheck.reset()


def call(path, method="GET", body=None, cookie=None, ip="198.51.100.7", ua=UA, origin=BASE):
    hdrs = {"host": "sal-kal.com", "user-agent": ua}
    if ip:
        hdrs["cf-connecting-ip"] = ip
    if cookie:
        hdrs["cookie"] = cookie
    raw = json.dumps(body).encode() if body is not None else b""
    if body is not None:
        hdrs["content-type"] = "application/json"
        hdrs["content-length"] = str(len(raw))
    if method != "GET" and origin:
        hdrs["origin"] = origin
    p, _, q = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": p, "raw_path": p.encode(), "query_string": q.encode(), "root_path": "",
             "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()], "client": ("10.0.0.5", 5555),
             "server": ("sal-kal.com", 80)}
    out, sent = [], [False]

    async def receive():
        if not sent[0]:
            sent[0] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.Event().wait()

    async def send(m):
        out.append(m)

    asyncio.run(server.app(scope, receive, send))
    start = next(m for m in out if m["type"] == "http.response.start")
    h = {k.decode(): v.decode() for k, v in start["headers"]}
    cookies = [v.decode().split(";")[0] for k, v in start["headers"] if k == b"set-cookie"]
    data = b"".join(m.get("body", b"") for m in out if m["type"] == "http.response.body")
    try:
        data = json.loads(data)
    except ValueError:
        pass
    return start["status"], h, cookies, data


# Cloudflare's answer, made up: what siteverify says for each token
ANSWERS = {
    "good": {"success": True, "action": "signin", "hostname": "sal-kal.com"},
    "other-action": {"success": True, "action": "contact", "hostname": "sal-kal.com"},
    "other-site": {"success": True, "action": "signin", "hostname": "evil.example"},
    "used": {"success": False, "error-codes": ["timeout-or-duplicate"]},
}
seen = []


def fake_siteverify(data):
    seen.append(data)
    if data["response"] == "down":
        raise OSError("network unreachable")
    return ANSWERS[data["response"]]


botcheck._siteverify = fake_siteverify


def start(token="good", who="ann", ip="198.51.100.7", **kw):
    return call("/auth/dev", "POST", {"token": token, "lang": "he", "v": "", "who": who}, ip=ip, **kw)


def finish(url, cookies, ip="198.51.100.7"):
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    st, h, ck, _ = call(f"/auth/dev/callback?code={q['code']}&state={q['state']}", cookie="; ".join(cookies), ip=ip)
    return st, h["location"], ck


def sign_in(who, ip="198.51.100.7"):
    st, _, ck, out = start(who=who, ip=ip)
    assert st == 200, (st, out)
    return finish(out["url"], ck, ip)


# ---- off: sign-in by a plain link, as before; nothing about Turnstile on the page
setup({"base_url": BASE})
st, h, ck, _ = call("/auth/dev?who=zed")
assert st == 303 and "/auth/dev/callback" in h["location"], (st, h)
assert call("/api/me")[3]["turnstile"] is None

# ---- on
setup({"base_url": BASE, "turnstile": {"site_key": "0x4AAAA-site", "secret": "0x4AAAA-secret"}})
assert call("/api/me")[3]["turnstile"] == "0x4AAAA-site"  # the page needs the site key (public), never the secret
assert "0x4AAAA-secret" not in json.dumps(call("/api/me")[3])

# the page lets Cloudflare's script and frame in
_, h, _, _ = call("/app")
csp = h["content-security-policy"]
assert "https://challenges.cloudflare.com" in csp.split("script-src")[1].split(";")[0] and "frame-src https://challenges.cloudflare.com" in csp
# a fresh nonce on every page (Cloudflare's Bot Fight Mode stamps it on the detection script it injects)
nonce = lambda c: c.split("'nonce-")[1].split("'")[0]
assert nonce(csp) != nonce(call("/app")[1]["content-security-policy"]) and len(nonce(csp)) >= 20

# a plain link no longer starts a sign-in: back to the sign-in page, no state cookie
st, h, ck, _ = call("/auth/dev?who=zed")
assert st == 303 and h["location"] == "/app#/signin" and not any(c.startswith(auth.STATE_COOKIE + "=") and len(c) > 12 for c in ck)

# no token, a bad one, one for another action or site, or Cloudflare unreachable: no sign-in (fails closed)
for tok in (None, "", "x" * 3000, "used", "other-action", "other-site", "down"):
    st, _, ck, out = start(token=tok)
    assert st == 403 and out["detail"] == "bot check", (tok, st, out)
    assert not any(c.startswith(auth.STATE_COOKIE + "=") for c in ck)
# the server sends Cloudflare the secret, the token and the visitor's address
assert seen[-1]["secret"] == "0x4AAAA-secret" and seen[-1]["remoteip"] == "198.51.100.7" and seen[-1]["idempotency_key"]

# a good token: the provider's address and the one-time state cookie
st, _, ck, out = start()
assert st == 200 and out["url"].startswith(BASE + "/auth/dev/callback?"), (st, out)
assert any(c.startswith(auth.STATE_COOKIE + "=") for c in ck)
# the sign-in completes once ...
st, loc, ck2 = finish(out["url"], ck)
assert st == 303 and loc == "/#/" and any(c.startswith(auth.SESSION_COOKIE + "=") for c in ck2), (st, loc)
# ... and the same state can't be used again (one solved check, one sign-in)
st, loc, ck2 = finish(out["url"], ck)
assert st == 303 and loc == "/app#/signin?signin=failed" and not any(c.startswith(auth.SESSION_COOKIE + "=") for c in ck2)

# crawlers, scripts and empty User-Agents can't start a sign-in (even with a token)
for ua in ("curl/8.5.0", "python-requests/2.32", "Mozilla/5.0 (compatible; Googlebot/2.1)", ""):
    st, _, _, out = start(ua=ua)
    assert st == 403, (ua, st, out)

# another site's page can't start one for you
assert start(origin="https://evil.example")[0] == 403

# ---- limits
# sign-in starts per visitor
setup({"base_url": BASE, "turnstile": {"site_key": "k", "secret": "s"}, "limits": {"start": [3, 600]}})
assert [start(who="b", ip="203.0.113.1")[0] for _ in range(4)] == [200, 200, 200, 429]
assert start(who="b", ip="203.0.113.2")[0] == 200  # another visitor isn't affected

# new accounts per visitor: the third new account from one address waits; signing back in never does
setup({"base_url": BASE, "turnstile": {"site_key": "k", "secret": "s"}, "limits": {"new_per_ip": [2, 86400]}})
ip = "203.0.113.9"
assert sign_in("c1", ip)[1] == "/#/"
assert sign_in("c2", ip)[1] == "/#/"
assert sign_in("c3", ip)[1] == "/app#/signin?signin=busy"
assert sign_in("c1", ip)[1] == "/#/" and sign_in("c2", ip)[1] == "/#/"  # existing accounts sign in as often as they like
assert sign_in("c3", "203.0.113.10")[1] == "/#/"  # from elsewhere it's fine

# new accounts site-wide
setup({"base_url": BASE, "turnstile": {"site_key": "k", "secret": "s"}, "limits": {"new_per_hour": [2, 3600]}})
assert sign_in("d1", "192.0.2.1")[1] == "/#/"
assert sign_in("d2", "192.0.2.2")[1] == "/#/"
assert sign_in("d3", "192.0.2.3")[1] == "/app#/signin?signin=busy"
assert sign_in("d1", "192.0.2.4")[1] == "/#/"

# the home network (no Cloudflare header) isn't held to the per-visitor limits
setup({"base_url": BASE, "turnstile": {"site_key": "k", "secret": "s"}, "limits": {"start": [1, 600], "new_per_ip": [1, 86400]}})
assert [start(who="h", ip=None)[0] for _ in range(3)] == [200, 200, 200]

# a new Google account needs an address Google verified (Facebook's isn't known either way: it never joins accounts)
setup({"base_url": BASE})
c = server.con()
assert auth.existing(c, "google", "g-1", "new@example.com", False) is None

print("botcheck ok")
