"""Accounts: sign in with Google or Facebook, and each account's own carts, address and settings.

OAuth 2.0 authorization-code flow written on the standard library (no extra packages): the browser goes to the
provider, comes back to /auth/<provider>/callback with a one-time code, and the server swaps that code for the
person's id, name and email over HTTPS with the app secret. A random session token goes into an HttpOnly cookie;
only its SHA-256 is stored (u.sessions), so a copy of the database can't be used to sign in.

Config: auth.json next to the price DB (or $SAL_AUTH), readable by Sal only. Fill it with
    ./set-login.sh google <client id>        (asks for the secret; runs python -m app.auth as the sal user)
    ./set-login.sh facebook <app id>
    ./set-login.sh base-url https://sal.example.com
    ./set-login.sh turnstile <site key>      Cloudflare Turnstile bot check on sign-in (asks for its secret key)
With no provider set up Sal runs as before: one household, no sign-in ("local" mode).

The household's carts and settings from before accounts belong to user 1. The first account to sign in takes them
over (only the account with `owner_email`, when that's set) and becomes the admin (reindex, the Google Maps key).
"""
import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import db as dbmod

# The owner: the only account that can open /admin (usage statistics, every account). Hard-coded on purpose, so no
# setting, database row or other sign-in can grant it: it has to be this Gmail address, verified by Google itself.
# Kept as its SHA-256 so the address isn't published with the source. Check: hashlib.sha256(b"<address>").hexdigest()
OWNER_EMAIL_SHA256 = "3e2c2a2bc827f8fa24c77cb6c6bf3aad8d21db84848dbf27e50e48a01a9c1774"


def owner_email(email):
    """True for the owner's address (any letter case)."""
    return bool(email) and hmac.compare_digest(hashlib.sha256(email.strip().lower().encode()).hexdigest(), OWNER_EMAIL_SHA256)

SESSION_COOKIE = "sal_session"
STATE_COOKIE = "sal_oauth"
SESSION_DAYS = 180
FB_VERSION = "v23.0"
UA = "Sal-basket-prices/1.0"


def _data_dir():
    return os.path.dirname(os.path.abspath(dbmod.DB_PATH))


def config_path():
    return os.environ.get("SAL_AUTH") or os.path.join(_data_dir(), "auth.json")


_cfg = {"mtime": None, "data": {}}


def config():
    """auth.json, re-read when it changes (so setting up a provider needs no restart)."""
    p = config_path()
    try:
        m = os.stat(p).st_mtime
    except OSError:
        return {}
    if m != _cfg["mtime"]:
        try:
            with open(p) as f:
                _cfg["data"] = json.load(f)
        except (OSError, ValueError) as e:
            print("auth.json unreadable:", e, file=sys.stderr)
            _cfg["data"] = {}
        _cfg["mtime"] = m
    return _cfg["data"]


def providers():
    c = config()
    out = []
    if (c.get("google") or {}).get("client_id") and (c.get("google") or {}).get("client_secret"):
        out.append("google")
    if (c.get("facebook") or {}).get("app_id") and (c.get("facebook") or {}).get("app_secret"):
        out.append("facebook")
    if os.environ.get("SAL_DEV_LOGIN") == "1":  # tests and local development only: never set on the server
        out.append("dev")
    return out


def accounts_on():
    return bool(providers())


def _secret():
    """Key for signing the short-lived sign-in cookie: auth.json "secret", else one generated once and kept in
    secret.key next to the DB."""
    s = config().get("secret")
    if s:
        return s.encode()
    p = os.path.join(_data_dir(), "secret.key")
    try:
        with open(p, "rb") as f:
            return f.read().strip()
    except OSError:
        key = secrets.token_urlsafe(48).encode()
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key


def sign(obj):
    body = base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def unsign(value, max_age):
    try:
        body, mac = value.rsplit(".", 1)
        if not hmac.compare_digest(mac, hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()):
            return None
        obj = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return obj if time.time() - obj.get("t", 0) <= max_age else None
    except (ValueError, AttributeError, TypeError):
        return None


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- users and sessions
PUBLIC = ("id", "name", "email", "avatar", "lang", "onboarded", "admin")


def user_row(c, uid):
    r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(r) if r else None


def public(u):
    if not u:
        return None
    return {k: u.get(k) for k in PUBLIC} | {"onboarded": bool(u.get("onboarded")), "admin": bool(u.get("admin")),
                                            "providers": u.get("providers", [])}


def session_user(c, token):
    if not token:
        return None
    h = _hash(token)
    r = c.execute("SELECT user_id, expires, seen FROM sessions WHERE token=?", (h,)).fetchone()
    now = int(time.time())
    if not r or r["expires"] < now:
        return None
    if now - (r["seen"] or 0) > 3600:  # rolling expiry, written at most hourly
        c.execute("UPDATE sessions SET seen=?, expires=? WHERE token=?", (now, now + SESSION_DAYS * 86400, h))
        c.execute("UPDATE users SET seen=? WHERE id=?", (now, r["user_id"]))
        c.commit()
    return user_row(c, r["user_id"])


def is_owner(c, u):
    """True only for an account signed in with Google as the owner's address, verified by Google (never Facebook,
    never an email typed or merged in some other way)."""
    if not u or not u.get("id"):
        return False
    return any(owner_email(r[0]) for r in c.execute(
        "SELECT email FROM identities WHERE user_id=? AND provider='google' AND verified=1", (u["id"],)))


def new_session(c, uid):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    c.execute("INSERT INTO sessions(token, user_id, created, seen, expires) VALUES(?,?,?,?,?)",
              (_hash(token), uid, now, now, now + SESSION_DAYS * 86400))
    c.execute("DELETE FROM sessions WHERE expires < ?", (now,))
    c.commit()
    return token


def end_session(c, token):
    if token:
        c.execute("DELETE FROM sessions WHERE token=?", (_hash(token),))
        c.commit()


def existing(c, provider, subject, email, verified):
    """The account this provider identity already belongs to (itself, or another sign-in that verified the same
    address), else None: signing in would make a new one."""
    email = (email or "").strip().lower() or None
    r = c.execute("SELECT user_id FROM identities WHERE provider=? AND subject=?", (provider, subject)).fetchone()
    if r:
        return r[0]
    if email and verified:  # only ever joined through another sign-in that verified the same address
        r = c.execute("SELECT user_id FROM identities WHERE email=? AND verified=1 ORDER BY created LIMIT 1", (email,)).fetchone()
        return r[0] if r else None
    return None


def sign_in(c, provider, subject, email, verified, name, avatar, lang=None):
    """The account for this provider identity: an existing one, the one with the same verified email (Google and
    Facebook on one account), the household's (first sign-in), or a new one. Returns the user id."""
    now = int(time.time())
    email = (email or "").strip().lower() or None
    uid = existing(c, provider, subject, email, verified)
    if uid is None:
        owner = (config().get("owner_email") or "").strip().lower()
        first = not c.execute("SELECT 1 FROM identities LIMIT 1").fetchone()
        if first and (not owner or (owner == email and verified)) and c.execute("SELECT 1 FROM users WHERE id=1").fetchone():
            uid = 1
            c.execute("UPDATE users SET admin=1 WHERE id=1")
    if uid is None:
        uid = c.execute("INSERT INTO users(name, email, avatar, created, seen, lang, onboarded, admin) VALUES(?,?,?,?,?,?,0,0)",
                        (name, email, avatar, now, now, lang)).lastrowid
    c.execute("INSERT OR REPLACE INTO identities(provider, subject, user_id, email, verified, created) VALUES(?,?,?,?,?,?)",
              (provider, subject, uid, email, int(bool(verified)), now))
    # the provider's current name and photo
    c.execute("UPDATE users SET name=coalesce(nullif(?, ''), name), email=coalesce(email, ?), avatar=?, seen=?, "
              "lang=coalesce(lang, ?) WHERE id=?", (name, email, avatar, now, lang, uid))
    c.commit()
    return uid


def delete_user(c, uid):
    """Everything kept about an account: carts, settings, sign-ins, sessions."""
    with c:
        c.execute("DELETE FROM cart_items WHERE cart_id IN (SELECT id FROM carts WHERE user_id=?)", (uid,))
        c.execute("DELETE FROM carts WHERE user_id=?", (uid,))
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        c.execute("DELETE FROM identities WHERE user_id=?", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,))


# ---------------------------------------------------------------- providers
def _http(url, data=None, headers=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        raise RuntimeError(f"{e.code} from {urllib.parse.urlsplit(url).netloc}: {detail}") from None


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def start(provider, redirect_uri, who=None):
    """(URL to send the browser to, signed state for the cookie)."""
    c = config()
    state, (verifier, challenge) = secrets.token_urlsafe(24), _pkce()
    if provider == "google":
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
            "client_id": c["google"]["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
            "scope": "openid email profile", "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256", "prompt": "select_account"})
    elif provider == "facebook":
        v = c["facebook"].get("version") or FB_VERSION
        url = f"https://www.facebook.com/{v}/dialog/oauth?" + urllib.parse.urlencode({
            "client_id": c["facebook"]["app_id"], "redirect_uri": redirect_uri, "response_type": "code",
            "scope": "public_profile,email", "state": state})
    elif provider == "dev":  # SAL_DEV_LOGIN=1 only: signs in as test user `who` without any provider
        url = f"{redirect_uri}?" + urllib.parse.urlencode({"code": f"dev-{who or 'tester'}", "state": state})
    else:
        raise ValueError(provider)
    return url, {"p": provider, "s": state, "v": verifier, "t": int(time.time())}


def finish(provider, code, redirect_uri, verifier):
    """Swap the one-time code for who signed in: (subject, email, email verified, name, photo URL)."""
    c = config()
    if provider == "google":
        tok = _http("https://oauth2.googleapis.com/token", {
            "code": code, "client_id": c["google"]["client_id"], "client_secret": c["google"]["client_secret"],
            "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier})
        me = _http("https://openidconnect.googleapis.com/v1/userinfo",
                   headers={"Authorization": f"Bearer {tok['access_token']}"})
        return me["sub"], me.get("email"), bool(me.get("email_verified")), me.get("name") or "", me.get("picture")
    if provider == "facebook":
        fb = c["facebook"]
        v = fb.get("version") or FB_VERSION
        tok = _http(f"https://graph.facebook.com/{v}/oauth/access_token?" + urllib.parse.urlencode({
            "client_id": fb["app_id"], "client_secret": fb["app_secret"], "redirect_uri": redirect_uri, "code": code}))
        at = tok["access_token"]
        proof = hmac.new(fb["app_secret"].encode(), at.encode(), hashlib.sha256).hexdigest()
        me = _http(f"https://graph.facebook.com/{v}/me?" + urllib.parse.urlencode({
            "fields": "id,name,email,picture.width(160).height(160)", "access_token": at, "appsecret_proof": proof}))
        pic = ((me.get("picture") or {}).get("data") or {})
        # Facebook doesn't say whether the address was confirmed: it is kept, but never used to join accounts
        return me["id"], me.get("email"), False, me.get("name") or "", None if pic.get("is_silhouette") else pic.get("url")
    if provider == "dev":
        who = "".join(ch for ch in code[4:] if ch.isalnum())[:20] or "tester"
        return f"dev-{who}", f"{who}@example.com", True, f"Test {who.title()}", None
    raise ValueError(provider)


# ---------------------------------------------------------------- setup from the command line
def _save(cfg):
    p = config_path()
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, p)


def main(argv=None):
    a = list(argv if argv is not None else sys.argv[1:])
    cfg = dict(config())
    secret = lambda: a[2] if len(a) == 3 else os.environ.get("SAL_SECRET") or getpass.getpass("secret: ")
    if a[:1] == ["google"] and len(a) in (2, 3):
        cfg["google"] = {"client_id": a[1], "client_secret": secret()}
    elif a[:1] == ["facebook"] and len(a) in (2, 3):
        cfg["facebook"] = {"app_id": a[1], "app_secret": secret()}
    elif a[:1] == ["turnstile"] and len(a) in (2, 3):  # Cloudflare Turnstile: site key, then its secret key
        cfg["turnstile"] = {"site_key": a[1], "secret": secret()}
    elif a[:1] == ["base-url"] and len(a) == 2:
        cfg["base_url"] = a[1].rstrip("/")
    elif a[:1] == ["owner-email"] and len(a) == 2:
        cfg["owner_email"] = a[1].strip().lower()
    elif a[:1] == ["off"] and len(a) == 2 and a[1] in ("google", "facebook", "turnstile"):
        cfg.pop(a[1], None)
    elif a[:1] in ([], ["status"]):
        print("config:", config_path())
        print("sign-in with:", ", ".join(p for p in providers() if p != "dev") or "nothing (local mode: no accounts)")
        print("base url:", cfg.get("base_url") or "(from each request)")
        t = cfg.get("turnstile") or {}
        print("bot check (Turnstile):", "on, site key " + t["site_key"] if t.get("site_key") and t.get("secret") else "off")
        return 0
    else:
        print(__doc__)
        return 2
    _save(cfg)
    print("saved", config_path())
    return 0


if __name__ == "__main__":
    sys.exit(main())
