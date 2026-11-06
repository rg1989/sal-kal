"""Write app/data/store-points.json: where every chain's branches are, for Sal-Kal on your own computer (app/local.py
seeds its geocache with it, so a new install places nearly every branch at once instead of spending an hour on
Nominatim lookups at 1 a second). Only the lookups made for the stores' own published name, address and town, never
anything else in the geocache. Data: OpenStreetMap contributors (ODbL), via Nominatim.
Run on the server: sudo -u sal env BASKET_DB=/srv/sal/data/basket.db /srv/sal/venv/bin/python deploy/export-store-points.py OUT
"""
import json
import sys
import time

sys.path.insert(0, __file__.rsplit("/deploy/", 1)[0])
from app import db as dbmod, geo  # noqa: E402

con = dbmod.connect()
cache = {r[0]: (r[1], r[2]) for r in con.execute("SELECT q, lat, lon FROM geocache")}
tn = geo.towns(con)
pts = {}
for s in con.execute("SELECT name, address, city FROM stores"):
    for q, _ in geo.queries(s, tn):
        if q in cache:
            lat, lon = cache[q]
            pts[q] = [q, round(lat, 6) if lat is not None else None, round(lon, 6) if lon is not None else None]
out = {"about": "Store locations for Sal-Kal: (query, lat, lon); lat null = no match. "
                "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright), via Nominatim.",
       "made": time.strftime("%Y-%m-%d"), "points": sorted(pts.values())}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
print(len(pts), "points,", sum(1 for p in pts.values() if p[1] is not None), "found")
