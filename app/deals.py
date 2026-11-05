"""Precompute real deals into the `deals` table (run after each indexing pass).

A deal counts only if its terms are confirmed (app.deal_terms.promo_terms: the description states the same terms as
the numbers, or the price and percentage agree at the shelf price), it is open to every shopper (no club, staff,
coupon or "spend ₪X"), the store's price and deal files are current, the product is a manufacturer barcode the chain
names like the other chains, its shelf price is trusted and has been checked, and its per-unit price beats the
product's TYPICAL price (products.median) — so a "sale" on an inflated shelf price doesn't show up.
Run: python -m app.deals
"""
import argparse
import logging
import time

from . import db as dbmod
from . import trust
from .engine import _rows, _terms, comparable, deal_cap, deal_units, get_setting

log = logging.getLogger("deals")

SCHEMA = """CREATE TABLE IF NOT EXISTS deals(
  key TEXT, chain_id TEXT, store_id INTEGER, price REAL, unit_price REAL, typical REAL, save_pct REAL,
  min_qty REAL, description TEXT, start_ts INTEGER, end_ts INTEGER, PRIMARY KEY(key, chain_id, store_id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS deals_store ON deals(chain_id, store_id);"""


def build(con):
    con.executescript(SCHEMA)
    now = int(time.strftime("%Y%m%d%H%M"))
    cutoff = trust.fresh_cutoff()
    checked_to = int(get_setting(con, "confirmed_through", 0) or 0)  # 0: not tracked, every price was checked
    t = time.time()
    price_flags = {(r[0], r[1], r[2]) for r in _rows(con, "SELECT key, chain_id, store_id FROM price_flags")}
    name_flags = {(r[0], r[1]) for r in _rows(con, "SELECT key, chain_id FROM name_flags")}
    # typical price = the indexer's median of confirmed prices, for widely sold manufacturer barcodes
    typical = {}
    if "median" in {r[1] for r in con.execute("PRAGMA table_info(products)")}:
        typical = {key: med for key, med in con.execute(
            "SELECT key, median FROM products WHERE chain_count >= 2 AND median > 0") if comparable(key)}
    log.info("typical prices for %d products", len(typical))
    if not typical or not _rows(con, "SELECT 1 FROM item_names LIMIT 1"):
        rows = []
    else:
        cols = {r[1] for r in con.execute("PRAGMA table_info(promos)")}
        extra = ", ".join(f"pr.{c}" if c in cols else f"NULL AS {c}" for c in ("max_qty", "remarks"))
        rows = con.execute(f"""SELECT pi.key, pi.chain_id, pi.store_id, p.price, pr.min_qty, pr.discounted_price,
                   pr.discount_rate, pr.description, pr.start_ts, pr.end_ts, {extra}
            FROM promo_items pi
            JOIN promos pr ON pr.chain_id=pi.chain_id AND pr.store_id=pi.store_id AND pr.promo_id=pi.promo_id
            JOIN prices p ON p.key=pi.key AND p.chain_id=pi.chain_id AND p.store_id=pi.store_id
            JOIN stores s ON s.chain_id=pi.chain_id AND s.store_id=pi.store_id
            WHERE pr.club_only=0 AND coalesce(pr.description, '') NOT LIKE '%עובד%'
              AND (pr.end_ts IS NULL OR pr.end_ts >= ?) AND (pr.start_ts IS NULL OR pr.start_ts <= ?)
              AND pi.ts >= s.promo_full_ts AND pr.ts >= s.promo_full_ts AND s.promo_full_ts >= ?
              AND p.ts >= s.price_full_ts AND s.last_price_ts >= ? AND (? = 0 OR p.ts <= ?)
              AND instr(pi.key, ':')=0
              AND EXISTS (SELECT 1 FROM item_names n WHERE n.key=pi.key AND n.chain_id=pi.chain_id
                          AND n.ts >= {int(trust.names_cutoff())})""",
                           (now, now, cutoff, cutoff, checked_to, checked_to))
    best = {}  # (key, chain, store) -> row
    memo = {}
    for r in rows:
        key, chain, store, price, mq, dp, rate, desc, start, end, max_qty, remarks = r
        typ = typical.get(key)
        if not typ or not price or price < 2:
            continue
        if (key, chain, store) in price_flags or (key, chain) in name_flags:
            continue
        pr = {"description": desc, "min_qty": mq, "discounted_price": dp, "discount_rate": rate,
              "remarks": remarks, "max_qty": max_qty}
        terms = _terms(pr, price, False, memo)
        if not terms:
            continue
        units = deal_units(terms)
        if units > 6:
            continue  # "10 for ₪X" isn't something you'd buy for a household
        cost = trust.cost_with(price, units, terms, deal_cap(pr, memo))
        if cost is None:
            continue
        unit = cost / units
        save = 1 - unit / typ
        # ponytail: outside 8–70% it's almost always a data error or a gift item
        if not (0.08 <= save <= 0.7) or unit > price:
            continue
        k = (key, chain, store)
        if k not in best or unit < best[k][4]:
            best[k] = (key, chain, store, price, round(unit, 2), round(typ, 2), round(save, 4), units,
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
