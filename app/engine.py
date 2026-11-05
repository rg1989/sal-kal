"""Basket comparison: price a list of products at every store and rank chains."""
import json
import statistics
import time
from collections import defaultdict

from . import geo
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
def line_cost(price, qty, promos):
    """Cheapest way to buy `qty` units given the store's active promotions."""
    base = price * qty
    best, best_promo = base, None
    for pr in promos:
        mq = max(pr["min_qty"] or 1, 1)
        unit = None
        dp, rate = pr["discounted_price"], pr["discount_rate"]
        if rate and 100 < rate <= 10000:  # some chains (Keshet) write 20% as 2000
            rate = rate / 100
        if rate and 0 < rate <= 100 and mq > 1:
            # "1+1" / "2nd at 50%": the discount covers one unit of the bundle
            unit = price * (1 - rate / 100 / mq)
        elif dp and dp > 0:
            # DiscountedPrice is the bundle price for MinQty units ("3 for 8.90"),
            # unless it's already below the unit price (then it's per unit).
            unit = dp / mq if (mq > 1 and dp >= price - 0.01) else dp
        elif rate and 0 < rate < 100:
            unit = price * (1 - rate / 100)
        if unit is None or unit <= 0 or unit >= price or unit < price * 0.05:
            continue
        if qty + 1e-9 < mq:
            continue
        bundles = int((qty + 1e-9) // mq) if mq > 1 else None
        if bundles is not None:
            cost = bundles * mq * unit + (qty - bundles * mq) * price
        else:
            cost = qty * unit
        if cost < best - 0.005:
            best, best_promo = cost, pr
    return best, best_promo


def _is_online(store):
    if store["store_type"] in (2, 3):
        return True
    name = (store["name"] or "") + " " + (store["sub_chain"] or "")
    return any(h in name for h in ONLINE_HINTS)


def _is_physical(store):
    return store["store_type"] in (None, 1, 3) and not (store["store_type"] is None and _is_online(store))


def compare(con, items, prefs=None, now_int=None):
    prefs = {**DEFAULT_PREFS, **(prefs or {})}
    items = [i for i in items if i.get("key") and float(i.get("qty") or 0) > 0]
    now_int = now_int or int(time.strftime("%Y%m%d%H%M"))
    if not items:
        return {"chains": [], "items": [], "generated": int(time.time())}
    keys = [i["key"] for i in items]
    qty = {i["key"]: float(i["qty"]) for i in items}
    ph = ",".join("?" * len(keys))

    products = {r["key"]: dict(r) for r in con.execute(f"SELECT * FROM products WHERE key IN ({ph})", keys)}
    # weighed produce / in-store codes differ per chain: line them up by normalized name
    aliases = {k: [] for k in keys}
    for k in keys:
        p = products.get(k)
        if p and p["scope"] == "chain" and p.get("norm") and len(p["norm"]) >= 2:
            aliases[k] = [r["key"] for r in con.execute(
                "SELECT key FROM products WHERE scope='chain' AND norm=? AND key!=? AND store_count>0", (p["norm"], k))]
    lookup = list(dict.fromkeys(keys + [a for v in aliases.values() for a in v]))
    ph = ",".join("?" * len(lookup))
    stores = {}
    prices = defaultdict(dict)  # (chain, store) -> key -> price
    for r in con.execute(f"""
        SELECT p.key, p.chain_id, p.store_id, p.price, p.ts, s.name, s.address, s.city, s.store_type,
               s.sub_chain, s.last_price_ts
        FROM prices p JOIN stores s ON s.chain_id=p.chain_id AND s.store_id=p.store_id
        WHERE p.key IN ({ph}) AND p.ts >= s.price_full_ts""", lookup):
        sk = (r["chain_id"], r["store_id"])
        if sk not in stores:
            stores[sk] = {k: r[k] for k in ("chain_id", "store_id", "name", "address", "city",
                                            "store_type", "sub_chain", "last_price_ts")}
        prices[sk][r["key"]] = r["price"]

    promos = defaultdict(lambda: defaultdict(list))
    club_clause = "" if prefs.get("club") else "AND pr.club_only=0"
    # staff discounts ("הנחת עובד") are in the files but aren't open to shoppers
    club_clause += " AND coalesce(pr.description, '') NOT LIKE '%עובד%'"
    if not prefs.get("club"):  # some chains mark club deals only in the text ("מבצע מועדון")
        club_clause += " AND coalesce(pr.description, '') NOT LIKE '%מועדון%'"
    for r in con.execute(f"""
        SELECT pi.key, pr.* FROM promo_items pi
        JOIN promos pr ON pr.chain_id=pi.chain_id AND pr.store_id=pi.store_id AND pr.promo_id=pi.promo_id
        JOIN stores s ON s.chain_id=pi.chain_id AND s.store_id=pi.store_id
        WHERE pi.key IN ({ph}) AND pi.ts >= s.promo_full_ts AND pr.ts >= s.promo_full_ts
          AND (pr.end_ts IS NULL OR pr.end_ts >= ?) AND (pr.start_ts IS NULL OR pr.start_ts <= ?) {club_clause}""",
            [*lookup, now_int, now_int]):
        promos[(r["chain_id"], r["store_id"])][r["key"]].append(dict(r))

    # median shelf price per product, used to estimate missing items fairly
    def find(sk, k):
        for cand in [k] + aliases[k]:
            if cand in prices[sk]:
                return cand
        return None

    all_prices = defaultdict(list)
    for sk in prices:
        for k in keys:
            hit = find(sk, k)
            if hit:
                all_prices[k].append(prices[sk][hit])
    median = {k: statistics.median(v) for k, v in all_prices.items()}

    def price_store(sk):
        lines, total, missing, est = [], 0.0, [], 0.0
        for k in keys:
            q = qty[k]
            hit = find(sk, k)
            if hit is None:
                missing.append(k)
                est += median.get(k, 0) * q
                lines.append({"key": k, "available": False})
                continue
            p = prices[sk][hit]
            cost, promo = line_cost(p, q, promos[sk].get(hit, []))
            total += cost
            lines.append({"key": k, "available": True, "price": p, "qty": q, "cost": round(cost, 2),
                          "full": round(p * q, 2), "by_name": hit != k,
                          "promo": {"description": promo["description"], "club": bool(promo["club_only"]),
                                    "ends": promo["end_ts"]} if promo else None})
        return {"total": round(total, 2), "missing": missing, "estimate_missing": round(est, 2),
                "comparable": round(total + est, 2), "covered": len(keys) - len(missing), "lines": lines}

    delivery = delivery_table(con)
    home = prefs.get("home") or None
    max_km = float(prefs["max_km"]) if home and prefs.get("max_km") else None
    coords = geo.store_coords(con) if home else {}
    by_chain = defaultdict(lambda: {"store": [], "online": []})
    for sk, st in stores.items():
        res = price_store(sk)
        dist = round(geo.km(home["lat"], home["lon"], *coords[sk]), 1) if sk in coords else None
        entry = {**st, **res, "distance_km": dist, "lat": coords[sk][0] if sk in coords else None,
                 "lon": coords[sk][1] if sk in coords else None}
        if _is_online(st):
            by_chain[st["chain_id"]]["online"].append(entry)
        # stores not located yet can't be shown to be within range, so a distance cap hides them
        if _is_physical(st) and (max_km is None or (dist is not None and dist <= max_km)):
            by_chain[st["chain_id"]]["store"].append(entry)

    def rank_key(e):
        return (e["comparable"], -e["covered"], e["distance_km"] if e["distance_km"] is not None else 1e9)

    names = {r["chain_id"]: r["name_he"] for r in con.execute("SELECT chain_id, name_he FROM chains")}
    chains = []
    for cid, groups in by_chain.items():
        store_opts = sorted(groups["store"], key=rank_key)
        online_opts = sorted(groups["online"], key=rank_key)
        rule = delivery.get(cid)
        online = None
        if online_opts:
            o = online_opts[0]
            fee, note = delivery_cost(rule, o["total"])
            online = {**_slim(o), "lines": o["lines"], "fee": fee, "fee_note": note,
                      "delivered": round(o["total"] + fee, 2) if fee is not None else None,
                      "delivered_comparable": round(o["comparable"] + fee, 2) if fee is not None else None,
                      "price_vs_store": None}
        in_store = None
        if store_opts:
            s = store_opts[0]
            in_store = {**_slim(s), "lines": s["lines"],
                        "branches": [_slim(b) for b in store_opts[:8]], "branch_count": len(store_opts)}
        if online and in_store:
            online["price_vs_store"] = round(online["total"] - in_store["total"], 2)
        if prefs.get("mode") == "delivery":
            if not online:
                continue
            score = online["delivered_comparable"] if online["delivered_comparable"] is not None else online["comparable"] + 1e6
            covered = online["covered"]
        else:
            if not in_store:
                continue
            score, covered = in_store["comparable"], in_store["covered"]
        chains.append({"chain_id": cid, "name": chain_name(cid, names.get(cid)), "name_he": names.get(cid),
                       "score": round(score, 2), "covered": covered, "in_store": in_store, "online": online,
                       "delivery_rule": rule})
    chains.sort(key=lambda c: (c["covered"] < len(keys), c["score"]))
    # multiple chain ids can share a brand (e.g. Victory) — keep the best per brand name
    seen, ranked = set(), []
    for c in chains:
        if c["name"] in seen:
            continue
        seen.add(c["name"])
        ranked.append(c)

    item_info = []
    for k in keys:
        p = products.get(k, {})
        ps = all_prices.get(k, [])
        item_info.append({"key": k, "qty": qty[k], "name": p.get("name") or k, "manufacturer": p.get("manufacturer"),
                          "is_weighted": bool(p.get("is_weighted")), "scope": p.get("scope"),
                          "min": min(ps) if ps else None, "max": max(ps) if ps else None,
                          "median": median.get(k), "stores": len(ps)})
    data_ts = max((s["last_price_ts"] or 0 for s in stores.values()), default=0)
    # every branch you'd walk into, each priced for the whole cart: ones that have everything first, then cheapest first
    # (the same order as the chains, so the first branch is the cheapest chain's best branch)
    every = sorted((e for g in by_chain.values() for e in g["store"]),
                   key=lambda e: (e["covered"] < len(keys), e["comparable"], e["distance_km"] if e["distance_km"] is not None else 1e9))
    branches = [{"chain_id": e["chain_id"], "chain": chain_name(e["chain_id"], names.get(e["chain_id"])), "store_id": e["store_id"],
                 "name": e["name"], "city": e["city"], "distance_km": e["distance_km"], "lat": e["lat"], "lon": e["lon"],
                 "total": e["total"], "comparable": e["comparable"], "covered": e["covered"], "missing": len(e["missing"])}
                for e in every]
    return {"chains": ranked, "branches": branches, "items": item_info, "prefs": prefs, "data_ts": data_ts,
            "generated": int(time.time())}


def _slim(e):
    return {k: e.get(k) for k in ("chain_id", "store_id", "name", "address", "city", "store_type", "total",
                                  "comparable", "covered", "missing", "estimate_missing", "last_price_ts", "distance_km",
                                  "lat", "lon")}


# ---------------------------------------------------------------- carts
def cart_items(con, cart_id):
    return [dict(r) for r in con.execute(
        "SELECT key, qty, name FROM cart_items WHERE cart_id=? ORDER BY position, rowid", (cart_id,))]
