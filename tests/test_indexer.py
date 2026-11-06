"""Run: .venv/bin/python tests/test_indexer.py"""
import logging
import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.TemporaryDirectory()
os.environ["BASKET_USER_DB"] = os.path.join(tmp.name, "user.db")  # never the household's own user.db

from app import db as dbmod  # noqa: E402
from app import indexer as ix  # noqa: E402
from app import names as namesmod  # noqa: E402
from app import trust  # noqa: E402

logging.getLogger("indexer").setLevel(logging.CRITICAL)  # the failing-confirm case logs a traceback on purpose
# the run fixtures below are dated 2026-11-05: judge freshness as of that day, or they go stale and the test fails
_cutoff = trust.fresh_cutoff
trust.fresh_cutoff = lambda now=None: _cutoff(now or time.mktime((2026, 11, 5, 12, 0, 0, 0, 0, -1)))

NOW = int(time.strftime("%Y%m%d%H%M"))
FAR = 299912312359  # a store whose prices never go stale


def ean(body):
    """body + its GS1 check digit."""
    s = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    code = body + str((10 - s % 10) % 10)
    assert trust.gs1_ok(code)
    return code


def fresh_db(name):
    path = os.path.join(tmp.name, name, "basket.db")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.environ["BASKET_USER_DB"] = os.path.join(os.path.dirname(path), "user.db")
    c = dbmod.connect(path)
    c.row_factory = None  # plain tuples to compare against
    return c, path


def store(c, chain, sid, folder=None):
    c.execute("INSERT OR IGNORE INTO stores(chain_id, store_id, price_full_ts, last_price_ts) VALUES(?,?,100,?)",
              (chain, sid, FAR))
    c.execute("INSERT INTO chains(chain_id, folder) VALUES(?,?) ON CONFLICT(chain_id) DO UPDATE SET folder=excluded.folder",
              (chain, folder or chain))


def price(c, key, chain, sid, p, name=None, folder=None, ts=100):
    store(c, chain, sid, folder)
    c.execute("INSERT OR REPLACE INTO prices VALUES(?,?,?,?,?)", (key, chain, sid, p, ts))
    c.execute("INSERT OR IGNORE INTO products(key, code, name, scope) VALUES(?,?,?,?)",
              (key, key.split(":")[-1], name or "", "chain" if ":" in key else "global"))
    if name and ":" not in key:
        c.execute("INSERT OR REPLACE INTO item_names VALUES(?,?,?,?)", (key, chain, name, NOW))


# ---------------------------------------------------------------- barcodes: shared produce codes are chain items
assert not ix.is_global_code("7290000000411")              # garlic at 16 chains, a different pack at each
assert not ix.is_global_code("7290000000091")              # coriander / a delivery fee / a drone
assert ix.product_key("7290027600007", "7290000000411") == ("7290027600007:7290000000411", "chain")
assert not ix.is_global_code("07290000000411")             # with a leading zero too
assert ix.is_global_code(ean("729000006618"))              # Osem's prefix 7290000060-: real barcodes
assert ix.is_global_code(ean("729000013600"))              # Tempo 7290000136-
assert ix.is_global_code("7290000066189") and ix.comparable("7290000066189")
assert not ix.comparable("7290027600007:7290000066189") and not ix.comparable("7290000000411")

# ---------------------------------------------------------------- item_names: old one-name table is migrated
p = os.path.join(tmp.name, "old", "basket.db")
os.makedirs(os.path.dirname(p))
o = sqlite3.connect(p)
o.executescript("""CREATE TABLE item_names(key TEXT, chain_id TEXT, name TEXT, PRIMARY KEY(key, chain_id)) WITHOUT ROWID;
                   CREATE TABLE stores(chain_id TEXT, store_id INTEGER, name TEXT, address TEXT, city TEXT,
                     store_type INTEGER, sub_chain TEXT, price_full_ts INTEGER DEFAULT 0, promo_full_ts INTEGER DEFAULT 0,
                     last_price_ts INTEGER DEFAULT 0, PRIMARY KEY(chain_id, store_id));
                   INSERT INTO stores(chain_id, store_id, price_full_ts) VALUES('A', 1, 202611050300), ('A', 2, 202611041200);
                   INSERT INTO item_names VALUES('7290000066189', 'A', 'ביסלי גריל'), ('7290000066189', 'B', 'ביסלי');""")
o.commit()
o.close()
os.environ["BASKET_USER_DB"] = os.path.join(tmp.name, "old", "user.db")
c = dbmod.connect(p)
c.row_factory = None
assert {r[1] for r in c.execute("PRAGMA table_info(item_names)")} == {"key", "chain_id", "name", "ts"}
assert c.execute("SELECT chain_id, name, ts FROM item_names ORDER BY chain_id").fetchall() == [
    ("A", "ביסלי גריל", 202611050300), ("B", "ביסלי", 0)]   # the chain's newest PriceFull time; unknown chain: 0
c.execute("INSERT INTO item_names VALUES('7290000066189', 'A', 'ביסלי גריל 70 גרם', 202611050400)")  # a 2nd name
assert "club_ids" in {r[1] for r in c.execute("PRAGMA table_info(promos)")}
c.close()
dbmod.connect(p).close()  # connecting again is a no-op

# ---------------------------------------------------------------- price files: every name, newest file time
c, _ = fresh_db("names")
d = os.path.join(tmp.name, "names", "dumps", "X")
os.makedirs(d)
BAR = ean("729000123456")


def price_file(folder, kind, chain, sid, ts, items):
    """items: [(code, name, price)]"""
    body = "".join(f"<Item><ItemCode>{code}</ItemCode><ItemName>{n}</ItemName><ItemPrice>{p}</ItemPrice>"
                   f"<bIsWeighted>0</bIsWeighted></Item>" for code, n, p in items)
    path = os.path.join(folder, f"{kind}{chain}-{sid:03d}-{ts}.xml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"<Root><ChainId>{chain}</ChainId><StoreId>{sid}</StoreId><Items>{body}</Items></Root>")
    return path


seen = {}
for sid, ts, name in ((1, 202611050100, "שוקו תנובה 1 ליטר"), (2, 202611050110, "שוקו תנובה 1 ליטר"),
                      (3, 202611050120, "שוקו וניל"), (1, 202611050900, "שוקו תנובה 1 ליטר")):
    f = price_file(d, "PriceFull", "7290000000101", sid, ts, [(BAR, name, 6.9)])
    ix.index_prices(c, f, {"chain": "7290000000101", "store": sid, "ts": ts}, {"7290000000101": set()}, seen)
assert c.execute("SELECT name, ts FROM item_names WHERE key=? ORDER BY name", (BAR,)).fetchall() == [
    ("שוקו וניל", 202611050120), ("שוקו תנובה 1 ליטר", 202611050900)], "both names; the newest file's time"
c.close()

# ---------------------------------------------------------------- name flags first; medians leave them out
c, _ = fresh_db("median")
K = ean("729000555555")
for sid in range(1, 6):
    price(c, K, "A", sid, 29.9, "דמי משלוח אתר אינטרנט")   # A sells a delivery fee under this barcode (5 stores)
price(c, K, "B", 1, 3.9, "כוסברה")
price(c, K, "C", 1, 4.5, "כוסברה טריה")
price(c, K, "D", 1, 4.9, "כוסברה ארוזה")
c.execute("UPDATE products SET name='כוסברה' WHERE key=?", (K,))
c.commit()
assert ix.confirm(c)
assert c.execute("SELECT chain_id FROM name_flags").fetchall() == [("A",)]
assert c.execute("SELECT count(*) FROM price_flags").fetchone() == (0,), "B, C, D's coriander isn't an outlier"
assert c.execute("SELECT median, store_count, chain_count FROM products WHERE key=?", (K,)).fetchone() == (4.5, 3, 3)

# a barcode price with no (recent) name from its chain isn't confirmed
price(c, K, "E", 1, 4.2)                                          # no item_names row for E
price(c, K, "F", 1, 4.4)
c.execute("INSERT INTO item_names VALUES(?, 'F', 'כוסברה', ?)", (K, ix.names_cutoff() - 10000))  # 4+ days old
c.commit()
assert ix.confirm(c)
assert c.execute("SELECT store_count, chain_count FROM products WHERE key=?", (K,)).fetchone() == (3, 3)
assert {r[0] for r in c.execute("SELECT chain_id FROM price_history WHERE key=?", (K,))} == {"B", "C", "D"}
c.close()

# ---------------------------------------------------------------- one median per retailer (the whisky 12-pack)
c, _ = fresh_db("whisky")
W = ean("500026702557")
for sid in range(1, 7):
    price(c, W, "7290661400001", sid, 119.0, 'מארז וויסקי ג וני ווקר בלאק 12* 50מ"ל', "MahsaniAShukNewSource")
price(c, W, "0000000000000", 29, 15.0, "וויסקי בלאק לייבל 50 מל", "CityMarketShops")   # City Market, 2 chain ids
price(c, W, "7290000000003", 1, 15.0, "וויסקי בלאק לייבל 50 מל", "CityMarketShops")
price(c, W, "7290000000003", 2, 19.9, "וויסקי בלאק לייבל 50 מל", "CityMarketShops")
c.commit()
ix.build_name_flags(c)
ix.build_price_flags(c, trust.fresh_cutoff())
flags = {(r[0], r[1]): r[2] for r in c.execute("SELECT chain_id, store_id, median FROM price_flags")}
assert len(flags) == 9, "two retailers 8x apart: no consensus, nothing counts (not the 6-branch chain's 119)"
assert flags[("7290661400001", 1)] == 15.0 and flags[("7290000000003", 1)] == 119.0   # shown: the other's price
assert c.execute("SELECT median FROM products WHERE key=?", (W,)).fetchone() == (None,)
price(c, W, "7290058108879", 1, 16.9, "וויסקי ג'וני ווקר בלאק 50 מל", "KingStore")    # a third retailer agrees
c.commit()
ix.build_price_flags(c, trust.fresh_cutoff())
assert {r[0] for r in c.execute("SELECT DISTINCT chain_id FROM price_flags")} == {"7290661400001"}
assert c.execute("SELECT median FROM products WHERE key=?", (W,)).fetchone() == (15.95,)   # City Market 15, King 16.9
c.close()

R = trust.OUTLIER_MIN_STORES
assert ix.judge_prices([("A", "A", 1, 10.0), ("B", "B", 1, 12.0)]) == (11.0, [])
assert ix.judge_prices([("A", "A", s, 10.0) for s in range(R)] + [("A", "A", 9, 99.0)])[1] == [("A", 9, 10.0)]
# too few stores to tell which is right: a normal spread stands, 3x apart (₪29.90 and ₪370) none counts
assert ix.judge_prices([("A", "A", 1, 10.0), ("A", "A", 2, 25.0)]) == (17.5, [])
assert ix.judge_prices([("A", "A", 1, 10.0), ("A", "A", 2, 99.0)]) == (54.5, [("A", 1, 54.5), ("A", 2, 54.5)])
# ₪0.10 or less is a placeholder: flagged, and left out of the typical price
assert ix.judge_prices([("A", "A", 1, 0.01), ("A", "A", 2, 12.9), ("A", "A", 3, 12.9)]) == (12.9, [("A", 1, 0.01)])
# a store far off the consensus is flagged even at a chain that agrees otherwise
assert ix.judge_prices([("A", "A", 1, 32.0), ("A", "A", 2, 10.0), ("A", "A", 3, 33.0), ("B", "B", 1, 30.0)])[1] == [
    ("A", 2, 31.0)]
# chain-scoped codes are judged within each retailer only
assert ix.judge_prices([("A", "A", 1, 10.0), ("B", "B", 1, 99.0)], compared=False) == (None, [])

# ---------------------------------------------------------------- voter map and generic words reach odd_names
calls = []
real_odd, real_gw = namesmod.odd_names, getattr(namesmod, "generic_words", None)


def fake_odd(names, card_name="", generic=frozenset(), voter=None):
    calls.append((names, generic, voter))
    return set(), None


namesmod.odd_names, namesmod.generic_words = fake_odd, lambda rows, min_keys=150: frozenset({"וויסקי"})
ix._ODD_NEW = None
c, _ = fresh_db("whisky")
ix.build_name_flags(c)
names_arg, generic_arg, voter_arg = calls[0]
assert names_arg["7290000000003"] == ["וויסקי בלאק לייבל 50 מל"] and isinstance(names_arg["7290661400001"], list)
assert voter_arg["0000000000000"] == voter_arg["7290000000003"] == "CityMarketShops"
assert generic_arg == frozenset({"וויסקי"})


def old_odd(names, card_name=""):  # names.py before the contract: one name per chain, no keywords
    assert all(isinstance(v, str) for v in names.values())
    return {"7290661400001"}, None


namesmod.odd_names = old_odd
ix._ODD_NEW = None
ix.build_name_flags(c)
assert c.execute("SELECT chain_id, name FROM name_flags").fetchall() == [
    ("7290661400001", 'מארז וויסקי ג וני ווקר בלאק 12* 50מ"ל')]
namesmod.odd_names = real_odd
if real_gw:
    namesmod.generic_words = real_gw
else:
    del namesmod.generic_words
ix._ODD_NEW = None
c.close()

# ---------------------------------------------------------------- rekey: old bare keys become chain items
c, _ = fresh_db("rekey")
G = "7290000000411"
price(c, G, "A", 1, 2.9, "שום יבש", ts=100)
price(c, G, "A", 2, 3.5, "שום יבש", ts=100)
price(c, G, "B", 1, 4.9, "שום 4 ראשים", ts=100)
c.execute("UPDATE products SET quantity='190', unit='גרם' WHERE key=?", (G,))
c.execute("INSERT INTO prices VALUES('A:7290000000411', 'A', 2, 3.2, 200)")   # a newer file already re-keyed it
c.execute("INSERT INTO products(key, code, name, scope) VALUES('A:7290000000411', ?, 'שום יבש ארוז', 'chain')", (G,))
c.execute("INSERT INTO promo_items VALUES(?, 'A', 1, 'p1', 100)", (G,))
c.execute("INSERT INTO price_history VALUES(?, 'A', 20261105, 2.9, 3.5, 2)", (G,))
c.execute("INSERT INTO price_last VALUES(?, 'A', 2.9, 3.5, 20261105)", (G,))
c.execute("INSERT INTO products_fts(key, name, manufacturer, code) VALUES(?, 'שום יבש', '', ?)", (G, G))
c.execute("INSERT INTO u.carts(id, name) VALUES(1, 'בית')")
c.execute("INSERT INTO u.cart_items VALUES(1, ?, 1, 'שום', 0)", (G,))
c.commit()
assert ix.rekey(c) == 1
q = lambda sql, *a: c.execute(sql, a).fetchall()  # noqa: E731
assert q("SELECT count(*) FROM prices WHERE key=?", G) == [(0,)]
assert q("SELECT key, store_id, price FROM prices ORDER BY key, store_id") == [
    ("A:7290000000411", 1, 2.9), ("A:7290000000411", 2, 3.2), ("B:7290000000411", 1, 4.9)]
assert q("SELECT key, name, scope, quantity FROM products WHERE key LIKE '%:%' ORDER BY key") == [
    ("A:7290000000411", "שום יבש ארוז", "chain", None), ("B:7290000000411", "שום 4 ראשים", "chain", "190")]
assert q("SELECT scope, store_count FROM products WHERE key=?", G) == [("chain", 0)]
assert q("SELECT key FROM promo_items") == [("A:7290000000411",)]
for t in ("item_names", "price_history", "price_last"):
    assert q(f"SELECT count(*) FROM {t} WHERE key=?", G) == [(0,)], t
assert sorted(r[0] for r in q("SELECT key FROM products_fts")) == ["A:7290000000411", "B:7290000000411"]
assert q("SELECT key FROM u.cart_items") == [(G,)]
assert ix.rekey(c) == 0
c.close()

# ---------------------------------------------------------------- promotions
c, _ = fresh_db("promos")
pd = os.path.join(tmp.name, "promos", "dumps", "Shufersal")
os.makedirs(pd)
NUT, COC, WAF, DEO = ean("800050038694"), ean("729000123457"), ean("729000123458"), ean("400590087496")


def promo_file(sid, ts, body):
    path = os.path.join(pd, f"PromoFull7290027600007-{sid:03d}-{ts}.xml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"<Root><ChainID>7290027600007</ChainID><StoreID>{sid}</StoreID><Promotions>{body}"
                 "</Promotions></Root>")
    return path


def promo(pid, desc, groups, extra="", start="2026-11-01T00:00:00.000", end="2027-01-01T02:59:00.000", club="0 - כלל הלקוחות"):
    gx = "".join(f"<Group><GroupID>{i + 1}</GroupID><MinPurchaseAmount>{mpa}</MinPurchaseAmount><PromotionItems>"
                 + "".join(f"<PromotionItem><ItemCode>{code}</ItemCode><MinQty>1</MinQty><MaxQty>{mq}</MaxQty>"
                           f"<DiscountRate>{rate}</DiscountRate><DiscountedPrice>{dp}</DiscountedPrice></PromotionItem>"
                           for code, mq, rate, dp in items)
                 + "</PromotionItems></Group>" for i, (mpa, items) in enumerate(groups))
    return (f"<Promotion><PromotionID>{pid}</PromotionID><PromotionDescription>{desc}</PromotionDescription>"
            f"<PromotionStartDateTime>{start}</PromotionStartDateTime><PromotionEndDateTime>{end}</PromotionEndDateTime>"
            f"<PromotionStartHour /><PromotionEndHour />{extra}<ClubID>{club}</ClubID><AdditionalIsCoupon>0</AdditionalIsCoupon>"
            f"<Groups>{gx}</Groups></Promotion>")


body = "".join([
    # spend ₪150 in group 2 (the real layout: a dummy item there)
    promo("1", "19.90 ממרח קקאו עם אגוזי לוז-מות150", [("", [(NUT, "", "", "19.90")]),
                                                       ("150.00", [("0000000000000", "", "", "")])]),
    # spend ₪150 in group 2, which also lists the item
    promo("2", "19.90 ממרח קקאו-מות150", [("", [(NUT, "", "", "19.90")]), ("150.00", [(NUT, "", "", "")])]),
    # limit 4 per customer, given only as RedemptionLimit
    promo("3", "מנה חמה ב3.90 מוגבל 4", [("", [(COC, "", "", "3.90")])], "<RedemptionLimit>4</RedemptionLimit>"),
    # MaxQty 3 and RedemptionLimit 4: the smaller
    promo("4", "וופל ב5", [("", [(WAF, "3", "", "5.00")])], "<RedemptionLimit>4</RedemptionLimit>"),
    # 1+1: group 1 = items you buy, group 2 = rewards; only items in both get the deal
    promo("5", "1+1 דאודורנט רול און", [("", [(COC, "", "", "16.90"), (WAF, "", "", "16.90")]),
                                         ("", [(WAF, "", "100", ""), (DEO, "", "100", "")])]),
    # starts in 3 weeks (only the DateTime fields are given)
    promo("6", "ב4.90", [("", [(DEO, "", "", "4.90")])], start="2026-11-30T00:00:00.000", end="2027-01-04T23:59:00.000"),
    # already ended
    promo("7", "ב2.90", [("", [(DEO, "", "", "2.90")])], start="2026-01-01T00:00:00.000", end="2026-02-01T02:59:00.000"),
    # credit-card club
    promo("8", "3.90 פסטה", [("", [(DEO, "", "", "3.90")])], club="2 - מועדון אשראי של הקמעונאי"),
])
f = promo_file(1, 202611050300, body)
meta = {"chain": "7290027600007", "store": 1, "ts": 202611050300}
# what an earlier version stored from this file: promo 1 (spend threshold) and promo 5 on every group's items
c.execute("""INSERT INTO promos(chain_id, store_id, promo_id, description, min_qty, discounted_price, club_only, ts)
             VALUES('7290027600007', 1, '1', 'old', 1, 19.9, 0, 202611050300)""")
c.executemany("INSERT INTO promo_items VALUES(?, '7290027600007', 1, ?, 202611050300)",
              [(NUT, "1"), (COC, "5"), (WAF, "5"), (DEO, "5")])
c.commit()
ix.index_promos(c, f, meta, {"7290027600007": set()}, NOW)
c.commit()
rows = {r[0]: r[1:] for r in c.execute("SELECT promo_id, max_qty, start_ts, end_ts, club_only, club_ids FROM promos")}
assert "1" not in rows and "2" not in rows, "spend thresholds in any group are not deals (and the old row went)"
assert rows["3"][0] == 4.0, "RedemptionLimit is a per-customer limit"
assert rows["4"][0] == 3.0, "the smaller of MaxQty and RedemptionLimit"
assert rows["6"][1:3] == (202611300000, 202701042359), "PromotionStartDateTime / EndDateTime"
assert "7" not in rows, "ended"
assert rows["8"][3:] == (1, "2") and rows["3"][3:] == (0, "0")
items = sorted(r[0] for r in c.execute("SELECT key FROM promo_items WHERE promo_id='5'"))
assert items == [WAF], "only group-1 items that are also in the reward group; the old extra rows went"
assert ix._promo_fields(ix.etree.fromstring(
    "<Promotion><PromotionID>9</PromotionID><PromotionItems><PromotionItem><ItemCode>1</ItemCode>"
    "<MinPurchaseAmount></MinPurchaseAmount></PromotionItem><PromotionItem><ItemCode>2</ItemCode>"
    "<MinPurchaseAmount>100</MinPurchaseAmount></PromotionItem></PromotionItems></Promotion>"))["spend"]
# a Cerberus-style file: dates and hours apart, MaxQty at the promotion
cer = ix._promo_fields(ix.etree.fromstring(
    "<Promotion><PromotionId>5</PromotionId><PromotionStartDate>01/11/2026</PromotionStartDate>"
    "<PromotionStartHour>00:00:00</PromotionStartHour><PromotionEndDate>05/12/2026</PromotionEndDate>"
    "<PromotionEndHour>23:59:59</PromotionEndHour><MaxQty>2</MaxQty><DiscountedPrice>18</DiscountedPrice>"
    "<PromotionItems><Item><ItemCode>7290000066189</ItemCode><IsGiftItem>0</IsGiftItem></Item></PromotionItems>"
    "</Promotion>"))
assert (cer["start"], cer["end"], cer["max_qty"], cer["codes"], cer["club_ids"]) == (
    202611010000, 202612052359, 2.0, ["7290000066189"], [])
c.close()

# ---------------------------------------------------------------- runs: checked prices, failing confirm, folders
c, dbp = fresh_db("run")
rd = os.path.join(tmp.name, "run", "dumps")
A, B = "7290000000101", "7290000000202"
for folder, chain, sid, ts, p in (("ChainA", A, 1, 202611050100, 6.9), ("ChainB", B, 1, 202611050200, 7.2),
                                  ("ChainB", B, 2, 202611050000, 7.4)):
    os.makedirs(os.path.join(rd, folder), exist_ok=True)
    price_file(os.path.join(rd, folder), "PriceFull", chain, sid, ts, [(BAR, "שוקו תנובה 1 ליטר", p)])
price_file(os.path.join(rd, "ChainA"), "Price", B, 7, 202206130915, [(BAR, "שוקו", 9.9)])  # a stray old file
assert ix.run(rd, dbp)
assert ix.confirmed_through(c) == 202611050200 and ix.get_setting(c, "confirm_pending") == 0
assert c.execute("SELECT folder FROM chains WHERE chain_id=?", (B,)).fetchone() == ("ChainB",)  # most of its files
assert c.execute("SELECT store_count FROM products WHERE key=?", (BAR,)).fetchone() == (3,)
assert not ix.confirm_due(c)

# an on-time file (newer than every checked row) and a failing price check: older rows stay confirmed
real = ix.build_price_flags
ix.build_price_flags = lambda con, cutoff: 1 / 0
price_file(os.path.join(rd, "ChainA"), "Price", A, 1, 202611050300, [(BAR, "שוקו תנובה 1 ליטר", 6.9)])
assert ix.run(rd, dbp)
assert ix.confirmed_through(c) == 202611050200 and ix.get_setting(c, "confirm_pending") == 1
assert c.execute("SELECT store_count FROM products WHERE key=?", (BAR,)).fetchone() == (2,), "A's new row isn't"
ix.build_price_flags = real
assert ix.run(rd, dbp)
assert ix.confirmed_through(c) == 202611050300 and ix.get_setting(c, "confirm_pending") == 0

# a late delta (older than the newest checked price) arrives, and the price check fails
price_file(os.path.join(rd, "ChainB"), "Price", B, 2, 202611050030, [(BAR, "שוקו תנובה 1 ליטר", 0.5)])
ix.build_price_flags = lambda con, cutoff: 1 / 0
assert ix.run(rd, dbp)
ix.build_price_flags = real
assert ix.confirmed_through(c) == 202611050029, "the late file's rows are not checked"
assert ix.get_setting(c, "confirm_pending") == 1 and ix.confirm_due(c)
# nothing newer than the late file counts (the ₪0.50 delta, and the rows after it) until a confirm succeeds
assert c.execute("SELECT store_count FROM products WHERE key=?", (BAR,)).fetchone() == (0,)
assert ix.run(rd, dbp)                                           # no new files, but the last confirm failed
assert ix.confirmed_through(c) == 202611050300 and ix.get_setting(c, "confirm_pending") == 0
assert c.execute("SELECT chain_id, store_id FROM price_flags").fetchall() == [(B, 2)], "₪0.50 is an outlier"
assert c.execute("SELECT store_count FROM products WHERE key=?", (BAR,)).fetchone() == (2,)
with ix.run_lock(dbp) as mine:                                   # one indexer at a time
    assert mine and ix.run(rd, dbp) is False
c.close()

# ---------------------------------------------------------------- end of a series that started today
c, _ = fresh_db("hist")
price(c, BAR, "A", 1, 6.9, "שוקו")
c.commit()
ix.record_history(c, 20261105)
c.execute("INSERT INTO name_flags VALUES(?, 'A', 'x')", (BAR,))
ix.record_history(c, 20261105)
assert c.execute("SELECT count(*) FROM price_history").fetchone() == (0,), "began and ended today: no rows"
c.execute("DELETE FROM name_flags")
ix.record_history(c, 20261105)
ix.record_history(c, 20261106)
c.execute("INSERT INTO name_flags VALUES(?, 'A', 'x')", (BAR,))
ix.record_history(c, 20261107)
assert c.execute("SELECT day, mn FROM price_history ORDER BY day").fetchall() == [(20261105, 6.9), (20261107, None)]
ix.rebuild_history(c, 20261107)
assert c.execute("SELECT day, mn FROM price_history").fetchall() == [(20261105, 6.9)]
assert c.execute("SELECT mn, day FROM price_last").fetchall() == [(6.9, 20261105)]
c.close()

# ---------------------------------------------------------------- --names: every store's current files
c, dbp = fresh_db("backfill")
nd = os.path.join(tmp.name, "backfill", "dumps", "ChainA")
os.makedirs(nd)
for sid in range(1, 7):
    price_file(nd, "PriceFull", A, sid, NOW, [(BAR, f"שוקו {sid}", 6.9), ("7290000000411", "שום", 2.9)])
price_file(nd, "Price", A, 6, NOW + 1, [(BAR, "שוקו חדש", 6.9)])
assert ix.backfill_names(c, os.path.dirname(nd), workers=2) == 7
assert c.execute("SELECT count(DISTINCT name) FROM item_names WHERE key=?", (BAR,)).fetchone() == (7,)
assert c.execute("SELECT count(*) FROM item_names WHERE key='7290000000411'").fetchone() == (0,)
c.close()

tmp.cleanup()
print("ok")
