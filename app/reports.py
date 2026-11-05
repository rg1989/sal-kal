"""Reports to sell, for the owner's admin page (/admin#reports): built only from the chains' public price files.

Nothing here reads user data (carts, accounts, searches, usage statistics); every number comes from the confirmed
price layer the indexer keeps: price_last (each product's cheapest and dearest confirmed shelf price per chain now),
price_history (the same, one row per day it changed), deals (deals whose terms check out) and, for branches, the
prices table under the same rules (indexer.CONFIRMED_ROW). Chain-internal codes (produce, deli) are never compared.

A report is {name, title, method, params, generated, columns: [{key, label, kind}], rows: [{key: value}],
summary: [[label, value, kind]], parts: {name: {title, columns, rows}}}; kind is text | he | code | money | pct |
index | int | day. The server sends it as JSON or as CSV (csv_text).
"""
import csv
import heapq
import io
import math
import re
import statistics
import threading
import time
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import catalog, trust
from .chains import chain_name
from .engine import comparable, get_setting

MIN_PRODUCTS = 50     # a chain compared on fewer products than this isn't ranked (noise)
MIN_CAT_PRODUCTS = 25  # same, per category
TOP = 500             # longest lists kept in a report
CACHE_SECONDS = 900


# ---------------------------------------------------------------- helpers
def today():
    return int(time.strftime("%Y%m%d"))


def day_minus(day, n):
    d = date(day // 10000, day // 100 % 100, day % 100) - timedelta(days=n)
    return d.year * 10000 + d.month * 100 + d.day


def days_between(a, b):
    """Calendar days a..b (YYYYMMDD ints), inclusive."""
    d, end = date(a // 10000, a // 100 % 100, a % 100), date(b // 10000, b // 100 % 100, b % 100)
    out = []
    while d <= end:
        out.append(d.year * 10000 + d.month * 100 + d.day)
        d += timedelta(days=1)
    return out


def clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def col(key, label, kind="text"):
    return {"key": key, "label": label, "kind": kind}


def labels(c):
    """{chain_id: display name}; chains that publish under two ids (Victory) share one name and are reported as one."""
    names = dict(c.execute("SELECT chain_id, name_he FROM chains"))
    return defaultdict(lambda: None, {cid: chain_name(cid, names.get(cid)) for cid in names})


def label_of(lab, cid):
    return lab.get(cid) or chain_name(cid)


def parse_codes(text):
    """Barcodes from pasted text: any run of 8-14 digits."""
    return list(dict.fromkeys(re.findall(r"(?<!\d)\d{8,14}(?!\d)", text or "")))


def chunks(seq, n=500):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def names_of(c, keys):
    out = {}
    for part in chunks(keys):
        ph = ",".join("?" * len(part))
        for k, name, q, u in c.execute(f"SELECT key, name, quantity, unit FROM products WHERE key IN ({ph})", part):
            out[k] = name or k
    return out


def by_product(c, keys=None):
    """(key, {chain name: cheapest confirmed shelf price}) for every barcode product priced now, in key order."""
    lab = labels(c)
    if keys is not None:
        rows = []
        for part in chunks(sorted(keys)):
            ph = ",".join("?" * len(part))
            rows += c.execute(f"SELECT key, chain_id, mn FROM price_last WHERE mn > 0 AND key IN ({ph}) ORDER BY key",
                              part).fetchall()
        rows.sort(key=lambda r: r[0])
    else:
        rows = c.execute("SELECT key, chain_id, mn FROM price_last WHERE mn > 0 AND instr(key, ':') = 0 ORDER BY key")
    cur, group = None, {}
    for key, cid, mn in rows:
        if key != cur:
            if group and comparable(cur):
                yield cur, group
            cur, group = key, {}
        name = label_of(lab, cid)
        if name not in group or mn < group[name]:
            group[name] = mn
    if group and comparable(cur):
        yield cur, group


def category_of(c):
    """{key: category slug}: the shop's categories (catalog.CATEGORIES), first match wins."""
    out = {}
    for cat in catalog.CATEGORIES:
        for (key,) in c.execute("SELECT key FROM products_fts WHERE products_fts MATCH ?", (catalog.fts_query(cat),)):
            out.setdefault(key, cat["slug"])
    return out


def cat_filter(c, slug):
    """Keys in one category, or None for every product."""
    cat = next((x for x in catalog.CATEGORIES if x["slug"] == slug), None)
    if not cat:
        return None, ""
    keys = {k for (k,) in c.execute("SELECT key FROM products_fts WHERE products_fts MATCH ?", (catalog.fts_query(cat),))}
    return keys, cat["name"]


def town_names(c):
    try:
        return {str(code): name for code, name in c.execute("SELECT code, name FROM towns")}
    except Exception:
        return {}


def geo_mean_index(log_sum, n):
    return round(100 * math.exp(log_sum / n), 1) if n else None


SOURCE = ("Source: the supermarket chains' price files, published under Israel's Food Act (2014) §30 and free to use. "
          "Confirmed prices only: current stores, the same barcode named alike across chains, prices in line with "
          "other stores. Shelf prices; deals not included unless stated.")


# ---------------------------------------------------------------- 1. chain price index
def price_level(c, p):
    min_chains = clamp(p.get("min_chains"), 2, 10, 3)
    cats = category_of(c)
    slugs = [(x["slug"], x["name"]) for x in catalog.CATEGORIES]
    acc = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    cheapest, below = Counter(), Counter()
    products = 0
    for key, g in by_product(c):
        if len(g) < min_chains:
            continue
        products += 1
        vals = list(g.values())
        m, lo = statistics.median(vals), min(vals)
        s = cats.get(key)
        for name, v in g.items():
            r = math.log(v / m)
            a = acc[name]["all"]
            a[0] += r
            a[1] += 1
            if s:
                b = acc[name][s]
                b[0] += r
                b[1] += 1
            if v - lo < 0.005:
                cheapest[name] += 1
            if v < m - 0.005:
                below[name] += 1
    rows, left_out = [], []
    for name, d in acc.items():
        n = d["all"][1]
        if n < MIN_PRODUCTS:
            left_out.append(name)
            continue
        row = {"chain": name, "products": n, "index": geo_mean_index(*d["all"]),
               "cheapest": cheapest[name] / n, "below": below[name] / n}
        for slug, _ in slugs:
            x = d.get(slug)
            row[slug] = geo_mean_index(*x) if x and x[1] >= MIN_CAT_PRODUCTS else None
        rows.append(row)
    rows.sort(key=lambda r: r["index"])
    cols = [col("chain", "Chain"), col("products", "Products compared", "int"), col("index", "Price index", "index"),
            col("cheapest", "Cheapest on", "pct"), col("below", "Below typical", "pct")]
    cols += [col(slug, name, "index") for slug, name in slugs if any(r.get(slug) is not None for r in rows)]
    return {
        "title": "Chain price index",
        "method": (f"100 = the typical price: for each barcode product sold at {min_chains}+ chains, each chain's "
                   "cheapest confirmed shelf price is divided by the median across chains; a chain's index is the "
                   "geometric mean over the products it sells (below 100 = cheaper). Categories need "
                   f"{MIN_CAT_PRODUCTS}+ products. Chains with fewer than {MIN_PRODUCTS} products compared are left out."),
        "params": {"min_chains": min_chains},
        "columns": cols, "rows": rows,
        "summary": [["Products compared", products, "int"], ["Chains ranked", len(rows), "int"]]
                   + ([["Left out (too few products)", ", ".join(sorted(left_out)), "text"]] if left_out else []),
    }


# ---------------------------------------------------------------- 2. a basket over time
def basket(c, p):
    size = clamp(p.get("size"), 5, 200, 30)
    period = clamp(p.get("period"), 7, 730, 90)
    codes = parse_codes(p.get("codes"))
    lab = labels(c)
    if codes:
        known = {k for part in chunks(codes) for (k,) in c.execute(
            f"SELECT key FROM products WHERE key IN ({','.join('?' * len(part))})", part)}
        keys = [k for k in codes if k in known and comparable(k)]
        now = dict(by_product(c, set(keys)))
        chains = sorted({n for k in keys for n in now.get(k, {})})
        chains = [n for n in chains if all(n in now.get(k, {}) for k in keys)]
    else:
        # a household basket: the most widely sold products of every shop category, taken in turn
        per_cat = []
        for cat in catalog.CATEGORIES:
            per_cat.append([k for (k,) in c.execute(
                """SELECT p.key FROM products_fts f JOIN products p ON p.key=f.key WHERE products_fts MATCH ?
                   AND p.scope='global' AND p.chain_count >= 5 AND p.store_count > 0 ORDER BY p.store_count DESC LIMIT 40""",
                (catalog.fts_query(cat),)) if comparable(k)])
        pool = list(dict.fromkeys(k for ks in per_cat for k in ks))
        now = dict(by_product(c, set(pool)))
        carry = Counter(n for k in pool for n in now.get(k, {}))
        chains = [n for n, x in carry.items() if pool and x >= 0.6 * len(pool)]
        per_cat = [[k for k in ks if k in now and all(n in now[k] for n in chains)] for ks in per_cat]
        keys = []
        while len(keys) < size and any(per_cat):
            for ks in per_cat:
                while ks and ks[0] in keys:
                    ks.pop(0)
                if ks and len(keys) < size:
                    keys.append(ks.pop(0))
    t = today()
    since = int(get_setting(c, "history_since", t) or t)
    start = max(day_minus(t, period - 1), since)
    ids_of = defaultdict(list)
    for (cid,) in c.execute("SELECT DISTINCT chain_id FROM price_last"):
        if label_of(lab, cid) in chains:
            ids_of[label_of(lab, cid)].append(cid)
    ids = {cid for v in ids_of.values() for cid in v}
    hist = []
    for part in chunks(keys):
        ph = ",".join("?" * len(part))
        hist += c.execute(f"SELECT key, chain_id, day, mn FROM price_history WHERE key IN ({ph}) AND day <= ?",
                          [*part, t]).fetchall()
    hist = sorted((r for r in hist if r[1] in ids), key=lambda r: r[2])
    state, i, rows = {}, 0, []
    for d in days_between(start, t):
        while i < len(hist) and hist[i][2] <= d:
            k, cid, _, mn = hist[i]
            state[(k, cid)] = mn
            i += 1
        row = {"day": d}
        for name in chains:
            total = 0.0
            for k in keys:
                vals = [v for v in (state.get((k, cid)) for cid in ids_of[name]) if v]
                if not vals:
                    total = None
                    break
                total += min(vals)
            row[name] = round(total, 2) if total is not None else None
        rows.append(row)
    last = rows[-1] if rows else {}
    chains.sort(key=lambda n: (last.get(n) is None, last.get(n) or 0))
    names = names_of(c, keys)
    items = [{"product": names.get(k, k), "code": k, **{n: now.get(k, {}).get(n) for n in chains}} for k in keys]
    return {
        "title": "Basket over time",
        "method": ("The basket's total at each chain per day: the chain's cheapest confirmed shelf price for every "
                   "product in it. A day is left blank at a chain that had no confirmed price for one of the products. "
                   + ("Basket: the products pasted in." if codes else
                      f"Basket: {len(keys)} barcode products, the most widely sold of each shop category in turn, "
                      "that every chain shown sells; chains shown sell 60%+ of the candidates.")),
        "params": {"size": size, "period": period, "codes": " ".join(codes)},
        "columns": [col("day", "Day", "day")] + [col(n, n, "money") for n in chains],
        "rows": rows,
        "summary": [["Products in the basket", len(keys), "int"], ["Chains", len(chains), "int"],
                    ["Price history since", since, "day"]],
        "parts": {"items": {"title": "Products in the basket",
                            "columns": [col("product", "Product", "he"), col("code", "Barcode", "code")]
                                       + [col(n, n, "money") for n in chains],
                            "rows": items}},
    }


# ---------------------------------------------------------------- 3. price changes
def changes(c, p):
    period = clamp(p.get("period"), 1, 730, 30)
    keys, cat_name = cat_filter(c, p.get("cat"))
    t = today()
    start = day_minus(t, period)
    since = int(get_setting(c, "history_since", t) or t)
    lab = labels(c)
    acc = defaultdict(lambda: {"tracked": 0, "up": 0, "down": 0, "log": 0.0, "moves": []})
    for k, cid in c.execute("""SELECT key, chain_id FROM price_last
                               WHERE mn > 0 AND instr(key, ':') = 0 AND day <= ?""", (start,)):
        if (keys is None or k in keys) and comparable(k):
            acc[label_of(lab, cid)]["tracked"] += 1
    moves = []
    for k, cid, now, was in [] if start < since else c.execute("""
            SELECT l.key, l.chain_id, l.mn, (SELECT h.mn FROM price_history h WHERE h.key=l.key
                   AND h.chain_id=l.chain_id AND h.day <= ? ORDER BY h.day DESC LIMIT 1)
            FROM price_last l WHERE l.mn > 0 AND instr(l.key, ':') = 0 AND l.day > ?""", (start, start)):
        if not was or (keys is not None and k not in keys) or not comparable(k):
            continue
        a = acc[label_of(lab, cid)]
        a["tracked"] += 1
        if abs(now - was) < 0.005:
            continue
        r = math.log(now / was)
        a["log"] += r
        a["up" if now > was else "down"] += 1
        a["moves"].append(now / was - 1)
        moves.append((abs(r), k, cid, was, now))
    rows = []
    for name, a in acc.items():
        if not a["tracked"]:
            continue
        rows.append({"chain": name, "tracked": a["tracked"], "up": a["up"], "down": a["down"],
                     "avg": math.exp(a["log"] / a["tracked"]) - 1,
                     "median": statistics.median(a["moves"]) if a["moves"] else None})
    rows.sort(key=lambda r: -r["avg"])
    top = heapq.nlargest(TOP, moves)
    names = names_of(c, {m[1] for m in top})
    move_rows = [{"product": names.get(k, k), "code": k, "chain": label_of(lab, cid), "was": was, "now": now,
                  "change": now / was - 1} for _, k, cid, was, now in top]
    return {
        "title": "Price changes",
        "method": (f"Each chain's cheapest confirmed shelf price for a product now, against the same on "
                   f"{start // 10000}-{start // 100 % 100:02d}-{start % 100:02d} ({period} days ago). Only products "
                   "the chain sold at both times. Average change = geometric mean over every such product, unchanged "
                   "ones included." + (" Price history starts on the day shown; earlier periods have nothing to compare."
                                      if start < since else "")),
        "params": {"period": period, "cat": p.get("cat") or ""},
        "columns": [col("chain", "Chain"), col("tracked", "Products tracked", "int"), col("up", "Went up", "int"),
                    col("down", "Went down", "int"), col("avg", "Average change", "pct"),
                    col("median", "Median change (changed only)", "pct")],
        "rows": rows,
        "summary": [["Period", f"{period} days", "text"], ["Category", cat_name or "All", "text"],
                    ["Price history since", since, "day"], ["Price changes found", len(moves), "int"]],
        "parts": {"moves": {"title": "Biggest changes",
                            "columns": [col("product", "Product", "he"), col("code", "Barcode", "code"),
                                        col("chain", "Chain"), col("was", "Was", "money"), col("now", "Now", "money"),
                                        col("change", "Change", "pct")],
                            "rows": move_rows}},
    }


# ---------------------------------------------------------------- 4. deals by chain
def deals(c, p):
    lab = labels(c)
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='deals'").fetchone():
        return {"title": "Deals by chain", "method": "No deals table yet.", "params": {}, "columns": [], "rows": []}
    per = defaultdict(dict)  # name -> key -> (save, stores, chain_id)
    for cid, k, save, stores in c.execute("SELECT chain_id, key, max(save_pct), count(*) FROM deals GROUP BY chain_id, key"):
        name = label_of(lab, cid)
        if k not in per[name] or save > per[name][k][0]:
            per[name][k] = (save, stores, cid)
    branches = Counter()
    for cid, n in c.execute("SELECT chain_id, count(DISTINCT store_id) FROM deals GROUP BY chain_id"):
        branches[label_of(lab, cid)] += n
    ids_of, sold_by = defaultdict(list), Counter()
    for cid, n in c.execute("SELECT chain_id, count(*) FROM price_last WHERE instr(key, ':') = 0 GROUP BY chain_id"):
        ids_of[label_of(lab, cid)].append(cid)
        sold_by[label_of(lab, cid)] += n
    for name, ids in ids_of.items():  # a chain under two ids: count each product once
        if len(ids) > 1:
            sold_by[name] = c.execute(f"""SELECT count(DISTINCT key) FROM price_last WHERE instr(key, ':') = 0
                                          AND chain_id IN ({','.join('?' * len(ids))})""", ids).fetchone()[0]
    rows, top = [], []
    for name, d in per.items():
        sold = sold_by[name]
        saves = [x[0] for x in d.values()]
        rows.append({"chain": name, "on_deal": len(d), "share": len(d) / sold if sold else None,
                     "median": statistics.median(saves), "deep": sum(1 for s in saves if s >= 0.3),
                     "branches": branches[name]})
        top += [(x[0], k, x[2], x[1]) for k, x in d.items()]
    rows.sort(key=lambda r: -r["on_deal"])
    best = heapq.nlargest(TOP, top)
    names = names_of(c, {b[1] for b in best})
    top_rows = []
    for save, k, cid, stores in best:
        r = c.execute("""SELECT unit_price, typical, description FROM deals WHERE key=? AND chain_id=?
                         ORDER BY save_pct DESC LIMIT 1""", (k, cid)).fetchone()
        top_rows.append({"product": names.get(k, k), "code": k, "chain": label_of(lab, cid), "price": r[0],
                         "typical": r[1], "save": save, "stores": stores, "deal": r[2]})
    return {
        "title": "Deals by chain",
        "method": ("Deals open to every shopper (no club, coupon, staff or spend-₪X deals) whose description and "
                   "numbers agree, on barcode products, at current stores, priced per unit against the product's "
                   "typical price; savings under 8% or over 70% are left out. Share = products on a deal at one or "
                   "more branches, of the barcode products the chain sells."),
        "params": {},
        "columns": [col("chain", "Chain"), col("on_deal", "Products on a deal", "int"),
                    col("share", "Share of its products", "pct"), col("median", "Median saving", "pct"),
                    col("deep", "Savings of 30%+", "int"), col("branches", "Branches with deals", "int")],
        "rows": rows,
        "summary": [["Products on a deal somewhere", len({k for d in per.values() for k in d}), "int"]],
        "parts": {"top": {"title": "Biggest savings",
                          "columns": [col("product", "Product", "he"), col("code", "Barcode", "code"),
                                      col("chain", "Chain"), col("price", "Deal price per unit", "money"),
                                      col("typical", "Typical price", "money"), col("save", "Saving", "pct"),
                                      col("stores", "Branches", "int"), col("deal", "Deal", "he")],
                          "rows": top_rows}},
    }


# ---------------------------------------------------------------- 5. price gaps
def gaps(c, p):
    mode = "branches" if p.get("mode") == "branches" else "chains"
    keys, cat_name = cat_filter(c, p.get("cat"))
    lab = labels(c)
    if mode == "chains":
        min_chains = clamp(p.get("min_chains"), 2, 20, 5)
        heap, ratios = [], []
        for k, g in by_product(c):
            if len(g) < min_chains or (keys is not None and k not in keys):
                continue
            lo_n, lo = min(g.items(), key=lambda x: x[1])
            hi_n, hi = max(g.items(), key=lambda x: x[1])
            ratios.append(hi / lo)
            item = (hi / lo, k, len(g), lo_n, lo, hi_n, hi)
            if len(heap) < TOP:
                heapq.heappush(heap, item)
            else:
                heapq.heappushpop(heap, item)
        top = sorted(heap, reverse=True)
        names = names_of(c, {t[1] for t in top})
        rows = [{"product": names.get(k, k), "code": k, "chains": n, "low_chain": lon, "low": lo,
                 "high_chain": hin, "high": hi, "gap": r - 1} for r, k, n, lon, lo, hin, hi in top]
        return {
            "title": "Same product, different price: across chains",
            "method": (f"Barcode products sold at {min_chains}+ chains: each chain's cheapest confirmed shelf price; "
                       "gap = dearest chain against cheapest chain."),
            "params": {"mode": mode, "cat": p.get("cat") or "", "min_chains": min_chains},
            "columns": [col("product", "Product", "he"), col("code", "Barcode", "code"), col("chains", "Chains", "int"),
                        col("low_chain", "Cheapest at"), col("low", "Price", "money"), col("high_chain", "Dearest at"),
                        col("high", "Price", "money"), col("gap", "Gap", "pct")],
            "rows": rows,
            "summary": [["Products compared", len(ratios), "int"], ["Category", cat_name or "All", "text"],
                        ["Median gap", (statistics.median(ratios) - 1) if ratios else None, "pct"],
                        ["Gap of 25%+", sum(1 for x in ratios if x >= 1.25), "int"]],
        }
    # branches: the same chain, the same product, different branches
    from .indexer import CONFIRMED_ROW
    per = defaultdict(lambda: [0, 0, []])  # name -> [products at 3+ branches, with a 10%+ gap, gaps]
    heap = []
    # left out: products sold at fewer stores than the outlier check needs (a carton priced as a pack would show),
    # and barcodes only one chain uses (internal codes such as 8888888888)
    for k, cid, mn, mx, n in c.execute("""
            SELECT l.key, l.chain_id, l.mn, l.mx, h.n FROM price_last l
            JOIN price_history h ON h.key=l.key AND h.chain_id=l.chain_id AND h.day=l.day
            JOIN products pr ON pr.key=l.key
            WHERE l.mn > 0 AND instr(l.key, ':') = 0 AND h.n >= 3 AND pr.store_count >= ? AND pr.chain_count >= 2""",
            (trust.OUTLIER_MIN_STORES,)):
        if (keys is not None and k not in keys) or not comparable(k):
            continue
        name = label_of(lab, cid)
        a = per[name]
        a[0] += 1
        a[2].append(mx / mn)
        if mx / mn >= 1.1:
            a[1] += 1
        item = (mx / mn, k, cid, mn, mx, n)
        if len(heap) < TOP:
            heapq.heappush(heap, item)
        else:
            heapq.heappushpop(heap, item)
    top = sorted(heap, reverse=True)
    names = names_of(c, {t[1] for t in top})
    towns = town_names(c)
    # the same stores price_last was built from: current, not flagged (prices newer than the last check count too)
    params = {"cutoff": trust.fresh_cutoff(), "through": 99999999999999}

    def where(store):
        sid, name, city = store
        city = towns.get(str(city), city) if city else ""
        name = name or f"Branch {sid}"
        return f"{name}{' · ' + city if city and city not in name else ''}"

    rows = []
    for i, (r, k, cid, mn, mx, n) in enumerate(top):
        row = {"product": names.get(k, k), "code": k, "chain": label_of(lab, cid), "branches": n, "low": mn,
               "high": mx, "gap": r - 1, "low_at": "", "high_at": ""}
        if i < 200:
            st = c.execute(f"""SELECT s.store_id, s.name, s.city, p.price FROM prices p JOIN stores s
                               ON s.chain_id=p.chain_id AND s.store_id=p.store_id
                               WHERE p.key=:key AND p.chain_id=:cid AND p.price > 0 AND {CONFIRMED_ROW}
                               ORDER BY p.price""", {**params, "key": k, "cid": cid}).fetchall()
            if st:
                row["low_at"], row["high_at"] = where(st[0][:3]), where(st[-1][:3])
        rows.append(row)
    summary_rows = [{"chain": name, "products": a[0], "gapped": a[1], "share": a[1] / a[0],
                     "median": statistics.median(a[2]) - 1} for name, a in per.items() if a[0] >= MIN_PRODUCTS]
    summary_rows.sort(key=lambda r: -r["share"])
    return {
        "title": "Same product, different price: across a chain's branches",
        "method": ("Barcode products a chain sells at 3+ current branches, sold by 2+ chains and at 4+ stores in all "
                   "(so prices far from the product's median are already left out): the chain's dearest confirmed "
                   "shelf price against its cheapest. Branches are named for the first 200 products."),
        "params": {"mode": mode, "cat": p.get("cat") or ""},
        "columns": [col("chain", "Chain"), col("products", "Products at 3+ branches", "int"),
                    col("gapped", "Priced 10%+ apart", "int"), col("share", "Share", "pct"),
                    col("median", "Median gap", "pct")],
        "rows": summary_rows,
        "summary": [["Category", cat_name or "All", "text"]],
        "parts": {"products": {"title": "Biggest gaps between branches",
                               "columns": [col("product", "Product", "he"), col("code", "Barcode", "code"),
                                           col("chain", "Chain"), col("branches", "Branches", "int"),
                                           col("low", "Cheapest", "money"), col("low_at", "At", "he"),
                                           col("high", "Dearest", "money"), col("high_at", "At", "he"),
                                           col("gap", "Gap", "pct")],
                               "rows": rows}},
    }


# ---------------------------------------------------------------- 6. product tracker (for a supplier or a brand)
def products(c, p):
    limit = 3000
    codes = parse_codes(p.get("codes"))
    prefix = re.sub(r"\D", "", p.get("prefix") or "")
    words = [w for w in re.findall(r"[\w\"'׳״%.-]+", p.get("q") or "") if len(w) >= 2][:6]
    keys = []
    if codes:
        known = {}
        for part in chunks(codes + [x.lstrip("0") for x in codes]):
            for (k,) in c.execute(f"SELECT key FROM products WHERE key IN ({','.join('?' * len(part))})", part):
                known[k.lstrip("0")] = k
        keys = [known[x.lstrip("0")] for x in codes if x.lstrip("0") in known]
    elif len(prefix) >= 3:
        keys = [k for (k,) in c.execute("""SELECT key FROM products WHERE key >= ? AND key < ? AND scope='global'
                                          AND store_count > 0 ORDER BY store_count DESC LIMIT ?""",
                                       (prefix, prefix + ":", limit))]
    elif words:
        q = " AND ".join('"' + w.replace('"', '""') + '"*' for w in words)
        keys = [k for (k,) in c.execute("""SELECT f.key FROM products_fts f JOIN products p ON p.key=f.key
                                          WHERE products_fts MATCH ? AND p.scope='global' AND p.store_count > 0
                                          ORDER BY p.store_count DESC LIMIT ?""", ("name : (" + q + ")", limit))]
    keys = [k for k in dict.fromkeys(keys) if comparable(k)]
    now = dict(by_product(c, set(keys)))
    coverage = Counter(n for g in now.values() for n in g)
    chains = [n for n, _ in coverage.most_common(14)]
    lab = labels(c)
    on_deal = defaultdict(set)
    for part in chunks(keys):
        for k, cid in c.execute(f"SELECT DISTINCT key, chain_id FROM deals WHERE key IN ({','.join('?' * len(part))})", part):
            on_deal[k].add(label_of(lab, cid))
    info = {}
    for part in chunks(keys):
        for k, name, q_, u, sc, cc, med in c.execute(f"""SELECT key, name, quantity, unit, store_count, chain_count, median
                                                          FROM products WHERE key IN ({','.join('?' * len(part))})""", part):
            info[k] = (name, catalog.size_of(name or "", q_, u), sc, cc, med)
    rows = []
    for k in keys:
        name, size, sc, cc, med = info.get(k, (k, None, None, None, None))
        g = now.get(k, {})
        rows.append({"product": name or k, "code": k, "size": size or "", "chains": len(g), "stores": sc,
                     "low": min(g.values()) if g else None, "typical": med, "high": max(g.values()) if g else None,
                     "deals": len(on_deal.get(k, ())), **{n: g.get(n) for n in chains}})
    return {
        "title": "Product tracker",
        "method": ("For each product: each chain's cheapest confirmed shelf price now, the number of current "
                   "branches selling it, its typical (median) price, and how many chains have a confirmed deal on it."),
        "params": {"codes": " ".join(codes), "prefix": prefix, "q": " ".join(words)},
        "columns": [col("product", "Product", "he"), col("code", "Barcode", "code"), col("size", "Size"),
                    col("chains", "Chains", "int"), col("stores", "Branches", "int"), col("low", "Lowest", "money"),
                    col("typical", "Typical", "money"), col("high", "Highest", "money"),
                    col("deals", "Chains with a deal", "int")] + [col(n, n, "money") for n in chains],
        "rows": rows,
        "summary": [["Products", len(rows), "int"]] + ([["Limit", f"first {limit}", "text"]] if len(rows) >= limit else []),
    }


# ---------------------------------------------------------------- what we hold
def holdings(c):
    t = today()
    cutoff = trust.fresh_cutoff()
    lab = labels(c)
    chains = {label_of(lab, cid) for (cid,) in c.execute("SELECT DISTINCT chain_id FROM price_last")}
    r = c.execute(f"""SELECT (SELECT count(*) FROM price_last WHERE instr(key, ':') = 0),
                             (SELECT count(*) FROM products WHERE scope='global' AND chain_count >= 3 AND store_count > 0),
                             (SELECT count(*) FROM stores WHERE last_price_ts >= ?),
                             (SELECT count(*) FROM price_history),
                             (SELECT count(DISTINCT day) FROM price_history)""", (cutoff,)).fetchone()
    products = c.execute("SELECT count(*) FROM (SELECT DISTINCT key FROM price_last WHERE instr(key, ':') = 0)").fetchone()[0]
    deals_n = c.execute("SELECT count(DISTINCT key) FROM deals").fetchone()[0] \
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='deals'").fetchone() else 0
    since = int(get_setting(c, "history_since", t) or t)
    return {"chains": len(chains), "stores": r[2], "products": products, "prices": r[0], "multi": r[1],
            "history_rows": r[3], "history_days": r[4], "history_since": since, "deals": deals_n}


# ---------------------------------------------------------------- running, caching, CSV
REPORTS = {"price-level": price_level, "basket": basket, "changes": changes, "deals": deals, "gaps": gaps,
           "products": products}
PARAMS = {"price-level": ("min_chains",), "basket": ("size", "period", "codes"), "changes": ("period", "cat"),
          "deals": (), "gaps": ("mode", "cat", "min_chains"), "products": ("codes", "prefix", "q")}
_cache, _lock = {}, threading.Lock()


def run(c, name, params):
    """A report by name (KeyError if there is none), cached for CACHE_SECONDS per data version and parameters."""
    fn = REPORTS[name]
    p = {k: str(params.get(k) or "")[:20000] for k in PARAMS[name]}
    stamp = (get_setting(c, "confirmed_through", None), get_setting(c, "deals_version", None), today())
    key = (name, tuple(sorted(p.items())))
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == stamp and time.time() - hit[1] < CACHE_SECONDS:
            return hit[2]
    t = time.time()
    rep = fn(c, p)
    rep.update(name=name, generated=int(time.time()), seconds=round(time.time() - t, 1), source=SOURCE,
               data_through=get_setting(c, "confirmed_through", None))
    rep.setdefault("parts", {})
    rep.setdefault("summary", [])
    with _lock:
        if len(_cache) > 40:
            _cache.clear()
        _cache[key] = (stamp, time.time(), rep)
    return rep


def _cell(v, kind):
    if v is None:
        return ""
    if kind == "money":
        return f"{v:.2f}"
    if kind == "pct":
        return f"{v * 100:.1f}"
    if kind == "index":
        return f"{v:.1f}"
    if kind == "day":
        s = str(v)
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return str(v)


def csv_text(rep, part=""):
    """The report (or one of its parts) as CSV, with a BOM so Excel reads the Hebrew. Percent columns are in %."""
    t = rep["parts"].get(part) if part else None
    cols, rows = (t["columns"], t["rows"]) if t else (rep["columns"], rep["rows"])
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([cl["label"] + (" (%)" if cl["kind"] == "pct" else " (₪)" if cl["kind"] == "money" else "") for cl in cols])
    for r in rows:
        w.writerow([_cell(r.get(cl["key"]), cl["kind"]) for cl in cols])
    return "﻿" + out.getvalue()
