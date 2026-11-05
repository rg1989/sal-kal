"""Run: .venv/bin/python tests/test_accounts.py
Accounts (app.auth), each account's own carts and settings (app.server), and the address lists (app.places), on a
scratch database. No network: the providers and the street lists are stubbed."""
import json
import os
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="sal-acc-")
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
os.environ["SAL_AUTH"] = os.path.join(TMP, "auth.json")
os.environ.pop("SAL_DEV_LOGIN", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3  # noqa: E402

# ---------------------------------------------------------------- the household from before accounts becomes user 1
u = sqlite3.connect(os.environ["BASKET_USER_DB"])
u.executescript("""CREATE TABLE carts(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, created INTEGER, updated INTEGER);
CREATE TABLE cart_items(cart_id INTEGER, key TEXT, qty REAL, name TEXT, position INTEGER, PRIMARY KEY(cart_id, key));
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
INSERT INTO carts(name, created, updated) VALUES ('Weekly shop', 1, 1);
INSERT INTO cart_items VALUES (1, '7290004131074', 2, 'חלב', 0);
INSERT INTO settings VALUES ('prefs', '{"home": {"label": "הרצל 3, רמת גן", "lat": 32.07, "lon": 34.83}, "max_km": 5}'),
                            ('delivery', '{"7290027600007": {"delivers_here": true}}');""")
u.commit()
u.close()

from fastapi import HTTPException  # noqa: E402
from app import auth, db as dbmod, places, server  # noqa: E402

c = dbmod.connect()
server.con = lambda: c
one = auth.user_row(c, 1)
assert one["onboarded"] == 1 and one["admin"] == 1, one              # had an address: no first-run steps
assert json.loads(one["prefs"])["max_km"] == 5
assert [tuple(r) for r in c.execute("SELECT user_id FROM carts")] == [(1,)]   # the household's cart is user 1's
dbmod.connect().close()                                              # a second connection doesn't add another user
assert c.execute("SELECT count(*) FROM users").fetchone()[0] == 1

# ---------------------------------------------------------------- local mode: no providers, everyone is the household
assert not auth.accounts_on() and auth.providers() == []
with open(os.environ["SAL_AUTH"], "w") as f:
    json.dump({"google": {"client_id": "id", "client_secret": "s"}}, f)
time.sleep(0.01)
assert auth.providers() == ["google"] and auth.accounts_on()        # auth.json is re-read when it changes

# ---------------------------------------------------------------- who gets which account
roman = auth.sign_in(c, "google", "g-1", "Roman@Example.com", True, "Roman", "https://pic/1", lang="he")
assert roman == 1                                                    # the first sign-in takes over the household
assert auth.user_row(c, 1)["email"] == "roman@example.com" and auth.user_row(c, 1)["lang"] == "he"
assert auth.sign_in(c, "google", "g-1", "roman@example.com", True, "Roman G.", None) == 1  # same identity again
# Facebook doesn't say whether an address was confirmed, so it never joins an account by email (its own account instead)
assert auth.sign_in(c, "facebook", "f-1", "roman@example.com", False, "Roman", None) != 1
assert auth.sign_in(c, "google", "g-2", "roman@example.com", True, "Roman 2", None) == 1   # a verified address does join
alice = auth.sign_in(c, "facebook", "f-2", "alice@example.com", False, "Alice", None)
assert alice not in (1,) and not auth.user_row(c, alice)["onboarded"] and not auth.user_row(c, alice)["admin"]
# Facebook doesn't say an address was confirmed: a later Google sign-in with it gets its own account, never Alice's
mallory = auth.sign_in(c, "google", "g-3", "alice@example.com", True, "Mallory", None)
assert mallory != alice
assert auth.sign_in(c, "facebook", "f-9", "alice@example.com", False, "Eve", None) not in (alice, mallory)

# ---------------------------------------------------------------- sessions
tok = auth.new_session(c, alice)
assert auth.session_user(c, tok)["id"] == alice
assert c.execute("SELECT count(*) FROM sessions WHERE token=?", (tok,)).fetchone()[0] == 0  # only its hash is kept
assert auth.session_user(c, tok + "x") is None and auth.session_user(c, None) is None
c.execute("UPDATE sessions SET expires=1")
assert auth.session_user(c, tok) is None                             # expired
tok = auth.new_session(c, alice)
auth.end_session(c, tok)
assert auth.session_user(c, tok) is None                             # signed out

# signed state for the provider round trip: tampering or age makes it worthless
st = auth.sign({"p": "google", "s": "abc", "t": int(time.time())})
assert auth.unsign(st, 600)["s"] == "abc"
assert auth.unsign(st[:-2] + "00", 600) is None and auth.unsign(st, -1) is None and auth.unsign("junk", 600) is None

# ---------------------------------------------------------------- each account sees only its own carts and settings
def as_user(uid):
    server._user.set(auth.user_row(c, uid))

as_user(alice)
assert server.list_carts() == []
mine = server.create_cart({"name": "Party", "items": [{"key": "7290110115395", "qty": 3, "name": "סבון"}]})
assert [x["id"] for x in server.list_carts()] == [mine["id"]]
for call in (lambda: server.get_cart(1), lambda: server.update_cart(1, {"name": "x"}), lambda: server.delete_cart(1),
             lambda: server.duplicate_cart(1)):
    try:
        call()
        raise AssertionError("reached someone else's cart")
    except HTTPException as e:
        assert e.status_code == 404
try:
    server.reindex()
    raise AssertionError("reindex is the admin's")
except HTTPException as e:
    assert e.status_code == 403
assert server.prefs()["home"] is None
server.put_settings({"prefs": {"home": {"label": "x", "lat": 32.1, "lon": 34.8, "town": 5000, "evil": "<b>"}, "max_km": 900,
                               "nonsense": 1}, "delivery": {"7290058140886": {"delivers_here": True}}})
p = server.prefs()
assert p["home"] == {"label": "x", "lat": 32.1, "lon": 34.8, "town": 5000} and p["max_km"] == 100 and "nonsense" not in p
assert server.overrides() == {"7290058140886": {"delivers_here": True}}
try:
    server.put_settings({"prefs": {"home": {"label": "Paris", "lat": 48.85, "lon": 2.35}}})
    raise AssertionError("an address abroad was accepted")
except HTTPException as e:
    assert e.status_code == 422
try:
    server.put_settings({"maps_key": "billed-to-the-owner"})
    raise AssertionError("only the admin sets the Google key")
except HTTPException as e:
    assert e.status_code == 403
as_user(1)
assert [x["name"] for x in server.list_carts()] == ["Weekly shop"]
assert server.overrides() == {"7290027600007": {"delivers_here": True}}  # the household's delivery settings came along
assert server.prefs()["home"]["label"] == "הרצל 3, רמת גן"

# the first-run flag and language
as_user(alice)
assert server.get_me()["user"]["onboarded"] is False
r = server.put_me({"onboarded": True, "lang": "en", "admin": True})["user"]
assert (r["onboarded"], r["lang"]) == (True, "en")
assert server.get_me()["user"]["admin"] is False

# deleting an account erases all of it, and nobody else's
server.delete_me(None)
assert auth.user_row(c, alice) is None
assert not c.execute("SELECT 1 FROM carts WHERE user_id=?", (alice,)).fetchone()
assert not c.execute("SELECT 1 FROM cart_items WHERE cart_id=?", (mine["id"],)).fetchone()
assert not c.execute("SELECT 1 FROM identities WHERE user_id=?", (alice,)).fetchone()
assert c.execute("SELECT count(*) FROM cart_items WHERE cart_id=1").fetchone()[0] == 1

# ---------------------------------------------------------------- picking an address from the official lists
pc = places.con()
pc.executemany("INSERT INTO towns VALUES(?,?,?,?,?,?)", [
    (8600, "רמת גן", "Ramat Gan", places.norm("רמת גן"), places.skel("RAMAT GAN"), 689),
    (2620, "קרית אונו", "Qiryat Ono", places.norm("קרית אונו"), places.skel("QIRYAT ONO"), 150),
    (6400, "הרצליה", "Herzeliyya", places.norm("הרצליה"), places.skel("HERZELIYYA"), 775),
    (1, "עין גדי", "En Gedi", places.norm("עין גדי"), places.skel("EN GEDI"), 1)])
pc.executemany("INSERT INTO streets VALUES(?,?,?,?)", [
    (8600, 4321, "הרצל", places.norm("הרצל")), (2620, 259, "אלון יגאל", places.norm("אלון יגאל")),
    (2620, 125, "ארלוזורוב", places.norm("ארלוזורוב")), (1, 9000, "עין גדי", places.norm("עין גדי"))])
pc.execute("INSERT INTO meta VALUES('fetched', ?)", (str(time.time()),))
pc.commit()
assert [t["code"] for t in places.towns("קריית או")] == [2620]       # either spelling finds the official name
assert [t["code"] for t in places.towns("herzliya")] == [6400]       # everyday English finds the CBS transliteration
assert [t["code"] for t in places.towns("")][:2] == [6400, 8600]     # biggest first
assert places.streets(2620, "יגאל אלון")["streets"] == [{"code": 259, "name": "אלון יגאל"}]  # first name first works
assert places.streets(2620)["total"] == 2
assert places.streets(1)["no_streets"] is True
assert places.streets(8600, "תל אביב")["streets"] == []
assert places._street_names("אלון יגאל") == ["אלון יגאל", "יגאל אלון"]
assert "קריית אונו" in places._city_names("קרית אונו") and "תל אביב-יפו" in places._city_names("תל אביב - יפו")

calls = []
def fake(endpoint, params):
    calls.append(params)
    if "street" in params:
        return [{"lat": "32.0700", "lon": "34.8200", "address": {"road": "הרצל"}}] if "3 הרצל" in params["street"] else []
    return [{"lat": "32.0684", "lon": "34.8248"}]
places._nominatim = fake
h = places.locate(8600, 4321, 3)
assert h["label"] == "הרצל 3, רמת גן" and h["precision"] == "street" and (h["town"], h["street"], h["number"]) == (8600, 4321, 3)
n = len(calls)
assert places.locate(8600, 4321, 3) == h and len(calls) == n        # cached: no second lookup
assert places.locate(2620)["precision"] == "town"
print("ok")
