"""Run: python tests/test_geo.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.geo import km, queries  # noqa: E402

# Tel Aviv (Azrieli) -> Jerusalem (Central Station) is ~54 km as the crow flies
assert 50 < km(32.0745, 34.7920, 31.7890, 35.2030) < 58
assert km(32.0, 34.8, 32.0, 34.8) == 0

# numeric CBS city code -> town name; street + town first
s = {"address": "התעשייה 29", "city": "9400", "name": "יהוד"}
assert queries(s, {9400: "יהוד"}) == [("התעשייה 29, יהוד", "street"), ("יהוד", "any")], queries(s, {9400: "יהוד"})
# unknown code and "unknown" address -> store name, then its last word as a town
assert queries({"address": "unknown", "city": "0", "name": "וולט עכו"}) == [("וולט עכו", "any"), ("עכו", "place")]
# chain name + town in the store name: the town tail is tried (only accepted if it's a place)
q = [x for x, _ in queries({"address": "", "city": "", "name": "כל בו חצי חינם - רחובות"})]
assert q == ["כל בו חצי חינם - רחובות", "חינם רחובות", "רחובות"], q
# full data -> street+city first, then street+name, then town, then name
s = {"address": "הרצל 10", "city": "רמת גן", "name": "רמי לוי רמת גן"}
assert queries(s)[:3] == [("הרצל 10, רמת גן", "street"), ("הרצל 10, רמי לוי רמת גן", "street"), ("רמת גן", "any")]
assert ("הרצל 10, גן", "street") not in queries(s)
print("ok")
