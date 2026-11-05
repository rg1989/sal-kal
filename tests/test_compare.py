"""Run: .venv/bin/python tests/test_compare.py
The comparison counts confirmed lines only: no stale stores, flagged prices or names, produce or unconfirmed deals."""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmp = tempfile.mkdtemp(prefix="sal-compare-")
os.environ["BASKET_USER_DB"] = os.path.join(tmp, "user.db")
from app import db, deals, engine  # noqa: E402

c = db.connect(os.path.join(tmp, "basket.db"))
ts = lambda hours_ago: int(time.strftime("%Y%m%d%H%M", time.localtime(time.time() - hours_ago * 3600)))
A, B, C, D, E = "7290027600007", "7290058140886", "7290055700007", "7290803800003", "7290873255550"
for cid, age in ((A, 1), (B, 2), (C, 1), (D, 72), (E, 1)):  # Yohananof (D) hasn't published for 3 days
    c.execute("INSERT INTO stores(chain_id, store_id, name, address, city, store_type, sub_chain, price_full_ts,"
              " promo_full_ts, last_price_ts) VALUES(?,1,'Branch','','',1,'',100,100,?)", (cid, ts(age)))
    c.execute("INSERT INTO chains(chain_id, name_he) VALUES(?, '')", (cid,))

MILK, BUNDLE, ODD, KINDER = "7290000042435", "7290000066332", "72917367", "80867067"
TOMATO, TOM_B, TOM_C = f"{A}:1475", f"{B}:2034", f"{C}:7290101501237"
prods = [(MILK, "חלב 1%", "global", None), (BUNDLE, "אפרופו", "global", None), (ODD, "טעמי", "global", None),
         (KINDER, "קינדר בואנו", "global", None), (TOMATO, "עגבניה מגי", "chain", "עגבניה מגי"),
         (TOM_B, "עגבניה מגי", "chain", "עגבניה מגי"), (TOM_C, "עגבניה מגי ארוזה", "chain", "עגבניה מגי")]
for key, name, scope, norm in prods:
    c.execute("INSERT INTO products(key, code, name, manufacturer, quantity, unit, is_weighted, scope, store_count,"
              " chain_count, min_price, max_price, norm) VALUES(?,?,?,'','','',?,?,1,3,1,10,?)",
              (key, key.split(":")[-1], name, 1 if scope == "chain" else 0, scope, norm))
prices = {A: {MILK: 6.0, BUNDLE: 9.9, ODD: 4.0, KINDER: 5.0, TOMATO: 13.9},
          B: {MILK: 5.5, BUNDLE: 9.0, ODD: 636.9, KINDER: 5.0, TOM_B: 16.9},
          C: {MILK: 5.8, BUNDLE: 9.5, ODD: 4.5, KINDER: 5.2, TOM_C: 15.9},
          D: {MILK: 4.0, BUNDLE: 8.0, ODD: 3.0, KINDER: 4.0},
          E: {MILK: 1.0}}
for cid, ps in prices.items():
    for key, p in ps.items():
        c.execute("INSERT INTO prices VALUES(?,?,1,?,100)", (key, cid, p))
c.execute("INSERT INTO price_flags VALUES(?,?,1,4.25)", (ODD, B))         # ₪636.90 where others charge ~₪4
c.execute("INSERT INTO price_flags VALUES(?,?,1,14.9)", (TOM_C, C))
c.execute("INSERT INTO name_flags VALUES(?,?,'פררו')", (KINDER, C))       # Carrefour calls this barcode something else
c.execute("INSERT INTO item_names VALUES(?,?,'קינדר בואנו דארק 43 גרם')", (KINDER, A))
c.execute("INSERT INTO item_names VALUES(?,?,'קינדר בואנו')", (KINDER, B))
promo = ("INSERT INTO promos(chain_id, store_id, promo_id, description, start_ts, end_ts, min_qty, discounted_price,"
         " discount_rate, club_only, ts, max_qty, remarks) VALUES(?,1,?,?,NULL,NULL,?,?,?,0,100,NULL,NULL)")
c.execute(promo, (A, "p1", 'מגוון ירק טרי מהשורש 2 יח ב15.90 ש"ח', 2, 15.9, 10.92))
c.execute(promo, (C, "p2", "שוקולד רושן", 3, 20, None))                  # terms not stated: not counted
c.execute(promo, (B, "p3", "טעמי 2ב5", 2, 5, None))                      # on a flagged price: never a deal
c.execute("INSERT INTO promo_items VALUES(?,?,1,'p1',100)", (BUNDLE, A))
c.execute("INSERT INTO promo_items VALUES(?,?,1,'p2',100)", (MILK, C))
c.execute("INSERT INTO promo_items VALUES(?,?,1,'p3',100)", (ODD, B))
c.commit()

cart = [{"key": MILK, "qty": 1}, {"key": BUNDLE, "qty": 2}, {"key": ODD, "qty": 1}, {"key": KINDER, "qty": 1},
        {"key": TOMATO, "qty": 1.5}]
res = engine.compare(c, cart, {"club": False, "mode": "store"})
json.dumps(res)  # serialisable for the API


def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


assert not {"comparable", "estimate_missing", "delivered_comparable"} & set(walk(res)), "estimates are gone"
assert all("median" not in i for i in res["items"])
assert res["n"] == 4

names = [ch["name"] for ch in res["chains"]]
# Shufersal has the whole cart; partial chains follow by products counted, then price — even Tiv Taam's ₪1 milk
assert names == ["Shufersal", "Rami Levy", "Carrefour", "Tiv Taam"], names
assert "Yohananof" not in names and [s["chain"] for s in res["stale"]] == ["Yohananof"], res["stale"]
by = {ch["name"]: ch["in_store"] for ch in res["chains"]}
line = lambda chain, key: next(l for l in by[chain]["lines"] if l["key"] == key)

shu = by["Shufersal"]
assert shu["complete"] and shu["covered"] == 4 and shu["missing"] == []
assert line("Shufersal", BUNDLE)["cost"] == 15.9 and line("Shufersal", BUNDLE)["promo"]["description"].startswith("מגוון")
assert shu["total"] == round(6.0 + 15.9 + 4.0 + 5.0, 2), shu["total"]       # the tomatoes are not in it
assert line("Shufersal", KINDER)["shelf_name"] == "קינדר בואנו דארק 43 גרם"
assert line("Rami Levy", KINDER)["shelf_name"] is None                       # same name as the card

rl = by["Rami Levy"]
odd = line("Rami Levy", ODD)
assert odd["status"] == "unconfirmed" and odd["reason"] == "price" and odd["median"] == 4.25 and "cost" not in odd
assert rl["total"] == round(5.5 + 18.0 + 5.0, 2) and rl["covered"] == 3 and rl["missing"] == [ODD] and not rl["complete"]

cf = by["Carrefour"]
k = line("Carrefour", KINDER)
assert k["status"] == "unconfirmed" and k["reason"] == "name" and k["shelf_name"] == "פררו"
milk = line("Carrefour", MILK)
assert milk["status"] == "ok" and milk["cost"] == 5.8 and milk["promo"] is None and milk["deals_not_counted"] == ["שוקולד רושן"]
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

# a cart of produce only: nothing to rank on price
res2 = engine.compare(c, [{"key": TOMATO, "qty": 1}], {})
assert res2["n"] == 0 and all(ch["complete"] and ch["in_store"]["total"] == 0 for ch in res2["chains"])

# line_cost keeps working for callers: confirmed deals only
assert engine.line_cost(9.9, 2, [{"description": "2 יח ב15.90", "min_qty": 2, "discounted_price": 15.9,
                                  "discount_rate": 10.92}])[0] == 15.9
assert engine.line_cost(9.9, 2, [{"description": "שוקולד", "min_qty": 2, "discounted_price": 15.9,
                                  "discount_rate": None}]) == (19.8, None)

# deals: only confirmed deals on trusted prices at fresh stores, against the median price
c.execute("UPDATE products SET median=9.5 WHERE key=?", (BUNDLE,))
c.execute("UPDATE products SET median=4.25 WHERE key=?", (ODD,))
c.commit()
deals.build(c)
rows = c.execute("SELECT key, chain_id, unit_price, typical, min_qty FROM deals").fetchall()
assert [tuple(r) for r in rows] == [(BUNDLE, A, 7.95, 9.5, 2)], [tuple(r) for r in rows]

c.close()
shutil.rmtree(tmp)
print("ok")
