"""Bot checks for signing in. Accounts are made only by signing in with Google or Facebook, so this is where a bot
would make accounts, and every layer below sits between the sign-in button and a new account:

1. Cloudflare Turnstile (free, no limit on checks): the sign-in page runs Cloudflare's challenge in the browser
   (invisible for most people; a checkbox when Cloudflare isn't sure: headless browsers, automation tools, data-
   centre addresses). The browser gets a one-time token; the server checks it with Cloudflare (siteverify) before it
   sends the browser on to Google or Facebook. A token is good once, for 5 minutes, for this site's address and
   the "signin" action only. On when auth.json has "turnstile": {"site_key", "secret"} (./set-login.sh turnstile).
2. One sign-in per check: each sign-in's state comes back from the provider once; a second callback with the same
   state (one solved check, many farmed provider accounts) is refused.
3. Limits per visitor (Cloudflare's Cf-Connecting-IP, kept hashed and in memory only): sign-in starts, and new
   accounts per day. Site-wide limits on new accounts per hour and per day, so a flood can't fill the database.
   People signing back in never count towards the new-account limits.
4. A new Google account needs an address Google verified; known crawler and script User-Agents can't start sign-in.

The home network and Tailscale (no Cf-Connecting-IP) are the household: the visitor limits don't apply there.
"""
import hashlib
import json
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import auth, guard

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
ACTION = "signin"
TOKEN_MAX = 2048  # Cloudflare's tokens are at most 2048 characters

# (attempts, per seconds): a visitor's sign-in starts, a visitor's new accounts, and new accounts site-wide.
# Israel's mobile carriers put many phones behind one address, so a visitor's limits leave room for a family.
# auth.json "limits" can change any of them: {"new_per_hour": [120, 3600], ...}
LIMITS = {
    "start": (12, 600),          # 12 sign-in starts in 10 minutes
    "new_per_ip": (6, 86400),    # 6 new accounts a day from one address
    "new_per_hour": (60, 3600),  # site-wide
    "new_per_day": (500, 86400),
}

_lock = threading.Lock()
_used = {}  # sign-in state -> time it came back (each is good once)
USED_KEEP = 900  # as long as the state cookie lives
USED_MAX = 20000


def config():
    return auth.config().get("turnstile") or {}


def site_key():
    """The public site key the sign-in page needs, or None when Turnstile isn't set up."""
    c = config()
    return c.get("site_key") if c.get("site_key") and c.get("secret") else None


def on():
    return bool(site_key())


def _limit(name):
    n, per = (auth.config().get("limits") or {}).get(name) or LIMITS[name]
    return float(n), float(per)


def _key(ip):
    return hashlib.sha256(f"sal-ip:{ip}".encode()).hexdigest()[:24]


def _take(name, who=""):
    """Spend one from the named limit; 0 when allowed, else seconds until it would be."""
    n, per = _limit(name)
    return guard._take(("signin", name, who), 1, n / per, n)


def _peek(name, who=""):
    """Would one more be allowed (without spending it)?"""
    n, per = _limit(name)
    key = ("signin", name, who)
    with guard._lock:
        b = guard._buckets.get(key)
        if b is None:
            return True
        return min(n, b[0] + (time.monotonic() - b[1]) * n / per) >= 1


def allow_start(ip, ua):
    """None when this visitor may start a sign-in, else why not ("bot", "busy")."""
    from . import stats
    if stats.is_bot(ua) or not (ua or "").strip():
        return "bot"
    if ip is not None and _take("start", _key(ip)):
        return "busy"
    return None


def allow_new(ip):
    """None when one more account may be made (for this visitor and site-wide), else "busy". Spends it."""
    if not _peek("new_per_hour") or not _peek("new_per_day"):
        return "busy"
    if ip is not None and _take("new_per_ip", _key(ip)):
        return "busy"
    _take("new_per_hour")
    _take("new_per_day")
    return None


def first_use(state):
    """True the first time a sign-in state comes back from the provider, False after."""
    now = time.monotonic()
    with _lock:
        if len(_used) >= USED_MAX:
            for k in [k for k, t in _used.items() if now - t > USED_KEEP]:
                del _used[k]
            if len(_used) >= USED_MAX:  # a flood of fresh states: none of them can be replayed for long anyway
                _used.clear()
        if state in _used and now - _used[state] <= USED_KEEP:
            return False
        _used[state] = now
        return True


def _siteverify(data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(VERIFY_URL, data=body, headers={"User-Agent": auth.UA, "Accept": "application/json"})
    with guard.outside("turnstile"), urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def verify(token, ip):
    """None when Cloudflare confirms this token (one human check, on this site, for signing in), else why not."""
    c = config()
    if not isinstance(token, str) or not token or len(token) > TOKEN_MAX:
        return "no token"
    data = {"secret": c.get("secret", ""), "response": token, "idempotency_key": secrets.token_hex(16)}
    if ip:
        data["remoteip"] = ip
    try:
        out = _siteverify(data)
    except (guard.Busy, OSError, ValueError, urllib.error.URLError) as e:  # fail closed: no check, no sign-in
        print("turnstile siteverify failed:", repr(e), file=sys.stderr)
        return "check unavailable"
    if not out.get("success"):
        return "failed: " + ",".join(out.get("error-codes") or [])[:60]
    if (out.get("metadata") or {}).get("result_with_testing_key"):  # Cloudflare's test keys (local testing): they
        return None  # pass every token anyway and name no action or site
    if out.get("action") != ACTION:
        return "wrong action"
    base = auth.config().get("base_url")
    want = urllib.parse.urlsplit(base).hostname if base else None
    if want and out.get("hostname") not in (want, "www." + want):
        return "wrong hostname"
    return None


def reset():
    """Forget every count (tests)."""
    with _lock:
        _used.clear()
