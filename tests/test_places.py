"""Run: .venv/bin/python tests/test_places.py
Picking a street from the official list the way people say it: full names, the street type spelled out."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp(prefix="sal-places-")
os.environ["SAL_PLACES_DB"] = os.path.join(tmp, "places.db")
from app import places  # noqa: E402

# official spellings (data.gov.il), Tel Aviv
STREETS = ["דרך בגין", "לילי מנחם", "בייליס מנחם מנדל", "שד רוטשילד", "הרצל", "סמ נורדאו", "שד נורדאו",
           "אלון יגאל", "סמ אלון", "דיזנגוף", "ככר דיזנגוף", "שדרות בן גוריון", "בנבנישתי", "ככר ב' בנובמבר",
           "דרך נמיר מרדכי", "נמירובר", "תל אביב - יפו"]
c = places.con()
with c:
    c.execute("INSERT INTO towns VALUES(5000, 'תל אביב - יפו', 'TEL AVIV - YAFO', ?, ?, ?)",
              (places.norm("תל אביב - יפו"), places.skel("TEL AVIV - YAFO"), len(STREETS)))
    c.executemany("INSERT INTO streets VALUES(5000, ?, ?, ?)", [(i + 1, n, places.norm(n)) for i, n in enumerate(STREETS)])


def found(q):
    return [s["name"] for s in places.streets(5000, q)["streets"]]


# the list drops first names and abbreviates the street type
assert found("מנחם בג") == ["דרך בגין"], found("מנחם בג")
assert found("מנחם בגין") == ["דרך בגין"]
assert found("דרך מנחם בגין") == ["דרך בגין"]
assert found("בגין") == ["דרך בגין"]
assert found("שדרות רוטשילד") == ["שד רוטשילד"]
assert found("רחוב הרצל") == ["הרצל"]
assert found("דוד בן גוריון") == ["שדרות בן גוריון"]
# the typed street type ranks first
assert found("שדרות נורדאו") == ["שד נורדאו", "סמ נורדאו"], found("שדרות נורדאו")
assert found("סמטת נורדאו") == ["סמ נורדאו", "שד נורדאו"]
assert found("רחוב דיזנגוף")[0] == "דיזנגוף"
# exact matches as before, and no first-name guesses beside them
assert found("יגאל אלון") == ["אלון יגאל"]
assert found("מרדכי נמיר") == ["דרך נמיר מרדכי"]
assert found("שדרות בן גוריון") == ["שדרות בן גוריון"]
assert found("מנחם ב") == ["בייליס מנחם מנדל"], found("מנחם ב")  # one letter doesn't stand for a word yet
assert "תל אביב - יפו" not in found("")

# a slip of the finger still finds it, but only when nothing is spelled like the list
assert found("דיזנגוב")[0] == "דיזנגוף", found("דיזנגוב")
assert found("רוטשילט") == ["שד רוטשילד"], found("רוטשילט")
assert "הרצל" in found("הרזל")
assert found("בנבנישתיי") == ["בנבנישתי"]
assert found("דיזנגוף")[0] == "דיזנגוף" and "דיזנגוב" not in found("דיזנגוף")
assert places._typo(["הר"], ["הצ"]) is None  # two letters must be exact
assert places._dist("abcd", "abdc") == 1
with c:
    c.execute("INSERT INTO towns VALUES(8600, 'רמת גן', 'RAMAT GAN', ?, ?, 900)", (places.norm("רמת גן"), places.skel("RAMAT GAN")))
    c.execute("INSERT INTO towns VALUES(9000, 'באר שבע', 'BEER SHEVA', ?, ?, 800)", (places.norm("באר שבע"), places.skel("BEER SHEVA")))
tn = lambda q: [x["name"] for x in places.towns(q)]
assert tn("רמת גנן") == ["רמת גן"] and tn("רמט גן") == ["רמת גן"], tn("רמט גן")
assert tn("בארשבע") == ["באר שבע"]
assert tn("רמת") == ["רמת גן"]

# spellings for OpenStreetMap: it knows דרך בגין as "מנחם בגין", so the bare surname is tried, and must be on the road
names = places._street_names("דרך בגין")
assert names == [("דרך בגין", None), ("בגין", ["בגינ"])], names
assert ("שדרות רוטשילד", None) in places._street_names("שד רוטשילד")
assert places._street_names("אלון יגאל") == [("אלון יגאל", None), ("יגאל אלון", None)]
assert places._street_names("סמ נורדאו") == [("סמ נורדאו", None)]  # an alley isn't the boulevard
assert places._street_names("רח 975") == [("רח 975", None)]
assert places._road_has({"address": {"road": "מנחם בגין"}}, ["בגינ"])
assert not places._road_has({"address": {"road": "בגין"}, "name": "שדרות בגין"}, ["מנחמ"])
assert not places._road_has({"address": {"road": "הרצל"}}, ["בגינ"])

# locating: Begin 120 isn't on OpenStreetMap, 118 next door is; the road is only found without "דרך"
calls = []
def fake(endpoint, params):
    calls.append(params)
    st = params.get("street")
    if st is None:
        return [{"lat": "32.0853", "lon": "34.7818"}]                              # the town centre
    road = {"lat": "32.0654", "lon": "34.7832", "address": {"road": "מנחם בגין"}}  # some stretch of the 3 km road
    if st == "118 בגין":
        return [{"lat": "32.0720", "lon": "34.7890", "address": {"road": "מנחם בגין", "house_number": "118"}}]
    if st == "132 בגין":
        return [{"lat": "32.0746", "lon": "34.7923", "address": {"road": "מנחם בגין", "house_number": "132"}}]
    return [road] if st.endswith(" בגין") and "דרך" not in st or st == "בגין" else []
places._nominatim = fake
h = places.locate(5000, 1, 120)
assert (h["precision"], h["near"], h["lat"], h["lon"]) == ("near", 118, 32.072, 34.789), h
n = len(calls)
assert places.locate(5000, 1, 120) == h and len(calls) == n                       # cached
h = places.locate(5000, 1, 132)
assert h["precision"] == "house" and len(calls) == n + 1, (h, calls[n:])          # the spelling that works, at once
assert places.locate(5000, 1)["precision"] == "street" and len(calls) == n + 1     # the street point is known too
print("ok")
