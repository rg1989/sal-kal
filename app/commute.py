"""On my way: shop along the trip between home and work instead of around home.

The trip is a road route by car, bike or on foot (OSRM: the project's demo server for cars, FOSSGIS's
routing.openstreetmap.de for bikes and walking). A branch is "on your way" when it is within `detour_km` of that
route; it gets its distance off the route (`off_km`) and how far along it is from home (`at_km`).

A plan spreads the cart over 1..MAX_STOPS of those branches, each product bought where it is cheapest among them.
Only confirmed lines count (engine status "ok"), as everywhere: a plan "has everything" only when every compared
product has a confirmed price at one of its stops, and every stop is the cheaper source of something (else it is
not a stop). Each extra stop must save at least MIN_SAVE, or the plan with fewer stops is the answer.
"""
import json
import math
import sys
import time
import urllib.request

from . import geo, guard

PROFILES = {  # OSRM route services; the FOSSGIS ones keep "driving" in the path whatever the profile
    "car": "https://router.project-osrm.org/route/v1/driving/",
    "bike": "https://routing.openstreetmap.de/routed-bike/route/v1/driving/",
    "foot": "https://routing.openstreetmap.de/routed-foot/route/v1/driving/",
}
TRAVEL = tuple(PROFILES)
# km off the route you may pick, by way of travel (a walk takes a short step aside, a car a few streets), and the default
DETOURS = {"car": (0.2, 0.5, 1.0, 2.0, 3.0), "bike": (0.2, 0.3, 0.5, 1.0), "foot": (0.1, 0.15, 0.2)}
DETOUR = {"car": 1.0, "bike": 0.5, "foot": 0.2}
MAX_STOPS = 5
MIN_SAVE = 1.0  # ₪: an extra stop that saves less than this isn't worth stopping for
SHORTLIST = 60  # branches the plan search looks at
NO = 10 ** 12   # agorot: no confirmed line at that store

_paths = {}  # (travel, points) -> (route, expires); road routes only
_down = {}   # (travel, points) -> until when the routing service is left alone for that trip


def detour_km(travel, km):
    """How far off the route a branch may be for this way of travel: the saved choice when it is one of that way's
    choices, the nearest choice when it isn't (a 500 m pick from the car doesn't stretch a walk), else the default."""
    opts = DETOURS.get(travel) or DETOURS["car"]
    try:
        km = float(km)
    except (TypeError, ValueError):
        return DETOUR.get(travel, 1.0)
    return min(opts, key=lambda o: (abs(o - km), o)) if km == km else DETOUR.get(travel, 1.0)


def _key(points):
    return tuple((round(a, 5), round(b, 5)) for a, b in points)


def route(points, travel="car"):
    """The road route through `points` [(lat, lon), ...] in order: {"coords": [[lon, lat], ...], "minutes", "km",
    "legs": [{"minutes", "km"}, ...]}. Cached for a week (typical times, no live traffic). Raises when the service
    can't be reached."""
    k = (travel, _key(points))
    hit = _paths.get(k)
    if hit and hit[1] > time.time():
        return hit[0]
    pts = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in k[1])
    url = f"{PROFILES.get(travel, PROFILES['car'])}{pts}?overview=full&geometries=geojson"
    with guard.outside("osrm" if travel not in ("bike", "foot") else "fossgis"), \
            urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": geo.UA}), timeout=20) as r:
        rt = json.load(r)["routes"][0]
    out = {"coords": [[round(x, 5), round(y, 5)] for x, y in rt["geometry"]["coordinates"]],
           "minutes": round(rt["duration"] / 60), "km": round(rt["distance"] / 1000, 1),
           "legs": [{"minutes": round(l["duration"] / 60), "km": round(l["distance"] / 1000, 1)} for l in rt.get("legs", [])]}
    if len(_paths) > 500:
        _paths.clear()
    _paths[k] = (out, time.time() + 7 * 86400)
    return out


def path(home, work, travel="car"):
    """Home -> work. When the routing service is down: the straight line, marked straight=True (the service is tried
    again a minute later; the line is never kept as the trip's route)."""
    a, b = (home["lat"], home["lon"]), (work["lat"], work["lon"])
    k = (travel, _key([a, b]))
    if _down.get(k, 0) <= time.time():
        try:
            return {**route([a, b], travel), "straight": False}
        except Exception as e:  # offline or busy
            print("route unavailable:", e, file=sys.stderr)
            if len(_down) > 500:
                _down.clear()
            _down[k] = time.time() + 60
    return {"coords": [[a[1], a[0]], [b[1], b[0]]], "minutes": None, "km": round(geo.km(*a, *b), 1), "legs": [],
            "straight": True}


def _plane(lat0):
    """Local flat coordinates in km around latitude lat0 (good to a few metres over a city or two)."""
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.574
    return lambda lat, lon: (lon * kx, lat * ky)


def corridor(coords, points, width_km):
    """Which of `points` {key: (lat, lon)} lie within width_km of the route `coords` [[lon, lat], ...]:
    {key: (off_km, at_km)}, off = distance from the route, at = distance along it from its start to the nearest spot."""
    if not coords:
        return {}
    xy = _plane(sum(c[1] for c in coords) / len(coords))
    P = [xy(lat, lon) for lon, lat in coords]
    if len(P) == 1:
        P = P * 2
    segs, at = [], 0.0  # (x0, y0, dx, dy, length², at start)
    for (x0, y0), (x1, y1) in zip(P, P[1:]):
        dx, dy = x1 - x0, y1 - y0
        l2 = dx * dx + dy * dy
        segs.append((x0, y0, dx, dy, l2, at))
        at += math.sqrt(l2)
    # a grid of cells, each listing the segments that pass within width_km of it
    cell = max(width_km, 0.25)
    grid = {}
    for i, (x0, y0, dx, dy, _, _) in enumerate(segs):
        lo_x, hi_x = min(x0, x0 + dx) - width_km, max(x0, x0 + dx) + width_km
        lo_y, hi_y = min(y0, y0 + dy) - width_km, max(y0, y0 + dy) + width_km
        for gx in range(math.floor(lo_x / cell), math.floor(hi_x / cell) + 1):
            for gy in range(math.floor(lo_y / cell), math.floor(hi_y / cell) + 1):
                grid.setdefault((gx, gy), []).append(i)
    out = {}
    for k, (lat, lon) in points.items():
        px, py = xy(lat, lon)
        best = None
        for i in grid.get((math.floor(px / cell), math.floor(py / cell)), ()):
            x0, y0, dx, dy, l2, a0 = segs[i]
            t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / l2))
            ex, ey = x0 + t * dx - px, y0 + t * dy - py
            d = math.sqrt(ex * ex + ey * ey)
            if best is None or d < best[0]:
                best = (d, a0 + t * math.sqrt(l2))
        if best and best[0] <= width_km:
            out[k] = (round(best[0], 3), round(best[1], 2))
    return out


# ---------------------------------------------------------------- the plan: where to buy what, on the way
def _vector(e, keys):
    by = {l["key"]: l for l in e.get("lines") or ()}
    out = []
    for k in keys:
        l = by.get(k)
        ok = l is not None and l.get("status") == "ok" and l.get("cost") is not None
        out.append(round(l["cost"] * 100) if ok else NO)
    return tuple(out)


def _minvec(vs):
    return vs[0] if len(vs) == 1 else tuple(map(min, *vs))


def _off(e):
    return e.get("off_km") if e.get("off_km") is not None else 1e9


def plans(entries, keys, max_stops=MAX_STOPS, beam=150):
    """entries: branches on the way, priced for the cart (compare()'s per-store dicts with "lines", "off_km",
    "at_km"); keys: the compared products. Returns the best plan for each number of stops that is worth it:
    [{"stops": n, "total", "covered", "complete", "missing": [keys], "saving": vs 1 stop or None,
      "stores": [{..., "subtotal", "lines"}] in the order you pass them from home}, ...], fewest stops first.
    Exact for one and two stops; a beam search beyond that."""
    keys = list(dict.fromkeys(keys))
    n = len(keys)
    if not n:
        return []
    # branches with the same prices for this cart are interchangeable: keep the one nearest the route
    cand = {}
    for e in entries:
        v = _vector(e, keys)
        if all(x == NO for x in v):
            continue
        cur = cand.get(v)
        if cur is None or (_off(e), e.get("at_km") or 0) < (_off(cur), cur.get("at_km") or 0):
            cand[v] = e
    rows = list(cand.items())

    def score(vec):  # fewest products without a confirmed price first, then the cheapest
        miss = sum(1 for x in vec if x == NO)
        return miss, sum(x for x in vec if x != NO)

    # a shortlist: the branches that are among the three cheapest for the most products (then the cheapest singles)
    if len(rows) > SHORTLIST:
        third = [sorted({v[x] for v, _ in rows})[:3][-1] for x in range(n)]
        useful = sorted(rows, key=lambda r: (-sum(1 for x in range(n) if r[0][x] != NO and r[0][x] <= third[x]),
                                             score(r[0]), _off(r[1])))[:SHORTLIST]
        singles = sorted(rows, key=lambda r: (score(r[0]), _off(r[1])))[:10]
        rows = list({r[0]: r for r in singles + useful}.values())
    # a branch no cheaper than another for anything, and no nearer the route, never helps
    rows = [(v, e) for i, (v, e) in enumerate(rows)
            if not any(j != i and _off(f) <= _off(e) and all(a <= b for a, b in zip(w, v)) for j, (w, f) in enumerate(rows))]
    m = len(rows)
    if not m:
        return []
    off = [_off(e) for _, e in rows]
    # states: frozenset of rows -> (rank, vector), rank = (missing, total, km off the route in all)
    states = {frozenset([i]): ((*score(rows[i][0]), off[i]), rows[i][0]) for i in range(m)}
    best = {}
    for size in range(1, max_stops + 1):
        if size > 1:
            nxt = {}
            # every pair (exact); beyond that the best `beam` sets so far, one more stop each
            ranked = states.items() if size == 2 else sorted(states.items(), key=lambda s: s[1][0])[:beam]
            for ids, (rank, vec) in ranked:
                top = max(ids)
                for j in range(m):
                    if j in ids or (size == 2 and j < top):
                        continue
                    nv = tuple(map(min, vec, rows[j][0]))
                    if nv == vec:  # buys nothing there: not a stop
                        continue
                    s_ = ids | {j}
                    if s_ not in nxt:
                        nxt[s_] = ((*score(nv), rank[2] + off[j]), nv)
            if not nxt:
                break
            states = nxt
        # the best set where every stop is the cheapest source of something once the others are there
        for ids, (rank, vec) in sorted(states.items(), key=lambda s: s[1][0]):
            if len(ids) == 1 or not any(_minvec([rows[i][0] for i in ids if i != j]) == vec for j in ids):
                best[size] = (ids, vec)
                break
    out, last = [], None
    for size in sorted(best):
        ids, vec = best[size]
        miss, total = score(vec)
        if last is not None:  # another stop only when it gets more products, or saves enough
            lmiss, ltotal = last
            if miss > lmiss or (miss == lmiss and ltotal - total < MIN_SAVE * 100):
                continue
        out.append(_plan(size, ids, vec, rows, keys))
        last = (miss, total)
    one = next((p for p in out if p["stops"] == 1 and p["complete"]), None)
    for p in out:
        p["saving"] = round(one["total"] - p["total"], 2) if one and p["complete"] and p is not one else None
    return out


def _plan(size, ids, vec, rows, keys):
    stops = sorted(ids, key=lambda i: (rows[i][1].get("at_km") or 0, _off(rows[i][1])))
    # each product at the cheapest stop; equal prices go to the stop with more of its own (fewer things to carry)
    wins = {i: sum(1 for a, b in zip(rows[i][0], vec) if a == b and a != NO) for i in stops}
    at = {i: [] for i in stops}
    missing = []
    for x, k in enumerate(keys):
        if vec[x] == NO:
            missing.append(k)
            continue
        i = max((i for i in stops if rows[i][0][x] == vec[x]), key=lambda i: (wins[i], -stops.index(i)))
        line = next(l for l in rows[i][1]["lines"] if l["key"] == k)
        at[i].append(line)
    stores = []
    for i in stops:
        e = rows[i][1]
        stores.append({**{k: e.get(k) for k in ("chain_id", "chain", "store_id", "name", "address", "city", "lat", "lon",
                                                 "off_km", "at_km", "last_price_ts")},
                       "subtotal": round(sum(l["cost"] for l in at[i]), 2), "lines": at[i]})
    total = sum(x for x in vec if x != NO)
    return {"stops": size, "total": round(total / 100, 2), "covered": len(keys) - len(missing),
            "complete": not missing, "missing": missing, "stores": stores}
