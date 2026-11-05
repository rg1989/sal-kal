"""Run: .venv/bin/python tests/test_way_server.py
On my way through app.server: the settings it keeps, the map's stores along the route, compare with stop plans, the
trip with stops and the store card, on a scratch database with the routing service faked (no network)."""
import json
import os
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="sal-way-")
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi import HTTPException  # noqa: E402

from app import auth, commute, db as dbmod, server  # noqa: E402

NOW_TS = int(time.strftime("%Y%m%d%H%M"))
NOW = int(time.strftime("%Y%m%d%H%M", time.localtime(time.time() - 3600)))
c = dbmod.connect(os.environ["BASKET_DB"])
server.con = lambda: c
PASTA, PASTE = "7290000888880", "7290000888897"
LAT = 32.0
KX = 111.32 * 0.848048096
east, north = (lambda km: km / KX), (lambda km: km / 110.574)
HOME = {"lat": LAT, "lon": 34.80, "label": "בית"}
WORK = {"lat": LAT, "lon": 34.90, "label": "עבודה"}
c.executescript(f"""
INSERT INTO chains(chain_id, name_he) VALUES('7290027600007', NULL), ('7290058140886', NULL), ('7290103152017', NULL);
INSERT INTO stores(chain_id, store_id, name, store_type, price_full_ts, promo_full_ts, last_price_ts) VALUES
  ('7290027600007', 1, 'S on the way', 1, 1, {NOW}, {NOW}), ('7290058140886', 1, 'R on the way', 1, 1, {NOW}, {NOW}),
  ('7290103152017', 1, 'O by home, off the way', 1, 1, {NOW}, {NOW}), ('7290027600007', 9, 'online', 2, 1, {NOW}, {NOW});
INSERT INTO products(key, code, name, is_weighted, scope, store_count, chain_count, min_price, max_price, norm, median) VALUES
  ('{PASTA}', '{PASTA}', 'פסטה', 0, 'global', 3, 3, 1, 10, 'פסטה', 5), ('{PASTE}', '{PASTE}', 'רסק', 0, 'global', 3, 3, 1, 10, 'רסק', 5);
INSERT INTO prices(key, chain_id, store_id, price, ts) VALUES
  ('{PASTA}', '7290027600007', 1, 6, 100), ('{PASTE}', '7290027600007', 1, 6, 100),
  ('{PASTA}', '7290058140886', 1, 3, 100), ('{PASTE}', '7290058140886', 1, 9, 100),
  ('{PASTA}', '7290103152017', 1, 1, 100), ('{PASTE}', '7290103152017', 1, 1, 100);
INSERT INTO item_names(key, chain_id, name, ts) VALUES
  ('{PASTA}', '7290027600007', 'פסטה', {NOW_TS}), ('{PASTE}', '7290027600007', 'רסק', {NOW_TS}),
  ('{PASTA}', '7290058140886', 'פסטה', {NOW_TS}), ('{PASTE}', '7290058140886', 'רסק', {NOW_TS}),
  ('{PASTA}', '7290103152017', 'פסטה', {NOW_TS}), ('{PASTE}', '7290103152017', 'רסק', {NOW_TS});
""")
COORDS = {("7290027600007", 1): (LAT + north(0.2), 34.80 + east(2)),
          ("7290058140886", 1): (LAT - north(0.3), 34.80 + east(7)),
          ("7290103152017", 1): (LAT + north(3), 34.80 + east(0.5)),
          ("7290027600007", 9): (LAT, 34.85)}
server.geo.store_coords = lambda con: COORDS
server.geo.exact_stores = lambda con: set(COORDS)
c.execute("UPDATE users SET prefs=? WHERE id=1", (json.dumps({"home": HOME, "max_km": 10}),))
c.commit()
server._user.set(auth.user_row(c, 1))

routes = []


def fake_route(points, travel="car"):
    """A straight road along the points; 1 minute a km."""
    routes.append((travel, points))
    km = sum((((a[0] - b[0]) * 110.574) ** 2 + ((a[1] - b[1]) * KX) ** 2) ** .5 for a, b in zip(points, points[1:]))
    return {"coords": [[lon, lat] for lat, lon in points], "minutes": round(km), "km": round(km, 1), "legs": []}


commute.route = fake_route
cart = {"items": [{"key": PASTA, "qty": 1}, {"key": PASTE, "qty": 1}], "prefs": {}}

# around home (as before): the cheapest store is the one by home
assert server.compare(cart)["chains"][0]["in_store"]["name"] == "O by home, off the way"
assert server.compare(cart)["way"] is None and "route" not in server.stores_near()

# settings: work is an address like home; the rest are checked
r = server.put_settings({"prefs": {"work": WORK, "area": "route", "travel": "plane", "detour_km": 99}})["prefs"]
assert r["area"] == "route" and r["travel"] == "car" and r["detour_km"] == 5.0 and r["work"]["label"] == "עבודה", r
r = server.put_settings({"prefs": {"area": "anything", "travel": "foot", "detour_km": 0.01}})["prefs"]
assert r["area"] == "home" and r["travel"] == "foot" and r["detour_km"] == 0.1, r
try:
    server.put_settings({"prefs": {"work": {"lat": 40.7, "lon": -74.0}}})
    raise AssertionError("an address outside Israel was kept")
except HTTPException as e:
    assert e.status_code == 422
server.put_settings({"prefs": {"area": "route", "travel": "bike", "detour_km": 1}})

# the map: the branches along the route, nearest to it first, with the route itself
m = server.stores_near()
assert [s["name"] for s in m["stores"]] == ["S on the way", "R on the way"], m["stores"]
assert m["work"]["label"] == "עבודה" and m["travel"] == "bike" and m["detour_km"] == 1 and m["max_km"] is None
assert m["route"]["coords"][0] == [34.80, LAT] and m["route"]["straight"] is False
assert routes[-1][0] == "bike"
# on foot the detour is 200 m at most: a wider pick (kept from the bike) counts as 200 m, on the map and in compare
server.put_settings({"prefs": {"travel": "foot", "detour_km": 0.5}})
assert server.stores_near()["detour_km"] == 0.2
res = server.compare({**cart, "prefs": {"travel": "foot", "detour_km": 3}})
assert not {"R on the way", "O by home, off the way"} & {ch["in_store"]["name"] for ch in res["chains"]}, res["chains"]  # 300 m and 3 km off
server.put_settings({"prefs": {"travel": "bike", "detour_km": 1}})

# compare: only those two, each at its distance off the route, and the two-stop plan
r = server.compare(cart)
assert [ch["in_store"]["name"] for ch in r["chains"]] == ["S on the way", "R on the way"], r["chains"]
assert r["chains"][0]["in_store"]["distance_km"] == 0.2
plans = r["way"]["plans"]
assert [(p["stops"], p["total"]) for p in plans] == [(1, 12), (2, 9)], plans
assert [s["name"] for s in plans[1]["stores"]] == ["S on the way", "R on the way"] and plans[1]["saving"] == 3

# cards: the cheapest on the way, not by home
near = server._near_stores(c)
assert set(near) == {("7290027600007", 1), ("7290058140886", 1)} and near[("7290027600007", 1)] == 0.2, near

# the trip with stops: what they add over going straight
routes.clear()
t = server.way_trip({"stops": [["7290027600007", 1], ["7290058140886", 1], ["nope", 3], "bad"]})
assert t["route"] and t["extra_minutes"] >= 0 and t["extra_km"] > 0 and t["travel"] == "bike", t
assert len(routes[-1][1]) == 4 and routes[-1][1][0] == (LAT, 34.80), routes[-1]
assert server.way_trip({"stops": []})["extra_km"] == 0

# the store card: home -> the store -> work
s = server.store_info("7290058140886", 1)
assert s["route"]["way"] is True and s["route"]["extra_km"] > 0 and len(s["route"]["coords"]) == 3, s["route"]

# the routing service down: the straight line, not kept, and compare still answers
commute.route = lambda points, travel="car": (_ for _ in ()).throw(OSError("down"))
server._way_cache.clear()
commute._paths.clear()
m = server.stores_near()
assert m["route"]["straight"] is True and len(m["stores"]) == 2
assert server.way_trip({"stops": [["7290027600007", 1]]}) == {"route": None}
assert server._way_cache == {}

# back around home
server.put_settings({"prefs": {"area": "home"}})
assert server.compare(cart)["way"] is None and server.compare(cart)["chains"][0]["in_store"]["name"] == "O by home, off the way"

# ================================================================ named places: around any one, on the way between any two
pl = server.prefs()["places"]
assert [(x["id"], x["name"]) for x in pl] == [("home", None), ("work", None)], pl   # from the single address and work
GYM = {"lat": LAT + north(0.1), "lon": 34.80 + east(7.2), "label": "חדר כושר"}
places = [{**x} for x in pl] + [{"id": "g1", "name": "  Gym  ", **GYM}]
r = server.put_settings({"prefs": {"places": places, "area": "home", "at": "g1", "max_km": 2}})["prefs"]
assert [x["name"] for x in r["places"]] == [None, None, "Gym"] and r["at"] == "g1"
assert r["home"]["label"] == "חדר כושר" and r["work"] is None      # around the gym: its address is where you are
assert [ch["in_store"]["name"] for ch in server.compare(cart)["chains"]] == ["R on the way"]  # 2 km around the gym
# on the way from the gym to home: the route starts at the gym
routes.clear()
r = server.put_settings({"prefs": {"area": "route", "from": "g1", "to": "home"}})["prefs"]
assert (r["from"], r["to"], r["home"]["label"], r["work"]["label"]) == ("g1", "home", "חדר כושר", "בית"), r
server._way_cache.clear()
m = server.stores_near()
assert m["route"]["coords"][0] == [GYM["lon"], GYM["lat"]] and m["work"]["label"] == "בית"
# the same place twice: the trip ends at another one
r = server.put_settings({"prefs": {"from": "home", "to": "home"}})["prefs"]
assert r["from"] == "home" and r["to"] == "work", (r["from"], r["to"])
# a place that's gone: back to the first one
r = server.put_settings({"prefs": {"places": [x for x in places if x["id"] != "g1"], "at": "g1"}})["prefs"]
assert r["at"] == "home" and len(r["places"]) == 2
# what the browser sends with compare: its picks count (checked like saved ones), never its places. A page opened
# before a place was moved elsewhere still holds the old point, and its stops would follow a route the map doesn't show.
moved = [{**x, "lat": x["lat"] + north(3)} if x["id"] == "work" else x for x in places]
res = server.compare({**cart, "prefs": {"places": moved, "area": "route", "from": "home", "to": "work"}})
assert res["way"] is not None and res["prefs"]["work"]["lat"] == WORK["lat"], res["prefs"]["work"]
assert [x["lat"] for x in res["prefs"]["places"]] == [x["lat"] for x in server.prefs()["places"]]
res = server.compare({**cart, "prefs": {"places": moved, "area": "route", "from": "g1", "to": "work"}})
assert res["prefs"]["from"] == "home"  # a place that isn't saved: the first one, as when saving
res = server.compare({**cart, "prefs": {"places": "nonsense"}})
assert "chains" in res
# bad places are refused whole
for bad in ([{"id": "BAD ID", **GYM}], [{"id": "a", **GYM}, {"id": "a", **GYM}], [{"id": "x", "lat": 51.5, "lon": -0.1}], "x"):
    try:
        server.put_settings({"prefs": {"places": bad}})
        raise AssertionError(f"kept {bad}")
    except HTTPException as e:
        assert e.status_code == 422
# the first-run setup still sends one address: it becomes the place you're around (keeping its name)
server.put_settings({"prefs": {"places": [{**places[0], "name": "Flat"}, places[1]], "area": "home", "at": "home"}})
r = server.put_settings({"prefs": {"home": {**HOME, "label": "חדש"}}})["prefs"]
assert [(x["id"], x["name"], x["label"]) for x in r["places"]][0] == ("home", "Flat", "חדש") and r["home"]["label"] == "חדש"
saved = json.loads(c.execute("SELECT prefs FROM users WHERE id=1").fetchone()[0])
assert saved["home"]["label"] == "חדש" and "places" in saved  # home kept in the saved prefs for the statistics
# a new account: its first address is its first place
c.execute("UPDATE users SET prefs='{}' WHERE id=1")
c.commit()
server._user.set(auth.user_row(c, 1))
assert server.prefs()["home"] is None and server.prefs()["places"] == []
r = server.put_settings({"prefs": {"home": HOME, "max_km": 5}})["prefs"]
assert [x["id"] for x in r["places"]] == ["home"] and r["home"]["label"] == "בית"
print("ok")
