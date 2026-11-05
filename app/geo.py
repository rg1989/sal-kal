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


def store_coords(con):
    """(chain_id, store_id) -> (lat, lon) using the most precise cached query."""
    cache = {r[0]: (r[1], r[2]) for r in con.execute("SELECT q, lat, lon FROM geocache WHERE lat IS NOT NULL")}
    tn = {r[0]: r[1] for r in con.execute("SELECT code, name FROM towns")} if _has_towns(con) else {}
    out = {}
    for s in con.execute("SELECT chain_id, store_id, name, address, city FROM stores"):
        for q, _ in queries(s, tn):
            if q in cache:
                out[(s["chain_id"], s["store_id"])] = cache[q]
                break
    return out


def exact_stores(con):
    """The branches placed at their own address (a street address, or the shop itself found by name), not at the
    centre of their town. Many chains publish only a town, and a town-level point is a few km off: fine for "within
    5 km of home", wrong for "on the route". A point counts as a town when a town's own query found it, or when 3+
    branches at different addresses share it."""
    cache = {r[0]: (r[1], r[2]) for r in con.execute("SELECT q, lat, lon FROM geocache WHERE lat IS NOT NULL")}
    tn = {r[0]: r[1] for r in con.execute("SELECT code, name FROM towns")} if _has_towns(con) else {}
    towns_at, found, shared = set(), {}, {}
    for s in con.execute("SELECT chain_id, store_id, name, address, city FROM stores"):
        qs = queries(s, tn)
        name = (s["name"] or "").strip()
        for q, kind in qs:
            if kind != "street" and q != name and q in cache:  # the town itself, or a tail of the name that is one
                towns_at.add(cache[q])
        for q, _ in qs:
            if q in cache:  # the most precise query that found something (as store_coords)
                found[(s["chain_id"], s["store_id"])] = cache[q]
                shared.setdefault(cache[q], set()).add(((s["address"] or "").strip(), s["city"]))
                break
    # (a street the map service doesn't know comes back as its town: the same point as the town's own query)
    towns_at |= {pt for pt, addrs in shared.items() if len(addrs) >= 3}
    return {sk for sk, pt in found.items() if pt not in towns_at}


def _has_towns(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name='towns'").fetchone() is not None


def run(con, budget=1500, pause=1.1):
    """Town-level pass for every store first (fast, few distinct towns), then street level."""
    tn = towns(con, fetch=True)
    cache = {r[0]: r[1] is not None for r in con.execute("SELECT q, lat FROM geocache")}
    stores = con.execute("SELECT name, address, city FROM stores").fetchall()
    sent, pending = 0, []

    def flush():
        con.executemany("INSERT OR REPLACE INTO geocache VALUES(?,?,?,?)", pending)
        con.commit()
        pending.clear()

    try:
        for street in (False, True):
            for s in stores:
                qs = [(q, k) for q, k in queries(s, tn) if (k == "street") == street]
                if any(cache.get(q) for q, _ in qs):
                    continue
                for q, kind in qs:
                    if q in cache:
                        continue
                    if sent >= budget:
                        return sent
                    try:
                        hit = lookup(q)
                    except Exception as e:  # offline or throttled: try again next run
                        log.warning("lookup failed (%s), stopping this run", e)
                        return sent
                    sent += 1
                    ok = hit and (kind != "place" or hit[0].get("category") in PLACE)
                    lat, lon = (float(hit[0]["lat"]), float(hit[0]["lon"])) if ok else (None, None)
                    cache[q] = ok
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
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    con = dbmod.connect(a.db)
    n = run(con, a.budget)
    log.info("geocoded %d queries; %d stores located", n, len(store_coords(con)))


if __name__ == "__main__":
    main()
