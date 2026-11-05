"""Basket comparison: price a list of products at every store and rank chains on confirmed prices only.

A line counts toward a store's total only when the store's prices are current, the product is the same one (a
barcode the chain names like the other chains do), the price isn't far off the same product elsewhere, and any deal
applied is one whose terms are confirmed (app.trust). Everything else is returned with its status and never counted.
Produce, deli and store codes differ per chain, so they are never compared: each store shows its own item instead.
"""
import json
import sqlite3
import time
from collections import defaultdict

from . import geo, trust
from .chains import DELIVERY_DEFAULTS, ONLINE_HINTS, chain_name

DEFAULT_PREFS = {"club": False, "mode": "store", "home": None, "max_km": None}


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


def delivery_table(con):
    """Defaults merged with the user's overrides, keyed by chain id."""
    out = {k: dict(v) for k, v in DELIVERY_DEFAULTS.items()}
    for k, v in get_setting(con, "delivery", {}).items():
        out.setdefault(k, {}).update(v)
        out[k]["custom"] = True
    return out


def delivery_cost(rule, subtotal):
    if not rule or rule.get("fee") is None:
        return None, "Delivery fee not set"
    if rule.get("delivers") is False:
        return None, "No home delivery"
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


def price_line(price, qty, promos, club=False, memo=None):
    """Cheapest way to buy `qty` units with the store's confirmed deals.
    Returns (cost, deal used or None, descriptions of deals there that aren't counted)."""
    best, best_promo, skipped = price * qty, None, []
    for pr in promos:
        cost = trust.cost_with(price, qty, _terms(pr, price, club, memo), pr.get("max_qty"))
        if cost is None:
            skipped.append(pr)
        elif cost < best - 0.005:
            best, best_promo = cost, pr
    seen, not_counted = set(), []
    for pr in skipped:
        d = (pr.get("description") or "").strip()
        if d and d not in seen:
            seen.add(d)
            not_counted.append(d)
    return best, best_promo, not_counted[:3]


def line_cost(price, qty, promos, club=False):
    """(cost, deal used or None) for `qty` units at shelf price `price` with confirmed deals only."""
    cost, promo, _ = price_line(price, qty, promos, club)
    return cost, promo


def _is_online(store):
    if store["store_type"] in (2, 3):
        return True
    name = (store["name"] or "") + " " + (store["sub_chain"] or "")
    return any(h in name for h in ONLINE_HINTS)


def _is_physical(store):
    return store["store_type"] in (None, 1, 3) and not (store["store_type"] is None and _is_online(store))


def _rows(con, sql, args=()):
    """Rows of a table the indexer adds (empty when an old database doesn't have it yet)."""
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        if "no such" in str(e):
            return []
        raise


def _code(key):
    return key.split(":", 1)[1] if ":" in key else key


def compare(con, items, prefs=None, now_int=None):
    prefs = {**DEFAULT_PREFS, **(prefs or {})}
    items = [i for i in items if i.get("key") and float(i.get("qty") or 0) > 0]
    now_int = now_int or int(time.strftime("%Y%m%d%H%M"))
    if not items:
        return {"chains": [], "branches": [], "stale": [], "n": 0, "items": [], "prefs": prefs, "data_ts": 0,
                "generated": int(time.time())}
    keys = list(dict.fromkeys(i["key"] for i in items))
    qty = {i["key"]: float(i["qty"]) for i in items}
    club = bool(prefs.get("club"))
    cutoff = trust.fresh_cutoff()

    def ph(seq):
        return ",".join("?" * len(seq))

    products = {r["key"]: dict(r) for r in con.execute(f"SELECT * FROM products WHERE key IN ({ph(keys)})", keys)}
    scope = {k: (products.get(k) or {}).get("scope") or ("chain" if ":" in k else "global") for k in keys}
    compared = [k for k in keys if scope[k] == "global"]
    produce = [k for k in keys if scope[k] != "global"]
    n = len(compared)

    # produce / in-store codes differ per chain: each store's same-name items are shown next to it, never compared
    aliases = {k: [] for k in produce}
    for k in produce:
        p = products.get(k)
        if p and p.get("norm") and len(p["norm"]) >= 2:
            aliases[k] = [r[0] for r in con.execute(
                "SELECT key FROM products WHERE scope='chain' AND norm=? AND key!=? AND store_count>0", (p["norm"], k))]
    alias_keys = list(dict.fromkeys(a for v in aliases.values() for a in v if a not in qty))
    if alias_keys:
        for r in con.execute(f"SELECT key, name FROM products WHERE key IN ({ph(alias_keys)})", alias_keys):
            products.setdefault(r[0], {"key": r[0], "name": r[1]})
    lookup = keys + alias_keys

    stores = {}
    prices = defaultdict(dict)  # (chain, store) -> key -> price
    for r in con.execute(f"""
        SELECT p.key, p.chain_id, p.store_id, p.price, s.name, s.address, s.city, s.store_type,
               s.sub_chain, s.last_price_ts
        FROM prices p JOIN stores s ON s.chain_id=p.chain_id AND s.store_id=p.store_id
        WHERE p.key IN ({ph(lookup)}) AND p.ts >= s.price_full_ts AND p.price > 0""", lookup):
        sk = (r["chain_id"], r["store_id"])
        if sk not in stores:
            stores[sk] = {k: r[k] for k in ("chain_id", "store_id", "name", "address", "city",
                                            "store_type", "sub_chain", "last_price_ts")}
        prices[sk][r["key"]] = r["price"]
    fresh = {sk for sk, st in stores.items() if (st["last_price_ts"] or 0) >= cutoff}

    price_flags = {(r[0], r[1], r[2]): r[3] for r in _rows(
        con, f"SELECT key, chain_id, store_id, median FROM price_flags WHERE key IN ({ph(lookup)})", lookup)}
    name_flags, shelf = {}, {}
    if compared:
        name_flags = {(r[0], r[1]): r[2] for r in _rows(
            con, f"SELECT key, chain_id, name FROM name_flags WHERE key IN ({ph(compared)})", compared)}
        shelf = {(r[0], r[1]): r[2] for r in _rows(
            con, f"SELECT key, chain_id, name FROM item_names WHERE key IN ({ph(compared)})", compared)}

    promos = defaultdict(lambda: defaultdict(list))
    if compared:
        club_clause = "" if club else "AND pr.club_only=0"
        # staff discounts ("הנחת עובד") are in the files but aren't open to shoppers
        club_clause += " AND coalesce(pr.description, '') NOT LIKE '%עובד%'"
        if not club:  # some chains mark club deals only in the text ("מבצע מועדון")
            club_clause += " AND coalesce(pr.description, '') NOT LIKE '%מועדון%'"
        for r in con.execute(f"""
            SELECT pi.key, pr.* FROM promo_items pi
            JOIN promos pr ON pr.chain_id=pi.chain_id AND pr.store_id=pi.store_id AND pr.promo_id=pi.promo_id
            JOIN stores s ON s.chain_id=pi.chain_id AND s.store_id=pi.store_id
            WHERE pi.key IN ({ph(compared)}) AND pi.ts >= s.promo_full_ts AND pr.ts >= s.promo_full_ts
              AND (pr.end_ts IS NULL OR pr.end_ts >= ?) AND (pr.start_ts IS NULL OR pr.start_ts <= ?) {club_clause}""",
                [*compared, now_int, now_int]):
            promos[(r["chain_id"], r["store_id"])][r["key"]].append(dict(r))
    memo = {}

    def shelf_name(k, chain):
        own = (name_flags.get((k, chain)) or shelf.get((k, chain)) or "").strip()
        card = ((products.get(k) or {}).get("name") or "").strip()
        return own if own and own != card else None

    def price_store(sk):
        chain, store = sk
        lines, total, covered, missing = [], 0.0, 0, []
        for k in keys:
            q = qty[k]
            p = prices[sk].get(k)
            if scope[k] != "global":
                ok_own = p is not None and (k, chain, store) not in price_flags
                line = {"key": k, "qty": q, "status": "not_compared", "own": ok_own, "similar": []}
                if ok_own:
                    line.update(price=p, full=round(p * q, 2), code=_code(k),
                                shelf_name=(products.get(k) or {}).get("name"))
                else:
                    sim = sorted((prices[sk][a], a) for a in aliases[k]
                                 if a in prices[sk] and (a, chain, store) not in price_flags)
                    line["similar"] = [{"key": a, "name": (products.get(a) or {}).get("name"), "price": pr}
                                       for pr, a in sim[:3]]
                lines.append(line)
                continue
            if p is None:
                missing.append(k)
                lines.append({"key": k, "qty": q, "status": "missing"})
                continue
            line = {"key": k, "qty": q, "price": p, "full": round(p * q, 2), "code": _code(k),
                    "shelf_name": shelf_name(k, chain)}
            if (k, chain) in name_flags:
                line.update(status="unconfirmed", reason="name")
                missing.append(k)
            elif (k, chain, store) in price_flags:
                line.update(status="unconfirmed", reason="price", median=price_flags[(k, chain, store)])
                missing.append(k)
            else:
                cost, promo, not_counted = price_line(p, q, promos[sk].get(k, []), club, memo)
                total += cost
                covered += 1
                line.update(status="ok", cost=round(cost, 2), deals_not_counted=not_counted,
                            promo={"description": promo.get("description"), "club": bool(promo.get("club_only")),
                                   "ends": promo.get("end_ts")} if promo else None)
            lines.append(line)
        return {"total": round(total, 2), "covered": covered, "missing": missing, "complete": covered == n,
                "lines": lines}

    delivery = delivery_table(con)
    home = prefs.get("home") or None
    max_km = float(prefs["max_km"]) if home and prefs.get("max_km") else None
    coords = geo.store_coords(con) if home else {}

    def place(sk):
        if sk not in coords:
            return None, None, None
        lat, lon = coords[sk]
        return round(geo.km(home["lat"], home["lon"], lat, lon), 1), lat, lon

    def in_range(dist):  # stores not located yet can't be shown to be within range, so a distance cap hides them
        return max_km is None or (dist is not None and dist <= max_km)

    def order(e):
        # whole cart first, cheapest first; then the most products, cheapest; nearest on ties (and with nothing to compare)
        d = e["distance_km"] if e["distance_km"] is not None else 1e9
        return (not e["complete"], -e["covered"], e["total"], d)

    by_chain = defaultdict(lambda: {"store": [], "online": []})
    stale_near = {}
    for sk, st in stores.items():
        dist, lat, lon = place(sk)
        if sk not in fresh:
            if _is_physical(st) and in_range(dist):
                cur = stale_near.get(st["chain_id"])
                if cur is None or (dist is not None and (cur["distance_km"] is None or dist < cur["distance_km"])):
                    stale_near[st["chain_id"]] = {"chain_id": st["chain_id"], "store_id": st["store_id"],
                                                  "name": st["name"], "city": st["city"], "distance_km": dist,
                                                  "last_price_ts": st["last_price_ts"]}
            continue
        entry = {**st, **price_store(sk), "distance_km": dist, "lat": lat, "lon": lon}
        if _is_online(st):
            by_chain[st["chain_id"]]["online"].append(entry)
        if _is_physical(st) and in_range(dist):
            by_chain[st["chain_id"]]["store"].append(entry)

    names = {r["chain_id"]: r["name_he"] for r in con.execute("SELECT chain_id, name_he FROM chains")}
    chains = []
    for cid, groups in by_chain.items():
        store_opts = sorted(groups["store"], key=order)
        online_opts = sorted(groups["online"], key=order)
        rule = delivery.get(cid)
        online = None
        if online_opts:
            o = online_opts[0]
            fee, note = delivery_cost(rule, o["total"])
            online = {**_slim(o), "lines": o["lines"], "fee": fee, "fee_note": note,
                      "delivered": round(o["total"] + fee, 2) if fee is not None else None, "price_vs_store": None}
        in_store = None
        if store_opts:
            s = store_opts[0]
            in_store = {**_slim(s), "lines": s["lines"],
                        "branches": [_slim(b) for b in store_opts[:8]], "branch_count": len(store_opts)}
        if online and in_store and online["missing"] == in_store["missing"]:  # same products counted at both
            online["price_vs_store"] = round(online["total"] - in_store["total"], 2)
        if prefs.get("mode") == "delivery":
            if not online:
                continue
            view, score = online, online["delivered"] if online["delivered"] is not None else online["total"]
            sort = (not online["complete"], -online["covered"], online["delivered"] is None, score)
        else:
            if not in_store:
                continue
            view, score = in_store, in_store["total"]
            d = in_store["distance_km"] if in_store["distance_km"] is not None else 1e9
            sort = (not in_store["complete"], -in_store["covered"], score, d)
        chains.append({"chain_id": cid, "name": chain_name(cid, names.get(cid)), "name_he": names.get(cid),
                       "score": round(score, 2), "covered": view["covered"], "complete": view["complete"],
                       "in_store": in_store, "online": online, "delivery_rule": rule, "_sort": sort})
    chains.sort(key=lambda c: c.pop("_sort"))
    # multiple chain ids can share a brand (e.g. Victory) — keep the best per brand name
    seen, ranked = set(), []
    for c in chains:
        if c["name"] in seen:
            continue
        seen.add(c["name"])
        ranked.append(c)

    # chains whose nearby branches all have out-of-date prices: shown apart, never ranked
    stale = []
    for cid, s in stale_near.items():
        name = chain_name(cid, names.get(cid))
        if name in seen or any(x["chain"] == name for x in stale):
            continue
        stale.append({**s, "chain": name})
    stale.sort(key=lambda s: s["distance_km"] if s["distance_km"] is not None else 1e9)

    item_info = []
    for k in keys:
        p = products.get(k, {})
        if scope[k] == "global":
            ps = [prices[sk][k] for sk in fresh if k in prices[sk] and (k, sk[0]) not in name_flags
                  and (k, sk[0], sk[1]) not in price_flags]
        else:
            ps = [prices[sk][k] for sk in fresh if k in prices[sk] and (k, sk[0], sk[1]) not in price_flags]
        item_info.append({"key": k, "qty": qty[k], "name": p.get("name") or k, "manufacturer": p.get("manufacturer"),
                          "is_weighted": bool(p.get("is_weighted")), "scope": scope[k], "compared": scope[k] == "global",
                          "min": min(ps) if ps else None, "max": max(ps) if ps else None, "stores": len(ps)})
    data_ts = max((stores[sk]["last_price_ts"] or 0 for sk in fresh), default=0)
    # every branch you'd walk into, each priced for the whole cart, in the same order as the chains
    every = sorted((e for g in by_chain.values() for e in g["store"]), key=order)
    branches = [{"chain_id": e["chain_id"], "chain": chain_name(e["chain_id"], names.get(e["chain_id"])),
                 "store_id": e["store_id"], "name": e["name"], "city": e["city"], "distance_km": e["distance_km"],
                 "lat": e["lat"], "lon": e["lon"], "total": e["total"], "covered": e["covered"],
                 "missing": len(e["missing"]), "complete": e["complete"], "last_price_ts": e["last_price_ts"]}
                for e in every]
    return {"chains": ranked, "branches": branches, "stale": stale, "n": n, "items": item_info, "prefs": prefs,
            "data_ts": data_ts, "generated": int(time.time())}


def _slim(e):
    return {k: e.get(k) for k in ("chain_id", "store_id", "name", "address", "city", "store_type", "total",
                                  "covered", "missing", "complete", "last_price_ts", "distance_km", "lat", "lon")}


# ---------------------------------------------------------------- carts
def cart_items(con, cart_id):
    return [dict(r) for r in con.execute(
        "SELECT key, qty, name FROM cart_items WHERE cart_id=? ORDER BY position, rowid", (cart_id,))]
