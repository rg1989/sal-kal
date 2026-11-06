"""Run: .venv/bin/python tests/test_store_truth.py
Nothing false about a store: what kind of store a Stores-file entry is (a door, a web shop, an app's listing, another
brand's shop), which online store prices a chain's delivery, where a branch is (only in its own town), how precise
that is (the store card, the map, drive times, opening hours), and what its card says (no "unknown", its own brand).
Scratch databases only; the network is never used."""
import json
import os
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="sal-truth-")
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import auth, chains, commute, db as dbmod, engine, geo, server  # noqa: E402
from app.chains import clean_place, general_web_shop, store_kind, sub_brand, walk_in  # noqa: E402

SAPIR, KING, CARRE, WOLT, MAHSANEI, SHUF = ("7290058156016", "7290058108879", "7290055700007", "7290058249350",
                                             "7290661400001", "7290027600007")

# ---------------------------------------------------------------- what each Stores-file entry is
assert store_kind(SAPIR, "וולט ויצמן חולון", 1) == "listing"          # Wolt's prices for shop 190, not a door
assert store_kind(KING, "וולט באקה", 1) == "listing"
assert store_kind(SAPIR, "מחסן אונליין שדרות", 1) == "online"          # an online warehouse, StoreType 1 or not
assert store_kind(SAPIR, "סופר ספיר חולון", 1) == "shop"
assert store_kind(WOLT, "וולט מרקט | מודיעין", 2) == "online"          # Wolt Market's own dark stores
assert store_kind(WOLT, "Wolt Market Israel Test Venue", 2) == "none"
assert store_kind(WOLT, "Wolt Market | Marlog Atlas (CLOSED)", 2) == "none"
assert store_kind(CARRE, "קרפור אונליין   כפר סבא @ קרפור (5304)", 2) == "online"
assert store_kind(CARRE, "@ יהלומים ביתן (9032)", 2) == "other"         # Yeinot Bitan's web shop
assert store_kind(CARRE, "כפר סבא @ קוויק", 2) == "other"              # Quik's
assert store_kind(SHUF, "שופרסל ONLINE", 2) == "online" and store_kind(SHUF, "דיל חולון", 3) == "shop"
assert walk_in(SAPIR, "סופר ספיר חולון", 1) and not walk_in(SAPIR, "וולט ויצמן חולון", 1)
assert not walk_in(SHUF, "שופרסל ONLINE", 2)
# a web shop that names no place is the chain's general one
assert general_web_shop(MAHSANEI, "97 אינטרנט") and not general_web_shop(MAHSANEI, "96 אינטרנט אילת")
assert general_web_shop(SHUF, "שופרסל ONLINE") and not general_web_shop("7290058148776", "אונליין - בני ברק")
# what a store card may show as an address
assert clean_place("unknown") == clean_place("0") == clean_place("h") == clean_place(None) == ""
assert clean_place("דרך הדרום http:// 1") == "" and clean_place("https://www.carrefour.co.il") == ""
assert clean_place("חורב 15 | www.kulinarik.co.il/|") == "חורב 15"
assert clean_place("יפת 0") == "יפת" and clean_place("הרצל 10") == "הרצל 10"
assert sub_brand("נטו חיסכון") == "נטו חיסכון" and sub_brand("1") == sub_brand("000") == sub_brand("") == ""

# ---------------------------------------------------------------- which online store prices a chain's delivery
st = lambda cid, sid, name, typ, ts=202611061200: {"chain_id": cid, "store_id": sid, "name": name, "store_type": typ,
                                                  "sub_chain": "", "last_price_ts": ts}
stores = {(s["chain_id"], s["store_id"]): s for s in [
    st(CARRE, 471, "קרפור אונליין   כפר סבא @ קרפור (5304)", 2), st(CARRE, 472, "@ יהלומים ביתן (9032)", 2),
    st(CARRE, 473, "כפר סבא @ קוויק", 2),
    st(MAHSANEI, 96, "96 אינטרנט אילת", 2), st(MAHSANEI, 97, "97 אינטרנט", 2), st(MAHSANEI, 20, "20 להבים", 2),
    st(SAPIR, 34, "און ליין - ראש פינה ת.", 2), st(SAPIR, 35, "און ליין - קרית שמונה ת.", 2, 0),
    st(SHUF, 413, "שופרסל ONLINE", 2)]}
cands = lambda cid: [sk for sk, s in stores.items() if sk[0] == cid and engine._is_online(s)]
far = lambda *near: {sk: ((2.0 if sk in near else 150.0), 0, 0) for sk in stores}
assert cands(CARRE) == [(CARRE, 471)]                                   # the other brands' shops aren't candidates
assert engine.online_store(cands(CARRE), stores, far(), True) == (CARRE, 471)
# Mahsanei HaShuk: the general web shop, not Eilat's (VAT-free prices) far away; Eilat's for a home in Eilat
assert engine.online_store(cands(MAHSANEI), stores, far(), True) == (MAHSANEI, 97)
assert engine.online_store(cands(MAHSANEI), stores, far((MAHSANEI, 96)), True) == (MAHSANEI, 96)
# Super Sapir's regional web shops: Rosh Pina's only near Rosh Pina, never for a home 150 km away
assert engine.online_store(cands(SAPIR), stores, far(), True) is None
assert engine.online_store(cands(SAPIR), stores, far((SAPIR, 34)), True) == (SAPIR, 34)
# one web shop: that one, wherever it is
assert engine.online_store(cands(SHUF), stores, far(), True) == (SHUF, 413)
assert engine.online_store(cands(SHUF), stores, {sk: (None, None, None) for sk in stores}, False) == (SHUF, 413)

# ---------------------------------------------------------------- where a branch is: only in its own town
pts = geo.town_points()
assert abs(pts[3570][0] - 32.106) < 0.01 and abs(pts[9000][1] - 34.79) < 0.01  # Ariel, Be'er Sheva (official list)
g = dbmod.connect(os.path.join(TMP, "geo.db"))
g.execute("CREATE TABLE IF NOT EXISTS towns(code INTEGER PRIMARY KEY, name TEXT)")
g.executemany("INSERT INTO towns VALUES(?,?)", [(9000, "באר שבע"), (2600, "אילת"), (3570, "אריאל"), (3000, "ירושלים"),
                                                (6500, "חדרה"), (3574, "בית אל"), (7100, "אשקלון"), (1155, "מבקיעים"),
                                                (6100, "בני ברק"), (1191, "ברק"), (4000, "חיפה")])
g.execute("INSERT INTO chains(chain_id, name_he) VALUES(?, 'סופר ספיר')", (SAPIR,))
SP = "7290172900007"
for cid, sid, name, addr, city in [
        (SP, 55, "סופר-פארם ביג באר שבע", "חיל ההנדסה 2 באר שבע", "9000"),  # its street came back in Eilat
        (SP, 56, "סופר-פארם גרנד קניון", "טוביהו 125", "9000"),             # its street, in Be'er Sheva
        (SHUF, 76, "דיל אריאל", "רח' מוריה", "3570"),                        # "אריאל" came back as a Jerusalem street
        (SHUF, 77, "שופרסל קניון", "", ""),                                 # found by name only, no town: not placed
        ("7290696200003", 36, "חדרה צפוני", "unknown", "0"),                 # town by its name; the point is elsewhere
        ("7290696200003", 37, "בני ברק", "רבי עקיבא 1", "0"),                # town by its name ("ברק" is part of it)
        (SAPIR, 81, "סופר ספיר בית אל* ת.", "הראי\"ה 34", "0"),               # "ספיר" (a moshav) is the brand here
        ("7290696200003", 34, "מבקיעים אשקלון", "", "0")]:                   # two towns named: can't tell
    g.execute("INSERT INTO stores(chain_id, store_id, name, address, city, store_type, price_full_ts) VALUES(?,?,?,?,?,1,1)",
              (cid, sid, name, addr, city))
for q, lat, lon in [("חיל ההנדסה 2 באר שבע, באר שבע", 29.5491, 34.9523), ("טוביהו 125, באר שבע", 31.2502, 34.7712),
                    ("רח' מוריה, אריאל", None, None), ("אריאל", 31.7901, 35.2033), ("שופרסל קניון", 32.07, 34.79),
                    ("צפוני", 31.756, 34.9913), ("חדרה צפוני", None, None),
                    ("רבי עקיבא 1, בני ברק", 32.0846, 34.8350), ("הראי\"ה 34, סופר ספיר בית אל* ת.", 31.9441, 35.2228),
                    ("מבקיעים אשקלון", 31.66, 34.58)]:
    g.execute("INSERT INTO geocache VALUES(?,?,?,0)", (q, lat, lon))
g.commit()
loc = geo.located(g)
town_pt = lambda code: (*pts[code], "town")
assert loc[(SP, 55)] == town_pt(9000), loc.get((SP, 55))          # not Eilat: Be'er Sheva's own point, town level
assert loc[(SP, 56)] == (31.2502, 34.7712, "exact")
assert loc[(SHUF, 76)] == town_pt(3570), loc.get((SHUF, 76))      # Ariel, not Jerusalem
assert (SHUF, 77) not in loc                                       # a name search alone is never a place
assert ("7290696200003", 36) not in loc                            # Hadera by its name, but the point isn't in Hadera
assert loc[("7290696200003", 37)][2] == "exact"                    # Bnei Brak by its name, and the point agrees
assert loc[(SAPIR, 81)][2] == "exact"                              # Beit El, the brand's "ספיר" left out
assert ("7290696200003", 34) not in loc                            # Mevo'im or Ashkelon: not guessed
assert geo.exact_stores(g) == {(SP, 56), ("7290696200003", 37), (SAPIR, 81)}
tn = {r[0]: r[1] for r in g.execute("SELECT code, name FROM towns")}
assert geo.store_town({"name": "וולט באקה", "address": "", "city": "0"}, tn, geo.town_index(tn)) == (None, None)
# a fresh answer from the map service: its address names the town, or it lies near the town's own point
assert geo.hit_ok({"lat": "29.5491", "lon": "34.9523", "address": {"city": "אילת"}}, 9000, tn) is False
assert geo.hit_ok({"lat": "31.25", "lon": "34.77", "address": {"city": "באר שבע"}}, 9000, tn) is True
assert geo.hit_ok({"lat": "32.95", "lon": "35.0", "address": {"city": "ירושלים"}}, 3000, tn) is True  # named: trusted
assert geo.hit_ok({"lat": "31.79", "lon": "35.2", "address": {"road": "אריאל"}}, 3570, tn) is False
# geocoding new stores: an answer outside the store's town is kept as a miss and the next spelling is asked
g.execute("INSERT INTO stores(chain_id, store_id, name, address, city, store_type, price_full_ts) "
          "VALUES('X', 1, 'סניף', 'הרצל 5', '4000', 1, 1), ('X', 2, 'וולט עכו', 'unknown', '0', 1, 1)")
g.commit()
answers = {"הרצל 5, חיפה": [{"lat": "31.0", "lon": "34.9", "address": {"city": "רחובות"}}],          # wrong town
           "הרצל 5, סניף": [{"lat": "32.80", "lon": "34.99", "address": {"city": "חיפה"}}]}         # Haifa
asked_q = []
geo.lookup = lambda q, limit=1: asked_q.append(q) or answers.get(q, [])
geo.towns = lambda con, fetch=False: {r[0]: r[1] for r in con.execute("SELECT code, name FROM towns")}
geo.run(g, budget=50, pause=0)
cache = {r[0]: r[1] for r in g.execute("SELECT q, lat FROM geocache")}
assert cache["הרצל 5, חיפה"] is None and cache["הרצל 5, סניף"] == 32.80, cache
assert "וולט עכו" not in asked_q and "עכו" not in asked_q  # no town known (עכו isn't on this list): never asked
assert geo.located(g)[("X", 1)] == (32.80, 34.99, "exact")
g.close()

# ---------------------------------------------------------------- through the server: map, cards, store card, hours
NOW = int(time.strftime("%Y%m%d%H%M", time.localtime(time.time() - 3600)))
OLD = int(time.strftime("%Y%m%d%H%M", time.localtime(time.time() - 5 * 86400)))
c = dbmod.connect(os.environ["BASKET_DB"])
server.con = lambda: c
c.executescript(f"""
INSERT INTO chains(chain_id, name_he) VALUES('{SAPIR}', 'סופר ספיר'), ('7290058159628', 'מעיין 2000');
INSERT INTO stores(chain_id, store_id, name, address, city, store_type, sub_chain, price_full_ts, promo_full_ts, last_price_ts) VALUES
  ('{SAPIR}', 190, 'סופר ספיר חולון', 'סוקולוב 48', 'unknown', 1, 'סופר ספיר', 1, {NOW}, {NOW}),
  ('{SAPIR}', 130, 'וולט ויצמן חולון', 'סוקולוב 48', '6600', 1, 'וולט', 1, {NOW}, {NOW}),
  ('{SAPIR}', 22, 'נטו קרית שמונה * ת.', 'unknown', '', 1, 'נטו חיסכון', 1, {NOW}, {NOW}),
  ('{SAPIR}', 3, 'מחסן אונליין שדרות', 'unknown', '0', 1, '', 1, {NOW}, {NOW}),
  ('{SAPIR}', 40, 'סופר ספיר ישן', 'הרצל 1', '6600', 1, 'סופר ספיר', 1, {OLD}, {OLD}),
  ('7290058159628', 38, 'מעיין 38', '', '', 1, '', 1, {NOW}, {NOW}),
  ('7290058159628', 39, 'מעיין 39', '', '', 1, '', 1, {NOW}, {NOW});
""")
HOME = {"lat": 32.0, "lon": 34.78, "label": "בית"}
COORDS = {(SAPIR, 190): (32.01, 34.78), (SAPIR, 130): (32.01, 34.78), (SAPIR, 22): (32.02, 34.79),
          (SAPIR, 3): (32.03, 34.78), (SAPIR, 40): (32.0, 34.79),
          ("7290058159628", 38): (32.015, 34.775), ("7290058159628", 39): (32.015, 34.775)}
EXACT = {(SAPIR, 190), (SAPIR, 130), ("7290058159628", 38), ("7290058159628", 39)}  # 22, 3, 40: their town's point
server.geo.store_coords = lambda con: COORDS
server.geo.exact_stores = lambda con: EXACT
c.execute("UPDATE users SET prefs=? WHERE id=1", (json.dumps({"home": HOME, "max_km": 10}),))
c.commit()
server._user.set(auth.user_row(c, 1))
routes = []
server.geo.route = lambda a, b: routes.append((a, b)) or {"coords": [[a[1], a[0]], [b[1], b[0]]], "minutes": 3, "km": 1.4}
asked = []
server.geo.drive_minutes = lambda home, dests, key=None: asked.extend(dests) or {d: 4 for d in dests}
server._osm_area = lambda home, km: ([[32.01, 34.78, geo._letters("סופר ספיר"), "Su-Th 07:00-23:00", "סופר ספיר"],
                                      [32.015, 34.775, geo._letters("מעיין 2000"), "Mo-Su 08:00-20:00", "מעיין"]], True)

# the map: shops you can walk into with current prices, each with how precisely it is placed
near = server.stores_near()["stores"]
assert sorted(s["name"] for s in near) == ["מעיין 38", "מעיין 39", "נטו קרית שמונה * ת.", "סופר ספיר חולון"], near
by = {s["name"]: s for s in near}
assert by["סופר ספיר חולון"]["precision"] == "exact" and by["נטו קרית שמונה * ת."]["precision"] == "town"
assert by["נטו קרית שמונה * ת."]["brand"] == "נטו חיסכון" and by["סופר ספיר חולון"]["brand"] is None  # = chain's name
assert by["סופר ספיר חולון"]["city"] == ""                       # "unknown" is no town
# cards' "near" price: the same doors
assert set(server._near_stores(c)) == {(SAPIR, 190), (SAPIR, 22), ("7290058159628", 38), ("7290058159628", 39)}

# the store card: a branch at its address gets its route; one at its town's point only how far its town is
card = server.store_info(SAPIR, 190)
assert card["precision"] == "exact" and card["route"]["km"] == 1.4 and card["address"] == "סוקולוב 48" and card["city"] == ""
n = len(routes)
card = server.store_info(SAPIR, 22)
assert card["precision"] == "town" and card["route"] is None and card["km"] == round(geo.km(32.0, 34.78, 32.02, 34.79), 1)
assert len(routes) == n and card["address"] == "" and card["brand"] == "נטו חיסכון"
# drive times only to branches at their own address
r = server.drive({"home": HOME, "stores": [[SAPIR, 190], [SAPIR, 22]]})
assert r["times"] == {f"{SAPIR}:190": 4} and asked == [(32.01, 34.78)], (r, asked)
# opening hours: only for a branch at its own address that no other branch of its chain shares (its Wolt listing at
# the same address is the same shop); never for a town-level point
h = server.store_hours(SAPIR, 190)
assert h["hours"] == "Su-Th 07:00-23:00" and h["placed"] is True, h
assert server.store_hours(SAPIR, 22)["hours"] is None and server.store_hours(SAPIR, 22)["placed"] is False
assert server.store_hours("7290058159628", 38)["hours"] is None  # 38 and 39 share a point: whose hours?

# compare: the Wolt listing and the warehouse are neither ranked nor branches; precision and brand come along
P = "7290000888880"
c.executescript(f"""
INSERT INTO products(key, code, name, is_weighted, scope, store_count, chain_count, min_price, max_price, norm, median) VALUES
  ('{P}', '{P}', 'פסטה', 0, 'global', 3, 2, 1, 10, 'פסטה', 5);
INSERT INTO prices(key, chain_id, store_id, price, ts) VALUES ('{P}', '{SAPIR}', 190, 5, 100), ('{P}', '{SAPIR}', 130, 4, 100),
  ('{P}', '{SAPIR}', 22, 6, 100), ('{P}', '{SAPIR}', 3, 5, 100);
INSERT INTO item_names(key, chain_id, name, ts) VALUES ('{P}', '{SAPIR}', 'פסטה', {int(time.strftime("%Y%m%d%H%M"))});
""")
res = server.compare({"items": [{"key": P, "qty": 1}], "prefs": {}})
assert [(b["store_id"], b["precision"], b["brand"]) for b in res["branches"]] == [(190, "exact", None), (22, "town", "נטו חיסכון")], \
    res["branches"]
assert res["chains"][0]["in_store"]["store_id"] == 190 and res["chains"][0]["in_store"]["precision"] == "exact"
# the warehouse is Super Sapir's online store; the Wolt listing (dearer, Wolt's) is not
assert res["chains"][0]["online"]["store_id"] == 3, res["chains"][0]["online"]
print("ok")
