"""Precompute real deals into the `deals` table (run after each indexing pass).

A deal counts only if its terms are confirmed (app.trust.promo_terms: the description states the same terms as
the numbers, or the price and percentage agree at the shelf price), it is open to every shopper (no club, staff,
coupon or "spend ₪X"), the store's prices are current and the shelf price is trusted, and its per-unit price beats
the product's TYPICAL (median) price — so a "sale" on an inflated shelf price doesn't show up.
Run: python -m app.deals
"""
import argparse
import logging
import statistics
import time

from . import db as dbmod
from . import trust
from .engine import _rows, _terms, deal_units

log = logging.getLogger("deals")

SCHEMA = """CREATE TABLE IF NOT EXISTS deals(
  key TEXT, chain_id TEXT, store_id INTEGER, price REAL, unit_price REAL, typical REAL, save_pct REAL,
  min_qty REAL, description TEXT, start_ts INTEGER, end_ts INTEGER, PRIMARY KEY(key, chain_id, store_id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS deals_store ON deals(chain_id, store_id);"""


def _medians(con, want, cutoff, price_flags, name_flags):
    """Median current shelf price of each wanted barcode at fresh stores, skipping untrusted prices.
    One pass over the prices in key order (their primary key), so memory stays flat."""
    out, cur, ps = {}, None, []
    for key, chain, store, price in con.execute("""SELECT p.key, p.chain_id, p.store_id, p.price FROM prices p
            JOIN stores s ON s.chain_id=p.chain_id AND s.store_id=p.store_id
            WHERE p.ts >= s.price_full_ts AND s.last_price_ts >= ? AND p.price > 0 AND instr(p.key, ':')=0
            ORDER BY p.key""", (cutoff,)):
        if key != cur:
            if ps:
                out[cur] = statistics.median(ps)
            cur, ps = key, []
        if key in want and (key, chain, store) not in price_flags and (key, chain) not in name_flags:
            ps.append(price)
    if ps:
        out[cur] = statistics.median(ps)
    return out


def build(con):
    con.executescript(SCHEMA)
    now = int(time.strftime("%Y%m%d%H%M"))
    cutoff = trust.fresh_cutoff()
    t = time.time()
    price_flags = {(r[0], r[1], r[2]) for r in _rows(con, "SELECT key, chain_id, store_id FROM price_flags")}
    name_flags = {(r[0], r[1]) for r in _rows(con, "SELECT key, chain_id FROM name_flags")}
    # typical price = median shelf price at current stores (the indexer keeps it in products.median), for widely
    # sold barcoded products
    has_median = "median" in {r[1] for r in con.execute("PRAGMA table_info(products)")}
    typical, need = {}, set()
    for key, med in con.execute(f"""SELECT key, {'median' if has_median else 'NULL'} FROM products
                                    WHERE scope='global' AND chain_count >= 2 AND min_price > 0"""):
        if med and med > 0:
            typical[key] = med
        else:
            need.add(key)
    if need:
        typical.update({k: v for k, v in _medians(con, need, cutoff, price_flags, name_flags).items() if v > 0})
    log.info("typical prices for %d products (%d computed here, %.0fs)", len(typical), len(need), time.time() - t)

    cols = {r[1] for r in con.execute("PRAGMA table_info(promos)")}
    extra = ", ".join(f"pr.{c}" if c in cols else f"NULL AS {c}" for c in ("max_qty", "remarks"))
    best = {}  # (key, chain, store) -> row
    memo = {}
    for r in con.execute(f"""SELECT pi.key, pi.chain_id, pi.store_id, p.price, pr.min_qty, pr.discounted_price,
               pr.discount_rate, pr.description, pr.start_ts, pr.end_ts, {extra}
        FROM promo_items pi
        JOIN promos pr ON pr.chain_id=pi.chain_id AND pr.store_id=pi.store_id AND pr.promo_id=pi.promo_id
        JOIN prices p ON p.key=pi.key AND p.chain_id=pi.chain_id AND p.store_id=pi.store_id
        JOIN stores s ON s.chain_id=pi.chain_id AND s.store_id=pi.store_id
        WHERE pr.club_only=0 AND coalesce(pr.description, '') NOT LIKE '%עובד%'
          AND (pr.end_ts IS NULL OR pr.end_ts >= ?) AND (pr.start_ts IS NULL OR pr.start_ts <= ?)
          AND pi.ts >= s.promo_full_ts AND p.ts >= s.price_full_ts AND s.last_price_ts >= ?
          AND instr(pi.key, ':')=0""", (now, now, cutoff)):
        key, chain, store, price, mq, dp, rate, desc, start, end, max_qty, remarks = r
        typ = typical.get(key)
        if not typ or not price or price < 2:
            continue
        if (key, chain, store) in price_flags or (key, chain) in name_flags:
            continue
        terms = _terms({"description": desc, "min_qty": mq, "discounted_price": dp, "discount_rate": rate,
                        "remarks": remarks}, price, False, memo)
        if not terms:
            continue
        units = deal_units(terms)
        if units > 6:
            continue  # "10 for ₪X" isn't something you'd buy for a household
        cost = trust.cost_with(price, units, terms, max_qty)
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
