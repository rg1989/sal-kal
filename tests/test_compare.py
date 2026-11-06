"""Run: .venv/bin/python tests/test_compare.py
The comparison counts confirmed lines only: no stale stores, flagged, unnamed or unchecked prices or names, produce,
unconfirmed deals or deals from out-of-date deal files."""
import json
import os
import shutil
import sys
import tempfile
import time
NOW_TS = int(time.strftime("%Y%m%d%H%M"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmp = tempfile.mkdtemp(prefix="sal-compare-")
os.environ["BASKET_DB"] = os.path.join(tmp, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(tmp, "user.db")
from app import chains, db, deal_terms, deals, engine  # noqa: E402

ts = lambda hours_ago: int(time.strftime("%Y%m%d%H%M", time.localtime(time.time() - hours_ago * 3600)))
PTS = ts(1)  # the deal files' time at fresh stores
OLD = ts(72)


def ean(body):
    """A valid EAN-13 from 12 digits."""
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    return body + str((10 - s % 10) % 10)


def connect(path):
    c = db.connect(path)
    if "ts" not in {r[1] for r in c.execute("PRAGMA table_info(item_names)")}:  # before db.py's migration
        c.execute("DROP TABLE item_names")
        c.execute("CREATE TABLE item_names(key TEXT, chain_id TEXT, name TEXT, ts INTEGER, "
                  "PRIMARY KEY(key, chain_id, name)) WITHOUT ROWID")
    if "club_ids" not in {r[1] for r in c.execute("PRAGMA table_info(promos)")}:
        c.execute("ALTER TABLE promos ADD COLUMN club_ids TEXT")
    return c


def store(c, cid, sid, age, store_type=1, name="Branch", promo_age=1):
    c.execute("INSERT INTO stores(chain_id, store_id, name, address, city, store_type, sub_chain, price_full_ts,"
              " promo_full_ts, last_price_ts) VALUES(?,?,?,'','',?,'',100,?,?)",
              (cid, sid, name, store_type, PTS if promo_age == 1 else ts(promo_age), ts(age)))
    c.execute("INSERT OR IGNORE INTO chains(chain_id, name_he) VALUES(?, '')", (cid,))


def product(c, key, name, scope="global", norm=None):
    c.execute("INSERT INTO products(key, code, name, manufacturer, quantity, unit, is_weighted, scope, store_count,"
              " chain_count, min_price, max_price, norm) VALUES(?,?,?,'','','',?,?,1,3,1,10,?)",
              (key, key.split(":")[-1], name, 1 if scope == "chain" else 0, scope, norm))


def price(c, key, cid, p, sid=1, ts_=100, named=None):
    """A shelf price; barcodes also get the chain's own name (named=False: the chain has no name for it)."""
    c.execute("INSERT INTO prices VALUES(?,?,?,?,?)", (key, cid, sid, p, ts_))
    if named is not False and ":" not in key:
        name = named or c.execute("SELECT name FROM products WHERE key=?", (key,)).fetchone()[0]
        c.execute("INSERT OR IGNORE INTO item_names VALUES(?,?,?,?)", (key, cid, name, NOW_TS))  # seen just now


PROMO = ("INSERT INTO promos(chain_id, store_id, promo_id, description, start_ts, end_ts, min_qty, discounted_price,"
         " discount_rate, club_only, ts, max_qty, remarks, club_ids) VALUES(?,?,?,?,NULL,NULL,?,?,?,?,?,?,NULL,?)")


def promo(c, cid, pid, key, desc, mq, dp, rate, sid=1, club=0, club_ids="0", max_qty=None, at=None):
    at = at or PTS
    c.execute(PROMO, (cid, sid, pid, desc, mq, dp, rate, club, at, max_qty, club_ids))
    c.execute("INSERT INTO promo_items VALUES(?,?,?,?,?)", (key, cid, sid, pid, at))


def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


# ================================================================ one store per chain, no home
c = connect(os.path.join(tmp, "basket.db"))
A, B, C, D, E = "7290027600007", "7290058140886", "7290055700007", "7290803800003", "7290873255550"
for cid, age in ((A, 1), (B, 2), (C, 1), (D, 72), (E, 1)):  # Yohananof (D) hasn't published for 3 days
    store(c, cid, 1, age)

MILK, BUNDLE, ODD, KINDER = "7290000042435", "7290000066332", "72917367", "80867067"
TOMATO, TOM_B, TOM_C = f"{A}:1475", f"{B}:2034", f"{C}:7290101501237"
for key, name, scope, norm in [(MILK, "חלב 1%", "global", None), (BUNDLE, "אפרופו", "global", None),
                               (ODD, "טעמי", "global", None), (KINDER, "קינדר בואנו", "global", None),
                               (TOMATO, "עגבניה מגי", "chain", "עגבניה מגי"), (TOM_B, "עגבניה מגי", "chain", "עגבניה מגי"),
                               (TOM_C, "עגבניה מגי ארוזה", "chain", "עגבניה מגי")]:
    product(c, key, name, scope, norm)
shelf = {(KINDER, A): "קינדר בואנו דארק 43 גרם"}
for cid, ps in {A: {MILK: 6.0, BUNDLE: 9.9, ODD: 4.0, KINDER: 5.0, TOMATO: 13.9},
                B: {MILK: 5.5, BUNDLE: 9.0, ODD: 636.9, KINDER: 5.0, TOM_B: 16.9},
                C: {MILK: 5.8, BUNDLE: 9.5, ODD: 4.5, KINDER: 5.2, TOM_C: 15.9},
                D: {MILK: 4.0, BUNDLE: 8.0, ODD: 3.0, KINDER: 4.0},
                E: {MILK: 1.0}}.items():
    for key, p in ps.items():
        price(c, key, cid, p, named=shelf.get((key, cid)))
c.execute("DELETE FROM item_names WHERE key=? AND chain_id=?", (KINDER, C))
c.execute("INSERT INTO price_flags VALUES(?,?,1,4.25)", (ODD, B))         # ₪636.90 where others charge ~₪4
c.execute("INSERT INTO price_flags VALUES(?,?,1,14.9)", (TOM_C, C))
c.execute("INSERT INTO name_flags VALUES(?,?,'פררו')", (KINDER, C))       # Carrefour calls this barcode something else
promo(c, A, "p1", BUNDLE, 'מגוון ירק טרי מהשורש 2 יח ב15.90 ש"ח', 2, 15.9, 10.92)
promo(c, C, "p2", MILK, "שוקולד רושן", 3, 20, None)                       # terms not stated: not counted
promo(c, B, "p3", ODD, "טעמי 2ב5", 2, 5, None)                            # on a flagged price: never a deal
promo(c, B, "p7", KINDER, "קינדר 2 ב 8", 2, 8, None)                      # buying one: a deal you could reach
# club deals: the store's own club (1) counts for a member; a credit-card club (2) or unknown clubs never do
promo(c, A, "p4", KINDER, "קינדר 2 ב 8", 2, 8, None, club=1, club_ids="2")
promo(c, A, "p5", KINDER, "מועדון קינדר ב 4", 1, 4, None, club=1, club_ids="1")
promo(c, A, "p6", KINDER, "קינדר ב 3", 1, 3, None, club=1, club_ids=None)
c.commit()

cart = [{"key": MILK, "qty": 1}, {"key": BUNDLE, "qty": 2}, {"key": ODD, "qty": 1}, {"key": KINDER, "qty": 1},
        {"key": TOMATO, "qty": 1.5}]
res = engine.compare(c, cart, {"club": False, "mode": "store"})
json.dumps(res)  # serialisable for the API

assert not {"comparable", "estimate_missing", "delivered_comparable"} & set(walk(res)), "estimates are gone"
assert all("median" not in i for i in res["items"])
assert res["n"] == 4

names = [ch["name"] for ch in res["chains"]]
# Shufersal has the whole cart; partial chains follow by products counted, then distance (none here: by name) — not
# by price, since their totals are over different products
assert names == ["Shufersal", "Carrefour", "Rami Levy", "Tiv Taam"], names
assert "Yohananof" not in names and [s["chain"] for s in res["stale"]] == ["Yohananof"], res["stale"]
by = {ch["name"]: ch["in_store"] for ch in res["chains"]}
line = lambda chain, key, r=None: next(l for l in {ch["name"]: ch["in_store"] for ch in (r or res)["chains"]}[chain]["lines"]
                                       if l["key"] == key)

shu = by["Shufersal"]
assert shu["complete"] and shu["covered"] == 4 and shu["missing"] == []
assert line("Shufersal", BUNDLE)["cost"] == 15.9 and line("Shufersal", BUNDLE)["promo"]["description"].startswith("מגוון")
assert shu["total"] == round(6.0 + 15.9 + 4.0 + 5.0, 2), shu["total"]       # the tomatoes are not in it
assert line("Shufersal", KINDER)["shelf_name"] == "קינדר בואנו דארק 43 גרם"
assert line("Shufersal", KINDER)["cost"] == 5.0 and line("Shufersal", KINDER)["deals_not_counted"] == []
assert line("Rami Levy", KINDER)["shelf_name"] is None                       # same name as the card

rl = by["Rami Levy"]
odd = line("Rami Levy", ODD)
assert odd["status"] == "unconfirmed" and odd["reason"] == "price" and odd["median"] == 4.25 and "cost" not in odd
assert rl["total"] == round(5.5 + 18.0 + 5.0, 2) and rl["covered"] == 3 and rl["missing"] == [ODD] and not rl["complete"]
k = line("Rami Levy", KINDER)
assert k["cost"] == 5.0 and k["deals_more"] == [{"description": "קינדר 2 ב 8", "units": 2, "total": 8.0}], k
assert k["deals_not_counted"] == []

cf = by["Carrefour"]
k = line("Carrefour", KINDER)
assert k["status"] == "unconfirmed" and k["reason"] == "name" and k["shelf_name"] == "פררו"
milk = line("Carrefour", MILK)
assert milk["status"] == "ok" and milk["cost"] == 5.8 and milk["promo"] is None and milk["deals_not_counted"] == ["שוקולד רושן"]
assert milk["deals_more"] == []
assert cf["total"] == round(5.8 + 19.0 + 4.5, 2)

tv = by["Tiv Taam"]
assert line("Tiv Taam", BUNDLE)["status"] == "missing" and tv["covered"] == 1

# produce is never compared: each store shows its own item, or same-name items it sells
t = line("Shufersal", TOMATO)
assert t["status"] == "not_compared" and t["own"] and t["price"] == 13.9 and "cost" not in t
t = line("Rami Levy", TOMATO)
assert not t["own"] and [s["key"] for s in t["similar"]] == [TOM_B] and t["similar"][0]["price"] == 16.9
assert line("Carrefour", TOMATO)["similar"] == []                            # its only match has a flagged price

items = {i["key"]: i for i in res["items"]}
assert items[MILK]["compared"] and not items[TOMATO]["compared"]
assert items[ODD]["max"] == 4.5, items[ODD]                                 # the ₪636.90 isn't in the range
assert items[KINDER]["stores"] == 2                                         # nor is Carrefour's other product
assert items[MILK]["min"] == 1.0 and items[MILK]["stores"] == 4              # the stale ₪4.00 isn't either

br = res["branches"]
assert [b["chain"] for b in br] == names and br[0]["complete"] and br[1]["missing"] == 1

# club member: only the store's own club deal (ClubId 1) counts; card-club and unknown-club deals are shown, not counted
r = engine.compare(c, cart, {"club": True})
k = line("Shufersal", KINDER, r)
assert k["cost"] == 4.0 and k["promo"]["description"] == "מועדון קינדר ב 4" and k["promo"]["club"], k
assert set(k["deals_not_counted"]) == {"קינדר 2 ב 8", "קינדר ב 3"}, k

# a store whose deal file is out of date: its deals are listed, not counted
c.execute("UPDATE stores SET promo_full_ts=? WHERE chain_id=?", (OLD, A))
c.execute("UPDATE promos SET ts=? WHERE chain_id=?", (OLD, A))
c.execute("UPDATE promo_items SET ts=? WHERE chain_id=?", (OLD, A))
c.commit()
r = engine.compare(c, cart, {})
b = line("Shufersal", BUNDLE, r)
assert b["cost"] == 19.8 and b["promo"] is None and b["deals_not_counted"] == ['מגוון ירק טרי מהשורש 2 יח ב15.90 ש"ח'], b
c.execute("UPDATE stores SET promo_full_ts=? WHERE chain_id=?", (PTS, A))
c.execute("UPDATE promos SET ts=? WHERE chain_id=?", (PTS, A))
c.execute("UPDATE promo_items SET ts=? WHERE chain_id=?", (PTS, A))
c.commit()

# a chain with no name of its own for a barcode: nothing to check it against, so it isn't counted
NONAME = ean("729000123456")
product(c, NONAME, "שוקו")
price(c, NONAME, A, 4.0)
price(c, NONAME, B, 3.0, named=False)
c.commit()
r = engine.compare(c, [{"key": NONAME, "qty": 1}], {})
assert line("Shufersal", NONAME, r)["status"] == "ok"
l = line("Rami Levy", NONAME, r)
assert l["status"] == "unconfirmed" and l["reason"] == "noname" and "cost" not in l
assert [ch["name"] for ch in r["chains"]] == ["Shufersal", "Rami Levy"] and r["items"][0]["stores"] == 1

# a price newer than the indexer's last check isn't counted yet; without the setting every price has been checked
engine.put_setting(c, "confirmed_through", 150)
c.execute("UPDATE prices SET ts=200 WHERE key=? AND chain_id=?", (MILK, C))
c.commit()
r = engine.compare(c, cart, {})
l = line("Carrefour", MILK, r)
assert l["status"] == "unconfirmed" and l["reason"] == "new" and "cost" not in l
assert {i["key"]: i for i in r["items"]}[MILK]["stores"] == 3
# ...unless it is in line with the product's typical price from that check: then it counts right away
mprice = c.execute("SELECT price FROM prices WHERE key=? AND chain_id=?", (MILK, C)).fetchone()[0]
c.execute("UPDATE products SET median=? WHERE key=?", (mprice * 1.2, MILK))
c.commit()
assert line("Carrefour", MILK, engine.compare(c, cart, {}))["status"] == "ok"
c.execute("UPDATE products SET median=? WHERE key=?", (mprice * 4, MILK))   # far from typical: waits for the check
c.commit()
assert line("Carrefour", MILK, engine.compare(c, cart, {}))["reason"] == "new"
c.execute("UPDATE products SET median=NULL WHERE key=?", (MILK,))
c.execute("DELETE FROM settings WHERE key='confirmed_through'")
c.commit()
assert line("Carrefour", MILK, engine.compare(c, cart, {}))["status"] == "ok"

# a bare code stored before the stricter barcode rules (fails the check digit, products.scope still 'global'):
# never compared; each chain shows its own item under that code when it names it the same, else same-name items
LETTUCE = "7290000000133"
assert not engine.comparable(LETTUCE) and not engine.comparable("7290000000916")   # a shared produce code
product(c, LETTUCE, "חסה סלנובה")
product(c, f"{A}:{LETTUCE}", "חסה סלנובה ארוזה", "chain", "חסה סלנובה ה")
product(c, f"{C}:{LETTUCE}", "פטריות שמפניון תפזור", "chain", "פטריות שמפניון תפזור")
product(c, f"{B}:777", "חסה סלנובה", "chain", "חסה סלנובה")
for key, cid, p in ((LETTUCE, A, 2.9), (LETTUCE, B, 9.9), (f"{A}:{LETTUCE}", A, 3.9), (f"{C}:{LETTUCE}", C, 12.0),
                    (f"{B}:777", B, 4.5)):
    price(c, key, cid, p)
c.commit()
r = engine.compare(c, [{"key": LETTUCE, "qty": 1}], {})
assert r["n"] == 0 and not r["items"][0]["compared"] and r["items"][0]["stores"] is None
l = line("Shufersal", LETTUCE, r)
assert l["status"] == "not_compared" and l["own"] and l["price"] == 3.9 and l["own_key"] == f"{A}:{LETTUCE}"
assert l["shelf_name"] == "חסה סלנובה ארוזה"
l = line("Rami Levy", LETTUCE, r)                       # its old bare-code row isn't its own item
assert not l["own"] and [s["key"] for s in l["similar"]] == [f"{B}:777"]
assert "Carrefour" not in [ch["name"] for ch in r["chains"]]  # mushrooms under the same code
# a cart of produce only: nothing to rank on price, and no chain has the "whole cart"
assert all(not ch["complete"] and not ch["in_store"]["complete"] for ch in r["chains"])
assert all(not b["complete"] for b in r["branches"])
res2 = engine.compare(c, [{"key": TOMATO, "qty": 1}], {})
assert res2["n"] == 0 and res2["chains"] and all(not ch["complete"] for ch in res2["chains"])

# each line is rounded before it is added, so a store's total is the sum of the lines shown
X = [ean(f"72900077700{i}") for i in range(4)]
for i, key in enumerate(X):
    product(c, key, f"מוצר {i}")
    price(c, key, E, 9.9)
for i, key in enumerate(X[:3]):
    promo(c, E, f"pct{i}", key, "15% הנחה", 1, None, 15)
promo(c, E, "lim", X[3], "מבצע ב 5 מוגבל 2", 1, 5, None, max_qty=3)
c.commit()
r = engine.compare(c, [{"key": k, "qty": 1} for k in X[:3]], {})
tt = r["chains"][0]["in_store"]
assert [l["cost"] for l in tt["lines"]] == [8.42] * 3 and tt["total"] == 25.26, tt["total"]

# a per-customer limit written in the text caps the deal too (the smaller of it and the file's MaxQty)
orig = getattr(deal_terms, "text_limit", None)
deal_terms.text_limit = lambda d: 2 if "מוגבל 2" in (d or "") else None
try:
    r = engine.compare(c, [{"key": X[3], "qty": 4}], {})
    assert r["chains"][0]["in_store"]["lines"][0]["cost"] == round(2 * 5 + 2 * 9.9, 2), r["chains"][0]["in_store"]
finally:
    if orig is None:
        del deal_terms.text_limit
    else:
        deal_terms.text_limit = orig

# one brand under several chain ids is one entry
assert chains.chain_name("0000000000000") == chains.chain_name("7290000000003") == "City Market"
assert chains.chain_name("7290058266241") == chains.chain_name("7290058288090") == "City Market (main company)"
assert chains.chain_name("7290058289400") == chains.chain_name("5144744100002") == "Mishnat Yosef"
CM = ean("729000555555")
product(c, CM, "במבה")
for cid, p in (("7290000000003", 5.0), ("0000000000000", 4.0)):
    store(c, cid, 1, 1)
    price(c, CM, cid, p)
c.commit()
r = engine.compare(c, [{"key": CM, "qty": 1}], {})
assert [(ch["name"], ch["score"]) for ch in r["chains"]] == [("City Market", 4.0)], r["chains"]

# line_cost keeps working for callers: confirmed deals only
assert engine.line_cost(9.9, 2, [{"description": "2 יח ב15.90", "min_qty": 2, "discounted_price": 15.9,
                                  "discount_rate": 10.92}])[0] == 15.9
assert engine.line_cost(9.9, 2, [{"description": "שוקולד", "min_qty": 2, "discounted_price": 15.9,
                                  "discount_rate": None}]) == (19.8, None)

# deals: only confirmed deals on trusted, named prices at stores with current price and deal files, against the
# typical (median) price; a store whose deal file is out of date has none
c.execute("UPDATE products SET median=9.5 WHERE key=?", (BUNDLE,))
c.execute("UPDATE products SET median=4.25 WHERE key=?", (ODD,))
c.execute("UPDATE products SET median=5.0 WHERE key=?", (KINDER,))
c.execute("UPDATE products SET median=12 WHERE key IN (?,?,?,?)", X)
c.commit()
deals.build(c)
rows = sorted(tuple(r) for r in c.execute("SELECT key, chain_id, unit_price, typical, min_qty FROM deals"))
want = sorted([(BUNDLE, A, 7.95, 9.5, 2), (KINDER, B, 4.0, 5.0, 2)] + [(k, E, 8.42, 12.0, 1) for k in X[:3]]
              + [(X[3], E, 5.0, 12.0, 1)])
assert rows == want, rows
c.execute("UPDATE stores SET promo_full_ts=? WHERE chain_id=?", (OLD, E))
c.execute("UPDATE promos SET ts=? WHERE chain_id=?", (OLD, E))
c.execute("UPDATE promo_items SET ts=? WHERE chain_id=?", (OLD, E))
c.execute("DELETE FROM item_names WHERE key=? AND chain_id=?", (KINDER, B))
c.commit()
deals.build(c)
rows = [tuple(r) for r in c.execute("SELECT key, chain_id FROM deals")]
assert rows == [(BUNDLE, A)], rows
c.close()

# ================================================================ several branches and web shops, with a home
g = connect(os.path.join(tmp, "geo.db"))
K = "7290058108879"  # King Store
P1, P2 = ean("729000888881"), ean("729000888882")
for key, name in ((P1, "פסטה"), (P2, "רסק")):
    product(g, key, name)
product(g, f"{A}:1475", "עגבניה", "chain", "עגבניה")
product(g, f"{B}:2034", "עגבניה", "chain", "עגבניה")
HOME = {"lat": 32.0, "lon": 34.8}
at = lambda km: (32.0 + km / 111.2, 34.8)
COORDS = {}
for cid, sid, age, typ, km_, name, ps in [
        (A, 1, 1, 1, 1.1, "Branch", {P1: 10, P2: 10, f"{A}:1475": 5.9}),
        (A, 52, 1, 3, 2.0, "Branch", {P1: 9, P2: 9, f"{A}:1475": 6.9}),  # a branch that also takes online orders
        (A, 50, 1, 2, 50.0, "שופרסל אונליין", {P1: 5, P2: 5}),           # far web shop, cheapest
        (A, 51, 1, 2, 3.0, "שופרסל אונליין", {P1: 11, P2: 11}),          # the nearest web shop
        (A, 53, 1, 2, None, "שופרסל אונליין", {P1: 4, P2: 4}),           # not located
        (B, 1, 1, 1, 0.5, "Branch", {P1: 8, P2: 8, f"{B}:2034": 4.9}),
        (B, 60, 72, 2, 1.0, "רמי לוי אונליין", {P1: 7, P2: 7}),          # its web shop's prices are old
        (D, 1, 1, 1, 4.0, "Branch", {P1: 9, P2: 9}),
        (D, 70, 1, 2, 1.0, "אונליין", {P1: 6, P2: 6}),                   # Yohananof: pickup only
        (E, 1, 1, 1, 50.0, "Branch", {P1: 1, P2: 1}),                    # cheapest, but beyond your distance
        (C, 1, 1, 1, 5.0, "Branch", {P1: 6}),                            # partial: ₪6
        (K, 1, 1, 1, 1.0, "Branch", {P2: 50})]:                          # partial: ₪50, but nearer
    store(g, cid, sid, age, typ, name)
    if km_ is not None:
        COORDS[(cid, sid)] = at(km_)
    for key, p in ps.items():
        price(g, key, cid, p, sid=sid)
g.commit()
engine.geo.store_coords = lambda con: COORDS
gcart = [{"key": P1, "qty": 1}, {"key": P2, "qty": 1}]
near = {"home": HOME, "max_km": 10}

r = engine.compare(g, gcart, {**near, "mode": "store"})
by = {ch["name"]: ch for ch in r["chains"]}
assert [ch["name"] for ch in r["chains"]] == ["Rami Levy", "Shufersal", "Yohananof", "King Store", "Carrefour"], \
    [ch["name"] for ch in r["chains"]]
assert "Tiv Taam" not in by and all(b["chain"] != "Tiv Taam" for b in r["branches"])  # 50 km away: not priced
s = by["Shufersal"]
assert s["in_store"]["store_id"] == 52 and s["online"]["store_id"] == 51, (s["in_store"]["store_id"], s["online"]["store_id"])
assert s["online"]["total"] == 22 and s["online"]["price_vs_store"] == 4.0 and s["online"]["delivered"] == round(22 + 35.9, 2)
assert by["Rami Levy"]["online"] is None and by["Rami Levy"]["online_stale"]["store_id"] == 60
assert by["Yohananof"]["online"]["fee"] is None and by["Yohananof"]["online"]["fee_note"] == "No home delivery"
assert r["stale"] == []

# delivery: nothing is ranked until the user confirms the chain delivers to them (the files don't say where)
r = engine.compare(g, gcart, {**near, "mode": "delivery"})
assert r["chains"] == [] and [a["chain"] for a in r["no_area"]] == ["Shufersal"], (r["chains"], r["no_area"])
assert r["no_area"][0]["store_id"] == 51 and r["no_area"][0]["total"] == 22
engine.put_setting(g, "delivery", {A: {"delivers_here": True}})
r = engine.compare(g, gcart, {**near, "mode": "delivery"})
assert [ch["name"] for ch in r["chains"]] == ["Shufersal"], [ch["name"] for ch in r["chains"]]  # nor Yohananof
assert r["chains"][0]["online"]["area_confirmed"] is True and r["no_area"] == []
assert [(s["chain"], s["online"], s["store_id"]) for s in r["stale"]] == [("Rami Levy", True, 60)], r["stale"]

# without a home, a chain with several web shops has none that can be said to serve you: never the cheapest of them
r = engine.compare(g, gcart, {"mode": "delivery"})
assert "Shufersal" not in {ch["name"] for ch in r["chains"]}, r["chains"]
engine.put_setting(g, "delivery", {})

# produce only: no chain is complete and they are ordered by distance
r = engine.compare(g, [{"key": f"{A}:1475", "qty": 1}], near)
assert [(ch["name"], ch["complete"]) for ch in r["chains"]] == [("Rami Levy", False), ("Shufersal", False)], r["chains"]
assert [(b["chain"], b["store_id"]) for b in r["branches"]] == [("Rami Levy", 1), ("Shufersal", 1), ("Shufersal", 52)]
g.close()

shutil.rmtree(tmp)
print("ok")
