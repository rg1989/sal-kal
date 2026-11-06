"""Run: .venv/bin/python tests/test_board.py
The public Price Board (app/board.py, /prices in app/server.py): store price changes are caught as the indexer writes
a newer file and kept only when confirmed; the pages are built ahead and served as they are, the same bytes for
everyone whatever the query string; anything else is a 404; search needs sign-in."""
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

from app import auth, board, indexer  # noqa: E402
from app import db as dbmod  # noqa: E402

indexer.run(DUMPS)
c = dbmod.connect()
RAMI, MILK, BAMBA = "7290058140886", "7290004131074", "7290000066318"
before = dict(c.execute("SELECT store_id, price FROM prices WHERE key=? AND chain_id=?", (MILK, RAMI)).fetchall())
assert len(before) >= 2, before

# Rami Levy raises milk at every store, and Bamba (and its weighed tomatoes) at store 10 only, in a later Price file
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
c.commit()

ver = board.build(c, os.environ["BOARD_DIR"], "https://sal-kal.com")
assert raw_count() == 0                                               # read and emptied
moves = c.execute("SELECT key, chain_id, day, was, now, stores FROM price_moves ORDER BY key").fetchall()
today = int(later[:8])
milk = [m for m in moves if m[0] == MILK]
assert len({m[3] for m in milk}) == len(milk)                         # one row per old price
assert sum(m[5] for m in milk) == len(before) and all(m[4] == 7.49 and m[2] == today for m in milk), moves
assert [tuple(m) for m in moves if m[0] == BAMBA] == [(BAMBA, RAMI, today, m[3], 6.2, 1) for m in moves if m[0] == BAMBA]
assert not any(m[3] == 5.0 for m in moves), moves                    # the undatable one
assert c.execute("SELECT value FROM settings WHERE key='moves_since'").fetchone()[0] == str(today)
again = board.collect(c)                                              # nothing new: nothing written twice
assert again == 0 and c.execute("SELECT count(*) FROM price_moves").fetchone()[0] == len(moves)

root = os.path.join(os.environ["BOARD_DIR"], ver)
files = sorted(os.listdir(root))
assert {"p1.html.gz", "changes.html.gz", "rows.json.gz", "meta.json", "sitemap.xml"} <= set(files), files
meta = json.load(open(os.path.join(root, "meta.json")))
assert meta["pages"] == 1 and meta["products"] >= 20, meta
p1 = gzip.decompress(open(os.path.join(root, "p1.html.gz"), "rb").read()).decode()
assert "חלב תנובה" in p1 and "7.49" in p1 and 'class="up"' in p1
assert "/static/prices.js?v=" in p1                                   # stamped at build: served as is
assert fx.WEIGHED[0] not in p1                                           # chain codes (produce) aren't on the board
ch = gzip.decompress(open(os.path.join(root, "changes.html.gz"), "rb").read()).decode()
assert "במבה" in ch and "6.20" in ch

# an older build is replaced by the next, and only KEEP stay
for _ in range(3):
    time.sleep(0.01)
    v = board.build(c, os.environ["BOARD_DIR"], "https://sal-kal.com")
assert len([e for e in os.listdir(os.environ["BOARD_DIR"]) if e.isdigit()]) == board.KEEP
assert open(os.path.join(os.environ["BOARD_DIR"], "current")).read() == v

# ---------------------------------------------------------------- through the web server
from app import server  # noqa: E402

server.con = lambda: c
auth.sign_in(c, "google", "g-owner", "owner@example.com", True, "Owner", None)
alice = auth.sign_in(c, "google", "g-alice", "alice@example.com", True, "Alice", None)
ALICE = f"{auth.SESSION_COOKIE}={auth.new_session(c, alice)}"


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
for bad in ("/prices/2", "/prices/0", "/prices/01", "/prices/-1", "/prices/1e3", "/prices/abc", "/prices/99999999999",
            "/prices/changes/x", "/prices/%2e%2e"):
    s, h, b = call(bad)
    assert s == 404 and len(b) < 200, (bad, s)                        # the same small 404, nothing computed
assert call("/prices/changes", headers=GZ)[0] == 200
s, h, sm = call("/sitemap.xml")
assert s == 200 and b"https://sal-kal.com/prices" in sm

# search: signed in only
assert call("/api/prices?q=חלב")[0] == 401
s, _, out = call("/api/prices?q=" + "%D7%97%D7%9C%D7%91", cookie=ALICE)  # חלב
out = json.loads(out)
assert s == 200 and out["total"] >= 2 and all("חלב" in r["name"] for r in out["rows"]), out
s, _, out = call(f"/api/prices?q={MILK[:7]}", cookie=ALICE)             # a barcode's start
assert any(r["key"] == MILK for r in json.loads(out)["rows"])
s, _, out = call("/api/prices?sort=rises", cookie=ALICE)
keys = [r["key"] for r in json.loads(out)["rows"]]
assert set(keys) == {MILK, BAMBA}, keys                               # only what rose, the biggest rise first
s, _, out = call("/api/prices?moved=true&sort=nonsense&cat=nope&page=-5", cookie=ALICE)
out = json.loads(out)
assert s == 200 and out["page"] == 1 and {r["key"] for r in out["rows"]} == {MILK, BAMBA}
s, _, out = call("/api/prices?q=" + "x " * 500, cookie=ALICE)           # long input: cut, not refused
assert s == 200
print("board ok:", len(moves), "changes,", meta["products"], "products")
