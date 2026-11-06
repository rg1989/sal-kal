"""Run: .venv/bin/python tests/test_board.py
Price Watch (app/board.py, /prices in app/server.py): store price changes are caught as the indexer writes a newer
file and kept only when confirmed; a change is the chain's only when 3+ of its branches made it (online and Eilat
stores never count), from the same old price to the same new one; a chain's level is its middle branch's price
against the middle chain's, without Eilat or online stores; the page and its data are built ahead and served as they
are, the same bytes for everyone whatever the query string; the old product pages redirect. The page is the light,
public part; search and each period's lists need sign-in."""
import asyncio
import gzip
import importlib.util
import json
import os
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="sal-board-")
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
os.environ["BOARD_DIR"] = os.path.join(TMP, "board")
os.environ["SAL_AUTH"] = os.path.join(TMP, "auth.json")
os.environ["SAL_STATS_SYNC"] = "1"
os.environ.pop("SAL_DEV_LOGIN", None)
with open(os.environ["SAL_AUTH"], "w") as f:
    json.dump({"google": {"client_id": "id", "client_secret": "s"}, "base_url": "https://sal-kal.com"}, f)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location("fx", os.path.join(ROOT, "tests", "make_fixtures.py"))
fx = importlib.util.module_from_spec(spec)
fx.OUT = DUMPS = os.path.join(TMP, "dumps")
sys.argv = ["make_fixtures", DUMPS]
spec.loader.exec_module(fx)
fx.main()

from app import auth, board, indexer, reports  # noqa: E402
from app import db as dbmod  # noqa: E402

RAMI, MILK, BAMBA, HUMMUS, BREAD, SUGAR, RICE = ("7290058140886", "7290004131074", "7290000066318", "7290005429071",
                                                 "7290000072753", "7290011194246", "7290110115852")  # SUGAR: chocolate
# Rami Levy gets two more branches (Haifa, and one in Eilat, where there's no VAT) next to its web shop (98)
STORES = [(10, "רמי לוי תלפיות", "ירושלים", 1), (39, "רמי לוי רמת גן", "רמת גן", 1), (98, "רמי לוי אינטרנט", "", 2),
          (41, "רמי לוי חיפה", "חיפה", 1), (77, "רמי לוי אילת", "אילת", 1)]
fx.write_gz(f"{DUMPS}/RamiLevy/Stores{RAMI}-000-{fx.NOW}.gz", fx.stores_xml(RAMI, "רמי לוי", STORES))
prices = {code: round(base * 0.94, 1) - 0.01 for code, _, _, base in fx.PRODUCTS}
for sid in (41, 77):
    items = [(code, name, manu, prices[code] * (0.5 if sid == 77 else 1), 0) for code, name, manu, _ in fx.PRODUCTS]
    fx.write_gz(f"{DUMPS}/RamiLevy/PriceFull{RAMI}-{sid:03d}-{fx.NOW}.gz", fx.price_xml(RAMI, sid, items))

indexer.run(DUMPS)
c = dbmod.connect()
lab = reports.labels(c)
brand = lambda cid: reports.label_of(lab, cid)
kinds = board.store_kinds(c)
assert kinds[(RAMI, 10)] == kinds[(RAMI, 41)] == board.BRANCH and kinds[(RAMI, 98)] == board.ONLINE, kinds
assert kinds[(RAMI, 77)] == board.EILAT_STORE, kinds
assert board.is_eilat("2600", "", "") and not board.is_eilat("4000", "שלי קרית חיים-אח\"י אילת", "שד. אח\"י אילת 20")
assert board.is_eilat("", "96 אינטרנט אילת", "") and board.is_eilat("אילת", "", "")
assert not board.is_eilat("תל אביב - יפו", "וולט מרקט | נווה צדק", "דרך אילת 23 א׳")  # a street named after it

# ---------------------------------------------------------------- rule 3: the level, without Eilat or online stores
cur, _ = board.current(c, kinds, brand)
rami = cur[MILK]["Rami Levy"]
assert len(rami) == 3 and min(rami) > prices[MILK] * 0.7, rami          # 10, 39, 41: not Eilat's half price nor 98
lv = board.levels({"a": {"X": [10, 10, 10], "Y": [12, 12], "Z": [8, 9, 30]},     # Z's middle branch: 9 (not 8)
                   "b": {"X": [5], "Y": [6]}})                                    # two chains: not compared
assert set(lv) == {"X", "Y", "Z"} and all(n == 1 for _, n in lv.values()), lv
assert abs(lv["X"][0]) < 1e-9 and abs(lv["Y"][0] - 0.2) < 1e-9 and abs(lv["Z"][0] + 0.1) < 1e-9, lv
assert board.level_words(-0.123)[1] == "12% cheaper than the middle chain"
assert board.level_words(0.087)[1] == "8.7% more expensive than the middle chain"
assert board.level_words(0.004)[1] == "Same as the middle chain"

# ---------------------------------------------------------------- store changes as the indexer writes them
before = dict(c.execute("SELECT store_id, price FROM prices WHERE key=? AND chain_id=?", (MILK, RAMI)).fetchall())
later = time.strftime("%Y%m%d%H%M", time.localtime(time.time() + 60))
for sid in sorted(before):
    items = [(MILK, "חלב תנובה 3% 1 ליטר", "תנובה", 7.49, 0)]
    if sid == 10:
        items += [(BAMBA, "במבה אסם 80 גרם", "אסם", 6.20, 0), (fx.WEIGHED[0], fx.WEIGHED[1], "", 9.99, 1)]
    fx.write_gz(f"{DUMPS}/RamiLevy/Price{RAMI}-{sid:03d}-{later}.gz", fx.price_xml(RAMI, sid, items))
raw_count = lambda: c.execute("SELECT count(*) FROM price_moves_raw").fetchone()[0]
indexer.run(DUMPS)
raw = c.execute("SELECT key, chain_id, store_id, was, now FROM price_moves_raw ORDER BY key, store_id").fetchall()
assert all(":" not in r[0] for r in raw), raw                     # chain codes (weighed produce) never compared
assert {(r[0], r[2]) for r in raw} >= {(MILK, s) for s in before}, raw
# a change from a price published long before (the store's files had stopped): can't be dated, not kept
c.execute("INSERT INTO price_moves_raw VALUES(?, ?, 39, 5.0, 5.5, 202001010000, ?)", (BAMBA, RAMI, int(later)))
# controlled changes (ts = the later file's): one store each, as the trigger writes them
T, W = int(later), int(later) - 100


def raw_rows(rows):
    c.executemany("INSERT INTO price_moves_raw VALUES(?, ?, ?, ?, ?, ?, ?)", [(k, RAMI, s, a, b, W, T) for k, s, a, b in rows])
    c.commit()


raw_rows([(SUGAR, 10, 6.5, 6.9), (SUGAR, 39, 6.5, 6.9), (SUGAR, 41, 6.5, 6.9), (SUGAR, 98, 6.5, 6.9), (SUGAR, 77, 6.5, 6.9),
          (HUMMUS, 10, 9.3, 9.9), (HUMMUS, 98, 9.3, 9.9), (HUMMUS, 77, 9.3, 9.9),          # one branch, a web shop, Eilat
          (BREAD, 10, 7.1, 7.9), (BREAD, 39, 6.9, 7.9), (BREAD, 41, 7.3, 7.9),            # three markdowns ending
          (RICE, 10, 11.1, 10.9), (RICE, 39, 11.1, 10.9), (RICE, 41, 11.1, 10.9)])        # a cut at three branches
ver = board.build(c, os.environ["BOARD_DIR"], "https://sal-kal.com")
today = int(later[:8])
assert raw_count() == 0                                               # read and emptied
assert board.branch_since(c) == today                                 # nothing before: exact from the first day
moves = c.execute("SELECT key, chain_id, day, was, now, stores FROM price_moves ORDER BY key").fetchall()
milk = [m for m in moves if m[0] == MILK]
assert len({m[3] for m in milk}) == len(milk)                         # one row per old price (every store, as before)
assert sum(m[5] for m in milk) == len(before) and all(m[4] == 7.49 and m[2] == today for m in milk), moves
assert not any(m[3] == 5.0 for m in moves), moves                    # the undatable one
assert c.execute("SELECT value FROM settings WHERE key='moves_since'").fetchone()[0] == str(today)
bm = {(r[0], r[3], r[4]): r[5] for r in c.execute("SELECT key, chain_id, day, was, now, branches FROM branch_moves WHERE chain_id=?", (RAMI,))}
assert bm[(SUGAR, 6.5, 6.9)] == 3, bm                                 # rule 2: the web shop and Eilat's branch left out
assert bm[(HUMMUS, 9.3, 9.9)] == 1 and bm[(RICE, 11.1, 10.9)] == 3, bm

# a branch that changes the same price twice in a day (in two builds) is one branch, not two
raw_rows([(SUGAR, 10, 6.9, 6.5)])
board.collect(c)
raw_rows([(SUGAR, 10, 6.5, 6.9)])
board.collect(c)
assert c.execute("SELECT branches FROM branch_moves WHERE key=? AND chain_id=? AND was=6.5 AND now=6.9", (SUGAR, RAMI)).fetchone()[0] == 3
again = board.collect(c)                                              # nothing new: nothing written twice
assert again == 0

# ---------------------------------------------------------------- rule 1: what counts as the chain's change
since = board.branch_since(c)
ev = {(e[0], e[1]): e for e in board.chain_events(c, kinds, brand, since)}
assert (SUGAR, "Rami Levy") in ev and ev[(SUGAR, "Rami Levy")][8:10] == (3, 3), ev.get((SUGAR, "Rami Levy"))
assert ev[(RICE, "Rami Levy")][3] == -1                               # a cut
assert (BAMBA, "Rami Levy") not in ev                                 # one branch
assert (HUMMUS, "Rami Levy") not in ev                                # one branch + a web shop + Eilat
assert (BREAD, "Rami Levy") not in ev                                 # three branches, each from its own price
milk_ev = ev.get((MILK, "Rami Levy"))
assert milk_ev is None or milk_ev[8] >= 3, milk_ev                    # the fixtures' prices differ per branch
# before branch_moves began (price_moves only, every store): at least stores minus the web shop and Eilat's branch
yday = reports.day_minus(today, 1)
c.executemany("INSERT INTO price_moves(key, chain_id, day, was, now, stores, ts) VALUES(?,?,?,?,?,?,?)",
              [("7290000171463", RAMI, yday, 7.4, 7.9, 5, yday * 10000 + 900),     # 5 - 2 = at least 3: counted
               ("7290000068428", RAMI, yday, 5.4, 5.9, 4, yday * 10000 + 900)])    # 4 - 2 = 2: not counted
c.commit()
ev = {(e[0], e[1]): e for e in board.chain_events(c, kinds, brand, since)}
assert ev[("7290000171463", "Rami Levy")][8:10] == (3, 5) and ("7290000068428", "Rami Levy") not in ev, ev
assert board.branches_words(3, 5, 4)[1] == "at least 3 of 4 branches" and board.branches_words(3, 3, 3)[1] == "at 3 of 3 branches"
assert board.branches_words(3, 3, 2)[1] == "at 3 branches"             # never "3 of 2"

board.MIN_PRODUCTS = 1  # the fixtures' chains sell ~25 products
ver = board.build(c, os.environ["BOARD_DIR"], "https://sal-kal.com")
root = os.path.join(os.environ["BOARD_DIR"], ver)
files = sorted(os.listdir(root))
assert {"p1.html.gz", "rows.json.gz", "meta.json", "sitemap.xml", "chg-all.json.gz"} <= set(files), files
assert not any(f.startswith(("p2.", "h-")) for f in files), files    # no product table pages, no price_history lines
meta = json.load(open(os.path.join(root, "meta.json")))
assert meta["products"] >= 20 and "Rami Levy" in meta["chains"], meta
p1 = gzip.decompress(open(os.path.join(root, "p1.html.gz"), "rb").read()).decode()
assert "מעקב מחירים" in p1 and "Price Watch" in p1 and "/static/prices.js?v=" in p1   # stamped at build: served as is
assert fx.WEIGHED[0] not in p1                                           # chain codes (produce) aren't on the page
assert "מהרשת האמצעית" in p1 and "the middle chain" in p1               # every level in plain words
# the page is a visitor's: the chains ranked, the sign-in sheet; no chain's lists, no search rows, no other period
blob = json.loads(p1.split('id="board-data">', 1)[1].split("</script>", 1)[0])
assert blob["body"]["chains"] == {} and "all" in blob["body"] and blob["rank"][0][0] in meta["chains"], blob
assert 'class="card rank guest-only"' in p1 and 'id="gate"' in p1 and 'id="member-chains"' in p1
assert '"tu"' not in p1 and 'class="cols"' not in p1 and SUGAR not in p1 and MILK not in p1  # no barcodes either
if 'id="peek"' in p1:  # a few rises, at most +50% each, then the sign-in
    peek = p1.split('id="peek"', 1)[1].split('class="veil"', 1)[0]
    assert 1 <= peek.count("<li>") <= board.PEEK and "data-signin" in p1.split('id="peek"', 1)[1], peek
body = json.loads(gzip.decompress(open(os.path.join(root, "chg-all.json.gz"), "rb").read()))
rl = body["chains"]["Rami Levy"]
ups = {it[0] for it in rl["tu"]}
assert SUGAR in ups and BAMBA not in ups and HUMMUS not in ups and BREAD not in ups, rl["tu"]
assert {it[0] for it in rl["td"]} == {RICE}, rl["td"]
assert rl["nup"] == len(ups) and rl["ndn"] == 1
sugar = next(it for it in rl["tu"] if it[0] == SUGAR)
assert sugar[7] == sugar[8] == 3 and sugar[9] == 3, sugar               # at 3 of the 3 branches that list it (41 incl.)
assert "Wolt Market" not in body["chains"]
rows = [json.loads(x) for x in gzip.decompress(open(os.path.join(root, "rows.json.gz"), "rb").read()).decode().split("\n")]
milk_row = next(r for r in rows if r["k"] == MILK)
assert milk_row["p"]["Rami Levy"][2] == 3, milk_row                   # branches now: 10, 39, 41 (not 77, 98)
assert not any(e[0] == "Rami Levy" and e[7] < 3 for r in rows for e in r["e"])
bamba_row = next(r for r in rows if r["k"] == BAMBA)
assert not [e for e in bamba_row["e"] if e[0] == "Rami Levy"], bamba_row  # single-branch changes appear nowhere

# an older build is replaced by the next, and only KEEP stay
for _ in range(2):
    time.sleep(0.01)
    v = board.build(c, os.environ["BOARD_DIR"], "https://sal-kal.com")
assert len([e for e in os.listdir(os.environ["BOARD_DIR"]) if e.isdigit()]) == board.KEEP
assert open(os.path.join(os.environ["BOARD_DIR"], "current")).read() == v

# ---------------------------------------------------------------- through the web server
from app import server  # noqa: E402

server.con = lambda: c


def call(path, cookie=None, headers=None, ip="198.51.100.9"):
    hdrs = {"host": "sal-kal.com", **(headers or {})}
    if cookie:
        hdrs["cookie"] = cookie
    if ip:
        hdrs["cf-connecting-ip"] = ip
    p, _, q = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "https",
             "path": p, "raw_path": p.encode(), "query_string": q.encode(), "root_path": "",
             "headers": [(k.lower().encode(), v.encode()) for k, v in hdrs.items()], "client": None,
             "server": ("sal-kal.com", 443)}
    out = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        out.append(m)
    asyncio.run(server.app(scope, receive, send))
    start = next(m for m in out if m["type"] == "http.response.start")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, b"".join(
        m.get("body", b"") for m in out if m["type"] == "http.response.body")


GZ = {"accept-encoding": "gzip, br"}
s, h, body = call("/prices", headers=GZ)                             # from the internet, not signed in
assert s == 200 and h["content-encoding"] == "gzip" and "public" in h["cache-control"] and h["etag"] == f'"{v}"', h
assert body == open(os.path.join(os.environ["BOARD_DIR"], v, "p1.html.gz"), "rb").read()  # the built bytes, as is
assert "nonce-" in h["content-security-policy"]
for junk in ("/prices?x=1", "/prices?page=99&q=<script>", "/prices?" + "a" * 3000):
    assert call(junk, headers=GZ)[2] == body                           # a query string changes nothing
s, h, _ = call("/prices", headers={**GZ, "if-none-match": f'"{v}"'})
assert s == 304
s, h, plain = call("/prices")                                         # no gzip: plain HTML
assert s == 200 and "content-encoding" not in h and plain == gzip.decompress(body)
for old in ("/prices/2", "/prices/57", "/prices/99999", "/prices/changes"):
    s, h, _ = call(old)
    assert s == 301 and h["location"] == "/prices", (old, s, h)       # the product table's pages are gone
for bad in ("/prices/0", "/prices/01", "/prices/1", "/prices/-1", "/prices/1e3", "/prices/abc", "/prices/999999",
            "/prices/changes/x", "/prices/%2e%2e"):
    s, h, b = call(bad)
    assert s == 404 and len(b) < 200, (bad, s)                        # the same small 404, nothing computed
# the depth needs sign-in: each period's lists and search
guest_s = call("/api/prices/data/chg-all.json", headers=GZ)[0]
assert guest_s == 401, guest_s
assert call("/prices/data/chg-all.json")[0] == 404                       # the old public address is gone
uid = auth.sign_in(c, "google", "g-reader", "reader@example.com", True, "Reader", None)
ME = f"{auth.SESSION_COOKIE}={auth.new_session(c, uid)}"
s, h, b = call("/api/prices/data/chg-all.json", cookie=ME, headers=GZ)
assert s == 200 and json.loads(gzip.decompress(b))["p"] == "all" and h["cache-control"].startswith("private"), h
assert call("/api/prices/data/chg-all.json", cookie=ME, headers={"if-none-match": h["etag"]})[0] == 304
for bad in ("/api/prices/data/h-1.json", "/api/prices/data/rows.json", "/api/prices/data/chg-x.json"):
    assert call(bad, cookie=ME)[0] == 404, bad
s, h, sm = call("/sitemap.xml")
assert s == 200 and sm.count(b"<loc>") == 2 and b"https://sal-kal.com/prices<" in sm and b"/prices/2" not in sm

# search: signed in, from the build in memory
assert call("/api/prices?q=" + "%D7%97%D7%9C%D7%91")[0] == 401        # חלב, not signed in
s, _, out = call("/api/prices?q=" + "%D7%97%D7%9C%D7%91", cookie=ME)
out = json.loads(out)
assert s == 200 and out["total"] >= 2 and all("חלב" in r["n"] for r in out["rows"]), out
s, _, out = call(f"/api/prices?q={MILK[:7]}", cookie=ME)               # a barcode's start
assert any(r["k"] == MILK for r in json.loads(out)["rows"])
s, _, out = call("/api/prices?q=%D7%A9%D7%95%D7%A7%D7%95%D7%9C%D7%93", cookie=ME)  # שוקולד
sug = next(r for r in json.loads(out)["rows"] if r["k"] == SUGAR)
assert [e[:3] for e in sug["e"] if e[0] == "Rami Levy"] == [["Rami Levy", today, 1]], sug
for q in ("", "x", "12", "%20%20"):                                    # too short: nothing searched
    s, _, out = call("/api/prices?q=" + q, cookie=ME)
    assert s == 200 and json.loads(out) == {"rows": [], "total": 0}, (q, out)
s, _, out = call("/api/prices?q=" + "ab " * 500, cookie=ME)            # long input: cut, not refused
assert s == 200 and len(json.loads(out)["rows"]) <= board.SEARCH_MAX
print("board ok:", len(moves), "store-level rows,", meta["products"], "products,", meta["changes"], "chain-level changes")
