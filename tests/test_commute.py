"""Run: .venv/bin/python tests/test_commute.py
On my way: which branches are along the route home -> work, and the cart spread over the fewest stops that save."""
import itertools
import os
import random
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp(prefix="sal-commute-")
os.environ["BASKET_DB"] = os.path.join(tmp, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(tmp, "user.db")
from app import commute, db, engine  # noqa: E402

# ================================================================ the corridor
# a route due east along latitude 32.0, from 34.80 to 34.90 (about 9.4 km), in three pieces
LAT = 32.0
KX = 111.32 * 0.848048096  # km per degree of longitude at 32°
route = [[34.80, LAT], [34.83, LAT], [34.86, LAT], [34.90, LAT]]
north = lambda km: km / 110.574
east = lambda km: km / KX
pts = {
    "mid": (LAT + north(0.5), 34.85),            # 0.5 km off, about 4.7 km along
    "far": (LAT + north(2.0), 34.85),            # 2 km off: not on the way at 1 km
    "start": (LAT - north(0.2), 34.80),          # by home
    "past": (LAT, 34.90 + east(0.6)),            # beyond work, 0.6 km on
    "behind": (LAT, 34.80 - east(1.5)),          # 1.5 km behind home
}
cor = commute.corridor(route, pts, 1.0)
assert set(cor) == {"mid", "start", "past"}, cor
assert abs(cor["mid"][0] - 0.5) < 0.01 and abs(cor["mid"][1] - (0.05 * KX)) < 0.02, cor["mid"]
assert abs(cor["start"][0] - 0.2) < 0.01 and cor["start"][1] == 0, cor["start"]
assert abs(cor["past"][0] - 0.6) < 0.01 and abs(cor["past"][1] - 0.1 * KX) < 0.02, cor["past"]
assert commute.corridor(route, pts, 0.3).keys() == {"start"}
assert set(commute.corridor(route, pts, 2.5)) == {"mid", "far", "start", "past", "behind"}
assert commute.corridor([], pts, 1) == {}
assert set(commute.corridor([[34.85, LAT]], pts, 1.0)) == {"mid"}  # a route of one point: a circle around it

# a long winding route: the grid gives the same answer as checking every segment
rnd = random.Random(7)
wind = [[34.8 + i * 0.002, 32.0 + 0.01 * ((i * 37) % 11) / 11] for i in range(400)]
many = {i: (32.0 + rnd.uniform(-0.02, 0.03), 34.8 + rnd.uniform(-0.01, 0.82)) for i in range(400)}


def slow(coords, points, width):
    xy = commute._plane(sum(c[1] for c in coords) / len(coords))
    P = [xy(lat, lon) for lon, lat in coords]
    out = {}
    for k, (lat, lon) in points.items():
        px, py = xy(lat, lon)
        d = min(((((x0 + t * (x1 - x0) - px) ** 2 + (y0 + t * (y1 - y0) - py) ** 2) ** .5)
                 for (x0, y0), (x1, y1) in zip(P, P[1:])
                 for t in [max(0, min(1, ((px - x0) * (x1 - x0) + (py - y0) * (y1 - y0)) / (((x1 - x0) ** 2 + (y1 - y0) ** 2) or 1)))]))
        if d <= width:
            out[k] = round(d, 3)
    return out


for w in (0.2, 1.0):
    fast = {k: v[0] for k, v in commute.corridor(wind, many, w).items()}
    assert fast == slow(wind, many, w), (w, set(fast) ^ set(slow(wind, many, w)))

# ================================================================ the plan
def entry(name, off, at, prices, status=None):
    """A branch on the way priced for the cart: prices {key: line cost}; status {key: "unconfirmed"}."""
    lines = []
    for k, c in prices.items():
        st = (status or {}).get(k, "ok")
        lines.append({"key": k, "qty": 1, "status": st, "cost": c if st == "ok" else None, "price": c})
    return {"chain_id": name, "chain": name, "store_id": 1, "name": name, "off_km": off, "at_km": at, "lines": lines}


K = ["a", "b", "c"]
X = entry("X", 0.1, 1.0, {"a": 10, "b": 10, "c": 10})
Y = entry("Y", 0.2, 5.0, {"a": 5, "b": 12, "c": 12})
Y2 = entry("Y2", 0.6, 2.0, {"a": 5, "b": 12, "c": 12})   # the same prices, further off the route: Y is kept
Z = entry("Z", 0.3, 3.0, {"c": 3})
Q = entry("Q", 0.9, 4.0, {"a": 1}, status={"a": "unconfirmed"})  # never counted
p = commute.plans([X, Y, Y2, Z, Q], K)
assert [(x["stops"], x["total"]) for x in p] == [(1, 29), (2, 20), (3, 18)], [(x["stops"], x["total"]) for x in p]
one, two, three = p
assert [s["chain"] for s in one["stores"]] == ["Y"] and one["saving"] is None and one["complete"]
assert [s["chain"] for s in two["stores"]] == ["Z", "Y"], "stops in the order you pass them from home"
assert two["saving"] == 9 and [[l["key"] for l in s["lines"]] for s in two["stores"]] == [["c"], ["a", "b"]]
assert [s["chain"] for s in three["stores"]] == ["X", "Z", "Y"] and three["saving"] == 11
assert [s["subtotal"] for s in three["stores"]] == [10, 3, 5]
assert all(x["covered"] == 3 and x["missing"] == [] for x in p)

# an extra stop that saves less than ₪1 isn't one
X9 = entry("X9", 0.1, 1.0, {"a": 10, "b": 9.5, "c": 10})
p = commute.plans([X, Y, Z, X9], K)
assert [x["stops"] for x in p] == [1, 2, 3] and p[2]["total"] == 17.5, [(x["stops"], x["total"]) for x in p]
# (X9 replaces X: b at 9.5; X itself would buy nothing)
assert {s["chain"] for s in p[2]["stores"]} == {"X9", "Z", "Y"}
U = entry("U", 0.1, 6.0, {"b": 9.2})  # with it: 4 stops save only 0.3 over the best 3
p = commute.plans([X9, Y, Z, U], K)
assert [x["stops"] for x in p] == [1, 2, 3], [(x["stops"], x["total"]) for x in p]

# nothing has everything: the plan says what's missing, and no single store means no "saving"
p = commute.plans([entry("A", 0.1, 1, {"a": 4}), entry("B", 0.1, 2, {"b": 4})], K)
assert [(x["stops"], x["covered"], x["complete"], x["missing"]) for x in p] == [(1, 1, False, ["b", "c"]), (2, 2, False, ["c"])], p
assert all(x["saving"] is None for x in p)
assert commute.plans([], K) == [] and commute.plans([X], []) == []
assert commute.plans([Q], ["a"]) == []  # only unconfirmed prices: no plan


# against every combination of up to 5 stores
def brute(entries, keys, size):
    vecs = [commute._vector(e, keys) for e in entries]
    best = None
    for ids in itertools.combinations(range(len(vecs)), size):
        v = tuple(map(min, *[vecs[i] for i in ids])) if size > 1 else vecs[ids[0]]
        if commute.NO in v:
            continue
        if size > 1 and any((tuple(map(min, *[vecs[i] for i in ids if i != j])) if size > 2 else vecs[[i for i in ids if i != j][0]]) == v
                            for j in ids):
            continue
        if best is None or sum(v) < best:
            best = sum(v)
    return best


rnd = random.Random(3)
for trial in range(60):
    keys = [f"k{i}" for i in range(rnd.randint(2, 7))]
    ents = [entry(f"S{i}", rnd.uniform(0, 1), rnd.uniform(0, 10),
                  {k: rnd.choice([3, 5, 7, 8, 9, 10, 12]) for k in keys if rnd.random() < 0.8}) for i in range(rnd.randint(3, 11))]
    got = {x["stops"]: round(x["total"] * 100) for x in commute.plans(ents, keys) if x["complete"]}
    for size in range(1, 6):
        b = brute(ents, keys, size)
        if size in got:
            assert got[size] == b, (trial, size, got, b)
    # every plan shown is the cheapest of its size, and each extra stop saves at least ₪1
    totals = [got[s] for s in sorted(got)]
    assert all(a - b >= 100 for a, b in zip(totals, totals[1:])), totals
    best_any = min((brute(ents, keys, s) for s in range(1, 6) if brute(ents, keys, s) is not None), default=None)
    if best_any is not None:
        assert min(totals) - best_any < 100 * 5, (totals, best_any)  # within the ₪1-per-stop rule of the best

# ================================================================ in the comparison: the branches along the route
NOW_TS = int(time.strftime("%Y%m%d%H%M"))
ts = lambda hours_ago: int(time.strftime("%Y%m%d%H%M", time.localtime(time.time() - hours_ago * 3600)))


def ean(body):
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    return body + str((10 - s % 10) % 10)


def connect(path):  # as tests/test_compare.py
    return db.connect(path)


def store(c, cid, sid, age):
    c.execute("INSERT INTO stores(chain_id, store_id, name, address, city, store_type, sub_chain, price_full_ts,"
              " promo_full_ts, last_price_ts) VALUES(?,?,'Branch','','',1,'',100,?,?)", (cid, sid, ts(1), ts(age)))
    c.execute("INSERT OR IGNORE INTO chains(chain_id, name_he) VALUES(?, '')", (cid,))


def product(c, key, name):
    c.execute("INSERT INTO products(key, code, name, manufacturer, quantity, unit, is_weighted, scope, store_count,"
              " chain_count, min_price, max_price, norm) VALUES(?,?,?,'','','',0,'global',1,3,1,10,NULL)", (key, key, name))


def price(c, key, cid, p, sid=1):
    c.execute("INSERT INTO prices VALUES(?,?,?,?,100)", (key, cid, sid, p))
    c.execute("INSERT OR IGNORE INTO item_names VALUES(?,?,?,?)", (key, cid, c.execute(
        "SELECT name FROM products WHERE key=?", (key,)).fetchone()[0], NOW_TS))



g = connect(os.path.join(tmp, "way.db"))
P1, P2 = ean("729000777771"), ean("729000777772")
product(g, P1, "פסטה")
product(g, P2, "רסק")
A, B, C, D = "7290027600007", "7290058140886", "7290103152017", "7290803800003"  # Shufersal, Rami Levy, Osher Ad, Yohananof
HOME, WORK = {"lat": LAT, "lon": 34.80}, {"lat": LAT, "lon": 34.90}
COORDS = {}
for cid, sid, at_, off_, ps in [
        (A, 1, 1.0, 0.1, {P1: 10, P2: 10}),
        (B, 1, 6.0, 0.4, {P1: 4, P2: 12}),     # cheap pasta on the way
        (C, 1, 3.0, 0.2, {P2: 5}),            # cheap paste on the way
        (D, 1, 0.5, 4.0, {P1: 1, P2: 1})]:    # cheapest, near home, but 4 km off the route
    store(g, cid, sid, 1)
    COORDS[(cid, sid)] = (LAT + north(off_), 34.80 + east(at_))
    for key, pr_ in ps.items():
        price(g, key, cid, pr_, sid=sid)
g.commit()
engine.geo.store_coords = lambda con: COORDS
cart = [{"key": P1, "qty": 1}, {"key": P2, "qty": 2}]
way = commute.corridor(route, COORDS, 1.0)
assert set(way) == {(A, 1), (B, 1), (C, 1)}
r = engine.compare(g, cart, {"home": HOME, "max_km": 10, "mode": "store"}, way=way)
assert [c["name"] for c in r["chains"]] == ["Rami Levy", "Shufersal", "Osher Ad"], [c["name"] for c in r["chains"]]
s = r["chains"][0]["in_store"]
assert s["distance_km"] == 0.4 and abs(s["at_km"] - 6.0) < 0.05 and s["total"] == 28, s
assert {b["chain"] for b in r["branches"]} == {"Shufersal", "Rami Levy", "Osher Ad"} and all("at_km" in b for b in r["branches"])
plans = r["way"]["plans"]
assert [(x["stops"], x["total"]) for x in plans] == [(1, 28), (2, 14)], [(x["stops"], x["total"]) for x in plans]
assert [s["chain"] for s in plans[1]["stores"]] == ["Osher Ad", "Rami Levy"] and plans[1]["saving"] == 14
assert [s["subtotal"] for s in plans[1]["stores"]] == [10, 4]
# around home (no corridor), the 4 km store counts and there are no plans
r = engine.compare(g, cart, {"home": HOME, "max_km": 10, "mode": "store"})
assert r["chains"][0]["name"] == "Yohananof" and r["way"] is None
# delivered home: on my way doesn't change where the web shop delivers from; no stops
r = engine.compare(g, cart, {"home": HOME, "mode": "delivery"}, way=way)
assert r["way"] == {"plans": []}
assert engine.compare(g, [], {}, way=way)["way"] == {"plans": []}

# ================================================================ only branches placed at their own address are "on the way"
import importlib
real_geo = importlib.reload(importlib.import_module("app.geo"))  # the comparison above faked store_coords
x = connect(os.path.join(tmp, "exact.db"))
x.execute("CREATE TABLE IF NOT EXISTS towns(code INTEGER PRIMARY KEY, name TEXT)")
x.executemany("INSERT INTO towns VALUES(?,?)", [(8600, "רמת גן"), (6600, "חולון")])  # each point is checked against its town
for cid, sid, name, addr, city in [
        ("A", 1, "Branch", "הרצל 10", "רמת גן"),          # its street: exact
        ("A", 2, "Branch", "unknown", "רמת גן"),          # only the town: the town's centre
        ("A", 3, "Branch", "ביאליק 5", "רמת גן"),          # a street the map didn't know: came back as the town
        ("B", 1, "שופרסל קניון איילון", "", ""),           # found by name only, no town to check it against: not placed
        ("C", 1, "Branch", "אחד 1", "חולון"), ("C", 2, "Branch", "שתיים 2", "חולון"),
        ("C", 3, "Branch", "שלוש 3", "חולון"),             # three addresses on one point: not their own
        ("D", 1, "Branch", "הקניון 1", "חולון"), ("D", 2, "Branch 2", "הקניון 1", "חולון")]:  # one mall, two shops
    x.execute("INSERT INTO stores(chain_id, store_id, name, address, city, store_type, price_full_ts) VALUES(?,?,?,?,?,1,1)",
              (cid, sid, name, addr, city))
for q, lat, lon in [("הרצל 10, רמת גן", 32.081, 34.811), ("רמת גן", 32.068, 34.824), ("ביאליק 5, רמת גן", 32.068, 34.824),
                    ("שופרסל קניון איילון", 32.099, 34.826), ("אחד 1, חולון", 32.01, 34.77), ("שתיים 2, חולון", 32.01, 34.77),
                    ("שלוש 3, חולון", 32.01, 34.77), ("הקניון 1, חולון", 32.02, 34.78)]:
    x.execute("INSERT INTO geocache VALUES(?,?,?,0)", (q, lat, lon))
x.commit()
assert real_geo.exact_stores(x) == {("A", 1), ("D", 1), ("D", 2)}, real_geo.exact_stores(x)
assert len(real_geo.store_coords(x)) == 8 and ("B", 1) not in real_geo.store_coords(x)

# ================================================================ the route service (no network: a fake answer)
calls = []


class Fake:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def fake_open(req, timeout=0):
    calls.append(req.full_url)
    if "fail" in req.full_url or "34.70000" in req.full_url:
        raise OSError("down")
    import json
    return Fake(json.dumps({"routes": [{"geometry": {"coordinates": [[34.8, 32.0], [34.9, 32.0]]}, "duration": 600,
                                        "distance": 9400, "legs": [{"duration": 600, "distance": 9400}]}]}).encode())


commute.urllib.request.urlopen = fake_open
pth = commute.path(HOME, WORK, "foot")
assert pth["minutes"] == 10 and pth["km"] == 9.4 and not pth["straight"] and "routed-foot" in calls[-1], (pth, calls)
commute.path(HOME, WORK, "foot")
assert len(calls) == 1, "cached"
assert "router.project-osrm.org" in (commute.path(HOME, WORK, "car"), calls[-1])[1]
down = commute.path({"lat": 32.0, "lon": 34.7}, WORK, "car")
assert down["straight"] and down["minutes"] is None and len(down["coords"]) == 2 and down["km"] > 18
# for the minute the service is left alone the line is still marked straight, and never kept as the road route
# (it was once kept, and the next request passed it off as the road: stops along a line nobody travels)
n = len(calls)
again = commute.path({"lat": 32.0, "lon": 34.7}, WORK, "car")
assert again["straight"] and len(calls) == n and not any(k[1][0] == (32.0, 34.7) for k in commute._paths)

# ================================================================ how far off the way, by way of travel
# on foot 200 m at most; a pick made for another way snaps to the nearest choice of this one
assert commute.detour_km("foot", 0.5) == 0.2 and commute.detour_km("foot", None) == 0.2 and commute.detour_km("foot", 0.1) == 0.1
assert commute.detour_km("car", 5) == 3.0 and commute.detour_km("car", None) == 1.0 and commute.detour_km("bike", 0.25) == 0.2
assert commute.detour_km("bike", "x") == 0.5 and commute.detour_km("plane", 0.4) == 0.5
assert all(max(v) <= 0.2 for k, v in commute.DETOURS.items() if k == "foot") and set(commute.DETOURS) == set(commute.TRAVEL)
print("ok")
