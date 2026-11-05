"""Run: .venv/bin/python tests/test_store.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.geo import _letters, match_hours, parse_hours  # noqa: E402

C = "Closed"
# Sunday first; unlisted days are closed; public-holiday rules are skipped
assert parse_hours("Su-Th 07:00-23:00; Fr 07:00-15:00; Sa off") == ["07:00–23:00"] * 5 + ["07:00–15:00", C]
assert parse_hours("Mo-Su 08:00-22:00") == ["08:00–22:00"] * 7
assert parse_hours("Su-Th 07:00-14:00,16:00-20:00; PH off") == ["07:00–14:00, 16:00–20:00"] * 5 + [C, C]
assert parse_hours("Sa-Mo 09:00-21:00") == ["09:00–21:00", "09:00–21:00", C, C, C, C, "09:00–21:00"]  # wraps the week
assert parse_hours("24/7") == ["Open 24 hours"] * 7
assert parse_hours("Su-Th 07:00-23:00; Fr 07:00-sunset") is None  # beyond the simple form: show the raw text
assert parse_hours("") is None and parse_hours(None) is None
# as people actually write them in OpenStreetMap
assert parse_hours("Mo, Tu, Su 08:00-22:00; We, Th 07:30-23:00; Fr 07:30-15:30") == \
    ["08:00–22:00", "08:00–22:00", "08:00–22:00", "07:30–23:00", "07:30–23:00", "07:30–15:30", C]
assert parse_hours("Su-Th 07:00-21:00; Fr 7:00-14:00")[5] == "07:00–14:00"
assert parse_hours("Su-Tu 06:30-23:00; We 06:30-23:30; Th 06:30-24:00; Fr 06:00-15:30; Sa,PH off")[4:] == ["06:30–24:00", "06:00–15:30", C]
# hours come only from a shop of the same chain, within 300 m, nearest first
shops = [[32.0822, 34.8557, _letters("AM:PM"), "24/7", "AM:PM"],                                   # same spot, other chain
         [32.0830, 34.8560, _letters("רמי לוי שיווק השקמה Rami Levy"), "Su-Th 07:00-23:00", "רמי לוי"],  # ~90 m
         [32.0900, 34.8600, _letters("רמי לוי"), "Mo-Su 08:00-20:00", "רמי לוי far"]]                   # ~1 km
assert match_hours(shops, 32.0822, 34.8557, ["Rami Levy", "רמי לוי"]) == ("Su-Th 07:00-23:00", "רמי לוי")
assert match_hours(shops, 32.0822, 34.8557, ["Osher Ad", "אושר עד"]) == (None, None)
assert match_hours(shops, 32.0822, 34.8557, ["Tiv Taam"]) == (None, None)
assert _letters("Tiv Ta'am") == _letters("Tiv Taam")  # OSM spells it with an apostrophe
print("ok")
