"""Sal-Kal on your own computer (app/local.py): which files the downloader asks for, and which stores count as near.
Run: .venv/bin/python tests/test_local.py"""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
tmp = tempfile.mkdtemp()
os.environ["SAL_HOME"] = tmp
os.environ["BASKET_DB"] = os.path.join(tmp, "data", "basket.db")

from app import local  # noqa: E402

# the file-name filter: these stores' full price and deal files only, in every chain's naming
rx = re.compile(local._regex([("7290027600007", 1), ("7290027600007", 34), ("7290058249350", 38)]))
yes = ["PriceFull7290027600007-001-202611060200.gz", "PromoFull7290027600007-034-202611060200.xml",
       "NULLPriceFull7290027600007-1-202611060200", "pricefull7290027600007-001-202611060200.gz",
       "PromoFull7290058249350-000-038-20261106-000027"]
no = ["PriceFull7290027600007-002-202611060200.gz", "Price7290027600007-001-202611060200.gz",
      "Stores7290027600007-000-202611060200.gz", "PriceFull7290058140886-001-202611060200.gz",
      "PriceFull7290027600007-341-202611060200.gz", "PromoFull7290058249350-000-380-20261106-000027"]
for n in yes:
    assert rx.search(n), n
for n in no:
    assert not rx.search(n), n

# distance to the way between two places
a, b = {"lat": 32.08, "lon": 34.78}, {"lat": 32.08, "lon": 34.88}
assert local._seg_km(32.08, 34.83, a, b) < 0.01
assert 1.0 < local._seg_km(32.09, 34.83, a, b) < 1.2
assert 9.0 < local._seg_km(32.08, 34.98, a, b) < 9.6

# near stores: around a place within the distance (+ margin), and along the way
con = local._con()
con.execute("CREATE TABLE IF NOT EXISTS towns(code INTEGER PRIMARY KEY, name TEXT)")
rows = [("1", 1, "A", "הרצל 1", "עיר"), ("1", 2, "B", "הרצל 2", "עיר"), ("2", 3, "C", "הרצל 3", "עיר"), ("2", 4, "D", "הרצל 4", "עיר")]
pts = {"הרצל 1, עיר": (32.08, 34.78), "הרצל 2, עיר": (32.08, 34.83), "הרצל 3, עיר": (32.13, 34.83), "הרצל 4, עיר": (32.08, 34.88)}
con.executemany("INSERT INTO stores(chain_id, store_id, name, address, city) VALUES(?,?,?,?,?)", rows)
con.executemany("INSERT INTO geocache VALUES(?,?,?,0)", [(q, la, lo) for q, (la, lo) in pts.items()])
home, work = {"id": "home", "lat": 32.08, "lon": 34.78}, {"id": "work", "lat": 32.08, "lon": 34.88}


def prefs(p):
    con.execute("DELETE FROM u.users")
    con.execute("INSERT INTO u.users(id, name, created, seen, onboarded, prefs) VALUES(1,'h',0,0,1,?)", (json.dumps(p),))
    con.commit()


prefs({"places": [home], "max_km": 1})
assert local.wanted_stores(con) == [("1", 1)], local.wanted_stores(con)
prefs({"places": [home], "max_km": 4})          # 4 + 2 km margin reaches store 2 (4.7 km)
assert local.wanted_stores(con) == [("1", 1), ("1", 2)]
prefs({"places": [home, work], "max_km": 1, "area": "route", "from": "home", "to": "work", "detour_km": 1})
assert local.wanted_stores(con) == [("1", 1), ("1", 2), ("2", 4)], local.wanted_stores(con)  # 3 is 5.6 km off the way
prefs({})
assert local.wanted_stores(con) == []
print("ok")
