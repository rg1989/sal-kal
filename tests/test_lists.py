"""Run: .venv/bin/python tests/test_lists.py
Category and search lists come fast without ever showing a deal that isn't live: the deal table is read once per
deals table (each index run) and a deal's start or end is checked when asked; lists are kept per query and place
until LIST_TTL, the first start or end of a deal among them, or new data."""
import json
import os
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="sal-lists-")
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import auth, db as dbmod, deals as dealsmod, engine, server  # noqa: E402

fmt = lambda t: int(time.strftime("%Y%m%d%H%M", time.localtime(t)))
NOW = fmt(time.time())
PAST, SOON, LATER, GONE = fmt(time.time() - 86400), fmt(time.time() + 3600), fmt(time.time() + 2 * 86400), fmt(time.time() - 3600)

# ================================================================ LiveDeals: which deal is live is decided when asked
ld = server.LiveDeals({"a": [(0.5, LATER, None), (0.3, PAST, SOON)], "b": [(0.2, None, None)]})
assert ld.get("a") == 0.3 and ld.get("b") == 0.2 and ld.get("zz") is None and ld.get("zz", 0) == 0
assert ld.get("a", now=SOON) == 0.3                      # still live through its last minute
assert ld.get("a", now=fmt(time.time() + 7200)) is None  # ended, and the bigger one hasn't started
assert ld.get("a", now=LATER) == 0.5                     # the bigger one started
assert ld.next_change(["a", "b"]) == SOON and ld.next_change(["b"]) == 999912312359
assert ld.next_change(["a"], now=fmt(time.time() + 7200)) == LATER - 1  # the minute before the next one starts

# ================================================================ deal_pct and the list cache, on a scratch database
c = dbmod.connect(os.environ["BASKET_DB"])
c.executescript(dealsmod.SCHEMA)
server.con = lambda: c
MILK, SOAP = "7290004131074", "7290110115395"
c.executescript(f"""
INSERT INTO chains(chain_id, name_he) VALUES('A', NULL);
INSERT INTO stores(chain_id, store_id, name, store_type, price_full_ts, promo_full_ts, last_price_ts) VALUES ('A', 1, 'A1', 1, 1, {NOW}, {NOW});
INSERT INTO products(key, code, name, is_weighted, scope, store_count, chain_count, min_price, max_price, norm) VALUES
  ('{MILK}', '{MILK}', 'חלב 1 ליטר', 0, 'global', 3, 3, 6.0, 7.0, 'חלב'), ('{SOAP}', '{SOAP}', 'חלב סבון', 0, 'global', 2, 3, 9.0, 9.9, 'סבון');
INSERT INTO prices(key, chain_id, store_id, price, ts) VALUES ('{MILK}', 'A', 1, 7.0, 100), ('{SOAP}', 'A', 1, 9.9, 100);
INSERT INTO deals VALUES ('{MILK}', 'A', 1, 5, 5, 7, 0.3, 1, 'מבצע', {PAST}, {SOON}),
                         ('{SOAP}', 'A', 1, 5, 5, 9.9, 0.5, 1, 'מבצע מועדון', {PAST}, NULL);
INSERT INTO products_fts(key, name, manufacturer, code) SELECT key, name, manufacturer, code FROM products;
""")
engine.put_setting(c, "deals_version", 1)
server._user.set(auth.user_row(c, 1))

pct = server.deal_pct(c, False)
assert pct.get(MILK) == 0.3 and pct.get(SOAP) is None             # club deals only with club prices
assert pct.live() == {MILK: 0.3}
assert server.deal_pct(c, True).get(SOAP) == 0.5
assert server.deal_pct(c, False) is pct                            # read once per deals table, not per deal ending
assert pct.get(MILK, now=fmt(time.time() + 7200)) is None          # ... and the ending is still seen
engine.put_setting(c, "deals_version", 2)
assert server.deal_pct(c, False) is not pct                        # a new deals table: read again
assert all(k[0] == 2 for k in server._deal_pct)                    # the old tables are let go

# the list cache: the same list until something it depends on changes
server._lists.clear()
r1 = server.browse(terms="חלב")
assert {p["key"] for p in r1["products"]} == {MILK, SOAP} and r1["deal_n"] == 1
assert server.browse(terms="חלב") is r1                            # kept
assert server.browse(terms="חלב", sort="price") is not r1          # another query
until, expires, _ = next(iter(server._lists.values()))
assert until == SOON and expires > time.time()                     # until the milk deal ends
prefs = json.loads(c.execute("SELECT prefs FROM users WHERE id=1").fetchone()[0] or "{}")
c.execute("UPDATE users SET prefs=? WHERE id=1", (json.dumps({**prefs, "club": True}),))
c.commit()
server._user.set(auth.user_row(c, 1))
r2 = server.browse(terms="חלב")
assert r2 is not r1 and r2["deal_n"] == 2                          # club deals count now: another list
engine.put_setting(c, "deals_version", 3)
assert server.browse(terms="חלב") is not r2                        # new deals: read again
r3 = server.browse(terms="חלב")
k3 = next(k for k, v in server._lists.items() if v[2] is r3)
server._lists[k3] = (fmt(time.time() - 120), time.time() + 600, r3)  # a deal in it has ended since
assert server.browse(terms="חלב") is not r3
assert server.browse(terms="") == {"total": 0, "products": [], "refine": [], "sizes": [], "deal_n": 0}
server.MAX_LISTS = 2
for q in ("a1", "b1", "c1"):
    server.browse(terms=q)
assert len(server._lists) == 2                                     # the oldest go first
# two requests for one list at once: the second waits for the first and gets the same list (read once)
import threading
server.MAX_LISTS = 400
server._lists.clear()
reads, real = [], server._browse
def slow(*a):
    reads.append(1)
    time.sleep(0.3)
    return real(*a)
server._browse = slow
got = []
th = [threading.Thread(target=lambda: got.append(server.browse(terms="חלב"))) for _ in range(3)]
[x.start() for x in th]
[x.join() for x in th]
assert len(reads) == 1 and got[0] is got[1] is got[2] and not server._lists_busy, (len(reads), server._lists_busy)
server._browse = real
print("ok")
