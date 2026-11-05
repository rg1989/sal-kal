"""Basket comparison: price a list of products at every store and rank chains on confirmed prices only.

A line counts toward a store's total only when the store's prices are current, the product is the same one (a
manufacturer barcode the chain names like the other chains do), the price isn't far off the same product elsewhere
and has been checked, and any deal applied is one whose terms are confirmed (app.deal_terms) from a current deal
file. Everything else is returned with its status and reason and never counted. Produce, deli and store codes differ
per chain, so they are never compared: each store shows its own item instead.
"""
import json
import re
import sqlite3
import time
from collections import defaultdict

from . import commute, deal_terms, geo, trust
from .chains import DELIVERY_DEFAULTS, ONLINE_HINTS, chain_name

try:
    from .indexer import is_global_code, normalize_name
except ImportError:  # the indexer needs lxml; these two don't
    def is_global_code(code):
        if not code or not code.isdigit() or not 8 <= len(code) <= 14 or len(code.lstrip("0")) < 8:
            return False
        if len(code) == 13 and code[0] == "2":
            return False
        return trust.gs1_ok(code)

    def normalize_name(name):
        s = re.sub(r"[^\w\s]", " ", name or "")
        s = re.sub(r"\d+", " ", s)
        return " ".join(s.split()).strip().lower()

DEFAULT_PREFS = {"club": False, "mode": "store", "home": None, "max_km": None}
SHARED_PRODUCE = re.compile(r"^729000000\d{4}$")  # chains reuse these codes for their own produce and bags


def comparable(key):
    """A cart key is compared across chains only when it is a manufacturer barcode. Bare codes stored before the
    barcode rules got stricter can fail this: they are treated like produce."""
    return bool(key) and ":" not in key and not SHARED_PRODUCE.match(key) and is_global_code(key)


# ---------------------------------------------------------------- settings
def get_setting(con, key, default):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except ValueError:
        return default


def put_setting(con, key, value):
    con.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)))
    con.commit()


def get_prefs(con):
    p = dict(DEFAULT_PREFS)
    p.update(get_setting(con, "prefs", {}))
    return p


def delivery_table(con, overrides=None):
    """Defaults merged with an account's overrides (the household's stored setting when none are given), by chain id."""
    out = {k: dict(v) for k, v in DELIVERY_DEFAULTS.items()}
    for k, v in (get_setting(con, "delivery", {}) if overrides is None else overrides).items():
        out.setdefault(k, {}).update(v)
        out[k]["custom"] = True
    return out


def delivery_cost(rule, subtotal):
    if rule and rule.get("delivers") is False:
        return None, "No home delivery"
    if not rule or rule.get("fee") is None:
        return None, "Delivery fee not set"
    note = None
    fee = float(rule["fee"])
    if rule.get("free_above") and subtotal >= float(rule["free_above"]):
        fee = 0.0
    elif rule.get("reduced_above") and subtotal >= float(rule["reduced_above"]) and rule.get("reduced_fee") is not None:
        fee = float(rule["reduced_fee"])
    if rule.get("min_order") and subtotal < float(rule["min_order"]):
        note = f"Below the ₪{float(rule['min_order']):.0f} minimum order"
    return fee, note


# ---------------------------------------------------------------- pricing
def _terms(pr, price, club, memo=None):
    """Confirmed terms of one promotion row at this shelf price (memoized: chains repeat a deal at every branch)."""
    k = (pr.get("description"), pr.get("min_qty"), pr.get("discounted_price"), pr.get("discount_rate"),
         pr.get("remarks"), club, price)
    if memo is not None and k in memo:
        return memo[k]
    t = trust.promo_terms(pr.get("description"), pr.get("min_qty"), pr.get("discounted_price"),
                          pr.get("discount_rate"), pr.get("remarks") or "", club=club, price=price)
    if memo is not None:
        memo[k] = t
    return t


def deal_units(terms):
    """How many units a confirmed deal needs: a bundle's size, else one."""
    if not terms:
        return 1
    return {"bundle": lambda t: t[1], "free": lambda t: t[1] + t[2], "second": lambda t: 2}.get(terms[0], lambda t: 1)(terms)


def deal_cap(pr, memo=None):
    """Units one shopper may buy at the deal price: the file's limit or one stated in the text, whichever is less."""
    desc = pr.get("description") or ""
    k = ("cap", desc)
    if memo is not None and k in memo:
        said = memo[k]
    else:
        text_limit = getattr(deal_terms, "text_limit", None)
        said = text_limit(desc) if text_limit else None
        if memo is not None:
            memo[k] = said
    caps = [float(c) for c in (pr.get("max_qty"), said) if c and float(c) > 0]
    return min(caps) if caps else None


def price_line(price, qty, promos, club=False, memo=None, deals_ok=True):
    """Cheapest way to buy `qty` units with the store's confirmed deals.
    Returns (cost, deal used or None, descriptions of deals there that can't be confirmed (or all of them when the
    store's deal file is out of date, deals_ok=False), confirmed deals the quantity doesn't reach as
    {description, units, total})."""
    best, best_promo = price * qty, None
    not_counted, more, seen = [], [], set()
    for pr in promos:
        d = (pr.get("description") or "").strip()
        terms = None if not deals_ok or pr.get("blocked") else _terms(pr, price, club, memo)
        if terms is None:
            if d and d not in seen:
                seen.add(d)
                not_counted.append(d)
            continue
        cap = deal_cap(pr, memo)
        cost = trust.cost_with(price, qty, terms, cap)
        if cost is not None:
            if cost < best - 0.005:
                best, best_promo = cost, pr
            continue
        units = deal_units(terms)
        if units > qty + 1e-9 and d and d not in seen:  # e.g. "2 for 55" while buying one
            total = trust.cost_with(price, units, terms, cap)
            if total is not None:
                seen.add(d)
                more.append({"description": d, "units": units, "total": round(total, 2)})
    return best, best_promo, not_counted[:3], more[:3]


def line_cost(price, qty, promos, club=False):
    """(cost, deal used or None) for `qty` units at shelf price `price` with confirmed deals only."""
    cost, promo, _, _ = price_line(price, qty, promos, club)
    return cost, promo


def _is_online(store):
    if store["store_type"] in (2, 3):
        return True
    name = (store["name"] or "") + " " + (store["sub_chain"] or "")
    return any(h in name for h in ONLINE_HINTS)


def _is_physical(store):
    return store["store_type"] in (None, 1, 3) and not (store["store_type"] is None and _is_online(store))


def _online_rank(store):
    """0 = the chain's web shop (StoreType 2, or named as one), 1 = a branch that also takes online orders."""
    if store["store_type"] == 2:
        return 0
    name = (store["name"] or "") + " " + (store["sub_chain"] or "")
    return 0 if any(h in name for h in ONLINE_HINTS) else 1


def _rows(con, sql, args=()):
    """Rows of a table the indexer adds (empty when an old database doesn't have it yet)."""
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        if "no such" in str(e):
            return []
        raise


def _columns(con, table):
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def _code(key):
    return key.split(":", 1)[1] if ":" in key else key


def _same_item(a, b):
    """Two names of one code at different chains say the same product."""
    return bool(a and b and trust.names_agree(trust.name_words(a), trust.name_words(b)))


def ph(seq):
    return ",".join("?" * len(seq))


def compare(con, items, prefs=None, now_int=None, overrides=None, way=None):
    """way: on my way (commute.corridor) = {(chain, store): (km off the route, km along it)} — the branches along the
    route home -> work are priced instead of those within max_km of home, and the result gets the stop plans."""
    prefs = {**DEFAULT_PREFS, **(prefs or {})}
    items = [i for i in items if i.get("key") and float(i.get("qty") or 0) > 0]
    now_int = now_int or int(time.strftime("%Y%m%d%H%M"))
    if not items:
        return {"chains": [], "branches": [], "stale": [], "n": 0, "items": [], "prefs": prefs, "data_ts": 0,
                "way": {"plans": []} if way is not None else None, "generated": int(time.time())}
    keys = list(dict.fromkeys(i["key"] for i in items))
    qty = {i["key"]: float(i["qty"]) for i in items}
    club = bool(prefs.get("club"))
    delivery_mode = prefs.get("mode") == "delivery"
    cutoff = trust.fresh_cutoff()
    # prices newer than the indexer's last check haven't been checked (missing setting: every row has been)
    checked_to = int(get_setting(con, "confirmed_through", 0) or 0)

    products = {r["key"]: dict(r) for r in con.execute(f"SELECT * FROM products WHERE key IN ({ph(keys)})", keys)}
    compared = [k for k in keys if comparable(k)]
    cset = set(compared)
    produce = [k for k in keys if k not in cset]
    n = len(compared)

    stores = {(r["chain_id"], r["store_id"]): dict(r) for r in con.execute(
        "SELECT chain_id, store_id, name, address, city, store_type, sub_chain, last_price_ts, promo_full_ts FROM stores")}
    chain_ids = sorted({sk[0] for sk in stores})

    # produce / in-store codes differ per chain: each store shows its own item, or same-name items, never compared
    own = {}  # cart key -> {chain: key of that chain's own item}
    for k in produce:
        if ":" in k:
            own[k] = {k.split(":", 1)[0]: k}
        else:  # a bare code from before the stricter barcode rules: each chain's own item under this code
            own[k] = {cid: f"{cid}:{k}" for cid in chain_ids}
    own_keys = list(dict.fromkeys(v for d in own.values() for v in d.values() if v not in qty))
    for i in range(0, len(own_keys), 500):
        part = own_keys[i:i + 500]
        for r in con.execute(f"SELECT * FROM products WHERE key IN ({ph(part)})", part):
            products[r["key"]] = dict(r)
    for k in produce:
        if ":" not in k:  # only where that chain's own name says the same product
            card = (products.get(k) or {}).get("name")
            own[k] = {cid: v for cid, v in own[k].items() if v in products and _same_item(card, products[v].get("name"))}
    own_keys = [v for d in own.values() for v in d.values() if v not in qty]
    aliases = {}
    for k in produce:
        p = products.get(k) or {}
        norm = p.get("norm") or normalize_name(p.get("name") or "")
        mine = set(own[k].values()) | {k}
        aliases[k] = [r[0] for r in con.execute(
            "SELECT key FROM products WHERE scope='chain' AND norm=? AND store_count>0", (norm,))
            if r[0] not in mine] if norm and len(norm) >= 2 else []
    alias_keys = list(dict.fromkeys(a for v in aliases.values() for a in v if a not in qty and a not in products))
    for i in range(0, len(alias_keys), 500):
        part = alias_keys[i:i + 500]
        for r in con.execute(f"SELECT key, name FROM products WHERE key IN ({ph(part)})", part):
            products.setdefault(r[0], {"key": r[0], "name": r[1]})
    lookup = list(dict.fromkeys(compared + [k for k in produce if ":" in k] + own_keys
                                + [a for v in aliases.values() for a in v]))

    # where you are: physical branches within your distance, and per chain the online store that serves you
    home = prefs.get("home") or None
    max_km = float(prefs["max_km"]) if home and prefs.get("max_km") else None
    coords = geo.store_coords(con) if home else {}
    loc = {}
    for sk in stores:
        if sk in coords:
            lat, lon = coords[sk]
            loc[sk] = (round(geo.km(home["lat"], home["lon"], lat, lon), 1), lat, lon)
        else:
            loc[sk] = (None, None, None)

    def in_range(dist):  # stores not located yet can't be shown to be within range, so a distance cap hides them
        return max_km is None or (dist is not None and dist <= max_km)

    def near(sk):  # on my way: along the route; otherwise within your distance of home
        return sk in way if way is not None else in_range(loc[sk][0])

    delivery = delivery_table(con, overrides)
    physical = {sk for sk, st in stores.items() if _is_physical(st) and near(sk)}
    online_by_chain = defaultdict(list)
    for sk, st in stores.items():
        if _is_online(st) and (st["last_price_ts"] or 0) > 0:
            online_by_chain[sk[0]].append(sk)
    online_pick, online_stale = {}, {}  # chain -> fresh candidate stores / the out-of-date store that serves you
    for cid, sks in online_by_chain.items():
        def rank(sk):
            d = loc[sk][0]
            return (_online_rank(stores[sk]), d is None, d or 0.0)
        sks.sort(key=rank)
        top = sks[0]
        # with a home: the nearest web shop (stores without coordinates last); without one, any of them
        group = [top] if loc[top][0] is not None else [sk for sk in sks if rank(sk)[:2] == rank(top)[:2]]
        fresh_group = [sk for sk in group if (stores[sk]["last_price_ts"] or 0) >= cutoff]
        if fresh_group:
            online_pick[cid] = fresh_group
        else:
            online_stale[cid] = max(group, key=lambda sk: stores[sk]["last_price_ts"] or 0)
    online_set = {sk for v in online_pick.values() for sk in v}
    want = physical | online_set

    store_filter, args = "", list(lookup)
    if 2 * len(want) < len(stores):  # a distance limit: read only those stores (with every store it costs more)
        store_filter = f" AND (p.chain_id || '/' || p.store_id) IN ({ph(want)})"
        args += [f"{c}/{s}" for c, s in want]
    prices = defaultdict(dict)  # (chain, store) -> key -> price
    new = set()                 # (store, key) priced after the last check
    if lookup:
        for key, cid, sid, price, ts in con.execute(f"""
            SELECT p.key, p.chain_id, p.store_id, p.price, p.ts
            FROM prices p JOIN stores s ON s.chain_id=p.chain_id AND s.store_id=p.store_id
            WHERE p.key IN ({ph(lookup)}) AND p.ts >= s.price_full_ts AND p.price > 0{store_filter}""", args):
            sk = (cid, sid)
            if sk not in want:
                continue
            prices[sk][key] = price
            if checked_to and (ts or 0) > checked_to:
                new.add((sk, key))
    fresh = {sk for sk in prices if (stores[sk]["last_price_ts"] or 0) >= cutoff}

    price_flags = {(r[0], r[1], r[2]): r[3] for r in _rows(
        con, f"SELECT key, chain_id, store_id, median FROM price_flags WHERE key IN ({ph(lookup)})", lookup)} \
        if lookup else {}
    name_flags, shelf = {}, {}
    if compared:
        name_flags = {(r[0], r[1]): r[2] for r in _rows(
            con, f"SELECT key, chain_id, name FROM name_flags WHERE key IN ({ph(compared)})", compared)}
        has_ts = "ts" in _columns(con, "item_names")
        newest = {}
        # only names seen recently: a chain's old name for a barcode no longer confirms it (trust.NAMES_DAYS)
        recent = f"AND ts >= {int(trust.names_cutoff())}" if has_ts else ""
        for r in _rows(con, f"SELECT key, chain_id, name, {'ts' if has_ts else '0'} FROM item_names "
                            f"WHERE key IN ({ph(compared)}) {recent}", compared):
            if (r[0], r[1]) not in newest or (r[3] or 0) > newest[(r[0], r[1])]:
                newest[(r[0], r[1])] = r[3] or 0
                shelf[(r[0], r[1])] = r[2]

    promos = defaultdict(lambda: defaultdict(list))
    priced = fresh  # every store priced below
    if compared and priced:
        club_clause = "" if club else "AND pr.club_only=0"
        # staff discounts ("הנחת עובד") are in the files but aren't open to shoppers
        club_clause += " AND coalesce(pr.description, '') NOT LIKE '%עובד%'"
        if not club:  # some chains mark club deals only in the text ("מבצע מועדון")
            club_clause += " AND coalesce(pr.description, '') NOT LIKE '%מועדון%'"
        pf, pargs = "", []
        if 2 * len(priced) < len(stores):
            pf = f" AND (pi.chain_id || '/' || pi.store_id) IN ({ph(priced)})"
            pargs = [f"{c}/{s}" for c, s in priced]
        for r in con.execute(f"""
            SELECT pi.key, pr.* FROM promo_items pi
            JOIN promos pr ON pr.chain_id=pi.chain_id AND pr.store_id=pi.store_id AND pr.promo_id=pi.promo_id
            JOIN stores s ON s.chain_id=pi.chain_id AND s.store_id=pi.store_id
            WHERE pi.key IN ({ph(compared)}) AND pi.ts >= s.promo_full_ts AND pr.ts >= s.promo_full_ts
              AND (pr.end_ts IS NULL OR pr.end_ts >= ?) AND (pr.start_ts IS NULL OR pr.start_ts <= ?) {club_clause}{pf}""",
                [*compared, now_int, now_int, *pargs]):
            pr = dict(r)
            if pr.get("club_only"):  # a club member gets the store's own club (ClubId 1), not card or other clubs
                ids = [x.strip() for x in str(pr.get("club_ids") or "").split(",") if x.strip()]
                pr["blocked"] = not ids or any(x not in ("0", "1") for x in ids)
            promos[(r["chain_id"], r["store_id"])][r["key"]].append(pr)
    memo = {}

    def shelf_name(k, chain):
        own_name = (name_flags.get((k, chain)) or shelf.get((k, chain)) or "").strip()
        card = ((products.get(k) or {}).get("name") or "").strip()
        return own_name if own_name and own_name != card else None

    def price_store(sk):
        chain, store = sk
        deals_ok = (stores[sk]["promo_full_ts"] or 0) >= cutoff
        lines, total, covered, missing = [], 0.0, 0, []
        def trusted(key):
            if (sk, key) in new:  # priced after the last full check: within range of its typical price, or not yet
                return _within(prices[sk][key], (products.get(key) or {}).get("median"))
            return (key, chain, store) not in price_flags
        for k in keys:
            q = qty[k]
            if k not in cset:
                ok_key = own[k].get(chain)
                p = prices[sk].get(ok_key) if ok_key else None
                ok_own = p is not None and trusted(ok_key)
                line = {"key": k, "qty": q, "status": "not_compared", "own": ok_own, "similar": []}
                if ok_own:
                    line.update(price=p, full=round(p * q, 2), code=_code(ok_key),
                                shelf_name=(products.get(ok_key) or {}).get("name"))
                    if ok_key != k:
                        line["own_key"] = ok_key
                else:
                    sim = sorted((prices[sk][a], a) for a in aliases[k] if a in prices[sk] and trusted(a))
                    line["similar"] = [{"key": a, "name": (products.get(a) or {}).get("name"), "price": pr}
                                       for pr, a in sim[:3]]
                lines.append(line)
                continue
            p = prices[sk].get(k)
            if p is None:
                missing.append(k)
                lines.append({"key": k, "qty": q, "status": "missing"})
                continue
            line = {"key": k, "qty": q, "price": p, "full": round(p * q, 2), "code": _code(k),
                    "shelf_name": shelf_name(k, chain)}
            if (k, chain) in name_flags:
                line.update(status="unconfirmed", reason="name")
            elif (k, chain) not in shelf:
                line.update(status="unconfirmed", reason="noname")
            elif (sk, k) in new and not _within(p, (products.get(k) or {}).get("median")):
                line.update(status="unconfirmed", reason="new")
            elif (sk, k) not in new and (k, chain, store) in price_flags:
                line.update(status="unconfirmed", reason="price", median=price_flags[(k, chain, store)])
            else:
                cost, promo, not_counted, more = price_line(p, q, promos[sk].get(k, []), club, memo, deals_ok)
                cost = round(cost, 2)
                total += cost
                covered += 1
                line.update(status="ok", cost=cost, deals_not_counted=not_counted, deals_more=more,
                            promo={"description": promo.get("description"), "club": bool(promo.get("club_only")),
                                   "ends": promo.get("end_ts")} if promo else None)
            if line["status"] != "ok":
                missing.append(k)
            lines.append(line)
        return {"total": round(total, 2), "covered": covered, "missing": missing, "complete": n > 0 and covered == n,
                "lines": lines}

    names = {r["chain_id"]: r["name_he"] for r in con.execute("SELECT chain_id, name_he FROM chains")}

    def order(e):
        # the whole cart, cheapest first; partial carts by products counted, then nearest (their totals are over
        # different products, so they aren't compared on price)
        d = e["distance_km"] if e["distance_km"] is not None else 1e9
        tie = (chain_name(e["chain_id"], names.get(e["chain_id"])), e["store_id"])
        return (0, 0, e["total"], d, tie) if e["complete"] else (1, -e["covered"], d, 0, tie)

    by_chain = defaultdict(lambda: {"store": [], "online": []})
    stale_near = {}
    for sk in prices:
        st = stores[sk]
        dist, lat, lon = loc[sk]
        if way is not None and sk in way:
            dist = round(way[sk][0], 1)
        if sk not in fresh:
            if sk in physical:
                cur = stale_near.get(st["chain_id"])
                if cur is None or (dist is not None and (cur["distance_km"] is None or dist < cur["distance_km"])):
                    stale_near[st["chain_id"]] = {"chain_id": st["chain_id"], "store_id": st["store_id"],
                                                  "name": st["name"], "city": st["city"], "distance_km": dist,
                                                  "last_price_ts": st["last_price_ts"]}
            continue
        entry = {**st, **price_store(sk), "distance_km": dist, "lat": lat, "lon": lon}
        if way is not None and sk in way:  # on my way, a branch's distance is how far off the route it is
            entry.update(distance_km=round(way[sk][0], 1), off_km=way[sk][0], at_km=way[sk][1])
        if sk in online_set:
            by_chain[st["chain_id"]]["online"].append(entry)
        if sk in physical:
            by_chain[st["chain_id"]]["store"].append(entry)

    chains, no_area = [], []
    for cid, groups in by_chain.items():
        name = chain_name(cid, names.get(cid))
        store_opts = sorted(groups["store"], key=order)
        online_opts = sorted(groups["online"], key=order)
        rule = delivery.get(cid)
        # the price files don't say where a chain delivers: only the user can confirm it delivers to them
        area_ok = bool((rule or {}).get("delivers_here"))
        if area_ok and (rule or {}).get("delivers") is False:
            rule = {**rule, "delivers": None}
        online = None
        if online_opts:
            o = online_opts[0]
            fee, note = delivery_cost(rule, o["total"])
            online = {**_slim(o), "lines": o["lines"], "fee": fee, "fee_note": note, "area_confirmed": area_ok,
                      "delivered": round(o["total"] + fee, 2) if fee is not None else None, "price_vs_store": None}
        in_store = None
        if store_opts:
            s = store_opts[0]
            in_store = {**_slim(s), "lines": s["lines"],
                        "branches": [_slim(b) for b in store_opts[:8]], "branch_count": len(store_opts)}
        if online and in_store and online["missing"] == in_store["missing"]:  # same products counted at both
            online["price_vs_store"] = round(online["total"] - in_store["total"], 2)
        if delivery_mode:
            if not online or (rule or {}).get("delivers") is False:
                continue
            if not area_ok:  # shown apart, never ranked
                no_area.append({"chain_id": cid, "chain": name, "store_id": online["store_id"], "name": online["name"],
                                "total": online["total"], "covered": online["covered"],
                                "complete": online["complete"], "last_price_ts": online["last_price_ts"]})
                continue
            view, score = online, online["delivered"] if online["delivered"] is not None else online["total"]
        else:
            if not in_store:
                continue
            view, score = in_store, in_store["total"]
        d = view["distance_km"] if view["distance_km"] is not None else 1e9
        if view["complete"]:
            sort = (0, view.get("delivered") is None if delivery_mode else 0, score, d, name)
        else:
            sort = (1, -view["covered"], d, 0, name)
        entry = {"chain_id": cid, "name": name, "name_he": names.get(cid), "score": round(score, 2),
                 "covered": view["covered"], "complete": view["complete"], "in_store": in_store, "online": online,
                 "delivery_rule": rule, "_sort": sort}
        if cid in online_stale:  # its web shop's prices are out of date: no delivery price
            st = online_stale[cid]
            entry["online_stale"] = {"store_id": st[1], "name": stores[st]["name"],
                                     "last_price_ts": stores[st]["last_price_ts"]}
        chains.append(entry)
    chains.sort(key=lambda c: c.pop("_sort"))
    # multiple chain ids can share a brand (e.g. Victory) — keep the best per brand name
    seen, ranked = set(), []
    for c in chains:
        if c["name"] in seen:
            continue
        seen.add(c["name"])
        ranked.append(c)

    # out-of-date prices are shown apart, never ranked: nearby branches in store mode, the web shop for delivery
    stale = []
    if delivery_mode:
        for cid, sk in online_stale.items():
            if (delivery.get(cid) or {}).get("delivers") is False:
                continue
            st = stores[sk]
            stale.append({"chain_id": cid, "store_id": st["store_id"], "name": st["name"], "city": st["city"],
                          "distance_km": loc[sk][0], "last_price_ts": st["last_price_ts"], "online": True,
                          "chain": chain_name(cid, names.get(cid))})
    else:
        stale = [{**s, "chain": chain_name(cid, names.get(cid))} for cid, s in stale_near.items()]
    stale.sort(key=lambda s: (s["distance_km"] if s["distance_km"] is not None else 1e9, -(s["last_price_ts"] or 0)))
    kept = []
    for s in stale:
        if s["chain"] in seen or any(x["chain"] == s["chain"] for x in kept):
            continue
        kept.append(s)
    stale = kept

    # each product's confirmed price range at current stores anywhere (not only those priced above)
    ranges = {}
    if compared:
        ranges.update({r[0]: r[1:] for r in _rows(con, f"""
            SELECT p.key, min(p.price), max(p.price), count(*) FROM prices p
            JOIN stores s ON s.chain_id=p.chain_id AND s.store_id=p.store_id
            WHERE p.key IN ({ph(compared)}) AND p.ts >= s.price_full_ts AND s.last_price_ts >= ? AND p.price > 0
              AND (? = 0 OR p.ts <= ?)
              AND NOT EXISTS (SELECT 1 FROM price_flags f WHERE f.key=p.key AND f.chain_id=p.chain_id
                              AND f.store_id=p.store_id)
              AND NOT EXISTS (SELECT 1 FROM name_flags f WHERE f.key=p.key AND f.chain_id=p.chain_id)
              AND EXISTS (SELECT 1 FROM item_names f WHERE f.key=p.key AND f.chain_id=p.chain_id
                          AND f.ts >= {int(trust.names_cutoff())})
            GROUP BY p.key""", [*compared, cutoff, checked_to, checked_to])})
    chain_keys = [k for k in produce if ":" in k]
    if chain_keys:
        ranges.update({r[0]: r[1:] for r in _rows(con, f"""
            SELECT p.key, min(p.price), max(p.price), count(*) FROM prices p
            JOIN stores s ON s.chain_id=p.chain_id AND s.store_id=p.store_id
            WHERE p.key IN ({ph(chain_keys)}) AND p.ts >= s.price_full_ts AND s.last_price_ts >= ? AND p.price > 0
              AND (? = 0 OR p.ts <= ?)
              AND NOT EXISTS (SELECT 1 FROM price_flags f WHERE f.key=p.key AND f.chain_id=p.chain_id
                              AND f.store_id=p.store_id)
            GROUP BY p.key""", [*chain_keys, cutoff, checked_to, checked_to])})
    item_info = []
    for k in keys:
        p = products.get(k, {})
        # a bare code that isn't a barcode: its items differ per chain, so there's no one range to give
        mn, mx, cnt = ranges.get(k, (None, None, 0 if k in cset or ":" in k else None))
        item_info.append({"key": k, "qty": qty[k], "name": p.get("name") or k, "manufacturer": p.get("manufacturer"),
                          "is_weighted": bool(p.get("is_weighted")), "scope": "global" if k in cset else "chain",
                          "compared": k in cset, "min": mn, "max": mx, "stores": cnt})
    data_ts = max((stores[sk]["last_price_ts"] or 0 for sk in fresh), default=0)
    # every branch you'd walk into, each priced for the whole cart, in the same order as the chains
    every = sorted((e for g in by_chain.values() for e in g["store"] if not n or e["covered"]), key=order)
    branches = [{"chain_id": e["chain_id"], "chain": chain_name(e["chain_id"], names.get(e["chain_id"])),
                 "store_id": e["store_id"], "name": e["name"], "city": e["city"], "distance_km": e["distance_km"],
                 "lat": e["lat"], "lon": e["lon"], "total": e["total"], "covered": e["covered"],
                 "missing": len(e["missing"]), "complete": e["complete"], "last_price_ts": e["last_price_ts"],
                 **({"off_km": e.get("off_km"), "at_km": e.get("at_km")} if way is not None else {})}
                for e in every]
    # on my way: the cart spread over 1..5 branches along the route, each product where it's cheapest (app.commute)
    plans = None
    if way is not None:
        plans = [] if delivery_mode else commute.plans(
            [{**e, "chain": chain_name(e["chain_id"], names.get(e["chain_id"]))} for e in every], compared)
    seen_area, no_area_kept = set(), []
    for a in sorted(no_area, key=lambda a: a["chain"]):
        if a["chain"] not in seen_area and a["chain"] not in seen:
            seen_area.add(a["chain"])
            no_area_kept.append(a)
    return {"chains": ranked, "branches": branches, "stale": stale, "n": n, "items": item_info, "prefs": prefs,
            "no_area": no_area_kept, "way": {"plans": plans} if way is not None else None,
            "data_ts": data_ts, "generated": int(time.time())}


def _within(price, median):
    """A new shelf price counts before the next full check when it is in line with the product's typical price from
    the last one (the same test as price_flags). No typical price yet (a new product, or chains that disagree): it waits."""
    lo, hi = trust.OUTLIER
    return bool(median) and median > 0 and median * lo <= price <= median * hi


def _slim(e):
    out = {k: e.get(k) for k in ("chain_id", "store_id", "name", "address", "city", "store_type", "total",
                                 "covered", "missing", "complete", "last_price_ts", "distance_km", "lat", "lon")}
    if "at_km" in e:  # on my way
        out.update(off_km=e["off_km"], at_km=e["at_km"])
    return out


# ---------------------------------------------------------------- carts
def cart_items(con, cart_id):
    return [dict(r) for r in con.execute(
        "SELECT key, qty, name FROM cart_items WHERE cart_id=? ORDER BY position, rowid", (cart_id,))]
