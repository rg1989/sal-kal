"""Run: .venv/bin/python tests/test_backfill.py
Kaggle CSVs written the way the publisher writes them (a cell equal to the one above it left empty, '' for a
really empty value), fetched into parts, replayed day by day and applied to a live DB that has its own rows."""
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile

TMP = tempfile.mkdtemp()
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import backfill, db as dbmod, trust  # noqa: E402

A, B, C = "7290058140886", "7290027600007", "7290803800003"


def ean(body):
    s = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return body + str((10 - s % 10) % 10)


MILK, SOAP, ODDP = ean("729001000001"), ean("729001000002"), ean("729001000003")
assert trust.gs1_ok(MILK) and trust.gs1_ok(SOAP) and trust.gs1_ok(ODDP)
NAMES = {MILK: "חלב תנובה 3% 1 ליטר", SOAP: "סבון ידיים נוזלי מרגוע", ODDP: "שוקולד פרה חלב 100 גרם"}

COLS = ["found_folder", "file_name", "chainid", "storeid", "itemcode", "itemname", "itemprice", "bisweighted"]


def kaggle_csv(files):
    """[(chain, store, ts, [(code, name, price, weighted)])] -> CSV text, masked like the publisher's writer."""
    out = io.StringIO()
    import csv
    w = csv.writer(out)
    w.writerow(COLS)
    for chain, store, ts, items in files:
        prev = None
        for code, name, price, weighted in items:
            row = ["app_data/dumps/X", f"PriceFull{chain}-{store:03d}-{ts}.xml", chain, f"{store:03d}", code,
                   name, price, weighted]
            row = ["''" if v == "" else str(v) for v in row]
            shown = row if prev is None else ["" if v == p and v != "''" else v for v, p in zip(row, prev)]
            prev = row
            w.writerow(shown)
    return out.getvalue()


# ---------------------------------------------------------------- 1. reading the CSV
text = kaggle_csv([(A, 1, 202501010010, [(MILK, NAMES[MILK], "5.00", 0), (SOAP, NAMES[SOAP], "5.00", 0),
                                         (ODDP, "", "7.90", 0), ("1234", "עגבניות", "7.90", 1)]),
                   (A, 2, 202501010010, [(MILK, NAMES[MILK], "5.00", 0)])])
assert "\n,,,," in text  # repeats really are blank cells
got = list(backfill.read_csv(io.StringIO(text, newline="")))
assert [g[0] for g in got] == [f"PriceFull{A}-001-202501010010.xml", f"PriceFull{A}-002-202501010010.xml"], got
rows = got[0][1]
assert rows[1] == (SOAP, "5.00", NAMES[SOAP], False), rows   # the price came from the row above
assert rows[2] == (ODDP, "7.90", "", False), rows            # '' = really no name, not the row above's
assert rows[3][3] is True                                    # weighed
assert got[1][1] == [(MILK, "5.00", NAMES[MILK], False)]     # each file starts whole again
red = backfill.reduce_file(*got[0])
assert red[:3] == (A, 1, 202501010010) and set(red[3]) == {MILK, SOAP, ODDP}, red  # the chain's own code is dropped
assert backfill.reduce_file("Promo7290058140886-001-202501010010.xml", rows) is None
assert backfill.reduce_file(f"PriceFull{A}-001-202501010010.xml", [(MILK, "0", "x", False)])[3] == {}

# ---------------------------------------------------------------- 2. days of files, fetched like Kaggle versions
D = [20250100 + i for i in range(0, 12)]  # D[1] = 2025-01-01 ... D[11] = 2025-01-11


def items(milk, soap="9.90", oddp="10.00", soap_name=None):
    return [(MILK, NAMES[MILK], milk, 0), (SOAP, soap_name or NAMES[SOAP], soap, 0), (ODDP, NAMES[ODDP], oddp, 0)]


def day_files(day, b=True, c=True, a_milk=("5.00", "5.50"), b_milk="5.20", c_milk="5.10", o4="100.00"):
    ts = day * 10000 + 10
    f = [(A, 1, ts, items(a_milk[0])), (A, 2, ts, items(a_milk[1])), (A, 3, ts, items("5.50")),
         (A, 4, ts, items("5.50", oddp=o4))]   # A4's chocolate is ten times the others': an outlier
    if b:
        f.append((B, 1, ts, items(b_milk, soap="4.90", soap_name="במבה אסם 80 גרם")))  # B's "soap" is Bamba
    if c:
        f.append((C, 1, ts, items(c_milk, soap="9.50", soap_name="סבון ידיים נוזלי")))
    return f


versions = {
    1: [*day_files(D[1]), (C, 99, 202301010010, items("3.00"))],          # + a closed branch's file from 2023
    2: [(B, 1, D[1] * 10000 + 2300, items("5.25", soap="4.90", soap_name="במבה אסם 80 גרם")),  # late file for day 1
        *day_files(D[2], c=False, a_milk=("5.40", "5.50"), b_milk="5.25")],             # C missing; A1's milk 5.00 -> 5.40
    # 3: no version at all (a day missing from the archive)
    4: day_files(D[4], a_milk=("5.40", "5.50"), b_milk="5.25", o4="110.00"),
    5: day_files(D[5], b=False, a_milk=("5.40", "5.50"), o4="110.00"),
    6: day_files(D[6], b=False, a_milk=("5.40", "5.50"), o4="110.00"),
    7: day_files(D[7], b=False, a_milk=("5.40", "5.50"), o4="110.00"),
    8: day_files(D[8], b=False, a_milk=("5.40", "5.50"), o4="110.00"),
    9: day_files(D[9], b=False, a_milk=("5.40", "5.50"), o4="110.00"),
    10: day_files(D[10], a_milk=("5.40", "5.50"), b_milk="5.30", o4="110.00"),  # B back after 5 days
    11: day_files(D[11], a_milk=("5.40", "5.50"), b_milk="5.30", o4="110.00"),
}
WORK = os.path.join(TMP, "work")
parts = os.path.join(WORK, "parts")
for v, files in versions.items():
    day = max(ts for _, _, ts, _ in files) // 10000
    created = f"{day // 10000}-{day // 100 % 100:02d}-{day % 100:02d}T23:30:00Z"
    by_chain = {}
    for f in files:
        by_chain.setdefault(f[0], []).append(f)
    listing = [(f"x/price_full_file_{ch}.csv", 1000, created) for ch in by_chain]

    @contextlib.contextmanager
    def opener(_v, path, _spool, by_chain=by_chain):
        yield io.StringIO(kaggle_csv(by_chain[path.split("_")[-1][:-4]]), newline="")

    m = backfill.fetch_version(v, parts, listing, opener)
    if v == 1:
        assert m["days"] == [D[1]] and len(m["blocks"]) == 6, m   # the 2023 file isn't kept
    if v == 2:
        assert m["days"] == [D[1], D[2]], m
ms = backfill.metas(WORK)
assert [m["version"] for m in ms] == [1, 2, 4, 5, 6, 7, 8, 9, 10, 11]
assert backfill.ready_until(ms[:1]) is None or backfill.ready_until(ms[:1]) < D[1]  # day 1's late files may follow
assert backfill.ready_until(ms) == D[1]              # versions after a missing number wait for it ...


# ... so the test treats every fetched version as in order (a real run fetches every number; a missing one
# leaves an empty part)
def ready(ms):
    return backfill.next_day(max(d for m in ms for d in m["days"]), -1)


backfill.ready_until = ready

# ---------------------------------------------------------------- 3. build, with a live DB to read names from
live = dbmod.connect()
live.execute("INSERT INTO products(key, code, name, scope) VALUES(?, ?, ?, 'global')", (MILK, MILK, "חלב תנובה 3%"))
live.commit()
assert backfill.build(WORK, os.environ["BASKET_DB"]) == 10  # days 1..10 (11 waits for a next version)
s = sqlite3.connect(os.path.join(WORK, "stage.db"))
H = lambda key, ch: s.execute("SELECT day, mn, mx, n FROM out_h WHERE key=? AND chain_id=? ORDER BY day",
                              (key, ch)).fetchall()
assert H(MILK, A) == [(D[1], 5.0, 5.5, 4), (D[2], 5.4, 5.5, 4)], H(MILK, A)
# B's day 1 is its later file (5.25); after day 4 it has no file for 5 days (more than BRIDGE_DAYS): its series end
# on its first day without one, and start again when it comes back
assert H(MILK, B) == [(D[1], 5.25, 5.25, 1), (D[5], None, None, 0), (D[10], 5.3, 5.3, 1)], H(MILK, B)
# C missed day 2 and nobody had day 3: held, not ended, and nothing changed when it came back
assert H(MILK, C) == [(D[1], 5.1, 5.1, 1)], H(MILK, C)
# B's "soap" is another product (its name disagrees with A's and C's): never in the history
assert H(SOAP, B) == [] and H(SOAP, A) == [(D[1], 9.9, 9.9, 4)] and H(SOAP, C) == [(D[1], 9.5, 9.5, 1)]
# A4's chocolate at 10x the typical price isn't counted
assert H(ODDP, A) == [(D[1], 10.0, 10.0, 3)], H(ODDP, A)
M = s.execute("SELECT key, chain_id, day, was, now, stores FROM out_m ORDER BY day").fetchall()
# A1's milk went up on day 2 (one branch); B's soap changes aren't kept (not the same product), the outlier branch's
# chocolate 100 -> 110 isn't either, and B's milk after 6 days without a file can't be dated
assert M == [(MILK, A, D[2], 5.0, 5.4, 1)], M
# a second run carries on from where it stopped
assert backfill.build(WORK, os.environ["BASKET_DB"]) == 0

# ---------------------------------------------------------------- 4. apply to a live DB that has its own rows
L = D[8]  # pretend the live history began on day 8 and the live board on day 9
live.execute("INSERT INTO u.settings VALUES('history_since', ?)", (str(L),))
live.execute("INSERT INTO u.settings VALUES('moves_since', ?)", (str(D[9]),))
live.execute("INSERT INTO price_history VALUES(?, ?, ?, 5.4, 5.5, 4)", (MILK, A, L))   # the indexer's own row
live.execute("INSERT INTO price_history VALUES(?, ?, ?, 7.0, 7.0, 1)", (MILK, A, D[9]))
live.commit()
before = live.execute("SELECT * FROM price_history WHERE day >= ?", (L,)).fetchall()
backfill.apply(WORK, os.environ["BASKET_DB"])
live = dbmod.connect()
ph = lambda key, ch: live.execute("SELECT day, mn, mx FROM price_history WHERE key=? AND chain_id=? ORDER BY day",
                                  (key, ch)).fetchall()
assert [tuple(r) for r in ph(MILK, A)] == [(D[1], 5.0, 5.5), (D[2], 5.4, 5.5), (L, 5.4, 5.5), (D[9], 7.0, 7.0)]
# C was still open when the live history began and the indexer has no row for it then: it ends that day
assert [tuple(r) for r in ph(MILK, C)] == [(D[1], 5.1, 5.1), (L, None, None)], ph(MILK, C)
assert [tuple(r) for r in ph(MILK, B)] == [(D[1], 5.25, 5.25), (D[5], None, None)], ph(MILK, B)  # not after L
mv = live.execute("SELECT key, chain_id, day FROM price_moves").fetchall()
assert [tuple(r) for r in mv] == [(MILK, A, D[2])], mv
setting = lambda k: (live.execute("SELECT value FROM u.settings WHERE key=?", (k,)).fetchone() or [None])[0]
assert setting("history_since") == str(D[1]) and setting("history_live_since") == str(L)
assert setting("moves_since") == str(D[2]) and setting("moves_live_since") == str(D[9])
src = backfill.source(live)
assert src["until"] == L and src["history"] == [D[1], D[5]] and src["license"].startswith("CC BY-NC-SA"), src
from app import server  # noqa: E402  (the product page's chart says where the older part comes from)
h = server.product_history(MILK)
assert h["source"]["until"] == L and h["source"]["by"].startswith("Sefi Erlich") and h["since"] == D[1], h
assert server.cart_history({"items": [{"key": MILK, "qty": 1}], "chains": [A]})["source"]["until"] == L
n = live.execute("SELECT count(*) FROM price_history").fetchone()[0]
backfill.apply(WORK, os.environ["BASKET_DB"])  # again: the same rows, not twice, and the live start days kept
live = dbmod.connect()
assert live.execute("SELECT count(*) FROM price_history").fetchone()[0] == n
assert setting("history_live_since") == str(L)
backfill.remove(os.environ["BASKET_DB"])
live = dbmod.connect()
assert [tuple(r) for r in live.execute("SELECT * FROM price_history ORDER BY key, chain_id, day")] == \
    [tuple(r) for r in before], "remove leaves exactly the indexer's rows"
assert live.execute("SELECT count(*) FROM price_moves").fetchone()[0] == 0
assert setting("history_since") == str(L) and setting("backfill") is None and backfill.source(live) is None
# history only (the Price Board's older changes wait until the board names the source)
backfill.apply(WORK, os.environ["BASKET_DB"], moves=False)
live = dbmod.connect()
assert live.execute("SELECT count(*) FROM price_moves").fetchone()[0] == 0
assert setting("moves_since") == str(D[9]) and setting("history_since") == str(D[1])
assert [tuple(r) for r in ph(MILK, A)][:2] == [(D[1], 5.0, 5.5), (D[2], 5.4, 5.5)]
backfill.remove(os.environ["BASKET_DB"])
print("backfill ok")
