"""Store locations: geocode addresses with OpenStreetMap Nominatim and measure distances.

Nominatim's policy is max 1 request/second with an identifying User-Agent, so every
lookup is cached forever in `geocache` and the background run is budgeted.
Many chains publish City as a numeric CBS settlement code; those are translated with
the official settlement list from data.gov.il (fetched once into `towns`).
Run: python -m app.geo [--db ...] [--budget 1500]
"""
import argparse
import json
import logging
import math
import os
import re
import time
import urllib.parse
import urllib.request

from . import db as dbmod
from . import guard

log = logging.getLogger("geo")
UA = "Sal-basket-prices/1.0 (self-hosted grocery price comparison)"
URL = "https://nominatim.openstreetmap.org/search"
TOWNS_URL = ("https://data.gov.il/api/3/action/datastore_search"
             "?resource_id=5c78e9fa-c2e2-4771-93ff-7f400a12f7ba&limit=5000")
PLACE = ("place", "boundary")  # Nominatim categories that are a town / neighbourhood, not a shop or street


def lookup(q, limit=1):
    params = urllib.parse.urlencode({"q": q, "format": "jsonv2", "countrycodes": "il", "limit": limit,
                                     "accept-language": "he", "addressdetails": 1})
    req = urllib.request.Request(f"{URL}?{params}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def label(hit):
    """Short human address: "הרצל 10, רמת גן"."""
    a = hit.get("address") or {}
    street = " ".join(p for p in (a.get("road"), a.get("house_number")) if p)
    town = a.get("city") or a.get("town") or a.get("village") or a.get("municipality") or ""
    parts = [p for p in (street or a.get("suburb") or a.get("neighbourhood"), town) if p]
    return ", ".join(parts) if parts else ", ".join(hit["display_name"].split(", ")[:3])


def km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def towns(con, fetch=False):
    """CBS settlement code -> name. Fetched from data.gov.il once (fetch=True), then read from the DB."""
    con.execute("CREATE TABLE IF NOT EXISTS towns(code INTEGER PRIMARY KEY, name TEXT)")
    if fetch and not con.execute("SELECT 1 FROM towns LIMIT 1").fetchone():
        try:
            req = urllib.request.Request(TOWNS_URL, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                recs = json.load(r)["result"]["records"]
            con.executemany("INSERT OR REPLACE INTO towns VALUES(?,?)",
                            [(int(x["סמל_ישוב"]), x["שם_ישוב"].strip()) for x in recs
                             if str(x.get("סמל_ישוב", "")).strip().isdigit()])
            con.commit()
        except Exception as e:
            log.warning("couldn't fetch the settlement list (%s); numeric city codes stay unknown", e)
    return {r[0]: r[1] for r in con.execute("SELECT code, name FROM towns")}


def queries(store, town_names=None):
    """Geocoding queries for a store, most precise first, as (query, kind).

    kind: "street" (address), "any" (town or full store name), "place" (a tail of the
    store name like "רחובות" from "כל בו חצי חינם - רחובות" — accepted only if it is a town).
    """
    addr = (store["address"] or "").strip()
    city = (store["city"] or "").strip()
    name = (store["name"] or "").strip()
    if addr.lower() in ("unknown", "0", "-"):
        addr = ""
    if city.isdigit():
        city = (town_names or {}).get(int(city), "")
    words = [w for w in re.split(r"[\s\-–,()]+", name) if w]
    tails = [" ".join(words[-2:]) if len(words) > 2 else "", words[-1] if len(words) > 1 else ""]
    tails = [t for t in tails if len(t) > 1 and not t.isdigit()]
    out = []
    if addr and city:
        out.append((f"{addr}, {city}", "street"))
    for t in [name] + tails[:1]:  # a lone last word ("גן" of "רמת גן") is too vague next to a street
        if addr and t and t != city:
            out.append((f"{addr}, {t}", "street"))
    out += [(city, "any"), (name, "any")] + [(t, "place") for t in tails]  # ponytail: town-level, a few km off
    seen, res = set(), []
    for q, kind in out:
        if q and q not in seen:
            seen.add(q)
            res.append((q, kind))
    return res


# ---------------------------------------------------------------- is the point in the store's own town?
# The map service answers a free-text query with its best guess anywhere in the country: "חיל ההנדסה 2 באר שבע"
# came back in Eilat, the town "אריאל" as Ariel Street in Jerusalem. A point counts only when it lies in the store's
# town (its CBS code, or the official town its City/name names), checked against that town's own point from the
# official list. A store whose town can't be told, or whose every point is elsewhere, isn't placed by a guess: it
# goes at its town's own point (town level), or nowhere.
TOWN_KM = 6         # a branch may be this far from its town's own point
BIG_KM, BIG_TOWNS = 8, {3000, 5000, 4000, 8300, 9000, 70, 7400, 7900, 7100, 1200, 2600, 6500, 2610}  # the widest
# cities (Jerusalem's Neve Yaakov is 7.6 km from its point; Herzliya's Weizmann St is 9.8 km from Tel Aviv's)
TOWN_POINTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "town-points.json")
POINTS_RID = "e9701dcb-9f1c-43bb-bd44-eb380ade542f"  # data.gov.il: settlements in four languages, with ITM X/Y
_points = None
_located = {}       # database + data signature -> {(chain, store): (lat, lon, precision)}


def town_points():
    """CBS town code -> (lat, lon): each town's own point, from the official list bundled in app/data."""
    global _points
    if _points is None:
        try:
            with open(TOWN_POINTS, encoding="utf-8") as f:
                _points = {int(k): tuple(v) for k, v in json.load(f)["points"].items()}
        except (OSError, ValueError, KeyError) as e:
            log.warning("town points unavailable (%s): stores can't be checked against their towns", e)
            _points = {}
    return _points


def fetch_town_points(path=TOWN_POINTS):
    """Rewrites the bundled town points from data.gov.il (a developer's step: python -m app.geo --town-points)."""
    from .places import _itm_to_wgs84  # places imports this module
    req = urllib.request.Request(f"https://data.gov.il/api/3/action/datastore_search?resource_id={POINTS_RID}&limit=5000",
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        recs = json.load(r)["result"]["records"]
    pts = {}
    for x in recs:
        if x.get("X") and x.get("Y") and str(x.get("symbol_number", "")).isdigit():
            lat, lon = _itm_to_wgs84(float(x["X"]), float(x["Y"]))
            if 29.3 <= lat <= 33.5 and 34.2 <= lon <= 35.95:
                pts[str(int(x["symbol_number"]))] = [round(lat, 5), round(lon, 5)]
    out = {"about": "Each town's own point (CBS settlement code -> lat, lon), to check that a geocoded store lies in its "
                    "town. Source: data.gov.il, \"יישובים בשפות עברית, אנגלית, ערבית, רוסית\" (resource "
                    f"{POINTS_RID}), ITM X/Y converted to WGS84. Refresh: python -m app.geo --town-points",
           "made": time.strftime("%Y-%m-%d"), "points": dict(sorted(pts.items(), key=lambda kv: int(kv[0])))}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    return len(pts)


def _fold(s):
    from .places import norm  # the same folding as the address picker: קריית = קרית, no quotes or dashes
    return norm(s)


# town names that are also everyday words in store names ("אזור תעשיה" is an industrial zone, not Azor)
GENERIC = {"אזור", "רחוב", "מרכז", "צומת", "כרמל", "אלון", "עזר", "ספיר", "שדה", "גבעה", "נווה", "מגדל", "כנרת"}
ABBREV = {"ת א": 5000, "ב ש": 9000, "פ ת": 7900}  # ת"א, ב"ש, פ"ת as chains write them


def town_index(tn):
    """Folded town name -> CBS code: the official names, OpenStreetMap's usual spellings, and each part of a joined
    name ("תל אביב - יפו" -> "יפו", "פרדס חנה-כרכור" -> "כרכור") unless that part is a word of another town's
    name too ("חצור" of "חצור-אשדוד" is also חצור הגלילית)."""
    exact, parts = {}, {}
    for code, name in tn.items():
        name = (name or "").strip()
        if not name:
            continue
        for v in (name, name.replace("קרית", "קריית"), re.sub(r"\(.*?\)", " ", name)):
            if _fold(v):
                exact.setdefault(_fold(v), code)
        for p in re.split(r"\s+-\s+|-", name):
            p = _fold(p)
            if len(p) >= 3 and p != _fold(name):
                parts.setdefault(p, set()).add(code)
    words = {}
    for n, code in exact.items():
        for i in range(len(n.split())):
            for j in range(i + 1, len(n.split()) + 1):
                words.setdefault(" ".join(n.split()[i:j]), set()).add(code)
    out = dict(exact)
    for p, codes in parts.items():
        if p not in out and len(codes) == 1 and words.get(p, set()) <= codes:
            out[p] = next(iter(codes))
    return out


def store_town(store, tn, index, brand=""):
    """(CBS code of a store's town, how it is known): "city" = its City field (a code or an official name); "name" =
    the one official town its name names (the chain's own brand words left out; nothing when it names two). The
    address is never read for this: street names are town names too ("ירושלים 1" in Kiryat Motzkin).
    (None, None) when it can't be told."""
    city = (store["city"] or "").strip()
    if city.isdigit():  # a CBS code; "0" or a code not on the list: told by the name below, if at all
        if int(city) in tn:
            return int(city), "city"
        city = ""
    if city and city.lower() not in ("unknown", "-", "לא ידוע"):
        hit = index.get(_fold(city))
        if hit is not None:
            return hit, "city"
    name = f" {' '.join(re.sub(r'[^\w ]', ' ', _fold(store['name'])).split())} "  # "בית אל*" too
    for w in _fold(brand).split():
        name = name.replace(f" {w} ", " ")
    hits = {n: code for n, code in index.items() if n not in GENERIC and len(n) >= 3 and f" {n} " in name}
    hits.update({n: code for n, code in ABBREV.items() if f" {n} " in name})
    # a name inside a longer one that matched is part of it ("ברק" of "בני ברק")
    codes = {code for n, code in hits.items() if not any(m != n and f" {n} " in f" {m} " for m in hits)}
    return (next(iter(codes)), "name") if len(codes) == 1 else (None, None)


def in_town(pt, code):
    """The point lies within TOWN_KM (BIG_KM for the widest cities) of the town's own point. False when the town or
    its point isn't known."""
    ref = town_points().get(code) if code is not None else None
    return bool(ref and pt and km(pt[0], pt[1], ref[0], ref[1]) <= (BIG_KM if code in BIG_TOWNS else TOWN_KM))


def hit_ok(hit, code, tn):
    """A fresh answer from the map service is kept only in the store's town: its address names that town, or failing
    that it lies near the town's own point."""
    a = hit.get("address") or {}
    names = {_fold(a.get(k)) for k in ("city", "town", "village", "municipality") if a.get(k)}
    if code is not None and tn.get(code):
        official = _fold(tn[code])
        if any(n and (n == official or n in official.split() or official in n) for n in names):
            return True
    try:
        return in_town((float(hit["lat"]), float(hit["lon"])), code)
    except (KeyError, TypeError, ValueError):
        return False


def _sig(con):
    db = con.execute("PRAGMA database_list").fetchone()[2]
    g = con.execute("SELECT count(*), max(at), total(lat) FROM geocache").fetchone()
    s = con.execute("SELECT count(*), total(store_id), total(length(name) + length(address) + length(city)) "
                    "FROM stores").fetchone()
    t = con.execute("SELECT count(*) FROM towns").fetchone() if _has_towns(con) else (0,)
    return (db, tuple(g), tuple(s), tuple(t), len(town_points()))


def _brands(con):
    """Chain id -> its brand words (the Hebrew name in its files and on its signs), left out when a store's name is
    read for its town ("סופר ספיר בית אל": ספיר is a moshav too)."""
    from .chains import BRAND_HE, chain_name
    he = {r[0]: r[1] or "" for r in con.execute("SELECT chain_id, name_he FROM chains")} \
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='chains'").fetchone() else {}
    ids = set(he) | {r[0] for r in con.execute("SELECT DISTINCT chain_id FROM stores")}
    return {cid: f"{he.get(cid, '')} {BRAND_HE.get(chain_name(cid), '')}" for cid in ids}


def located(con):
    """(chain_id, store_id) -> (lat, lon, precision): "exact" = found at its own street address in its own town;
    "town" = its town's point (the town's own query, a tail of its name that is a town, a search by the store's name,
    an address the map service only knows as its town, or the official town point when no point found fits). Points
    outside the store's town are never used, nor any point for a store whose town can't be told."""
    key = _sig(con)
    hit = _located.get(key)
    if hit is not None:
        return hit
    cache = {r[0]: (r[1], r[2]) for r in con.execute("SELECT q, lat, lon FROM geocache WHERE lat IS NOT NULL")}
    tn = {r[0]: r[1] for r in con.execute("SELECT code, name FROM towns")} if _has_towns(con) else {}
    index, pts, brands = town_index(tn), town_points(), _brands(con)
    towns_at, found, shared = set(), {}, {}
    for s in con.execute("SELECT chain_id, store_id, name, address, city FROM stores"):
        sk = (s["chain_id"], s["store_id"])
        code, how = store_town(s, tn, index, brands.get(s["chain_id"], ""))
        qs = queries(s, tn)
        name = (s["name"] or "").strip()
        for q, kind in qs:
            if kind != "street" and q != name and q in cache and in_town(cache[q], code):  # the town, or a tail that is one
                towns_at.add(cache[q])
        for q, kind in qs:  # the most precise query that found a point in the store's town
            if q in cache and in_town(cache[q], code):
                found[sk] = (cache[q], "street" if kind == "street" else "town")
                if kind == "street":
                    shared.setdefault(cache[q], set()).add(((s["address"] or "").strip(), s["city"]))
                break
        else:
            # nothing found fits: the town's own point, when the store's own City says the town (a town told only by
            # the store's name needs the map service to agree with it)
            if how == "city" and code in pts:
                found[sk] = (pts[code], "town")
    # (a street the map service doesn't know comes back as its town: the same point as the town's own query)
    towns_at |= {pt for pt, addrs in shared.items() if len(addrs) >= 3}
    out = {sk: (pt[0], pt[1], "exact" if how == "street" and pt not in towns_at else "town")
           for sk, (pt, how) in found.items()}
    if len(_located) > 8:
        _located.clear()
    _located[key] = out
    return out


def store_coords(con):
    """(chain_id, store_id) -> (lat, lon): every placed store, at its address or its town's point (see located)."""
    return {sk: (v[0], v[1]) for sk, v in located(con).items()}


def exact_stores(con):
    """The branches placed at their own street address in their own town, not at a town's point. Many chains publish
    only a town, and a town-level point is a few km off: fine for "within 5 km of home", wrong for "on the route",
    for a road distance or for a route on the map. A search by the store's name never counts as its address."""
    return {sk for sk, v in located(con).items() if v[2] == "exact"}


def _has_towns(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name='towns'").fetchone() is not None


def run(con, budget=1500, pause=1.1):
    """Town-level pass for every store first (fast, few distinct towns), then street level. A store is done once a
    point in its own town is cached; an answer elsewhere is kept as a miss, so the next spelling is asked instead.
    Stores whose town can't be told aren't asked about: no answer could be checked."""
    tn = towns(con, fetch=True)
    index, brands = town_index(tn), _brands(con)
    cache = {r[0]: (r[1], r[2]) if r[1] is not None else None for r in con.execute("SELECT q, lat, lon FROM geocache")}
    stores = con.execute("SELECT chain_id, name, address, city FROM stores").fetchall()
    sent, pending = 0, []

    def flush():
        con.executemany("INSERT OR REPLACE INTO geocache VALUES(?,?,?,?)", pending)
        con.commit()
        pending.clear()

    try:
        for street in (False, True):
            for s in stores:
                code, _ = store_town(s, tn, index, brands.get(s["chain_id"], ""))
                if code is None or code not in town_points():
                    continue
                qs = [(q, k) for q, k in queries(s, tn) if (k == "street") == street]
                if any(cache.get(q) and in_town(cache[q], code) for q, _ in qs):
                    continue
                for q, kind in qs:
                    if q in cache:
                        continue
                    if sent >= budget:
                        return sent
                    try:
                        hits = lookup(q, limit=3)
                    except Exception as e:  # offline or throttled: try again next run
                        log.warning("lookup failed (%s), stopping this run", e)
                        return sent
                    sent += 1
                    ok = next((h for h in hits or [] if (kind != "place" or h.get("category") in PLACE)
                               and hit_ok(h, code, tn)), None)
                    lat, lon = (float(ok["lat"]), float(ok["lon"])) if ok else (None, None)
                    cache[q] = (lat, lon) if ok else None
                    pending.append((q, lat, lon, int(time.time())))
                    if len(pending) >= 10:  # batch writes: the indexer may hold the write lock
                        flush()
                    time.sleep(pause)
                    if ok:
                        break
        return sent
    finally:
        flush()


# ---------------------------------------------------------------- drive times
OSRM = "https://router.project-osrm.org/table/v1/driving/"
GOOGLE = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
_drive_cache = {}  # (live, home, dest) -> (minutes, expires)


def _osrm(home, dests):
    """Typical drive minutes (no live traffic) from the free OSRM demo server."""
    pts = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in [home] + dests)
    req = urllib.request.Request(f"{OSRM}{pts}?sources=0", headers={"User-Agent": UA})
    with guard.outside("osrm"), urllib.request.urlopen(req, timeout=20) as r:
        row = json.load(r)["durations"][0][1:]
    return [round(s / 60) if s is not None else None for s in row]


def _google(home, dests, key):
    """Drive minutes in current traffic (Google Routes API, needs the user's key)."""
    wp = lambda p: {"waypoint": {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}}
    body = json.dumps({"origins": [wp(home)], "destinations": [wp(d) for d in dests],
                       "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE"}).encode()
    req = urllib.request.Request(GOOGLE, data=body, headers={
        "Content-Type": "application/json", "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "originIndex,destinationIndex,duration,condition"})
    with guard.outside("google"), urllib.request.urlopen(req, timeout=20) as r:  # billed per use: a daily cap
        els = json.load(r)
    out = [None] * len(dests)
    for e in els:
        if e.get("condition") == "ROUTE_EXISTS" and e.get("duration"):
            out[e["destinationIndex"]] = round(int(e["duration"].rstrip("s")) / 60)
    return out


OSRM_ROUTE = "https://router.project-osrm.org/route/v1/driving/"
_route_cache = {}


def route(home, dest):
    """The driving route home -> dest as GeoJSON coordinates, with typical minutes and road km (OSRM demo server)."""
    k = (round(home[0], 5), round(home[1], 5), round(dest[0], 5), round(dest[1], 5))
    hit = _route_cache.get(k)
    if hit and hit[1] > time.time():
        return hit[0]
    pts = f"{home[1]:.5f},{home[0]:.5f};{dest[1]:.5f},{dest[0]:.5f}"
    req = urllib.request.Request(f"{OSRM_ROUTE}{pts}?overview=full&geometries=geojson", headers={"User-Agent": UA})
    with guard.outside("osrm"), urllib.request.urlopen(req, timeout=20) as r:
        rt = json.load(r)["routes"][0]
    out = {"coords": [[round(x, 5), round(y, 5)] for x, y in rt["geometry"]["coordinates"]],
           "minutes": round(rt["duration"] / 60), "km": round(rt["distance"] / 1000, 1)}
    if len(_route_cache) > 500:
        _route_cache.clear()
    _route_cache[k] = (out, time.time() + 7 * 86400)
    return out


OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter"]
_letters = lambda t: re.sub(r"[^a-zא-ת0-9]", "", (t or "").lower())
NAME_TAGS = ("name", "name:he", "name:en", "brand", "brand:he", "brand:en", "operator")


def osm_shops(lat, lon, radius_m):
    """Food shops with opening hours around (lat, lon) from OpenStreetMap, in one Overpass query:
    [[lat, lon, letters of every name/brand tag, opening_hours, name], ...]."""
    q = (f'[out:json][timeout:90];nwr(around:{int(radius_m)},{lat:.5f},{lon:.5f})'
         '[shop~"^(supermarket|convenience|chemist|department_store|wholesale|general|greengrocer|deli)$"]'
         '[opening_hours];out tags center;')
    with guard.outside("overpass"):  # a site-wide budget: nobody can get this address banned there
        return _overpass(q)


def _overpass(q):
    err = None
    for url in OVERPASS:  # public servers; the first is often busy
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:  # a busy server queues for ages: give up, retry later
                els = json.load(r)["elements"]
            break
        except Exception as e:
            err = e
    else:
        raise err
    out = []
    for e in els:
        t, p = e.get("tags", {}), e.get("center") or e
        out.append([p["lat"], p["lon"], _letters(" ".join(t.get(k, "") for k in NAME_TAGS)), t["opening_hours"], t.get("name")])
    return out


def match_hours(shops, lat, lon, brands, within_km=0.3):
    """The nearest shop within 300 m of a branch whose name or brand matches the chain: (opening_hours, name).
    Only a matching shop counts — never another store's hours."""
    want = [_letters(b) for b in brands if _letters(b)]
    hits = [(km(lat, lon, s[0], s[1]), s) for s in shops if any(w in s[2] for w in want)]
    hits = [h for h in hits if h[0] <= within_km]
    if not hits:
        return None, None
    best = min(hits, key=lambda h: h[0])[1]
    return best[3], best[4]


DAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]  # the Israeli week starts on Sunday


def parse_hours(s):
    """OSM opening_hours -> 7 strings, Sunday first ("07:00–23:00", "Closed"). Handles the common "days times; …"
    form as people actually type it ("Mo, Tu, Su 08:00-22:00", "Fr 7:00-14:00", "Sa,PH off"). None for anything
    fancier; the page then shows the original text."""
    s = (s or "").strip()
    if not s:
        return None
    if s == "24/7":
        return ["Open 24 hours"] * 7
    span = r"\d{1,2}:\d\d\s*-\s*\d{1,2}:\d\d"
    week, seen = ["Closed"] * 7, False
    for rule in (r.strip() for r in s.split(";")):
        if not rule:
            continue
        m = re.fullmatch(rf"([A-Za-z ,\-]+?)\s+(off|closed|{span}(?:\s*,\s*{span})*)", rule)
        if not m:
            return None
        parts = [x.strip() for x in m.group(1).split(",") if x.strip() and x.strip() != "PH"]  # public holidays: not shown
        if not parts:
            continue
        t = m.group(2)
        val = "Closed" if t in ("off", "closed") else ", ".join(
            "–".join(f"{int(h):02d}:{mm}" for h, mm in (x.split(":") for x in r.split("-")))
            for r in re.sub(r"\s", "", t).split(","))
        for part in parts:
            a, _, b = part.partition("-")
            if a not in DAYS or (b and b not in DAYS):
                return None
            i, j = DAYS.index(a), DAYS.index(b or a)
            for k in range((j - i) % 7 + 1):
                week[(i + k) % 7] = val
        seen = True
    return week if seen else None


def drive_minutes(home, dests, key=None):
    """Minutes by car from home to each (lat, lon). Live traffic with a Google key, else typical times."""
    live, now = bool(key), time.time()
    hk = (round(home[0], 5), round(home[1], 5))
    out, todo = {}, []
    for d in dict.fromkeys(dests):
        hit = _drive_cache.get((live, hk, d))
        if hit and hit[1] > now:
            out[d] = hit[0]
        else:
            todo.append(d)
    for i in range(0, len(todo), 25):
        chunk = todo[i:i + 25]
        try:
            try:
                mins = _google(home, chunk, key) if live else _osrm(home, chunk)
            except guard.Busy:
                if not live:
                    raise
                mins = _osrm(home, chunk)  # the day's live-traffic lookups are used up: typical times
            ttl = 600 if live else 7 * 86400  # traffic changes; typical times don't
        except Exception as e:
            log.warning("drive times unavailable (%s)", e)
            mins, ttl = [None] * len(chunk), 60
        for d, m in zip(chunk, mins):
            out[d] = m
            _drive_cache[(live, hk, d)] = (m, now + ttl)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--budget", type=int, default=1500)
    ap.add_argument("--town-points", action="store_true", help="refresh app/data/town-points.json from data.gov.il")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.town_points:
        log.info("%d town points written to %s", fetch_town_points(), TOWN_POINTS)
        return
    con = dbmod.connect(a.db)
    n = run(con, a.budget)
    log.info("geocoded %d queries; %d stores located", n, len(store_coords(con)))


if __name__ == "__main__":
    main()
