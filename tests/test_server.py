"""Run: .venv/bin/python tests/test_server.py
Card prices, deals and price history from app.server, on a scratch database (never the live one)."""
import os
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="sal-test-")
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")  # app.server connects at import: keep it off the live DB
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db as dbmod, deals as dealsmod, engine, server  # noqa: E402
NOW_TS = int(time.strftime("%Y%m%d%H%M"))  # names seen just now

c = dbmod.connect(os.environ["BASKET_DB"])
c.executescript(dealsmod.SCHEMA)
server.con = lambda: c
fmt = lambda t: int(time.strftime("%Y%m%d%H%M", time.localtime(t)))
NOW, OLD, SOON = fmt(time.time() - 3600), fmt(time.time() - 3 * 86400), fmt(time.time() + 86400)
MILK, SOAP, BAD = "7290004131074", "7290110115395", "7290000066333"   # BAD fails the GS1 check digit
c.executescript(f"""
INSERT INTO chains(chain_id, name_he, folder) VALUES('A', NULL, 'ChainA'), ('B', NULL, 'ChainB');
INSERT INTO stores(chain_id, store_id, name, store_type, price_full_ts, promo_full_ts, last_price_ts) VALUES
  ('A', 1, 'A1', 1, 1, {NOW}, {NOW}), ('A', 2, 'A2', 1, 1, {NOW}, {NOW}), ('B', 1, 'B1', 1, 1, {NOW}, {NOW}),
  ('B', 2, 'B old', 1, 1, {NOW}, {OLD}), ('A', 3, 'A old deals', 1, 1, {OLD}, {NOW});
INSERT INTO products(key, code, name, is_weighted, scope, store_count, chain_count, min_price, max_price, norm) VALUES
  ('A:100', '100', 'עגבניה', 1, 'chain', 2, 1, 9.9, 12.9, 'עגבניה'),
  ('B:200', '200', 'עגבניה', 0, 'chain', 1, 1, 3.9, 3.9, 'עגבניה'),
  ('{MILK}', '{MILK}', 'חלב 1 ליטר', 0, 'global', 3, 3, 6.0, 7.0, 'חלב'),
  ('{SOAP}', '{SOAP}', 'סבון', 0, 'global', 2, 3, 9.0, 9.9, 'סבון'),
  ('{BAD}', '{BAD}', 'קוד פנימי', 0, 'global', 2, 2, 1.0, 99.0, 'קוד');
INSERT INTO prices(key, chain_id, store_id, price, ts) VALUES ('A:100', 'A', 1, 9.9, 100), ('A:100', 'A', 2, 12.9, 100), ('B:200', 'B', 1, 3.9, 100),
  ('{MILK}', 'A', 1, 7.0, 90), ('{MILK}', 'B', 1, 6.0, 100), ('{SOAP}', 'A', 1, 9.9, 100), ('{SOAP}', 'B', 1, 9.0, 100),
  ('{BAD}', 'A', 1, 1.0, 100), ('{BAD}', 'B', 1, 99.0, 100), ('{MILK}', 'A', 3, 7.0, 90);
INSERT INTO item_names(key, chain_id, name, ts) VALUES ('{MILK}', 'A', 'חלב 1 ליטר', {NOW_TS}), ('{MILK}', 'B', 'חלב 1 ליטר', {NOW_TS}), ('{SOAP}', 'A', 'סבון', {NOW_TS});
""")
near = {("A", 1): 1.0, ("A", 2): 2.0, ("A", 3): 2.0, ("B", 1): 0.5, ("B", 2): 0.4}
server._near_stores = lambda con: near
server.geo.store_coords = lambda con: {sk: (32.0, 34.8) for sk in near}
engine.put_setting(c, "prefs", {"home": {"lat": 32.0, "lon": 34.8, "label": "x"}, "max_km": 5})


def card(key):
    rows = c.execute("SELECT * FROM products WHERE key=?", (key,)).fetchall()
    return server._enrich(c, server._present(c, rows, 10))[0]


# ---------------------------------------------------------------- cards (F15, F18, F19)
rows = c.execute("SELECT * FROM products WHERE scope='chain' ORDER BY store_count DESC").fetchall()
cards = server._enrich(c, server._present(c, rows, 10))
assert len(cards) == 1, cards                         # one card per produce name ...
t = cards[0]
assert t["key"] == "A:100" and t["chain"] == "A" and t["chain_count"] == 2
assert (t["min_price"], t["max_price"]) == (9.9, 12.9)          # ... with its own chain's prices, not B's ₪3.90
assert t["near"] == {"price": 9.9, "chain": "A", "km": 1.0}, t["near"]  # priced at its own chain only
assert card(MILK)["near"]["price"] == 6.0             # a barcode: the cheapest confirmed price near you, any chain
assert card(SOAP)["near"] == {"price": 9.9, "chain": "A", "km": 1.0}  # B never named its soap: not checked, not counted
b = card(BAD)                                          # not a real barcode: no price across chains
assert b["near"] is None and b["min_price"] is None and b["max_price"] is None
assert server.comparable(MILK) and not server.comparable(BAD) and not server.comparable("A:100")
c.execute("INSERT INTO prices(key, chain_id, store_id, price, ts) VALUES(?, 'B', 2, 1.0, 100)", (MILK,))  # stale store
m = card(MILK)                                         # the range is worked out now, from confirmed prices only
assert (m["min_price"], m["max_price"]) == (6.0, 7.0) and m["near"]["chain"] == "B", m
assert [d["key"] for d in server.cards(keys=f"{SOAP},nope,{MILK}")] == [SOAP, MILK]  # recently viewed, in order
engine.put_setting(c, "confirmed_through", 99)         # B's milk (file 100) is newer than the last check; A's (90) isn't
assert card(MILK)["near"]["price"] == 7.0
c.execute("UPDATE prices SET ts=50 WHERE key=? AND chain_id='B'", (MILK,))
assert card(MILK)["near"]["price"] == 6.0
engine.put_setting(c, "confirmed_through", 0)          # 0 / missing: not tracked, every price counts as checked
c.execute("UPDATE prices SET ts=100 WHERE key=? AND chain_id='B'", (MILK,))
assert card(MILK)["near"]["price"] == 6.0
c.execute("DELETE FROM u.settings WHERE key='confirmed_through'")
assert card(MILK)["near"]["price"] == 6.0

# ---------------------------------------------------------------- deals at request time (F21)
c.executemany("""INSERT INTO deals(key, chain_id, store_id, price, unit_price, typical, save_pct, min_qty, description,
                                    start_ts, end_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", [
    (MILK, "A", 1, 7.0, 5.0, 6.5, 0.23, 1, "live", NOW, SOON),
    (MILK, "B", 2, 6.0, 4.0, 6.5, 0.38, 1, "store out of date", NOW, SOON),
    (MILK, "A", 2, 7.0, 3.0, 6.5, 0.54, 1, "ended", NOW, NOW),
    (MILK, "A", 3, 7.0, 3.5, 6.5, 0.46, 1, "deal file out of date", NOW, SOON),
    (SOAP, "B", 1, 9.0, 6.0, 9.5, 0.37, 1, "B's soap name not checked", NOW, SOON),
])
engine.put_setting(c, "deals_version", 1)
got = server.deals(kind="mine", keys=f"{MILK},{SOAP}", limit=5)
assert [d["deal"]["description"] for d in got] == ["live"], got
assert server.deal_pct(c, False) == {MILK: 0.23, SOAP: 0.37}   # the filter counts live deals at current stores
c.execute("UPDATE prices SET price=7.5 WHERE key=? AND chain_id='A' AND store_id=1", (MILK,))  # shelf price changed
engine.put_setting(c, "deals_version", 2)
assert server.deals(kind="mine", keys=MILK, limit=5) == []
c.execute("UPDATE prices SET price=7.0 WHERE key=? AND chain_id='A' AND store_id=1", (MILK,))

# ---------------------------------------------------------------- price history (F16, F17, F38)
c.executemany("INSERT INTO price_history(key, chain_id, day, mn, mx, n) VALUES(?,?,?,?,?,?)", [
    (MILK, "A", 20261101, 6.0, 6.5, 2), (MILK, "A", 20261103, None, None, 0), (MILK, "A", 20261105, 6.4, 6.4, 1),
    (MILK, "B", 20261101, 5.0, 5.0, 1), (MILK, "B", 20261104, None, None, 0),
    (SOAP, "A", 20261102, 9.0, 9.0, 1), (SOAP, "B", 20261101, 9.0, 9.0, 1),
    (BAD, "A", 20261101, 1.0, 1.0, 1),
])
c.execute("INSERT INTO name_flags(key, chain_id, name) VALUES(?, 'B', 'סבון ידיים')", (SOAP,))
c.execute("INSERT OR REPLACE INTO settings VALUES('history_since', '20261101')")
h = {s["chain"]: s for s in server.product_history(MILK)["chains"]}
assert h["A"]["points"] == [[20261101, 6.0, 6.5], [20261105, 6.4, 6.4]] and h["A"]["ends"] == [20261103]
assert not h["A"]["ended"] and h["B"]["ended"] and h["B"]["ends"] == [20261104]   # no row with a NULL price is served
assert [s["chain"] for s in server.product_history(SOAP)["chains"]] == ["A"]     # B's "soap" is another product
assert server.product_history(BAD)["chains"] == []
r = server.cart_history({"items": [{"key": MILK, "qty": 2}, {"key": SOAP, "qty": 1}, {"key": "A:100", "qty": 1}],
                         "chains": ["A", "B"]})  # produce is never in a total, as in the comparison
# A: milk from the 1st, soap only from the 2nd -> no total on the 1st (never soap's later price); milk's gap ends it
assert r["series"] == {"A": [[20261102, 21.0], [20261105, 21.8]]}, r["series"]
assert r["ends"] == {"A": [20261103]}, r["ends"]
assert "B" not in r["series"]                        # B's soap is flagged and its milk ended: no cart total there
assert server._on([[20261105, 7.2], [20261107, 7.6]], 20261101) is None
assert server._on([[20261105, 7.2], [20261107, None]], 20261108) is None
assert server._on([[20261105, 7.2], [20261107, 7.6]], 20261106) == 7.2
print("ok")
