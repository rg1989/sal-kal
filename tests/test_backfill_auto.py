"""Run: .venv/bin/python tests/test_backfill_auto.py
The gap filler (backfill.auto): weekly samples + a few daily days are applied, then auto fills the skipped days in
blocks, newest first. Afterwards every series must read, day by day, exactly as a straight daily build of the same
days reads, and the price changes must be the same ones."""
import contextlib
import csv
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile

TMP = tempfile.mkdtemp()
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import backfill, db as dbmod  # noqa: E402

A_, B_, C_ = "7290058140886", "7290027600007", "7290803800003"


def ean(body):
    s = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return body + str((10 - s % 10) % 10)


MILK, SOAP, NEW = ean("729001000001"), ean("729001000002"), ean("729001000004")
E = [20241200 + i for i in range(0, 13)]  # E[1] = 1 Dec 2024 ... E[12]


def items(day, chain, store):
    """Each chain's shelf on a day: prices that change on some days, a product that only appears from E[5]."""
    milk = {A_: 5.0 if day < E[3] else 5.4 if day < E[6] else 5.2, B_: 5.3 if day < E[7] else 5.6, C_: 5.1}[chain]
    milk += 0.5 * (store - 1)
    out = [(MILK, "חלב תנובה 3% 1 ליטר", f"{milk:.2f}"),
           (SOAP, "סבון ידיים נוזלי" if chain != B_ else "במבה אסם 80 גרם", "9.90" if day < E[8] else "10.90")]
    if day >= E[5] and chain != C_:
        out.append((NEW, "שוקולד פרה 100 גרם", "6.90" if chain == A_ else "7.20"))
    return out


def files_of(day):
    chains = [(A_, 1), (A_, 2), (B_, 1), (C_, 1)]
    if day == E[4]:
        chains = [x for x in chains if x[0] != C_]       # C has no file one day
    if E[6] <= day <= E[7]:
        chains = [x for x in chains if x[0] != B_]       # B none for two days
    return [(ch, st, day * 10000 + 10, items(day, ch, st)) for ch, st in chains]


def kaggle_csv(files):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["file_name", "itemcode", "itemname", "itemprice", "bisweighted"])
    for chain, store, ts, its in files:
        prev = None
        for code, name, price in its:
            row = [f"PriceFull{chain}-{store:03d}-{ts}.xml", code, name, price, "0"]
            w.writerow(row if prev is None else ["" if v == p else v for v, p in zip(row, prev)])
            prev = row
    return out.getvalue()


def fetch(day, parts, sample=False, v=None):
    files = files_of(day)
    created = f"{day // 10000}-{day // 100 % 100:02d}-{day % 100:02d}T03:30:00Z"

    @contextlib.contextmanager
    def opener(_v, _path, _spool):
        yield io.StringIO(kaggle_csv(files), newline="")
    return backfill.fetch_version(v or 1000 + day % 100, parts, [("x/price_full_file_all.csv", 1, created)], opener,
                                  sample)


def on(rows, day):
    """A change-only series' value on a day (None: no confirmed price that day)."""
    v = None
    for d, mn, mx in rows:
        if d > day:
            break
        v = None if mn is None else (round(mn, 2), round(mx, 2))
    return v


def series(con, table, days_to):
    out = {}
    for k, ch, d, mn, mx in con.execute(f"SELECT key, chain_id, day, mn, mx FROM {table} WHERE day <= ? "
                                        "ORDER BY key, chain_id, day", (days_to,)):
        out.setdefault((k, ch), []).append((d, mn, mx))
    return out


# ---------------------------------------------------------------- the reference: every day, built straight through
REF = os.path.join(TMP, "ref")
for d in E[1:12]:
    fetch(d, os.path.join(REF, "parts"))
live = dbmod.connect()
live.commit()
backfill.build(REF, os.environ["BASKET_DB"], until=E[10])
ref = sqlite3.connect(os.path.join(REF, "stage.db"))
ref_h = series(ref, "out_h", E[10])
ref_m = {tuple(r) for r in ref.execute("SELECT key, chain_id, day, was, now, stores FROM out_m WHERE day <= ?",
                                       (E[10],))}
assert ref_m, "the fixture has price changes"

# ---------------------------------------------------------------- weekly samples E1, E8 + daily E9, E10, applied
WORK = os.path.join(TMP, "work")
fetch(E[1], os.path.join(WORK, "parts"), True, 1)
fetch(E[8], os.path.join(WORK, "parts"), True, 8)
for d in (E[9], E[10], E[11]):
    fetch(d, os.path.join(WORK, "parts"))
with open(os.path.join(WORK, "plan.json"), "w") as f:
    json.dump({"samples": [[E[1], 1], [E[8], 8]]}, f)
backfill.build(WORK, os.environ["BASKET_DB"], until=E[10])
live.execute("INSERT INTO u.settings VALUES('history_since', ?)", (str(E[11]),))
live.execute("INSERT INTO u.settings VALUES('moves_since', ?)", (str(E[11]),))
live.commit()
backfill.apply(WORK, os.environ["BASKET_DB"])
live = dbmod.connect()
src = backfill.source(live)
assert src["weekly"] == [E[1], E[8]] and src["daily_from"] == E[9], src

# ---------------------------------------------------------------- the gap filler, two blocks of 4 days
AUTO = os.path.join(TMP, "auto-work")
versions = lambda lo, hi: [1000 + d % 100 for d in E[1:12] if lo <= d <= hi]
fetcher = lambda v, parts: fetch(20241200 + v - 1000, parts)
run = lambda: backfill.auto(os.environ["BASKET_DB"], AUTO, days=4, start=E[1], versions=versions, fetcher=fetcher)
assert run() == 4                      # E5..E8
live = dbmod.connect()
src = backfill.source(live)
assert src["daily_from"] == E[5] and src["weekly"] == [E[1], E[1]], src
assert run() == 4                      # E1..E4
assert run() == 0                      # every day is in
live = dbmod.connect()
src = backfill.source(live)
assert src["daily_from"] == E[1] and src["weekly"] is None and src["history"][0] == E[1], src
assert not os.path.exists(os.path.join(AUTO, "auto"))                 # its files are gone

# every series, every day, reads as the straight daily build does
got = series(live, "price_history", E[10])
for pair in set(ref_h) | set(got):
    for d in E[1:11]:
        assert on(ref_h.get(pair, []), d) == on(got.get(pair, []), d), (pair, d, ref_h.get(pair), got.get(pair))
got_m = {tuple(r) for r in live.execute("SELECT key, chain_id, day, was, now, stores FROM price_moves")}
assert got_m == ref_m, (got_m ^ ref_m)

# the one-off apply would take the filled days out again: it refuses
try:
    backfill.apply(WORK, os.environ["BASKET_DB"])
    raise AssertionError("apply after auto must refuse")
except SystemExit:
    pass

# a stopped run resumes: its downloaded files are kept until the block is applied
backfill.remove(os.environ["BASKET_DB"])
shutil.rmtree(AUTO, ignore_errors=True)
print("backfill auto ok")
