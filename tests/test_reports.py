"""Run: .venv/bin/python tests/test_reports.py"""
import math
import os
import sqlite3
import sys
import tempfile

TMP = tempfile.mkdtemp()
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")  # keep every import off the live DB
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db as dbmod  # noqa: E402
from app import deals as dealsmod  # noqa: E402
from app import reports  # noqa: E402


def ean(body):
    """body (12 digits) + its GS1 check digit."""
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    return body + str((10 - s % 10) % 10)


MILK, BREAD, SOAP, ODD = ean("729010000001"), ean("729010000002"), ean("729010000003"), ean("729010000004")
VIC1, VIC2 = "7290696200003", "7290058103393"  # Victory publishes under two chain ids: one chain in reports

c = sqlite3.connect(":memory:")
c.executescript(dbmod.SCHEMA.replace("PRAGMA journal_mode=WAL;", ""))
c.executescript(dealsmod.SCHEMA)
c.execute("ALTER TABLE products ADD COLUMN median REAL")
c.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
c.execute("CREATE TABLE towns(code INTEGER PRIMARY KEY, name TEXT)")
c.executemany("INSERT INTO chains VALUES(?, ?, '')", [("A", "Alef"), ("B", "Bet"), ("C", "Gimel"), (VIC1, ""), (VIC2, "")])
c.executemany("INSERT INTO products(key, code, name, scope, store_count, chain_count, median) VALUES(?,?,?,?,?,?,?)", [
    (MILK, MILK, "חלב 3% 1 ליטר", "global", 9, 4, 6.0), (BREAD, BREAD, "לחם אחיד", "global", 9, 4, 8.0),
    (SOAP, SOAP, "סבון ידיים", "global", 9, 4, 10.0), (ODD, ODD, "מוצר", "global", 2, 1, 5.0),
    ("A:123", "123", "עגבניה", "chain", 3, 1, None)])
c.executemany("INSERT INTO products_fts(key, name, manufacturer, code) VALUES(?,?,'',?)",
              [(MILK, "חלב 3% 1 ליטר", MILK), (BREAD, "לחם אחיד", BREAD), (SOAP, "סבון ידיים", SOAP)])
c.executemany("INSERT INTO settings VALUES(?, ?)", [("history_since", "20261101"), ("confirmed_through", "202611100000")])

TODAY = 20261110
reports.today = lambda: TODAY
reports.MIN_PRODUCTS = reports.MIN_CAT_PRODUCTS = 1

# current prices (each chain's cheapest confirmed shelf price): A is cheap, C is dear
last = [(MILK, "A", 5.0, 5.5), (MILK, "B", 6.0, 6.0), (MILK, "C", 7.5, 8.0), (MILK, VIC1, 6.5, 6.5), (MILK, VIC2, 6.2, 6.2),
        (BREAD, "A", 7.0, 9.0), (BREAD, "B", 8.0, 8.0), (BREAD, "C", 9.0, 9.0), (BREAD, VIC1, 8.0, 8.0),
        (SOAP, "A", 9.0, 9.0), (SOAP, "B", 10.0, 10.0), (SOAP, "C", 12.0, 12.0),
        (ODD, "A", 5.0, 5.0), ("A:123", "A", 3.0, 3.0)]
# history: milk at A was 4.0 until the 5th, then 5.0; bread at B started on the 8th; everything else since the 1st
hist = [(MILK, "A", 20261101, 4.0, 4.0, 3), (MILK, "A", 20261105, 5.0, 5.5, 3), (BREAD, "B", 20261108, 8.0, 8.0, 3)]
for k, cid, mn, mx in last:
    day = max([h[2] for h in hist if h[0] == k and h[1] == cid] or [20261101])
    c.execute("INSERT INTO price_last VALUES(?,?,?,?,?)", (k, cid, mn, mx, day))
    if not any(h[0] == k and h[1] == cid for h in hist):
        hist.append((k, cid, 20261101, mn, mx, 3))
c.executemany("INSERT INTO price_history VALUES(?,?,?,?,?,?)", hist)
c.executemany("INSERT INTO deals VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
    (MILK, "A", 1, 5.0, 4.0, 6.0, 0.333, 2, "2 ב 8", None, None), (MILK, "A", 2, 5.0, 4.5, 6.0, 0.25, 2, "2 ב 9", None, None),
    (SOAP, "C", 1, 12.0, 8.0, 10.0, 0.2, 1, "8 ש״ח", None, None)])

# ---------------------------------------------------------------- chain price index
rep = reports.run(c, "price-level", {"min_chains": "3"})
idx = {r["chain"]: r for r in rep["rows"]}
assert set(idx) == {"Alef", "Bet", "Gimel", "Victory"}, idx  # Victory's two ids are one chain; ODD (1 chain) and produce left out
# milk: Alef 5, Bet 6, Gimel 7.5, Victory min(6.5, 6.2) = 6.2 -> median 6.1; bread median 8; soap (3 chains) median 10
want = 100 * math.exp((math.log(5 / 6.1) + math.log(7 / 8) + math.log(9 / 10)) / 3)
assert abs(idx["Alef"]["index"] - round(want, 1)) < 1e-9, (idx["Alef"], want)
assert [r["chain"] for r in rep["rows"]][0] == "Alef" and rep["rows"][-1]["chain"] == "Gimel"
assert idx["Alef"]["cheapest"] == 1.0 and idx["Victory"]["products"] == 2
assert idx["Alef"]["dairy"] is not None and idx["Victory"]["cleaning"] is None  # Victory sells no soap

# ---------------------------------------------------------------- basket over time (own basket)
rep = reports.run(c, "basket", {"codes": f"{MILK}, {BREAD}", "period": "30"})
assert [x["key"] for x in rep["columns"][1:]] == ["Alef", "Bet", "Victory", "Gimel"], rep["columns"]  # cheapest first
day = {r["day"]: r for r in rep["rows"]}
assert rep["rows"][0]["day"] == 20261101 and rep["rows"][-1]["day"] == TODAY  # history starts on the 1st
assert day[20261104]["Alef"] == 4.0 + 7.0 and day[20261105]["Alef"] == 5.0 + 7.0
assert day[20261107]["Bet"] is None and day[20261108]["Bet"] == 14.0  # no bread price at Bet before the 8th
assert day[TODAY]["Victory"] == 6.2 + 8.0
assert [i["code"] for i in rep["parts"]["items"]["rows"]] == [MILK, BREAD]

# ---------------------------------------------------------------- price changes over 7 days (since the 3rd)
rep = reports.run(c, "changes", {"period": "7"})
ch = {r["chain"]: r for r in rep["rows"]}
assert ch["Alef"]["up"] == 1 and ch["Alef"]["tracked"] == 4, ch["Alef"]  # milk 4 -> 5; bread, soap, odd unchanged
assert abs(ch["Alef"]["avg"] - (math.exp(math.log(5 / 4) / 4) - 1)) < 1e-9
assert ch["Bet"]["tracked"] == 2, ch["Bet"]  # Bet's bread wasn't sold 7 days ago: not tracked
moves = rep["parts"]["moves"]["rows"]
assert len(moves) == 1 and moves[0]["code"] == MILK and abs(moves[0]["change"] - 0.25) < 1e-9

# ---------------------------------------------------------------- deals
rep = reports.run(c, "deals", {})
d = {r["chain"]: r for r in rep["rows"]}
assert d["Alef"]["on_deal"] == 1 and d["Alef"]["branches"] == 2 and abs(d["Alef"]["median"] - 0.333) < 1e-9
assert abs(d["Alef"]["share"] - 1 / 4) < 1e-9  # milk, of the 4 barcode products Alef sells (ODD included, produce not)
assert rep["parts"]["top"]["rows"][0]["deal"] == "2 ב 8"

# ---------------------------------------------------------------- price gaps across chains
rep = reports.run(c, "gaps", {"min_chains": "3"})
g = {r["code"]: r for r in rep["rows"]}
assert g[MILK]["low_chain"] == "Alef" and g[MILK]["high_chain"] == "Gimel" and abs(g[MILK]["gap"] - 0.5) < 1e-9
assert ODD not in g
rep = reports.run(c, "gaps", {"min_chains": "3", "cat": "dairy"})
assert [r["code"] for r in rep["rows"]] == [MILK]

# ---------------------------------------------------------------- product tracker
rep = reports.run(c, "products", {"codes": f"0{SOAP}\n{MILK} 123"})  # a leading zero, and a code that isn't a barcode
assert [r["code"] for r in rep["rows"]] == [SOAP, MILK], rep["rows"]
milk = rep["rows"][1]
assert milk["low"] == 5.0 and milk["high"] == 7.5 and milk["Victory"] == 6.2 and milk["deals"] == 1
assert [r["code"] for r in reports.run(c, "products", {"q": "חלב"})["rows"]] == [MILK]
assert reports.run(c, "products", {"prefix": "7290100"})["rows"]

# ---------------------------------------------------------------- CSV and holdings
text = reports.csv_text(reports.run(c, "changes", {"period": "7"}), "moves")
assert text.startswith("﻿Product,Barcode,Chain,Was (₪),Now (₪),Change (%)"), text[:80]
assert ",25.0" in text and "4.00" in text
h = reports.holdings(c)
assert h["chains"] == 4 and h["history_since"] == 20261101 and h["deals"] == 2, h
try:
    reports.run(c, "nope", {})
    raise AssertionError("an unknown report must raise KeyError")
except KeyError:
    pass
print("ok")
