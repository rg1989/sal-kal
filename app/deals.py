"""Precompute real deals into the `deals` table (run after each indexing pass).

A deal counts only if it is open to every shopper (no club, no staff, no "spend ₪X")
and its per-unit price beats the product's TYPICAL price across all stores — so a
"sale" on an inflated shelf price doesn't show up. Run: python -m app.deals
"""
import argparse
import logging
import time
from collections import defaultdict

from . import db as dbmod
from .engine import line_cost

log = logging.getLogger("deals")

SCHEMA = """CREATE TABLE IF NOT EXISTS deals(
  key TEXT, chain_id TEXT, store_id INTEGER, price REAL, unit_price REAL, typical REAL, save_pct REAL,
  min_qty REAL, description TEXT, start_ts INTEGER, end_ts INTEGER, PRIMARY KEY(key, chain_id, store_id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS deals_store ON deals(chain_id, store_id);"""


def build(con):
    con.executescript(SCHEMA)
    now = int(time.strftime("%Y%m%d%H%M"))
    t = time.time()
    # typical price = average shelf price across stores, for widely sold barcoded products
    typical = {r[0]: r[1] for r in con.execute(
        "SELECT key, (min_price + max_price) / 2.0 FROM products WHERE scope='global' AND chain_count >= 2 AND min_price > 0")}
    avg = defaultdict(lambda: [0.0, 0])
    for key, price in con.execute("""SELECT p.key, p.price FROM prices p JOIN stores s
            ON s.chain_id=p.chain_id AND s.store_id=p.store_id WHERE p.ts >= s.price_full_ts AND instr(p.key, ':')=0"""):
        if key in typical:
            a = avg[key]; a[0] += price; a[1] += 1
    typical = {k: a[0] / a[1] for k, a in avg.items() if a[1]}
    log.info("typical prices for %d products (%.0fs)", len(typical), time.time() - t)

    best = {}  # (key, chain, store) -> row
    for r in con.execute("""SELECT pi.key, pi.chain_id, pi.store_id, p.price, pr.min_qty, pr.discounted_price,
               pr.discount_rate, pr.description, pr.start_ts, pr.end_ts
        FROM promo_items pi
        JOIN promos pr ON pr.chain_id=pi.chain_id AND pr.store_id=pi.store_id AND pr.promo_id=pi.promo_id
        JOIN prices p ON p.key=pi.key AND p.chain_id=pi.chain_id AND p.store_id=pi.store_id
        JOIN stores s ON s.chain_id=pi.chain_id AND s.store_id=pi.store_id
        WHERE pr.club_only=0 AND coalesce(pr.description, '') NOT LIKE '%עובד%'
          AND (pr.end_ts IS NULL OR pr.end_ts >= ?) AND (pr.start_ts IS NULL OR pr.start_ts <= ?)
          AND pi.ts >= s.promo_full_ts AND p.ts >= s.price_full_ts AND instr(pi.key, ':')=0""", (now, now)):
        key, chain, store, price, mq, dp, rate, desc, start, end = r
        typ = typical.get(key)
        if not typ or not price or price < 2:
            continue
        mq = max(mq or 1, 1)
        if mq > 6:
            continue  # "10 for ₪X" isn't something you'd buy for a household
        cost, promo = line_cost(price, mq, [{"min_qty": mq, "discounted_price": dp, "discount_rate": rate}])
        if not promo:
            continue
        unit = cost / mq
        save = 1 - unit / typ
        # ponytail: outside 8–70% it's almost always a data error or a gift item
        if not (0.08 <= save <= 0.7) or unit > price:
            continue
        k = (key, chain, store)
        if k not in best or unit < best[k][4]:
            best[k] = (key, chain, store, price, round(unit, 2), round(typ, 2), round(save, 4), mq,
                       (desc or "")[:120], start, end)
    with con:
        con.execute("DELETE FROM deals")
        con.executemany("INSERT INTO deals VALUES(?,?,?,?,?,?,?,?,?,?,?)", best.values())
    with con:  # separate, tiny transaction on user.db
        con.execute("INSERT INTO settings VALUES('deals_version', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(int(time.time())),))
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    log.info("%d deals across %d products (%.0fs)", len(best), len({k[0] for k in best}), time.time() - t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build(dbmod.connect(a.db))


if __name__ == "__main__":
    main()
