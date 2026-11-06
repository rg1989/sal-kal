"""Price Watch (/prices, "מעקב מחירים"): what every barcode product sold at two or more chains costs at each chain now,
which chains raised and cut shelf prices over time, and how each chain's prices compare with the middle chain's.

Two levels, per the owner (2026-11-06): the page itself is for everyone and light, the depth is for people signed in.
  - The page (/prices, the same bytes for everyone, so Cloudflare may cache it): the default period's rises and cuts
    across chains, the chains ranked by price level with their counts, a few of the widest rises (PEEK), the method.
    Nothing a visitor sends makes the server read the database: after each index run (python -m app.board, in
    sal-index) the page is written out ready, gzipped, under <data>/board/<version>/, and the server hands it out.
  - Signed in (prices.js asks /api/me, then fills the page in): every period, each chain's chart and biggest changes
    (GET /api/prices/data/chg-<period>.json) and search (GET /api/prices, from rows.json.gz, kept in memory). Both
    need sign-in like every /api call; the page shows what they hold and offers the sign-in instead.

The rules, so that nothing on the page says more about a chain than its own files show:
  - Branches are physical stores outside Eilat: online stores (StoreType 2, or a web shop by name) and Eilat's
    branches (no VAT there) are left out of every count, price and change (store_kinds).
  - A price change is the chain's only when at least MIN_BRANCHES of those branches made it on the same day, to the
    same new price (chain_events). A change at one or two branches is shown nowhere.
  - A chain's price for a product is its middle (median) branch's; its level is the geometric mean, over products
    sold at LEVEL_CHAINS chains or more, of that price against the middle chain's (the median of the chains').
  - "At X of Y branches": Y = the chain's branches that listed the product from that day on, so Y covers the same
    period as X.
  - No line is drawn from price_history (a chain's cheapest price moves when a branch's file goes out of date):
    the charts count chain-level changes only.

Where the changes come from (collect): one store's shelf price for a barcode product moving from one published file
to the next (the prices_moved trigger, app.db, catches it as the indexer writes it), kept when the product is the
same one at that chain (named like the other chains), both prices are in line with its typical price
(trust.OUTLIER) and the old price was published within RAW_GAP_DAYS of the new one. Per store, one net change a day.
  price_moves     every store, as before (the archive backfill writes it too): which stores isn't kept, so for days
                  before branch_moves began a change counts with the lower bound stores - the chain's online and
                  Eilat stores, shown as "at least X".
  branch_moves    branches only, exact, from the day after the first build that wrote it (setting
                  branch_moves_since); branch_moves_st holds each branch's net change per day for the last days.
Shelf prices only; deals are not included. Built only from the chains' public price files, never carts, accounts,
searches or usage statistics.
"""
import argparse
import bisect
import gzip
import html
import json
import logging
import math
import os
import re
import shutil
import statistics
import time
from collections import Counter, defaultdict

from . import db as dbmod
from . import backfill, reports, trust
from .chains import ONLINE_HINTS
from .engine import comparable, get_setting

try:  # the app's own rule for a branch you can walk into (no web shop, warehouse or delivery app's listing)
    from .chains import walk_in
except ImportError:  # an older chains.py: StoreType 2 or a web shop's name
    walk_in = None

log = logging.getLogger("board")

MIN_CHAINS = 2       # a product is searchable when this many chains have a current branch price for it
MIN_BRANCHES = 3     # a price change is the chain's when at least this many of its branches made it, same day and price
LEVEL_CHAINS = 3     # a product counts toward the price levels when this many chains sell it
MIN_PRODUCTS = 50    # a chain is listed when its level covers this many products
RAW_GAP_DAYS = 4     # an old price older than this before the new one is not a change we can date
LEDGER_DAYS = RAW_GAP_DAYS  # each branch's net change per day is kept this long (late files still fold in)
KEEP = 2             # built versions kept (the current one and the one before)
PERIODS = (7, 30, 90, 365)  # periods offered once the recorded changes go back further than each; then "all"
WEEKLY_OVER = 120    # a period longer than this many days is charted by week
TOP_DATA = 30        # each chain's biggest rises and cuts in the period's data file, per direction (signed in)
PEEK = 3             # rises at the most branches on the page itself (what visitors see of the lists)
PEEK_MAX = 0.5       # ... each at most +50%
RANK_CUT = 12        # chains in the ranking before "All N chains" (the rest are on the page, folded; prices.css)
PER_CHAIN_EVENTS = 4  # a search result shows this many of each chain's latest changes
SEARCH_MAX = 30      # search results per query
DATA_NAME = re.compile(r"chg-(?:\d{1,3}|all)\.json")  # the data files the web server hands out
EILAT = "2600"       # Eilat's CBS town code

BRANCH, ONLINE, EILAT_STORE = "branch", "online", "eilat"

CHAINS_HE = {  # as in app/static/i18n.js
    'Shufersal': 'שופרסל', 'Rami Levy': 'רמי לוי', 'Victory': 'ויקטורי', 'Yohananof': 'יוחננוף', 'Osher Ad': 'אושר עד',
    'Tiv Taam': 'טיב טעם', 'Hazi Hinam': 'חצי חינם', 'Keshet Teamim': 'קשת טעמים', 'Super-Pharm': 'סופר-פארם',
    'King Store': 'קינג סטור', 'Carrefour': 'קרפור', 'Yeinot Bitan': 'יינות ביתן', 'Mahsanei HaShuk': 'מחסני השוק',
    'Yellow': 'יילו', 'Wolt Market': 'וולט מרקט', 'Fresh Market': 'פרש מרקט', 'Good Pharm': 'גוד פארם',
    'Salach Dabach': 'סאלח דבאח', 'Zol Ubegadol': 'זול ובגדול', 'Dor Alon': 'דור אלון', 'Bareket': 'ברקת',
    'Polizer': 'פוליצר', 'Super Yuda': 'סופר יודה', 'Super Sapir': 'סופר ספיר', 'Shuk HaIr': 'שוק העיר',
    'Netiv HaHesed': 'נתיב החסד', 'Maayan 2000': 'מעיין 2000', 'Shefa Birkat Hashem': 'שפע ברכת השם', 'H. Cohen': 'ח. כהן',
    'City Market': 'סיטי מרקט', 'City Market Kiryat Gat': 'סיטי מרקט קריית גת',
    'City Market (main company)': 'סיטי מרקט חברה ראשית', 'Mishnat Yosef': 'משנת יוסף',
    'Stop Market': 'סטופ מרקט',
}
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def board_dir(db_path=None):
    return os.environ.get("BOARD_DIR") or os.path.join(os.path.dirname(os.path.abspath(db_path or dbmod.DB_PATH)), "board")


# ---------------------------------------------------------------- 0. which stores count
def is_eilat(city, name, address):
    """An Eilat store: its town is Eilat (CBS code 2600, or the name). Only when the town is missing do the name and
    address decide, since "אח"י אילת" and "דרך אילת" are streets in Haifa and Tel Aviv."""
    city = str(city or "").strip()
    if city.isdigit():
        return city == EILAT
    if city:
        return "אילת" in city or "eilat" in city.lower()
    return "אילת" in (name or "") or "אילת" in (address or "")


def store_kind(chain_id, store_type, name, sub_chain, city, address):
    """BRANCH (a shop you can walk into, outside Eilat), ONLINE (anything else: a web shop, a warehouse, an app's
    listing) or EILAT_STORE."""
    if walk_in is not None:
        shop = walk_in(chain_id, name, store_type, sub_chain)
    else:
        shop = not (store_type == 2 or (store_type is None and any(h in f"{name or ''} {sub_chain or ''}" for h in ONLINE_HINTS)))
    if not shop:
        return ONLINE
    return EILAT_STORE if is_eilat(city, name, address) else BRANCH


def store_kinds(c):
    """{(chain_id, store_id): BRANCH | ONLINE | EILAT_STORE}"""
    return {(cid, sid): store_kind(cid, st, name, sub, city, addr) for cid, sid, name, addr, city, st, sub in c.execute(
        "SELECT chain_id, store_id, name, address, city, store_type, sub_chain FROM stores")}


# ---------------------------------------------------------------- 1. store price changes -> price_moves, branch_moves
def _epoch(ts, cache={}):
    v = cache.get(ts)
    if v is None:
        try:
            v = time.mktime(time.strptime(str(ts), "%Y%m%d%H%M"))
        except (ValueError, OverflowError):
            v = 0
        if len(cache) > 50000:
            cache.clear()
        cache[ts] = v
    return v


def ensure(c):
    """The board's own tables (the shared schema in app.db keeps price_moves_raw and price_moves)."""
    c.executescript("""
        CREATE TABLE IF NOT EXISTS branch_moves_st(key TEXT, chain_id TEXT, store_id INTEGER, day INTEGER, was REAL,
          now REAL, ts INTEGER, PRIMARY KEY(key, chain_id, store_id, day)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS branch_moves(key TEXT, chain_id TEXT, day INTEGER, was REAL, now REAL,
          branches INTEGER, ts INTEGER, PRIMARY KEY(key, chain_id, day, was, now)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS branch_moves_day ON branch_moves(day);""")


def branch_since(c):
    v = get_setting(c, "branch_moves_since", None)
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def collect(c):
    """Fold the store-level changes the trigger caught (price_moves_raw) into price_moves (every store, as before)
    and branch_moves (branches only, exact); then empty what was read. Returns the number of chain-day changes
    written to price_moves."""
    ensure(c)
    today = reports.today()
    since = branch_since(c)
    if since is None:  # the first build that writes branch_moves: a day it saw only part of isn't counted from it
        since = today if not c.execute("SELECT 1 FROM price_moves LIMIT 1").fetchone() else reports.day_minus(today, -1)
        c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('branch_moves_since', ?)", (str(since),))
        c.commit()
    top = c.execute("SELECT max(rowid) FROM price_moves_raw").fetchone()[0]
    if top is None:
        return 0
    t0 = time.time()
    raw = c.execute("""SELECT key, chain_id, store_id, was, now, was_ts, ts FROM price_moves_raw
                       WHERE rowid <= ? ORDER BY rowid""", (top,)).fetchall()
    keys = sorted({r[0] for r in raw})
    med, odd, pflag = {}, set(), set()
    for part in reports.chunks(keys):
        ph = ",".join("?" * len(part))
        med.update(c.execute(f"SELECT key, median FROM products WHERE key IN ({ph})", part).fetchall())
        odd.update((k, ch) for k, ch in c.execute(f"SELECT key, chain_id FROM name_flags WHERE key IN ({ph})", part))
        pflag.update(tuple(r) for r in c.execute(
            f"SELECT key, chain_id, store_id FROM price_flags WHERE key IN ({ph})", part))
    lo, hi = trust.OUTLIER
    sane = lambda key, was, now: (lambda m: not m or (lo * m <= was <= hi * m and lo * m <= now <= hi * m))(med.get(key))
    net = {}  # (key, chain, store, day) -> [first old price, last new price, first ts]: a store's change over a day
    for key, ch, st, was, now, was_ts, ts in raw:
        if not comparable(key) or (key, ch) in odd or not was_ts or not ts:
            continue
        if _epoch(ts) - _epoch(was_ts) > RAW_GAP_DAYS * 86400:
            continue
        k = (key, ch, st, ts // 10000)
        if k in net:
            net[k][1] = now
        else:
            net[k] = [was, now, ts]
    groups = {}
    for (key, ch, st, day), (was, now, ts) in net.items():
        if abs(was - now) < 0.005 or (key, ch, st) in pflag or not sane(key, was, now):
            continue
        g = groups.setdefault((key, ch, day, round(was, 2), round(now, 2)), [0, ts])
        g[0] += 1
        g[1] = min(g[1], ts)
    # branches: each one's net change per day (a ledger), then that day's changes rebuilt from the ledger, so a branch
    # that changes a price twice in a day (in two builds) counts once
    kinds = store_kinds(c)
    first = max(since, reports.day_minus(today, LEDGER_DAYS))
    mine = {k: v for k, v in net.items() if k[3] >= first and kinds.get((k[1], k[2])) == BRANCH}
    with c:
        c.executemany("""INSERT INTO price_moves(key, chain_id, day, was, now, stores, ts) VALUES(?,?,?,?,?,?,?)
                         ON CONFLICT(key, chain_id, day, was, now) DO UPDATE SET stores=stores+excluded.stores,
                         ts=min(ts, excluded.ts)""", [(*k, *v) for k, v in groups.items()])
        c.executemany("""INSERT INTO branch_moves_st(key, chain_id, store_id, day, was, now, ts) VALUES(?,?,?,?,?,?,?)
                         ON CONFLICT(key, chain_id, store_id, day) DO UPDATE SET now=excluded.now,
                         ts=min(ts, excluded.ts)""", [(*k, *v) for k, v in mine.items()])
        days = sorted({(k[0], k[1], k[3]) for k in mine})
        for key, ch, day in days:
            out = {}
            for st, was, now, ts in c.execute("""SELECT store_id, was, now, ts FROM branch_moves_st
                                                 WHERE key=? AND chain_id=? AND day=?""", (key, ch, day)):
                if abs(was - now) < 0.005 or (key, ch, st) in pflag or not sane(key, was, now):
                    continue
                g = out.setdefault((round(was, 2), round(now, 2)), [0, ts])
                g[0] += 1
                g[1] = min(g[1], ts)
            c.execute("DELETE FROM branch_moves WHERE key=? AND chain_id=? AND day=?", (key, ch, day))
            c.executemany("INSERT INTO branch_moves(key, chain_id, day, was, now, branches, ts) VALUES(?,?,?,?,?,?,?)",
                          [(key, ch, day, w, n, b, ts) for (w, n), (b, ts) in out.items()])
        c.execute("DELETE FROM branch_moves_st WHERE day < ?", (reports.day_minus(today, LEDGER_DAYS),))
        c.execute("DELETE FROM price_moves_raw WHERE rowid <= ?", (top,))
        if groups:
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('moves_since', ?)",
                      (str(min(k[2] for k in groups)),))
    log.info("price changes: %d store changes read, %d kept as %d chain-day changes, %d at branches (%.0fs)",
             len(raw), sum(v[0] for v in groups.values()), len(groups), len(mine), time.time() - t0)
    return len(groups)


# ---------------------------------------------------------------- 2. the data
def _day(d):
    return f"{d % 100}.{d // 100 % 100}"


def money(v):
    return f"{v:.2f}"


def norm(s):
    """Search text: lower case, no quotes, dashes or niqqud, Hebrew final letters as their plain forms."""
    s = re.sub(r"[֑-ׇ\"'`״׳\-–_.,()/]", " ", (s or "").lower())
    return " ".join(s.translate(str.maketrans("ךםןףץ", "כמנפצ")).split())


def current(c, kinds, brand, want=()):
    """One pass over the prices, branches only. Returns
      cur     {key: {chain: sorted prices}}: each barcode product's confirmed shelf price at each branch with a
              current file (the indexer's confirmed-price rules, indexer.CONFIRMED_ROW + CONFIRMED_PAIR);
      listed  {(key, chain): sorted days}: for the keys in `want`, the day of each branch's newest file with the
              product, current or not (listed_since counts from it)."""
    from .indexer import CONFIRMED_PAIR, confirmed_through
    cutoff = trust.fresh_cutoff()
    c.execute("DROP TABLE IF EXISTS temp.board_st")
    c.execute("""CREATE TEMP TABLE board_st(chain_id TEXT, store_id INTEGER, pf INTEGER, fresh INTEGER,
                 PRIMARY KEY(chain_id, store_id)) WITHOUT ROWID""")
    c.executemany("INSERT INTO temp.board_st VALUES(?,?,?,?)", [
        (cid, sid, pf or 0, int((lp or 0) >= cutoff)) for cid, sid, pf, lp in c.execute(
            "SELECT chain_id, store_id, price_full_ts, last_price_ts FROM stores") if kinds.get((cid, sid)) == BRANCH])
    flagged = defaultdict(set)
    for key, cid, sid in c.execute("SELECT key, chain_id, store_id FROM price_flags"):
        flagged[(key, cid)].add(str(sid))
    out, listed, ok = defaultdict(dict), defaultdict(list), {}
    q = f"""SELECT g.key, g.chain_id, g.ps, g.ds, ({CONFIRMED_PAIR}) FROM (
              SELECT p.key, p.chain_id,
                group_concat(CASE WHEN b.fresh AND p.ts >= b.pf AND p.ts <= :through AND p.price > 0
                                  THEN p.store_id || '=' || p.price END) ps,
                group_concat(p.ts / 10000) ds
              FROM prices p JOIN temp.board_st b ON b.chain_id=p.chain_id AND b.store_id=p.store_id
              WHERE instr(p.key, ':') = 0 GROUP BY p.key, p.chain_id) g"""
    for key, cid, ps, ds, pair_ok in c.execute(q, {"through": confirmed_through(c), "ncut": trust.names_cutoff()}):
        good = ok.get(key)
        if good is None:
            good = ok[key] = comparable(key)
        if not good:
            continue
        b = brand(cid)
        if key in want and ds:
            listed[(key, b)] += [int(x) for x in ds.split(",")]
        if not ps or not pair_ok:
            continue
        bad = flagged.get((key, cid))
        vals = [float(p) for s, _, p in (x.partition("=") for x in ps.split(",")) if not bad or s not in bad]
        if not vals:
            continue
        if b in out[key]:  # a chain that publishes under two ids (Victory)
            vals += out[key][b]
        out[key][b] = sorted(vals)
    c.execute("DROP TABLE IF EXISTS temp.board_st")
    c.commit()  # the temp table's writes opened a transaction: a reader left in it would never see newer data
    for v in listed.values():
        v.sort()
    return out, listed


def levels(cur):
    """{chain: (ratio - 1, products)}: per product sold at LEVEL_CHAINS chains or more, the chain's middle branch
    price against the middle chain's; the geometric mean over its products."""
    acc = defaultdict(lambda: [0.0, 0])
    for g in cur.values():
        if len(g) < LEVEL_CHAINS:
            continue
        meds = {b: statistics.median(v) for b, v in g.items()}
        m = statistics.median(meds.values())
        for b, v in meds.items():
            acc[b][0] += math.log(v / m)
            acc[b][1] += 1
    return {b: (math.exp(s / n) - 1, n) for b, (s, n) in acc.items() if n}


def chain_events(c, kinds, brand, since):
    """Chain-level price changes: [(key, chain, day, dir, was lo, was hi, now lo, now hi, branches lo, branches hi,
    change lo, change hi)], dir 1 = rise, -1 = cut. A change counts when at least MIN_BRANCHES branches went from the
    same old price to the same new price on the same day (a coordinated change, not markdowns ending at a few
    branches each at its own price); several such price pairs on a day make one change, as ranges. From
    branch_moves (exact) on the days it covers; before that from price_moves, whose stores may include the chain's
    online and Eilat stores: at least stores - those (branches lo), at most stores (branches hi)."""
    excl = Counter(cid for (cid, _), k in kinds.items() if k != BRANCH)
    odd = set(c.execute("SELECT key, chain_id FROM name_flags"))
    groups = defaultdict(lambda: [0, 0, 0])  # (key, chain id, day, dir, was, now) -> [exact, at most, unknown]
    ok = {}
    rows = []
    if since is not None:
        rows.append((c.execute("SELECT key, chain_id, day, was, now, branches FROM branch_moves WHERE day >= ?",
                               (since,)), True))
    rows.append((c.execute("SELECT key, chain_id, day, was, now, stores FROM price_moves"
                           + (" WHERE day < ?" if since is not None else ""), (since,) if since is not None else ()),
                 False))
    for cur, exact in rows:
        for key, cid, day, was, now, n in cur:
            good = ok.get(key)
            if good is None:
                good = ok[key] = comparable(key)
            if not good or (key, cid) in odd or not (was > 0 and now > 0 and n > 0) or abs(now - was) < 0.005:
                continue
            g = groups[(key, cid, day, 1 if now > was else -1, round(was, 2), round(now, 2))]
            g[0 if exact else 2] += n
            g[1] += n
    merged = {}
    for (key, cid, day, d, was, now), (known, hi, unknown) in groups.items():
        lo = known + max(0, unknown - excl.get(cid, 0))
        if lo < MIN_BRANCHES:
            continue
        b = brand(cid)
        e = merged.get((key, b, day, d))
        if e is None:
            e = merged[(key, b, day, d)] = [[], 0, 0]
        e[0].append((was, now))
        e[1] += lo
        e[2] += hi
    out = []
    for (key, b, day, d), (pairs, lo, hi) in merged.items():
        ws, ns = [p[0] for p in pairs], [p[1] for p in pairs]
        ch = [n / w - 1 for w, n in pairs]
        out.append((key, b, day, d, round(min(ws), 2), round(max(ws), 2), round(min(ns), 2), round(max(ns), 2),
                    lo, hi, round(min(ch), 4), round(max(ch), 4)))
    out.sort(key=lambda e: (e[2], e[0], e[1]))
    return out


def listed_since(listed, events):
    """{(key, chain, day): Y}: the chain's branches that listed the product on or after that day (any of their
    files since then: the branches a change on that day could have been made at)."""
    out = {}
    for e in events:
        v = listed.get((e[0], e[1]), ())
        out[(e[0], e[1], e[2])] = len(v) - bisect.bisect_left(v, e[2])
    return out


def periods(first, today):
    """[(name, first day)]: the periods offered, each only once the recorded changes go back further than it (so a
    "30 days" never covers 8), then "all" (since the first recorded change)."""
    if not first or first > today:
        return []
    span = reports.days_between(first, today)
    return [(str(p), max(reports.day_minus(today, p - 1), first)) for p in PERIODS if p < len(span)] + [("all", first)]


def _buckets(n):
    """[(first, last)] positions of a period's n days: each day, or weeks counted back from the last day."""
    if n <= WEEKLY_OVER:
        return [(i, i) for i in range(n)]
    return [(max(0, e - 6), e) for e in range(n - 1, -1, -7)][::-1]


def gather(c):
    """Everything the page is made of."""
    ensure(c)
    t0 = time.time()
    t = reports.today()
    lab = reports.labels(c)
    brand = lambda cid: reports.label_of(lab, cid)
    kinds = store_kinds(c)
    since = branch_since(c)
    events = chain_events(c, kinds, brand, since)
    cur, listed = current(c, kinds, brand, {e[0] for e in events})
    ys = listed_since(listed, events)
    t1 = time.time()
    keys = {k for k, g in cur.items() if len(g) >= MIN_CHAINS}
    meta = {}
    for part in reports.chunks(sorted(keys)):
        meta.update((k, (name, sc)) for k, name, sc in c.execute(
            f"SELECT key, name, store_count FROM products WHERE key IN ({','.join('?' * len(part))})", part))
    lv = levels(cur)
    fresh = trust.fresh_cutoff()
    branches = Counter()
    for cid, sid, lp in c.execute("SELECT chain_id, store_id, last_price_ts FROM stores"):
        if kinds.get((cid, sid)) == BRANCH and (lp or 0) >= fresh:
            branches[brand(cid)] += 1
    t2 = time.time()
    # days with data at all (a day without a single recorded store change is a hole in the data, not "no changes")
    seen = {d for (d,) in c.execute("SELECT DISTINCT day FROM price_moves")}
    seen |= {d for (d,) in c.execute("SELECT DISTINCT day FROM branch_moves")}
    first = min(seen) if seen else None
    names = {k: n for k, (n, _) in meta.items()}
    missing = sorted({e[0] for e in events} - set(names))
    for part in reports.chunks(missing):
        names.update(c.execute(f"SELECT key, name FROM products WHERE key IN ({','.join('?' * len(part))})", part))
    # chains listed: a level over MIN_PRODUCTS products, cheapest first
    chains = sorted(({"chain": b, "level": lv[b][0], "n": lv[b][1], "branches": branches.get(b, 0)}
                     for b in lv if lv[b][1] >= MIN_PRODUCTS), key=lambda r: (r["level"], r["chain"]))
    listed = {r["chain"] for r in chains}
    # each period: per chain, products raised and cut per day (or week), in all, and the biggest changes
    item = lambda e: [e[0], names.get(e[0]) or e[0], e[2], e[4], e[5], e[6], e[7], e[8], e[9],
                      ys.get((e[0], e[1], e[2])), e[10], e[11]]
    bodies = {}
    for name, start in periods(first, t):
        ds = reports.days_between(start, t)
        bks = _buckets(len(ds))
        at = {d: i for i, (lo, hi) in enumerate(bks) for d in ds[lo:hi + 1]}
        per = {b: {"up": [set() for _ in bks], "dn": [set() for _ in bks], "tu": [], "td": []} for b in listed}
        for e in events:
            if e[2] < start or e[1] not in per:
                continue
            p = per[e[1]]
            p["up" if e[3] > 0 else "dn"][at[e[2]]].add(e[0])
            p["tu" if e[3] > 0 else "td"].append(e)
        out = {}
        for b, p in per.items():
            ups, dns = set().union(*p["up"]), set().union(*p["dn"])
            tu = sorted(p["tu"], key=lambda e: (-e[11], -e[8], -e[2], e[0]))
            td = sorted(p["td"], key=lambda e: (e[10], -e[8], -e[2], e[0]))
            out[b] = {"up": [len(s) for s in p["up"]], "dn": [len(s) for s in p["dn"]], "nup": len(ups), "ndn": len(dns),
                      "tu": [item(e) for e in tu[:TOP_DATA]], "td": [item(e) for e in td[:TOP_DATA]]}
        holes = [int(not any(d in seen for d in ds[lo:hi + 1])) for lo, hi in bks]
        bodies[name] = {"p": name, "from": start, "to": t, "step": 1 if len(ds) <= WEEKLY_OVER else 7,
                        "ends": [ds[hi] for _, hi in bks], "starts": [ds[lo] for lo, _ in bks], "holes": holes,
                        "all": {"up": [sum(out[b]["up"][i] for b in out) for i in range(len(bks))],
                                "dn": [sum(out[b]["dn"][i] for b in out) for i in range(len(bks))],
                                "nup": sum(x["nup"] for x in out.values()), "ndn": sum(x["ndn"] for x in out.values())},
                        "chains": out}
    # the page's own few: each period's rises made at the most branches, different products and, while there are
    # enough, different chains (the lists themselves need sign-in). Ordinary rises only (at most PEEK_MAX): a jump
    # beyond that is more often a sale published as the shelf price ending, true but no example of a price rise.
    peek = {}
    for name, start in periods(first, t):
        ups = sorted((e for e in events if e[2] >= start and e[3] > 0 and e[1] in listed and e[11] <= PEEK_MAX),
                     key=lambda e: (-e[8], -e[11], -e[2], e[0]))
        pick, used_k, used_b = [], set(), set()
        for strict in (True, False):
            for e in ups:
                if len(pick) >= PEEK:
                    break
                if e[0] in used_k or (strict and e[1] in used_b):
                    continue
                pick.append(e)
                used_k.add(e[0])
                used_b.add(e[1])
        pick.sort(key=lambda e: (-e[8], -e[11], -e[2], e[0]))
        peek[name] = [[e[0], names.get(e[0]) or e[0], e[1], e[2], e[4], e[5], e[6], e[7], e[8], e[9],
                       ys.get((e[0], e[1], e[2])), e[10], e[11]] for e in pick]
    # search rows: each product's price range per chain now and each chain's latest changes
    by_key = defaultdict(list)
    for e in events:
        by_key[e[0]].append(e)
    products = []
    for key in keys:
        name, sc = meta.get(key, (key, 0))
        p = {b: [round(v[0], 2), round(v[-1], 2), len(v)] for b, v in cur[key].items()}
        evs, count = [], Counter()
        for e in sorted(by_key.get(key, ()), key=lambda e: -e[2]):
            if e[1] in p and count[e[1]] < PER_CHAIN_EVENTS:
                count[e[1]] += 1
                evs.append([e[1], e[2], e[3], e[4], e[5], e[6], e[7], e[8], e[9], ys.get((key, e[1], e[2]))])
        products.append({"k": key, "n": name or key, "s": sc or 0, "p": p, "e": evs})
    products.sort(key=lambda r: (-r["s"], -len(r["p"]), r["k"]))
    log.info("board data: changes and prices %.0fs, levels %.0fs, rest %.0fs", t1 - t0, t2 - t1, time.time() - t2)
    legacy = any(e[8] < e[9] for e in events)
    return {"today": t, "built": int(time.time()), "source": backfill.source(c), "products": products,
            "chains": chains, "bodies": bodies, "events": events, "since": since, "legacy": legacy,
            "first": first, "peek": peek}


# ---------------------------------------------------------------- 3. the page
def esc(s):
    return html.escape(str(s), quote=True)


def both(he, en):
    return f'<span lang="he">{esc(he)}</span><span lang="en">{esc(en)}</span>'


def chain_he(b):
    if b in CHAINS_HE:
        return CHAINS_HE[b]
    try:  # a chain named after this list was written: its shop-sign name
        from .chains import BRAND_HE
        return BRAND_HE.get(b) or b
    except ImportError:
        return b


def chain_label(b):
    return both(chain_he(b), b)


def num(n):
    return f"{n:,}"


def pct_txt(x):
    a = abs(x) * 100
    return f"{a:.0f}%" if a >= 9.5 else f"{a:.1f}%".replace(".0%", "%")


def level_words(x):
    """(he, en): the chain's level in plain words."""
    if abs(x) < 0.005:
        return "כמו הרשת האמצעית", "Same as the middle chain"
    if x < 0:
        return f"{pct_txt(x)} זולה מהרשת האמצעית", f"{pct_txt(x)} cheaper than the middle chain"
    return f"{pct_txt(x)} יקרה מהרשת האמצעית", f"{pct_txt(x)} more expensive than the middle chain"


def counts_words(nup, ndn):
    """(he, en): "raised 120 products · cut 40" for a chain over the period."""
    he = "העלתה מוצר אחד" if nup == 1 else f"העלתה {num(nup)} מוצרים"
    en = "Raised 1 product" if nup == 1 else f"Raised {num(nup)} products"
    return f"{he} · הורידה {num(ndn)}", f"{en} · cut {num(ndn)}"


def rng(lo, hi):
    return money(lo) if hi - lo < 0.005 else f"{money(lo)}–{money(hi)}"


def chg_txt(lo, hi):
    """+12%, or +5–12% when the branches' changes differ (a rise's are all up, a cut's all down)."""
    fmt = lambda x: f"{x * 100:.0f}" if x >= 0.0095 else f"{x * 100:.1f}"
    a, b = sorted((abs(lo), abs(hi)))
    fa, fb, sign = fmt(a), fmt(b), "+" if hi > 0 else "−"
    return f"{sign}{fb}%" if fa == fb else f"{sign}{fa}–{fb}%"


def branches_words(lo, hi, y):
    """(he, en): "at X of Y branches", "at least X" when only a lower bound is known."""
    of_he, of_en = (f" מתוך {num(y)}", f" of {num(y)}") if y and y >= lo else ("", "")
    if lo < hi:
        return f"לפחות ב־{num(lo)}{of_he} סניפים", f"at least {num(lo)}{of_en} branches"
    return f"ב־{num(lo)}{of_he} סניפים", f"at {num(lo)}{of_en} branches"


# ---------------------------------------------------------------- 3a. what a visitor sees, and what sign-in adds
LOCK = ('<svg class="lk" viewBox="0 0 20 20" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round"><rect x="4.2" y="8.6" width="11.6" height="8.6" rx="2.2"/>'
        '<path d="M6.9 8.6V6.4a3.1 3.1 0 0 1 6.2 0v2.2"/></g></svg>')
FEATURES = (  # what signing in adds, as the sign-in sheet lists it (data-f: the one the visitor reached for is marked)
    ("search", '<circle cx="9" cy="9" r="5.3"/><path d="M13 13l4 4"/>',
     "חיפוש כל מוצר: המחיר שלו בכל רשת", "Any product: its price at every chain"),
    ("chain", '<path d="M3.5 16.5h13"/><path d="M6 13.5V9M10 13.5V5.5M14 13.5v-6"/>',
     "העלאות והורדות של כל רשת, יום אחר יום", "Each chain's rises and cuts, day by day"),
    ("items", '<path d="M7.5 5.5h9M7.5 10h9M7.5 14.5h9"/><path d="M3.8 5.5h.4M3.8 10h.4M3.8 14.5h.4"/>',
     "המוצרים שכל רשת העלתה והורידה, עם סניפים ותאריכים", "The products each chain raised and cut, with branches and dates"),
)


def signup(cls=""):
    """The sign-in button: to the app's sign-in (prices.js remembers to come back here)."""
    return f'<a class="btn primary signup{cls}" href="/app" data-signin>' + both("הרשמה חינם", "Sign up free") + "</a>"


def peek_li(it):
    """One of the page's few rises (a visitor's look at the lists): product, prices, chain, branches, day."""
    key, name, b, day, wlo, whi, nlo, nhi, lo, hi, y, plo, phi = it
    bh, be = branches_words(lo, hi, y)
    return (f'<li><span class="nm" dir="auto">{esc(name)}</span>'
            f'<span class="pr"><bdi dir="ltr">{rng(wlo, whi)} → <b>{rng(nlo, nhi)}</b></bdi>'
            f' <b class="up"><bdi dir="ltr">{esc(chg_txt(plo, phi))}</bdi></b></span>'
            f'<span class="sub"><b class="pk-ch">{chain_label(b)}</b> · {both(bh, be)} · <bdi dir="ltr">{_day(day)}</bdi></span></li>')


def peek_html(d, default, body):
    """The default period's rises at the most branches, then the rest of the list veiled, with the sign-in."""
    items = d.get("peek", {}).get(default) or []
    if not items:
        return ""
    he, en = period_name(default)
    veil = "".join('<li><span class="sk-a"></span><span class="sk-b"></span><span class="sk-c"></span></li>' for _ in range(3))
    return ('<section class="sec guest-only" id="peek"><h2>' + both("הועלו בהכי הרבה סניפים", "Raised at the most branches")
            + f'<span class="h2-sub">{both(he, en)}</span></h2><div class="card peek"><ol class="mvs">'
            + "".join(peek_li(it) for it in items) + "</ol>"
            + f'<div class="veil" aria-hidden="true"><ol class="mvs sk">{veil}</ol></div>'
            + '<div class="peek-cta"><p>' + LOCK + "<span>"
            + both("השינויים הגדולים של כל רשת, וחיפוש כל מוצר", "Every chain's biggest changes, and any product's price")
            + "</span></p>" + signup() + "</div></div></section>")


def rank_html(d, default, body):
    """Every chain by price level, cheapest first: a bar from the middle chain, its words, and how many products it
    raised and cut in the default period. A row offers the chain's own card (sign-in)."""
    rows = d["chains"]
    if not rows:
        return ""
    span = max(abs(r["level"]) for r in rows) or 1
    he, en = period_name(default)
    out = []
    for r in rows:
        b, lv = r["chain"], r["level"]
        x = (body or {}).get("chains", {}).get(b) or {"nup": 0, "ndn": 0}
        lh, le = level_words(lv)
        ch, ce = counts_words(x["nup"], x["ndn"])
        cls = "dn" if lv < -0.005 else "up" if lv > 0.005 else ""
        bar = f'<span class="{"neg" if lv < 0 else "pos"}" style="--f:{min(1.0, abs(lv) / span):.3f}"></span>'
        out.append(f'<li><button type="button" class="rk" data-gate="chain" data-chain="{esc(b)}">'
                   f'<span class="rk-n">{chain_label(b)}</span>'
                   f'<span class="rk-w {cls}">{both(lh, le)}</span>'
                   f'<span class="lbar" aria-hidden="true">{bar}</span>'
                   + (f'<span class="rk-c"><span aria-hidden="true"><span class="up">▲&#8239;{num(x["nup"])}</span>'
                      f'<span class="dn">▼&#8239;{num(x["ndn"])}</span></span><span class="sr-only">{both(ch, ce)}</span></span>'
                      if body else "")
                   + f'<span class="rk-go">{LOCK}</span></button></li>')
    key = (f'<span class="rk-key"><span class="up" aria-hidden="true">▲</span>{both("מוצרים שהרשת העלתה", "products raised")}'
           f'<span class="dn" aria-hidden="true">▼</span>{both("הורידה", "cut")} · {both(he, en)}</span>') if body else ""
    cut = len(out) > RANK_CUT + 2
    more = (f'<button type="button" class="more rk-more" aria-expanded="false">'
            f'{both(f"כל {len(out)} הרשתות", f"All {len(out)} chains")}</button>') if cut else ""
    return ('<figure class="card rank guest-only"><figcaption><b>' + both("מחיר לעומת הרשת האמצעית", "Price against the middle chain")
            + "</b>" + key + f'</figcaption><ol class="rks{" cut" if cut else ""}">' + "".join(out) + "</ol>" + more + "</figure>")


def gate_html(d):
    """The sign-in sheet (a <dialog>): what signing in adds, the one reached for marked (prices.js)."""
    pers = [period_name(p) for p in d["bodies"]]
    feats = list(FEATURES)
    if len(pers) > 1:
        feats.append(("period", '<rect x="3.5" y="4.5" width="13" height="12" rx="2"/><path d="M3.5 8.5h13M7 3v3M13 3v3"/>',
                      "תקופות: " + " · ".join(h for h, _ in pers), "Periods: " + " · ".join(e for _, e in pers)))
    li = "".join(f'<li data-f="{f}"><svg viewBox="0 0 20 20" aria-hidden="true"><g fill="none" stroke="currentColor" '
                 f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{ic}</g></svg>{both(h, e)}</li>'
                 for f, ic, h, e in feats)
    return ('<dialog id="gate" class="gate" aria-labelledby="gate-t">'
            '<button type="button" class="gx" data-close aria-label="סגירה / Close"><svg viewBox="0 0 20 20" aria-hidden="true">'
            '<path d="M5 5l10 10M15 5L5 15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></button>'
            f'<p class="g-k">{LOCK}{both("חינם, עם כניסה", "Free with sign-in")}</p>'
            '<h3 id="gate-t">' + both("מעקב המחירים המלא", "The full Price Watch") + "</h3>"
            f'<ul class="g-list">{li}</ul>' + signup(" wide") + "</dialog>")


def span_txt(a, b):
    """30.10 – 6.11.2026 (the year once, unless the span crosses one)."""
    if a // 10000 == b // 10000:
        return f"{_day(a)} – {_day(b)}.{b // 10000}"
    return f"{_day(a)}.{a // 10000} – {_day(b)}.{b // 10000}"


def period_name(p):
    if p == "all":
        return "הכול", "All"
    if p == "365":
        return "שנה", "1 year"
    return f"{p} ימים", f"{p} days"


def credit(d):
    """Price changes from before Sal-Kal recorded them come from the OpenIsraeliSupermarkets archive (app.backfill), whose
    license (CC BY-NC-SA 3.0 IGO) asks for its name, its license and that it was adapted, wherever they are shown."""
    s = d.get("source")
    last = ((s or {}).get("moves") or [None, None])[1]  # the archive's last day of price changes (the only part shown)
    if not s or not last:
        return ""
    a = lambda href, text: f'<a href="{esc(href)}" target="_blank" rel="noopener">{esc(text)}</a>'
    src, lic, day = a(s["url"], s["by"]), a(s["license_url"], s["license"]), esc(f"{_day(last)}.{last // 10000}")
    return (f'<p class="stamp"><span lang="he">שינויים עד {day}: מתוך {src} ({lic}), בעיבוד.</span>'
            f'<span lang="en">Changes up to {day}: from {src} ({lic}), adapted.</span></p>')


def stamp(d):
    tm = time.localtime(d["built"])
    when = f'{tm.tm_mday}.{tm.tm_mon} {tm.tm_hour:02d}:{tm.tm_min:02d}'
    n = len(d["products"])
    return ('<p class="stamp">' + both(f"עודכן {when} · {n:,} מוצרים · מחירי מדף, בלי מבצעים",
                                       f"Updated {when} · {n:,} products · shelf prices, no deals") + "</p>" + credit(d))


def method_html(d):
    items = [
        ("מחירי מדף מהקבצים שהרשתות מחויבות לפרסם (חוק קידום התחרות בענפי המזון, 2014, סעיף 30). מוצרים עם ברקוד, באותו שם בכל הרשתות. בלי מבצעים.",
         "Shelf prices from the files chains must publish (Food Act 2014, section 30). Barcode products named alike at every chain. Deals not included."),
        ("סניפים: חנויות פיזיות; חנויות אונליין וסניפי אילת (בלי מע״מ) לא נספרים. מחירים ומספרי סניפים של היום: סניפים עם קובץ מחירים מ־26 השעות האחרונות.",
         "Branches: physical stores; online stores and Eilat branches (no VAT) are left out. Today's prices and branch counts: branches with a price file from the last 26 hours."),
        ("מחיר רשת למוצר: המחיר בסניף האמצעי שלה. הרשת האמצעית: החציון של מחירי הרשתות. ההשוואה במוצרים שנמכרים בשלוש רשתות לפחות, בממוצע גאומטרי.",
         "A chain's price for a product: its middle branch's. The middle chain: the median of the chains' prices. Compared over products sold at three chains or more, as a geometric mean."),
        (f"העלאה או הורדה: {MIN_BRANCHES} סניפים של הרשת לפחות העבירו מוצר מאותו מחיר מדף לאותו מחיר חדש, באותו יום. שינוי בסניף אחד או שניים לא מוצג.",
         f"A rise or cut: {MIN_BRANCHES} or more of the chain's branches moved a product from the same shelf price to the same new one on the same day. A change at one or two branches isn't shown."),
        ("״מתוך Y סניפים״: סניפי הרשת שמכרו את המוצר מאותו יום והלאה.",
         "\"Of Y branches\": the chain's branches that listed the product from that day on."),
    ]
    if d.get("legacy"):
        s = d.get("since")
        day = f"{_day(s)}.{s // 10000}" if s else ""
        items.append((f"״לפחות״: שינויים מלפני {day} נספרו יחד עם חנויות האונליין וסניפי אילת של הרשת; המספר המוצג מוריד את כולן.",
                      f"\"At least\": changes from before {day} were counted with the chain's online and Eilat stores; the number shown leaves all of those out."))
    return ('<section class="sec" id="method"><h2>' + both("איך זה נספר", "How it's counted") + '</h2><div class="card method"><ul>'
            + "".join(f"<li>{both(h, e)}</li>" for h, e in items) + "</ul></div></section>")


def page_html(tpl, title, desc, canonical, main, data=None):
    links = f'<link rel="canonical" href="{canonical}">'
    blob = ""
    if data is not None:
        blob = ('<script type="application/json" id="board-data">'
                + json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + "</script>")
    return (tpl.replace("<!--TITLE-->", esc(title)).replace("<!--DESC-->", esc(desc)).replace("<!--LINKS-->", links)
            .replace("<!--MAIN-->", main).replace("<!--DATA-->", blob))


TITLE = "מעקב מחירים · סל קל"
DESC = ("כמה עולה כל מוצר בכל רשת, אילו רשתות העלו והורידו מחירי מדף ואיפה זול יותר, "
        "לפי קובצי המחירים שהרשתות מחויבות בחוק לפרסם.")


def stamp_static(page):
    """Each script and stylesheet URL carries its file's change time (as app.server._page does), so the server sends
    the built bytes as they are. A deploy that changes them reaches the page with the next build (install-app.sh
    runs one)."""
    def one(mt):
        f = os.path.join(STATIC, mt.group(2))
        return f'{mt.group(1)}/static/{mt.group(2)}?v={int(os.stat(f).st_mtime) if os.path.exists(f) else 0}"'
    return re.sub(r'(src="|href=")/static/([\w./-]+\.(?:js|css))"', one, page)


def series_all(body):
    """A period body as the page carries it: the all-chains series only (each chain's comes with sign-in)."""
    if not body:
        return None
    return {**{k: v for k, v in body.items() if k != "chains"}, "chains": {}}


def legend(products=False):
    up = both("מוצרים שהרשת העלתה", "Products the chain raised") if products else both("העלאות מחיר", "Price rises")
    dn = both("מוצרים שהרשת הורידה", "Products the chain cut") if products else both("הורדות מחיר", "Price cuts")
    return f'<ul class="legend"><li><i class="ru"></i>{up}</li><li><i class="fd"></i>{dn}</li></ul>'


def main_html(d, default):
    """The page, as a visitor sees it; prices.js fills in the signed-in parts (.member-only) and hides the visitor's
    (.guest-only) once /api/me says who you are."""
    body = d["bodies"].get(default)
    intro = ('<section class="intro"><h1>' + both("מעקב מחירים", "Price Watch") + "</h1>" + stamp(d) + "</section>")
    find = ('<section class="sec find" id="find" role="search">'
            '<label class="find-box"><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="9" cy="9" r="5.5" fill="none" stroke="currentColor" stroke-width="1.8"/>'
            '<path d="M13.2 13.2L17 17" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>'
            '<span class="sr-only">' + both("חיפוש מוצר או ברקוד", "Search a product or barcode") + "</span>"
            '<input id="q" type="search" maxlength="200" autocomplete="off" enterkeyhint="search" dir="auto" '
            'data-ph-he="חיפוש מוצר או ברקוד" data-ph-en="Search a product or barcode" placeholder="חיפוש מוצר או ברקוד">'
            f'<span class="find-lk guest-only" aria-hidden="true">{LOCK}</span></label>'
            '<div id="results" aria-live="polite"></div></section>')
    peek = ""
    if not body:
        trends = ('<section class="sec" id="trends"><h2>' + both("העלאות והורדות", "Rises and cuts") + '</h2><p class="empty">'
                  + both("עוד לא נרשמו שינויי מחיר.", "No price changes recorded yet.") + "</p></section>")
    else:
        # every period's button is there; a visitor's other periods carry a lock and open the sign-in sheet
        seg = "".join(f'<button type="button" role="radio" data-p="{esc(p)}" aria-checked="{str(p == default).lower()}">'
                      + ("" if p == default else LOCK) + f'{both(*period_name(p))}</button>' for p in d["bodies"])
        a = body["all"]
        trends = ('<div class="filters" id="filters"><div class="seg" role="radiogroup" aria-label="תקופה / Period">' + seg + "</div>"
                  f'<p class="range"><bdi dir="ltr" id="range">{esc(span_txt(body["from"], body["to"]))}</bdi></p></div>'
                  '<section class="sec" id="trends"><h2>' + both("העלאות והורדות", "Rises and cuts") + "</h2>"
                  '<figure class="card trend"><figcaption><b>' + both("כל הרשתות", "All chains") + "</b>"
                  f'<span id="all-cnt">{both(f"{num(a["nup"])} העלאות מחיר · {num(a["ndn"])} הורדות", f"{num(a["nup"])} price rises · {num(a["ndn"])} cuts")}</span></figcaption>'
                  '<div class="plot" id="all-plot"></div>' + legend()
                  + '<div class="cfoot"><button type="button" class="lnk" id="tbl-toggle" aria-expanded="false" aria-controls="all-table" hidden>'
                  + both("כטבלה", "As a table") + '</button></div><div id="all-table" hidden></div></figure></section>')
        peek = peek_html(d, default, body)
    chains = ('<section class="sec" id="chains"><h2>' + both("הרשתות, מהזולה ליקרה", "Chains, cheapest first") + "</h2>"
              + rank_html(d, default, body)
              + '<div class="member-only">' + (f'<div class="chains-key">{legend(products=True)}</div>' if body else "")
              + '<ol class="chains" id="member-chains"></ol></div></section>')
    return intro + find + trends + peek + chains + method_html(d) + gate_html(d)


def render(d, base="https://sal-kal.com"):
    """{file name: bytes}: the page (gzipped), each period's data, the search rows and the site map."""
    tpl = open(os.path.join(STATIC, "prices.html"), encoding="utf-8").read()
    names = list(d["bodies"])
    default = "30" if "30" in d["bodies"] else (names[-1] if names else None)
    data = {"v": d["built"], "today": d["today"], "default": default,
            "periods": [{"p": p, "from": b["from"], "to": b["to"]} for p, b in d["bodies"].items()],
            "he": {r["chain"]: chain_he(r["chain"]) for r in d["chains"]},
            "rank": [[r["chain"], round(r["level"], 5), r["n"], r["branches"]] for r in d["chains"]],
            "body": series_all(d["bodies"].get(default))}
    page = page_html(tpl, TITLE, DESC, base + "/prices", main_html(d, default), data)
    out = {"p1.html.gz": gzip.compress(stamp_static(page).encode("utf-8"), 6, mtime=0)}
    for p, body in d["bodies"].items():
        out[f"chg-{p}.json.gz"] = gzip.compress(
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6, mtime=0)
    lines = [json.dumps({**r, "q": norm(r["n"])}, ensure_ascii=False, separators=(",", ":")) for r in d["products"]]
    out["rows.json.gz"] = gzip.compress("\n".join(lines).encode("utf-8"), 6, mtime=0)
    out["sitemap.xml"] = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                          + "".join(f"<url><loc>{u}</loc></url>" for u in (base + "/", base + "/prices")) + "</urlset>\n").encode()
    out["meta.json"] = json.dumps({"products": len(d["products"]), "built": d["built"], "chains": [r["chain"] for r in d["chains"]],
                                   "changes": len(d["events"]), "data": sorted(k[:-3] for k in out if k.startswith("chg-"))},
                                  ensure_ascii=False).encode()
    return out


def publish(files, root):
    """Write a build next to the old ones and switch `current` to it in one rename; keep the last KEEP builds."""
    os.makedirs(root, exist_ok=True)
    version = str(int(time.time() * 1000))
    tmp = os.path.join(root, f".{version}")
    os.makedirs(tmp)
    for name, data in files.items():
        with open(os.path.join(tmp, name), "wb") as fh:
            fh.write(data)
    os.rename(tmp, os.path.join(root, version))
    with open(os.path.join(root, ".current"), "w") as fh:
        fh.write(version)
    os.replace(os.path.join(root, ".current"), os.path.join(root, "current"))
    builds = sorted((e for e in os.listdir(root) if e.isdigit()), key=int)
    for old in builds[:-KEEP]:
        shutil.rmtree(os.path.join(root, old), ignore_errors=True)
    for e in os.listdir(root):  # half-written builds of a run that was killed
        if e.startswith(".") and e[1:].isdigit() and e != f".{version}":
            shutil.rmtree(os.path.join(root, e), ignore_errors=True)
    return version


def build(c, root=None, base=None):
    t0 = time.time()
    collect(c)
    d = gather(c)
    try:
        from . import auth
        base = base or (auth.config().get("base_url") or "").rstrip("/")
    except Exception:
        pass
    version = publish(render(d, base or "https://sal-kal.com"), root or board_dir())
    log.info("price watch %s: %d products, %d chains, %d chain-level changes (%.0fs)", version, len(d["products"]),
             len(d["chains"]), len(d["events"]), time.time() - t0)
    return version


# ---------------------------------------------------------------- 4. reading a build (the web server)
class Board:
    """The current build, re-read when `current` changes (checked at most every few seconds)."""

    def __init__(self, root):
        self.root, self.version, self.checked, self.meta, self.rows, self.files = root, None, 0, {}, None, {}
        self.found = {}

    def _sync(self):
        now = time.monotonic()
        if now - self.checked < 5:
            return
        self.checked = now
        try:
            v = open(os.path.join(self.root, "current")).read().strip()
        except OSError:
            return
        if v != self.version and v.isdigit():
            try:
                self.meta = json.load(open(os.path.join(self.root, v, "meta.json")))
            except (OSError, ValueError):
                return
            self.version, self.rows, self.files, self.found = v, None, {}, {}

    def file(self, name):
        """(gzipped bytes, version) of a built file, or (None, None)."""
        self._sync()
        if not self.version:
            return None, None
        hit = self.files.get(name)
        if hit is None:
            try:
                hit = open(os.path.join(self.root, self.version, name), "rb").read()
            except OSError:
                return None, None
            if len(self.files) > 64:
                self.files.clear()
            self.files[name] = hit
        return hit, self.version

    def _rows(self):
        if self.rows is None:
            if not self.version:
                return [], [], []
            try:
                raw = gzip.decompress(open(os.path.join(self.root, self.version, "rows.json.gz"), "rb").read())
            except OSError:
                return [], [], []
            # barcodes, search texts and the rows as built (parsed again only for the few rows a search returns)
            keys, texts, lines = [], [], []
            for line in raw.decode("utf-8").split("\n"):
                if line:
                    r = json.loads(line)
                    keys.append(r["k"])
                    texts.append(r["q"])
                    lines.append(line)
            self.rows = (keys, texts, lines)
        return self.rows

    def search(self, q=""):
        """Signed-in search: (up to SEARCH_MAX rows, total matches). Words match the name (in any order) or, when all
        digits, the start of the barcode. Nothing below 2 letters or 3 digits."""
        self._sync()
        words = [w for w in norm(q[:200]).split()[:12] if (len(w) >= 3 if w.isdigit() else len(w) >= 2)]
        if not words:
            return [], 0
        key = " ".join(words)
        hit = self.found.get(key)
        if hit is not None:
            return hit
        keys, texts, lines = self._rows()
        idx = range(len(keys))
        for w in sorted(words, key=len, reverse=True):  # the longest word first: it narrows the most
            if w.isdigit():
                idx = [i for i in idx if keys[i].startswith(w) or w in texts[i]]
            else:
                idx = [i for i in idx if w in texts[i]]
            if not idx:
                break
        total, out = len(idx), [lines[i] for i in idx[:SEARCH_MAX]]
        res = ([{k: v for k, v in json.loads(x).items() if k != "q"} for x in out], total)
        if len(self.found) > 500:
            self.found.clear()
        self.found[key] = res
        return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build(dbmod.connect(a.db), a.out or board_dir(a.db))


if __name__ == "__main__":
    main()
