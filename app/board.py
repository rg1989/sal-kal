"""The public Price Board (/prices): every barcode product sold at two or more chains, what it costs at each chain
now, when each chain last changed its shelf price and what it was before, and which chains raised and lowered
prices.

Anyone may read it, signed in or not, so nothing a visitor sends makes the server compute anything: after each
index run (python -m app.board, in sal-index) every page is written out ready, gzipped, under <data>/board/<version>/,
and the web server only hands those bytes out (app.server /prices). Signed-in people also get search, categories
and sorting (GET /api/prices), answered from rows.json.gz, which this build writes too.

What counts as a price change: one store's shelf price for a barcode product moving from one published file to the
next (the prices_moved trigger, app.db, catches it as the indexer writes it). The chain's cheapest price moving is
not one: that also happens when a branch's file goes out of date or comes back. A change is kept only when the
product is the same one at that chain (named like the other chains), both prices are in line with the product's
typical price (trust.OUTLIER) and the old price was published within RAW_GAP_DAYS of the new one. Shelf prices only;
deals are not included.

Built only from the chains' public price files (price_last, price_history, price_moves): never carts, accounts,
searches or usage statistics.
"""
import argparse
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
from collections import defaultdict

from . import db as dbmod
from . import catalog, reports, trust
from .engine import comparable, get_setting

log = logging.getLogger("board")

MIN_CHAINS = 2       # a product is on the board when this many chains have a confirmed price for it
PAGE = 100           # products per page
COLUMNS = 10         # chains with a column of their own
# the column order: the grocery chains most shoppers know, then whichever chains sell the most products on the board
# (counting products alone would put City Market, 32 shops, before Rami Levy; counting branches, pharmacies first)
KNOWN = ("Shufersal", "Rami Levy", "Victory", "Carrefour", "Yohananof", "Osher Ad", "Tiv Taam", "Hazi Hinam",
         "Mahsanei HaShuk", "Keshet Teamim")
MOVE_DAYS = 90       # a cell shows the chain's last price change when it is this recent
FEED_DAYS = 30       # the changes page and the chain table cover this many days
FEED = 1000          # longest changes list
HIGHLIGHT = 10       # biggest rises and drops on the first page
RAW_GAP_DAYS = 4     # an old price older than this before the new one is not a change we can date
MIN_PRODUCTS = 50    # a chain with fewer products on the board isn't in the chain table
KEEP = 2             # built versions kept (the current one and the one before)

CHAINS_HE = {  # as in app/static/i18n.js
    'Shufersal': 'שופרסל', 'Rami Levy': 'רמי לוי', 'Victory': 'ויקטורי', 'Yohananof': 'יוחננוף', 'Osher Ad': 'אושר עד',
    'Tiv Taam': 'טיב טעם', 'Hazi Hinam': 'חצי חינם', 'Keshet Teamim': 'קשת טעמים', 'Super-Pharm': 'סופר-פארם',
    'King Store': 'קינג סטור', 'Carrefour': 'קרפור', 'Yeinot Bitan': 'יינות ביתן', 'Mahsanei HaShuk': 'מחסני השוק',
    'Yellow': 'יילו', 'Wolt Market': 'וולט מרקט', 'Fresh Market': 'פרש מרקט', 'Good Pharm': 'גוד פארם',
    'Salach Dabach': 'סאלח דבאח', 'Zol Ubegadol': 'זול ובגדול', 'Dor Alon': 'דור אלון', 'Bareket': 'ברקת',
    'Polizer': 'פוליצר', 'Super Yuda': 'סופר יודה', 'Super Sapir': 'סופר ספיר', 'Shuk HaIr': 'שוק העיר',
    'Netiv HaHesed': 'נתיב החסד', 'Maayan 2000': 'מעיין 2000', 'Shefa Birkat Hashem': 'שפע ברכת השם', 'H. Cohen': 'ח. כהן',
    'City Market': 'סיטי מרקט', 'City Market Kiryat Gat': 'סיטי מרקט קריית גת', 'Mishnat Yosef': 'משנת יוסף',
    'Stop Market': 'סטופ מרקט',
}
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
SORTS = ("popular", "rises", "drops", "recent", "gap")


def board_dir(db_path=None):
    return os.environ.get("BOARD_DIR") or os.path.join(os.path.dirname(os.path.abspath(db_path or dbmod.DB_PATH)), "board")


# ---------------------------------------------------------------- 1. store price changes -> price_moves
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


def collect(c):
    """Fold the store-level changes the trigger caught (price_moves_raw) into price_moves, keeping only the ones we
    can confirm; then empty what was read. Returns the number of chain-day changes written."""
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
        if abs(was - now) < 0.005 or (key, ch, st) in pflag:
            continue
        m = med.get(key)
        if m and not (lo * m <= was <= hi * m and lo * m <= now <= hi * m):
            continue
        g = groups.setdefault((key, ch, day, round(was, 2), round(now, 2)), [0, ts])
        g[0] += 1
        g[1] = min(g[1], ts)
    with c:
        c.executemany("""INSERT INTO price_moves(key, chain_id, day, was, now, stores, ts) VALUES(?,?,?,?,?,?,?)
                         ON CONFLICT(key, chain_id, day, was, now) DO UPDATE SET stores=stores+excluded.stores,
                         ts=min(ts, excluded.ts)""", [(*k, *v) for k, v in groups.items()])
        c.execute("DELETE FROM price_moves_raw WHERE rowid <= ?", (top,))
        if groups:
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('moves_since', ?)",
                      (str(min(k[2] for k in groups)),))
    log.info("price changes: %d store changes read, %d kept as %d chain-day changes (%.0fs)",
             len(raw), sum(v[0] for v in groups.values()), len(groups), time.time() - t0)
    return len(groups)


# ---------------------------------------------------------------- 2. the board's data
def _day(d):
    return f"{d % 100}.{d // 100 % 100}"


def _iso(d):
    return f"{d // 10000}-{d // 100 % 100:02d}-{d % 100:02d}"


def money(v):
    return f"{v:.2f}"


def norm(s):
    """Search text: lower case, no quotes, dashes or niqqud, Hebrew final letters as their plain forms."""
    s = re.sub(r"[֑-ׇ\"'`״׳\-–_.,()/]", " ", (s or "").lower())
    return " ".join(s.translate(str.maketrans("ךםןףץ", "כמנפצ")).split())


def gather(c):
    """Everything the pages are made of. Products: [{key, name, stores, cat, p: {chain: [mn, mx]},
    mv: {chain: [day, was, now, branches]}}] in board order (most stores first)."""
    t = reports.today()
    lab = reports.labels(c)
    brand = lambda cid: reports.label_of(lab, cid)
    since = int(get_setting(c, "history_since", t) or t)
    msince = get_setting(c, "moves_since", None)
    ok = {}
    cur, best_id = defaultdict(dict), {}
    for key, cid, mn, mx in c.execute("SELECT key, chain_id, mn, mx FROM price_last WHERE mn > 0 AND instr(key, ':') = 0"):
        good = ok.get(key)
        if good is None:
            good = ok[key] = comparable(key)
        if not good:
            continue
        b = brand(cid)
        p = cur[key].get(b)
        if p is None or mn < p[0]:
            cur[key][b] = [mn, max(mx, p[1]) if p else mx]
            best_id[(key, b)] = cid
        else:
            p[1] = max(p[1], mx)
    keys = {k for k, g in cur.items() if len(g) >= MIN_CHAINS}
    meta = {k: (name, sc) for k, name, sc in c.execute(
        "SELECT key, name, store_count FROM products WHERE scope='global'") if k in keys}
    cats = reports.category_of(c)
    # store-level changes, per chain: the latest one of each product, and every one in the feed's period
    start_mv, start_feed = reports.day_minus(t, MOVE_DAYS), reports.day_minus(t, FEED_DAYS - 1)
    last_mv, feed = {}, []
    for key, cid, day, was, now, st in c.execute(
            "SELECT key, chain_id, day, was, now, stores FROM price_moves WHERE day >= ? ORDER BY day, ts", (start_mv,)):
        if not ok.get(key, comparable(key)):
            continue
        b = brand(cid)
        k = (key, b)
        prev = last_mv.get(k)
        if prev is None or day > prev[0] or (day == prev[0] and st >= prev[3]):
            last_mv[k] = [day, was, now, st]
        if day >= start_feed:
            feed.append((day, key, b, was, now, st))
    products = []
    for key in keys:
        name, sc = meta.get(key, (key, 0))
        mv = {b: last_mv[(key, b)] for b in cur[key] if (key, b) in last_mv}
        products.append({"key": key, "name": name or key, "stores": sc or 0, "cat": cats.get(key, ""),
                         "p": cur[key], "mv": mv})
    products.sort(key=lambda r: (-r["stores"], -len(r["p"]), r["key"]))
    # each chain's cheapest price over time, for the charts: change-only points [day, price or None (no price)]
    hist = defaultdict(dict)
    rows = c.execute("SELECT key, chain_id, day, mn FROM price_history ORDER BY key, chain_id, day")
    for key, cid, day, mn in rows:
        if key in keys:
            b = brand(cid)
            if best_id.get((key, b)) == cid:
                pts = hist[key].setdefault(b, [])
                v = round(mn, 2) if mn is not None else None
                if not pts or pts[-1][1] != v:
                    pts.append([day, v])
    # a chart only for chains whose price really changed (two different prices), not a flat line
    hist = {k: {b: p for b, p in h.items() if len({v for _, v in p if v is not None}) > 1} for k, h in hist.items()}
    hist = {k: h for k, h in hist.items() if h}
    # chains: how many products, price level (100 = the median across chains), changes in the last 7 and 30 days
    count = defaultdict(int)
    for r in products:
        for b in r["p"]:
            count[b] += 1
    columns = [b for b in KNOWN if count.get(b, 0) >= MIN_PRODUCTS]
    columns += [b for b, _ in sorted(count.items(), key=lambda x: (-x[1], x[0])) if b not in columns]
    columns = columns[:COLUMNS]
    level = defaultdict(lambda: [0.0, 0])
    for r in products:
        if len(r["p"]) >= 3:
            m = statistics.median(v[0] for v in r["p"].values())
            for b, v in r["p"].items():
                level[b][0] += math.log(v[0] / m)
                level[b][1] += 1
    chains = []
    for b, n in count.items():
        if n < MIN_PRODUCTS:
            continue
        row = {"chain": b, "products": n, "level": reports.geo_mean_index(*level[b]) if level[b][1] else None}
        for days in (7, 30):
            s = reports.day_minus(t, days - 1)
            latest = {}
            for day, key, bb, was, now, st in feed:
                if bb == b and day >= s:
                    latest[key] = (was, now)  # feed is in day order: the last one wins
            ups = [now / was for was, now in latest.values() if now > was]
            downs = [now / was for was, now in latest.values() if now < was]
            row[f"up{days}"], row[f"down{days}"] = len(ups), len(downs)
            if days == 30:
                row["up_pct"] = statistics.median(ups) - 1 if ups else None
        chains.append(row)
    chains.sort(key=lambda r: (-r["up30"], r["level"] if r["level"] is not None else 999, r["chain"]))
    fresh = dict(c.execute("SELECT chain_id, count(*) FROM stores WHERE last_price_ts >= ? GROUP BY chain_id",
                           (trust.fresh_cutoff(),)))
    branches = defaultdict(int)
    for cid, n in fresh.items():
        branches[brand(cid)] += n
    names = {k: n for k, (n, _) in meta.items()}
    missing = {key for _, key, *_ in feed if key not in names}
    for part in reports.chunks(sorted(missing)):
        names.update(c.execute(f"SELECT key, name FROM products WHERE key IN ({','.join('?' * len(part))})", part))
    feed.sort(key=lambda m: (-m[0], -m[5], -abs(math.log(m[4] / m[3]))))
    return {"today": t, "since": since, "moves_since": int(msince) if msince else None, "built": int(time.time()),
            "products": products, "hist": hist, "columns": columns, "chains": chains, "feed": feed,
            "names": names, "branches": dict(branches)}


# ---------------------------------------------------------------- 3. pages
def esc(s):
    return html.escape(str(s), quote=True)


def both(he, en):
    return f'<span lang="he">{esc(he)}</span><span lang="en">{esc(en)}</span>'


def chain_label(b):
    return both(CHAINS_HE.get(b, b), b)


def pct(x):
    return f"{x * 100:+.0f}%" if abs(x) >= 0.005 else f"{x * 100:+.1f}%"


def cell(r, b):
    p = r["p"].get(b)
    if not p:
        return '<td class="na">—</td>'
    lows = min(v[0] for v in r["p"].values())
    cls = ' class="lo"' if p[0] - lows < 0.005 and len(r["p"]) > 1 else ""
    rng = f' title="{money(p[0])}–{money(p[1])}"' if p[1] - p[0] >= 0.005 else ""
    out = f'<td{cls}><b{rng}>{money(p[0])}</b>'
    mv = r["mv"].get(b)
    if mv:
        day, was, now, st = mv
        d = "up" if now > was else "dn"
        out += (f'<i class="{d}" data-mv="{_iso(day)}|{money(was)}|{money(now)}|{st}">{"▲" if d == "up" else "▼"}'
                f'{money(was)}<s>{_day(day)}</s></i>')
    return out + "</td>"


def row_html(r, columns):
    others = [b for b in r["p"] if b not in columns]
    more = (f'<td class="more">{len(others)}</td>' if others else '<td class="more na"></td>')
    return (f'<tr data-k="{esc(r["key"])}"><th scope="row"><button type="button" class="pn" dir="auto">{esc(r["name"])}'
            f'</button><span class="bc">{esc(r["key"])}</span></th>'
            + "".join(cell(r, b) for b in columns) + more + "</tr>")


def row_data(r):
    """What the page script needs for a row's details: every chain's price and last change."""
    return {"p": {b: [round(v[0], 2), round(v[1], 2)] for b, v in r["p"].items()},
            "mv": {b: [m[0], round(m[1], 2), round(m[2], 2), m[3]] for b, m in r["mv"].items()}}


def head_row(columns):
    return ("<thead><tr><th scope=\"col\" class=\"pc\">" + both("מוצר", "Product") + "</th>"
            + "".join(f'<th scope="col">{chain_label(b)}</th>' for b in columns)
            + '<th scope="col" class="more">' + both("עוד", "More") + "</th></tr></thead>")


def pager(page, pages):
    if pages <= 1:
        return ""
    href = lambda n: "/prices" if n == 1 else f"/prices/{n}"
    show = sorted({1, 2, pages, *range(max(1, page - 2), min(pages, page + 2) + 1)})
    out, last = [], 0
    for n in show:
        if n - last > 1:
            out.append('<span class="gap">…</span>')
        cur = ' aria-current="page"' if n == page else ""
        out.append(f'<a href="{href(n)}"{cur}>{n}</a>')
        last = n
    prev = f'<a class="step" rel="prev" href="{href(page - 1)}" aria-label="Previous">‹</a>' if page > 1 else ""
    nxt = f'<a class="step" rel="next" href="{href(page + 1)}" aria-label="Next">›</a>' if page < pages else ""
    return f'<nav class="pager" aria-label="Pages">{prev}{"".join(out)}{nxt}</nav>'


def chain_table(d):
    if not d["chains"]:
        return ""
    tracked = bool(d["moves_since"])
    rows = []
    for r in d["chains"]:
        lv = r["level"]
        lvc = "" if lv is None else (' class="hi"' if lv > 102 else ' class="lo"' if lv < 98 else "")
        up = f'<bdi dir="ltr">{pct(r["up_pct"])}</bdi>' if r.get("up_pct") is not None else "—"
        moves = (f'<td class="up">{r["up7"]:,}</td><td class="dn">{r["down7"]:,}</td>'
                 f'<td class="up">{r["up30"]:,}</td><td class="dn">{r["down30"]:,}</td><td>{up}</td>') if tracked else ""
        rows.append(f'<tr><th scope="row">{chain_label(r["chain"])}</th>'
                    f'<td{lvc}>{lv if lv is not None else "—"}</td>{moves}<td>{r["products"]:,}</td></tr>')
    if tracked:
        since = both("מעקב שינויים מ־" + _day(d["moves_since"]), "Changes tracked since " + _day(d["moves_since"]))
        heads = ('<th scope="col" class="up">' + both("העלו · 7 ימים", "Raised · 7 days") + "</th>"
                 '<th scope="col" class="dn">' + both("הורידו · 7 ימים", "Lowered · 7 days") + "</th>"
                 '<th scope="col" class="up">' + both("העלו · 30 יום", "Raised · 30 days") + "</th>"
                 '<th scope="col" class="dn">' + both("הורידו · 30 יום", "Lowered · 30 days") + "</th>"
                 '<th scope="col">' + both("העלייה החציונית", "Median rise") + "</th>")
    else:
        since = both("שינויי מחיר יופיעו כאן מ־" + _day(d["today"]), "Price changes show here from " + _day(d["today"]))
        heads = ""
    return ('<section class="chains" id="chains"><h2>' + both("מי העלה מחירים", "Who raised prices")
            + f' <span class="note">{since}</span></h2>'
            '<div class="scroll"><table class="ct"><thead><tr><th scope="col">' + both("רשת", "Chain") + "</th>"
            '<th scope="col">' + both("רמת מחיר", "Price level") + "</th>" + heads
            + '<th scope="col">' + both("מוצרים", "Products") + "</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>"
            '<p class="note">' + both("רמת מחיר 100 = המחיר החציוני בין הרשתות. מתחת ל־100 = זול יותר.",
                                      "Price level 100 = the median across chains. Below 100 = cheaper.") + "</p></section>")


def move_row(d, m, with_day=True):
    day, key, b, was, now, st = m
    up = now > was
    return (f'<tr><td class="d">{_day(day)}</td>' if with_day else "<tr>") + (
        f'<th scope="row"><a href="/app#/p/{esc(key)}" dir="auto">{esc(d["names"].get(key) or key)}</a></th>'
        f'<td>{chain_label(b)}</td><td class="pr"><bdi dir="ltr">{money(was)} → <b>{money(now)}</b></bdi></td>'
        f'<td class="{"up" if up else "dn"}"><bdi dir="ltr">{pct(now / was - 1)}</bdi></td><td class="st">{st:,}</td></tr>')


def moves_head(with_day=True):
    return ("<thead><tr>" + ('<th scope="col">' + both("יום", "Day") + "</th>" if with_day else "")
            + '<th scope="col">' + both("מוצר", "Product") + '</th><th scope="col">' + both("רשת", "Chain")
            + '</th><th scope="col">' + both("היה → עכשיו", "Was → now") + '</th><th scope="col">'
            + both("שינוי", "Change") + '</th><th scope="col">' + both("סניפים", "Branches") + "</th></tr></thead>")


def highlights(d):
    """The first page's biggest rises and drops of the last 7 days, at enough branches to be a chain's decision."""
    s = reports.day_minus(d["today"], 6)
    wide = lambda m: m[5] >= min(3, d["branches"].get(m[2], 1))
    recent = [m for m in d["feed"] if m[0] >= s and wide(m)]
    ups = sorted((m for m in recent if m[4] > m[3]), key=lambda m: -m[4] / m[3])[:HIGHLIGHT]
    downs = sorted((m for m in recent if m[4] < m[3]), key=lambda m: m[4] / m[3])[:HIGHLIGHT]
    if not ups and not downs:
        return ""
    box = lambda title, ms, cls: (f'<div class="hl {cls}"><h3>{title}</h3><div class="scroll"><table class="mt">'
                                  + moves_head() + "<tbody>" + "".join(move_row(d, m) for m in ms)
                                  + "</tbody></table></div></div>")
    return ('<section class="highlights"><h2>' + both("השבוע", "This week") + "</h2><div class=\"hls\">"
            + (box(both("העליות הגדולות", "Biggest rises"), ups, "ups") if ups else "")
            + (box(both("הירידות הגדולות", "Biggest drops"), downs, "downs") if downs else "")
            + '</div><p class="more-link"><a href="/prices/changes">' + both("כל השינויים ←", "All changes →")
            + "</a></p></section>")


def stamp(d):
    tm = time.localtime(d["built"])
    when = f'{tm.tm_mday}.{tm.tm_mon} {tm.tm_hour:02d}:{tm.tm_min:02d}'
    n = len(d["products"])
    return ('<p class="stamp">' + both(f"עודכן {when} · {n:,} מוצרים · מחירי מדף, בלי מבצעים",
                                       f"Updated {when} · {n:,} products · shelf prices, no deals") + "</p>")


def page_html(tpl, title, desc, canonical, main, data=None, prev=None, nxt=None):
    links = f'<link rel="canonical" href="{canonical}">'
    links += f'<link rel="prev" href="{prev}">' if prev else ""
    links += f'<link rel="next" href="{nxt}">' if nxt else ""
    blob = ""
    if data is not None:
        blob = ('<script type="application/json" id="board-data">'
                + json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + "</script>")
    return (tpl.replace("<!--TITLE-->", esc(title)).replace("<!--DESC-->", esc(desc)).replace("<!--LINKS-->", links)
            .replace("<!--MAIN-->", main).replace("<!--DATA-->", blob))


TITLE = "מחירון הסופרמרקטים · סל קל"
DESC = ("כל המוצרים, כל הרשתות: כמה עולה כל מוצר בכל רשת היום, מה היה המחיר הקודם ומי העלה מחירים. "
        "לפי קובצי המחירים שהרשתות מחויבות בחוק לפרסם.")


def stamp_static(page):
    """Each script and stylesheet URL carries its file's change time (as app.server._page does), so the server sends
    the built bytes as they are. A deploy that changes them reaches the pages with the next build (install-app.sh
    runs one)."""
    def one(mt):
        f = os.path.join(STATIC, mt.group(2))
        return f'{mt.group(1)}/static/{mt.group(2)}?v={int(os.stat(f).st_mtime) if os.path.exists(f) else 0}"'
    return re.sub(r'(src="|href=")/static/([\w./-]+\.(?:js|css))"', one, page)


def render(d, base="https://sal-kal.com"):
    """{file name: bytes} of every page (gzipped), the signed-in search rows and the site map."""
    tpl = open(os.path.join(STATIC, "prices.html"), encoding="utf-8").read()
    cols, prods = d["columns"], d["products"]
    pages = max(1, math.ceil(len(prods) / PAGE))
    out = {}
    intro = ('<section class="intro"><h1>' + both("מחירון הסופרמרקטים", "Supermarket prices")
             + "</h1>" + stamp(d) + "</section>")
    for n in range(1, pages + 1):
        part = prods[(n - 1) * PAGE:n * PAGE]
        head = intro + (chain_table(d) + highlights(d) if n == 1 else "")
        main = (head + '<section class="products" id="products"><h2>'
                + both("כל המוצרים", "All products") + (both(f" · עמוד {n}", f" · page {n}") if n > 1 else "")
                + '</h2><div id="tools"></div><div class="scroll grid-wrap"><table class="grid" data-cols="'
                + esc("|".join(cols)) + '">' + head_row(cols) + '<tbody id="rows">'
                + "".join(row_html(r, cols) for r in part) + "</tbody></table></div>"
                + f'<div id="pager">{pager(n, pages)}</div></section>')
        data = {"cols": cols, "rows": {r["key"]: row_data(r) for r in part},
                "hist": {r["key"]: d["hist"][r["key"]] for r in part if r["key"] in d["hist"]},
                "he": {b: CHAINS_HE.get(b, b) for r in part for b in r["p"]},
                "cats": [{"slug": x["slug"], "name": x["name"], "he": x["he"]} for x in catalog.CATEGORIES]}
        url = base + ("/prices" if n == 1 else f"/prices/{n}")
        out[f"p{n}.html.gz"] = page_html(
            tpl, TITLE if n == 1 else f"עמוד {n} · {TITLE}", DESC, url, main, data,
            prev=(base + ("/prices" if n == 2 else f"/prices/{n - 1}")) if n > 1 else None,
            nxt=base + f"/prices/{n + 1}" if n < pages else None)
    feed = d["feed"][:FEED]
    main = (intro + '<section class="changes"><h2>' + both(f"שינויי מחיר · {FEED_DAYS} הימים האחרונים",
                                                          f"Price changes · last {FEED_DAYS} days") + "</h2>"
            + ('<div class="scroll"><table class="mt">' + moves_head() + "<tbody>"
               + "".join(move_row(d, m) for m in feed) + "</tbody></table></div>" if feed else
               '<p class="empty">' + both("עוד לא נרשמו שינויי מחיר.", "No price changes recorded yet.") + "</p>")
            + "</section>")
    out["changes.html.gz"] = page_html(tpl, f"שינויי מחיר · {TITLE}", DESC, base + "/prices/changes", main)
    out = {k: gzip.compress(stamp_static(v).encode("utf-8"), 6, mtime=0) for k, v in out.items()}
    # the signed-in search: one JSON line per product, in board order, with what search and sorting need
    lines = []
    for r in prods:
        lows = [v[0] for v in r["p"].values()]
        moves = [(m[2] / m[1] - 1, m[0]) for m in r["mv"].values()]
        recent = [x for x, day in moves if day >= reports.day_minus(d["today"], FEED_DAYS - 1)]
        lines.append(json.dumps({
            "k": r["key"], "n": r["name"], "q": norm(r["name"]), "c": r["cat"], "s": r["stores"],
            "md": max((day for _, day in moves), default=0), "up": max(recent, default=0), "dn": min(recent, default=0),
            "gap": round(max(lows) / min(lows), 4) if min(lows) > 0 else 1, "p": r["p"], "mv": r["mv"]},
            ensure_ascii=False, separators=(",", ":")))
    out["rows.json.gz"] = gzip.compress("\n".join(lines).encode("utf-8"), 6, mtime=0)
    urls = [base + "/prices"] + [base + f"/prices/{n}" for n in range(2, pages + 1)] + [base + "/prices/changes"]
    out["sitemap.xml"] = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                          + "".join(f"<url><loc>{u}</loc></url>" for u in [base + "/", *urls]) + "</urlset>\n").encode()
    out["meta.json"] = json.dumps({"pages": pages, "products": len(prods), "built": d["built"], "columns": cols,
                                   "chains": sorted({b for r in prods for b in r["p"]})}, ensure_ascii=False).encode()
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
    log.info("price board %s: %d products, %d changes in %d days (%.0fs)", version, len(d["products"]),
             len(d["feed"]), FEED_DAYS, time.time() - t0)
    return version


# ---------------------------------------------------------------- 4. reading a build (the web server)
class Board:
    """The current build, re-read when `current` changes (checked at most every few seconds)."""

    def __init__(self, root):
        self.root, self.version, self.checked, self.meta, self.rows, self.files = root, None, 0, {}, None, {}

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
            self.version, self.rows, self.files = v, None, {}

    def file(self, name):
        """(gzipped bytes, version) of a page, or (None, None)."""
        self._sync()
        if not self.version:
            return None, None
        hit = self.files.get(name)
        if hit is None:
            try:
                hit = open(os.path.join(self.root, self.version, name), "rb").read()
            except OSError:
                return None, None
            if len(self.files) > 4000:  # a build is ~700 pages, ~15 MB: they all fit
                self.files.clear()
            self.files[name] = hit
        return hit, self.version

    def pages(self):
        self._sync()
        return self.meta.get("pages", 0)

    def search(self, q="", cat="", sort="popular", moved=False, page=1):
        """Signed-in search over the board: (rows for the page, total)."""
        self._sync()
        if self.rows is None:
            if not self.version:
                return [], 0
            try:
                raw = gzip.decompress(open(os.path.join(self.root, self.version, "rows.json.gz"), "rb").read())
            except OSError:
                return [], 0
            rows = []
            for line in raw.decode("utf-8").split("\n"):
                if line:
                    r = json.loads(line)
                    rows.append((r["k"], r["q"], r["c"], r["md"], r["up"], r["dn"], r["gap"], line))
            self.rows = rows
        words = norm(q).split()[:12]
        hits = []
        for i, r in enumerate(self.rows):
            if cat and r[2] != cat:
                continue
            if moved and not r[3]:
                continue
            if words and not all((w.isdigit() and r[0].startswith(w)) or w in r[1] for w in words):
                continue
            hits.append((i, r))
        order = {"rises": lambda x: (-x[1][4], x[0]), "drops": lambda x: (x[1][5], x[0]),
                 "recent": lambda x: (-x[1][3], x[0]), "gap": lambda x: (-x[1][6], x[0])}.get(sort)
        if order:
            hits.sort(key=order)
        if sort == "rises":
            hits = [h for h in hits if h[1][4] > 0]
        elif sort == "drops":
            hits = [h for h in hits if h[1][5] < 0]
        part = hits[(page - 1) * PAGE:page * PAGE]
        out = []
        for _, r in part:
            x = json.loads(r[7])
            out.append({"key": x["k"], "name": x["n"], "stores": x["s"], "cat": x["c"], "p": x["p"], "mv": x["mv"]})
        return out, len(hits)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build(dbmod.connect(a.db), a.out or board_dir(a.db))


if __name__ == "__main__":
    main()
