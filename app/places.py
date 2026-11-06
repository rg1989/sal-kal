"""Real addresses only: you pick your town and street from Israel's official lists, then a house number.

The lists come from data.gov.il (Population and Immigration Authority / CBS settlement and street codes) and are kept
in places.db next to the price DB, so picking is instant and never calls an outside service. Only the address you
finally choose is located on the map: one OpenStreetMap (Nominatim) lookup per new address, cached for good.
Nominatim is strict about load (1 request/second, no autocomplete), which is why the picking happens here.

Run once (also runs by itself the first time a list is needed):  python -m app.places [--refresh]
"""
import argparse
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request

from . import db as dbmod
from . import geo

API = "https://data.gov.il/api/3/action/datastore_search"
TOWNS_RID = "5c78e9fa-c2e2-4771-93ff-7f400a12f7ba"    # רשימת ישובים: code, Hebrew name, English name
STREETS_RID = "9ad3862c-8391-4b2f-84a4-2d4c68625f4b"  # רשימת רחובות: town code, street code, street name
REFRESH_DAYS = 90
NOMINATIM = "https://nominatim.openstreetmap.org"

SCHEMA = """
CREATE TABLE IF NOT EXISTS towns(code INTEGER PRIMARY KEY, name TEXT, name_en TEXT, norm TEXT, skel TEXT, streets INTEGER);
CREATE TABLE IF NOT EXISTS streets(town INTEGER, code INTEGER, name TEXT, norm TEXT, PRIMARY KEY(town, code));
CREATE TABLE IF NOT EXISTS located(k TEXT PRIMARY KEY, v TEXT, at INTEGER);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""
_lock = threading.Lock()
_local = threading.local()


def path():
    return os.environ.get("SAL_PLACES_DB") or os.path.join(os.path.dirname(os.path.abspath(dbmod.DB_PATH)), "places.db")


def con():
    c = getattr(_local, "con", None)
    if c is None:
        os.makedirs(os.path.dirname(path()), exist_ok=True)
        c = _local.con = sqlite3.connect(path(), timeout=30, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
    return c


# ---------------------------------------------------------------- matching what people type
FINALS = str.maketrans("ךםןףץ", "כמנפצ")


def norm(s):
    """Hebrew for matching: no quotes, geresh or punctuation, final letters as regular ones, and the doubled vav/yod
    of full spelling folded (קריית = קרית, שכונת = שכונת), so either spelling finds the official name."""
    s = re.sub(r"[\"'׳״`()\[\].,/\\-]+", " ", (s or "").lower()).translate(FINALS)
    s = s.replace("יי", "י").replace("וו", "ו")
    return " ".join(s.split())


def skel(s):
    """English town names as consonant skeletons: the official transliterations (HERZELIYYA, PETAH TIQWA, ELAT) and
    everyday ones (Herzliya, Petah Tikva, Eilat) come out the same."""
    s = (s or "").lower().replace("q", "k").replace("w", "v").replace("ph", "f").replace("ch", "h").replace("tz", "z")
    s = re.sub(r"[^a-z ]+", " ", s)
    words = []
    for w in s.split():
        w = re.sub(r"[aeiouy]", "", w)
        w = re.sub(r"(.)\1+", r"\1", w)
        if w:
            words.append(w)
    return " ".join(words)


def _match(qwords, name_words):
    """Every typed word starts some word of the name, in any order ("יגאל אלון" finds the official "אלון יגאל")."""
    return all(any(w.startswith(q) for w in name_words) for q in qwords)


# Street-type words and titles (after norm()). The official list writes "שד", "רח", "דרכ" and often drops first names
# ("דרך בגין" is Menachem Begin Road), while people type "שדרות", "רחוב" and full names.
STREET_WORDS = {"דרכ", "שד", "שדרות", "שדרת", "רח", "רחוב", "סמ", "סמטת", "סמטה", "כיכר", "ככר",
                "הרב", "רבי", "דר", "פרופ"}
KIND = {"שד": "שדרות", "שדרת": "שדרות", "רח": "רחוב", "סמ": "סמטה", "סמטת": "סמטה", "ככר": "כיכר"}


def _kind(words):
    """The street type in a name: שדרות / רחוב / סמטה / כיכר / דרכ, a plain name being a רחוב."""
    for w in words:
        k = KIND.get(w, w)
        if k in ("שדרות", "רחוב", "סמטה", "כיכר", "דרכ"):
            return k
    return "רחוב"


def _loose(qwords, name_words):
    """The second pass when the exact one misses: every distinctive word of the name (no street type, no title) is
    started by a typed word of 2+ letters, and at most one typed word is left over (a first name the list drops).
    "מנחם בגין" finds "דרך בגין"; "שדרות רוטשילד" finds "שד רוטשילד"; "רחוב הרצל" finds "הרצל".
    Returns a sort key (left-over words, other street type than typed, fewer letters matched) or None."""
    core = [w for w in name_words if len(w) > 1 and w not in STREET_WORDS]
    typed = [q for q in qwords if q not in STREET_WORDS]
    if not core or not typed:
        return None
    if not all(any(w.startswith(q) for q in typed if len(q) > 1) for w in core):
        return None
    hit = [q for q in typed if any(w.startswith(q) for w in name_words)]
    left = len(typed) - len(hit)
    if left > 1:
        return None
    other = len(typed) < len(qwords) and _kind(qwords) != _kind(name_words)
    return (left, other, -sum(map(len, hit)))


def _dist(a, b):
    """Edit distance with swapped neighbours counting once (optimal string alignment)."""
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def _typo(qwords, name_words):
    """The last pass, when nothing is spelled like the list: every typed word of 2+ letters starts a word of the name
    with at most one slip (two in words of 7+ letters): a letter missing, extra, wrong or two swapped. Words of under
    3 letters must be exact. Returns the slips counted, or None. "רמת גנן" finds רמת גן, "הרזל" finds הרצל."""
    total = 0
    for q in qwords:
        if len(q) < 2:
            continue
        allow = 0 if len(q) < 3 else 1 if len(q) < 7 else 2
        best = None
        for w in name_words:
            if w.startswith(q):
                best = 0
                break
            if allow and len(w) >= len(q) - allow and (w[0] == q[0] or w[0] == q[1] or w[1:2] == q[0] or w[1:2] == q[1:2]):
                d = min(_dist(q, w[:k]) for k in range(max(1, len(q) - allow), len(q) + allow + 1))
                best = d if best is None else min(best, d)
        if best is None or best > allow:
            return None
        total += best
    return (total, max(0, len(name_words) - len(qwords))) if any(len(q) >= 2 for q in qwords) else None


def title(s):
    return " ".join(w[:1].upper() + w[1:].lower() if not w.startswith("'") else w for w in (s or "").split())


# ---------------------------------------------------------------- the official lists
def _fetch(rid):
    url = f"{API}?{urllib.parse.urlencode({'resource_id': rid, 'limit': 100000})}"
    req = urllib.request.Request(url, headers={"User-Agent": geo.UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.load(r)
    if not d.get("success"):
        raise RuntimeError(f"data.gov.il: {rid} failed")
    return d["result"]["records"]


def refresh(force=False):
    """Download both lists (about 1,300 towns and 64,000 streets, a few MB) when missing or older than 90 days."""
    c = con()
    with _lock:
        row = c.execute("SELECT v FROM meta WHERE k='fetched'").fetchone()
        if row and not force and time.time() - float(row[0]) < REFRESH_DAYS * 86400:
            return False
        towns, streets = _fetch(TOWNS_RID), _fetch(STREETS_RID)
        per = {}
        for r in streets:
            per[int(r["סמל_ישוב"])] = per.get(int(r["סמל_ישוב"]), 0) + 1
        with c:
            c.execute("DELETE FROM towns")
            c.execute("DELETE FROM streets")
            c.executemany("INSERT OR REPLACE INTO towns VALUES(?,?,?,?,?,?)", [
                (int(r["סמל_ישוב"]), r["שם_ישוב"].strip(), title(r.get("שם_ישוב_לועזי", "").strip()),
                 norm(r["שם_ישוב"]), skel(r.get("שם_ישוב_לועזי", "")), per.get(int(r["סמל_ישוב"]), 0))
                for r in towns if str(r.get("סמל_ישוב", "")).strip().isdigit()])
            c.executemany("INSERT OR REPLACE INTO streets VALUES(?,?,?,?)", [
                (int(r["סמל_ישוב"]), int(r["סמל_רחוב"]), " ".join(r["שם_רחוב"].split()), norm(r["שם_רחוב"]))
                for r in streets])
            c.execute("INSERT OR REPLACE INTO meta VALUES('fetched', ?)", (str(time.time()),))
        return True


def ready():
    c = con()
    if not c.execute("SELECT 1 FROM towns LIMIT 1").fetchone():
        refresh(force=True)


def town(code):
    r = con().execute("SELECT code, name, name_en, streets FROM towns WHERE code=?", (code,)).fetchone()
    return {"code": r[0], "name": r[1], "en": r[2], "streets": r[3]} if r else None


def towns(q, limit=12):
    """Towns whose Hebrew or English name matches what's typed; whole-name prefix first, then bigger towns (more
    streets) first. Empty query: the biggest towns."""
    ready()
    c = con()
    rows = c.execute("SELECT code, name, name_en, norm, skel, streets FROM towns").fetchall()
    q = (q or "").strip()
    if not q:
        rows.sort(key=lambda r: -r[5])
        return [{"code": r[0], "name": r[1], "en": r[2]} for r in rows[:limit]]
    hq, eq = norm(q), skel(q)
    hits = []
    for code, name, en, n, sk, size in rows:
        if hq and _match(hq.split(), n.split()):
            hits.append((0 if n.startswith(hq) else 1, -size, code, name, en))
        elif eq and re.search(r"[a-z]", q.lower()) and _match(eq.split(), sk.split()):
            hits.append((0 if sk.startswith(eq) else 1, -size, code, name, en))
    if not hits and hq:  # a slip of the finger: the nearest spellings, fewest slips first
        for code, name, en, n, sk, size in rows:
            d = _typo(hq.split(), n.split()) or _typo([hq.replace(" ", "")], [n.replace(" ", "")])  # or a space left out
            if d is not None:
                hits.append((d[0], -size, code, name, en))
    hits.sort()
    return [{"code": h[2], "name": h[3], "en": h[4]} for h in hits[:limit]]


def streets(town_code, q="", limit=60):
    """A town's streets matching what's typed (any word order); empty query: all of them, alphabetical, so the list
    itself can be scrolled and picked from."""
    ready()
    rows = con().execute("SELECT code, name, norm FROM streets WHERE town=? ORDER BY name", (town_code,)).fetchall()
    t = town(town_code)
    # the settlement itself is listed as a "street" (code 9000+) where addresses have no street names
    named = [r for r in rows if not (t and r[1] == t["name"])] or rows
    hq = norm(q)
    if hq:
        qw = hq.split()
        exact = [r for r in named if _match(qw, r[2].split())]
        exact.sort(key=lambda r: (not r[2].startswith(hq), r[1]))
        seen = {r[0] for r in exact}
        loose = [(k, r) for r in named if r[0] not in seen for k in [_loose(qw, r[2].split())] if k]
        loose.sort(key=lambda x: (x[0], x[1][1]))
        # a left-over word (a first name) only when nothing fits better: "יגאל אלון" shouldn't add "סמ אלון"
        most = 0 if exact or (loose and loose[0][0][0] == 0) else 1
        named = exact + [r for k, r in loose if k[0] <= most]
        if not exact and not (loose and loose[0][0][0] == 0):  # a slip of the finger beats guessing a first name
            seen = {r[0] for r in named}
            near = [(d, r) for r in rows if r[0] not in seen and not (t and r[1] == t["name"])
                    for d in [_typo(qw, r[2].split())] if d is not None]
            near.sort(key=lambda x: (x[0], x[1][1]))
            named = [r for d, r in near] + named
    return {"total": len(named), "streets": [{"code": r[0], "name": r[1]} for r in named[:limit]],
            "no_streets": len(rows) > 0 and all(t and r[1] == t["name"] for r in rows)}


def street(town_code, street_code):
    r = con().execute("SELECT code, name FROM streets WHERE town=? AND code=?", (town_code, street_code)).fetchone()
    return {"code": r[0], "name": r[1]} if r else None


# ---------------------------------------------------------------- putting the chosen address on the map
_last_call = [0.0]
_nlock = threading.Lock()


def _nominatim(endpoint, params):
    """One Nominatim request, at most one per second across the whole server (their usage policy)."""
    with _nlock:
        wait = 1.1 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        try:
            url = f"{NOMINATIM}/{endpoint}?{urllib.parse.urlencode({**params, 'format': 'jsonv2', 'addressdetails': 1, 'accept-language': 'he'})}"
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": geo.UA}), timeout=20) as r:
                return json.load(r)
        finally:
            _last_call[0] = time.time()


def _city_names(name):
    """The official spelling first, then OpenStreetMap's usual ones (קריית for קרית, תל אביב-יפו for תל אביב - יפו)."""
    out = [name, re.sub(r"(^|\s)קרית(?=\s)", r"\1קריית", name), name.replace(" - ", "-"),
           " ".join(re.sub(r"\(.*?\)", " ", name).split())]
    if name.startswith("תל אביב"):
        out.append("תל אביב-יפו")
    return list(dict.fromkeys(o for o in out if o))


STRIP = {"דרך", "שד", "שד'", "שדרות", "רח", "רח'", "רחוב"}  # the same street with or without the type word (not סמ: an alley)


def _street_names(name):
    """Spellings to look the street up by, as (name, words the hit's road must have or None). Official names put a
    person's surname first ("אלון יגאל"); street signs and OpenStreetMap don't. They also abbreviate the street type and
    drop first names: OpenStreetMap only finds "דרך בגין" as "מנחם בגין", so the name without "דרך" is tried too (and
    then the road found must carry its words)."""
    out = [(name, None)]
    w = name.split()
    if len(w) == 2 and norm(w[0]) not in STREET_WORDS:
        out.append((f"{w[1]} {w[0]}", None))
    if len(w) >= 2 and w[0] in STRIP and not "".join(w[1:]).isdigit():
        rest, must = w[1:], norm(" ".join(w[1:])).split()
        if w[0] in ("שד", "שד'"):
            out.append((" ".join(["שדרות", *rest]), None))
            if len(rest) == 2:
                out.append((f"שדרות {rest[1]} {rest[0]}", None))
        out.append((" ".join(rest), must))
        if len(rest) == 2:
            out.append((f"{rest[1]} {rest[0]}", must))
    return out


def _road_has(hit, must):
    if not must:
        return True
    a = hit.get("address") or {}
    words = set(norm(" ".join(str(x) for x in (a.get("road"), a.get("pedestrian"), a.get("square"), hit.get("name")) if x)).split())
    return all(m in words for m in must)


def _near(hit, centre, km=20):
    return centre is None or geo.km(float(hit["lat"]), float(hit["lon"]), *centre) <= km


def locate(town_code, street_code=None, number=None):
    """Coordinates for an address picked from the lists: the house when OpenStreetMap has it, else the street, else
    the town centre. {label, lat, lon, precision: house|street|town, town, street, number}. Cached."""
    t = town(town_code)
    if not t:
        raise ValueError("unknown town")
    s = street(town_code, street_code) if street_code else None
    number = int(number) if number else None
    key = f"{town_code}:{street_code or ''}:{number or ''}"
    c = con()
    got = _memo(c, key)
    # a street that wasn't found is asked again the next day (OpenStreetMap gets fixed; so do the spellings here)
    if got and not (got["v"]["precision"] == "town" and s and time.time() - got["at"] > 86400):
        return got["v"]
    # the town centre: to check street hits are in the right town, and the last resort. Kept, with the spelling of the
    # town OpenStreetMap knows, so each new address costs only the street lookups (Nominatim: 1 request/second)
    centre = (_memo(c, f"centre:{town_code}") or {}).get("v")
    if not centre:
        for city in _city_names(t["name"]):
            res = _nominatim("search", {"city": city, "countrycodes": "il", "limit": 1})
            if res:
                centre = {"lat": float(res[0]["lat"]), "lon": float(res[0]["lon"]), "city": city}
                _keep(c, f"centre:{town_code}", centre)
                break
    hit, precision, near = (centre, "town", None)
    at = (centre["lat"], centre["lon"]) if centre else None
    if s:
        # the spelling and town name that found this street before: one request for a new house number, not eight
        via = (_memo(c, f"via:{town_code}:{s['code']}") or {}).get("v")
        found = None  # (point on the street, spelling, city, words the road must have) when the house isn't on the map
        if via:
            found = ({"lat": via["lat"], "lon": via["lon"]}, via["name"], via["city"], via["must"])
            tries = [({"street": f"{number} {via['name']}", "city": via["city"]}, "house", via["must"], via["name"])] if number else []
        else:
            cities = _city_names(t["name"])
            if centre:
                cities = [centre["city"]] + [x for x in cities if x != centre["city"]]
            tries = []
            for city in cities[:2]:
                for name, must in _street_names(s["name"]):
                    if number:
                        tries.append(({"street": f"{number} {name}", "city": city}, "house", must, name))
                    tries.append(({"street": name, "city": city}, "street", must, name))
            tries.sort(key=lambda x: x[1] != "house")  # every spelling with the number before any without
        for params, prec, must, name in tries[:8]:
            if found and (prec == "street" or params["city"] != found[2]):
                break
            res = [h for h in _nominatim("search", {**params, "countrycodes": "il", "limit": 3})
                   if _near(h, at) and _road_has(h, must)]
            house = [h for h in res if (h.get("address") or {}).get("house_number")]
            if house:
                hit, precision = house[0], "house"
                if not via:
                    _keep(c, f"via:{town_code}:{s['code']}", {"name": name, "city": params["city"], "must": must,
                                                               "lat": float(hit["lat"]), "lon": float(hit["lon"])})
                found = None
                break
            if res and not found:
                found = (res[0], name, params["city"], must)
                _keep(c, f"via:{town_code}:{s['code']}", {"name": name, "city": params["city"], "must": must,
                                                           "lat": float(res[0]["lat"]), "lon": float(res[0]["lon"])})
        if found:
            hit, precision = found[0], "street"
            # the house isn't on the map: the nearest number on the same side of the street that is, a few doors off
            # (a long road's "street" point can be kilometres away: Begin Road is 3 km)
            for m in [number + d for d in (-2, 2, -4, 4) if number and number + d > 0]:
                res = [h for h in _nominatim("search", {"street": f"{m} {found[1]}", "city": found[2], "countrycodes": "il", "limit": 3})
                       if _near(h, at) and _road_has(h, found[3])
                       and re.match(rf"{m}(\D|$)", str((h.get("address") or {}).get("house_number") or ""))]
                if res:
                    hit, precision, near = res[0], "near", m
                    break
    if not hit:
        raise LookupError("not found")
    label = ", ".join(p for p in (" ".join(str(x) for x in (s["name"] if s else "", number or "") if x), t["name"]) if p)
    out = {"label": label, "lat": float(hit["lat"]), "lon": float(hit["lon"]), "precision": precision,
           "town": t["code"], "street": s["code"] if s else None, "number": number}
    if near:
        out["near"] = near
    _keep(c, key, out)
    return out


def _memo(c, k):
    row = c.execute("SELECT v, at FROM located WHERE k=?", (k,)).fetchone()
    return {"v": json.loads(row[0]), "at": row[1]} if row else None


def _keep(c, k, v):
    with c:
        c.execute("INSERT OR REPLACE INTO located VALUES(?,?,?)", (k, json.dumps(v, ensure_ascii=False), int(time.time())))


def reverse(lat, lon):
    """The address at a point (your phone's location), matched to the official lists where it can be. The point itself
    is kept as home: it is more exact than any lookup."""
    ready()
    r = _nominatim("reverse", {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "zoom": 18})
    a = (r or {}).get("address") or {}
    if a.get("country_code") not in (None, "il"):
        raise LookupError("outside Israel")
    city = a.get("city") or a.get("town") or a.get("village") or a.get("municipality") or ""
    road, num = a.get("road") or "", a.get("house_number") or ""
    c = con()
    t = None
    if city:
        cn = norm(city)
        for code, n in c.execute("SELECT code, norm FROM towns"):
            if n == cn or n.replace(" ", "") == cn.replace(" ", ""):
                t = code
                break
    s = None
    if t and road:
        rn = set(norm(road).split())
        for code, n in c.execute("SELECT code, norm FROM streets WHERE town=?", (t,)):
            if set(n.split()) == rn:
                s = code
                break
    num = int(re.match(r"\d+", num).group()) if re.match(r"\d+", num) else None
    tt, ss = (town(t) if t else None), (street(t, s) if s else None)
    label = ", ".join(p for p in (" ".join(str(x) for x in ((ss or {}).get("name") or road, num or "") if x),
                                  (tt or {}).get("name") or city) if p)
    if not label:
        raise LookupError("no address here")
    return {"label": label, "lat": lat, "lon": lon, "precision": "gps", "town": t, "street": s, "number": num,
            "town_name": (tt or {}).get("name") or city, "street_name": (ss or {}).get("name") or road}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    changed = refresh(force=a.refresh)
    c = con()
    print("towns", c.execute("SELECT count(*) FROM towns").fetchone()[0],
          "streets", c.execute("SELECT count(*) FROM streets").fetchone()[0], "(downloaded)" if changed else "")


if __name__ == "__main__":
    main()
