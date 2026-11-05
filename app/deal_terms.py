"""Which deals Sal counts: only when the deal's own description states the same terms as its numbers
(or its price and percentage agree at the shelf price), with no condition the price files can't express."""
import re

NUM = r"(\d+(?:\.\d{1,2})?)"
# "2ב15", "2 ב-15.90", "2 יח' ב15.90", "3 יחידות ב 20", "2- ב 26"
BUNDLE_RE = re.compile(r"(?<![\d.])(\d{1,2})\s*-?\s*(?:יח(?:ידות|י|')?\.?\s*)?-?\s*ב\s*-?\s*" + NUM + r"(?![\d.])")
FREE_RE = re.compile(r"(?<![\d.])(\d)\s*\+\s*(\d)(?![\d.%])")              # 1+1, 2+1
SECOND_RE = re.compile(r"(?:ה)?שני(?:יה|ה)?\s*ב\s*-?\s*(\d{1,2})\s*%")     # השני ב-50%
HALF_RE = re.compile(r"(?:ה)?שני(?:יה|ה)?\s*ב?\s*חצי\s*מחיר")              # השני בחצי מחיר
PCT_RE = re.compile(r"(\d{1,2})\s*%")
NUMS_RE = re.compile(r"(?<![\d.])" + NUM + r"(?![\d])")
# conditions the price files can't express: coupons, vouchers, club sign-up, card / app only, staff, spend thresholds
CONDITION_RE = re.compile(r"קופון|שובר|הצטרפ|כרטיס|אשראי|אפליקצי|עובד|מעל\s*-?\s*₪?\s*\d")
CLUB_RE = re.compile(r"מועדון")


def _eq(a, b, tol=0.011):
    return a is not None and b is not None and abs(a - b) <= tol


def promo_terms(desc, min_qty, dp, rate, remarks="", club=False, price=None):
    """The deal's terms, only when its description states the same terms as its numbers, or (some chains write
    only the product name) when its price and its percentage agree with each other at this shelf price.
    Club deals ("מועדון") count only for a club member (club=True).

    Returns one of:
      ("unit", price)          every unit at this price
      ("bundle", n, total)     n units for total
      ("free", buy, get)       buy n, get m free (paid: buy units of every buy+get)
      ("second", pct)          the second unit pct% off
      ("pct", pct)             every unit pct% off
    or None when the deal can't be confirmed (it is then shown, not counted)."""
    text = (desc or "").replace("₪", " ").replace('ש"ח', " ").replace("ש״ח", " ")
    said = f"{desc or ''} {remarks or ''}"
    if CONDITION_RE.search(said) or (not club and CLUB_RE.search(said)):
        return None
    mq = float(min_qty or 1)
    mq_int = int(round(mq)) if mq >= 1 and abs(mq - round(mq)) < 1e-6 else None
    if rate and 100 < rate <= 10000:  # some chains (Keshet) write 20% as 2000
        rate = rate / 100
    nums = [float(n) for n in NUMS_RE.findall(text)]

    m = FREE_RE.search(text)
    if m:
        buy, get = int(m.group(1)), int(m.group(2))
        if buy >= 1 and get >= 1 and (mq_int in (None, 1, buy + get)):
            return ("free", buy, get)
        return None
    m = SECOND_RE.search(text)
    if m and mq_int in (None, 1, 2):
        return ("second", float(m.group(1)))
    if HALF_RE.search(text) and mq_int in (None, 1, 2):
        return ("second", 50.0)
    # the file's percentage is the saving its price gives at this shelf price: two numbers that confirm each other
    agrees = lambda paid, units: bool(price and rate and 0 < rate < 100 and price * units > 0
                                      and _eq(100 * (1 - paid / (price * units)), rate, 0.6))
    if mq_int and mq_int > 1:
        if dp and dp > 0:
            for n, total in BUNDLE_RE.findall(text):
                n, total = int(n), float(total)
                if n == mq_int and (_eq(total, dp) or _eq(total, dp * mq_int, 0.02 * n)):
                    return ("bundle", n, round(total, 2))
            if agrees(dp, mq_int):
                return ("bundle", mq_int, round(dp, 2))
            if agrees(dp, 1):  # the price is per unit
                return ("bundle", mq_int, round(dp * mq_int, 2))
        return None
    # one unit: a sale price written in the description, or a percentage off written as one
    if dp and dp > 0 and (any(_eq(n, dp) for n in nums) or agrees(dp, 1)):
        return ("unit", round(dp, 2))
    if rate and 0 < rate < 100:
        for p in PCT_RE.findall(text):
            if _eq(float(p), rate, 0.51):
                return ("pct", float(p))
    return None


def cost_with(price, qty, terms, max_qty=None):
    """What qty units cost at shelf price `price` under confirmed deal terms. None when the deal doesn't apply
    (not enough units, or no saving)."""
    if not terms or not price or price <= 0 or qty <= 0:
        return None
    kind = terms[0]
    cap = qty if not max_qty or max_qty <= 0 else min(qty, max_qty)  # units the deal may cover
    if kind == "unit":
        unit = terms[1]
        if not (price * 0.05 <= unit < price):
            return None
        return cap * unit + (qty - cap) * price
    if kind == "pct":
        unit = price * (1 - terms[1] / 100)
        return cap * unit + (qty - cap) * price
    if kind == "bundle":
        n, total = terms[1], terms[2]
    elif kind == "free":
        n, total = terms[1] + terms[2], terms[1] * price
    elif kind == "second":
        n, total = 2, price * (2 - terms[1] / 100)
    else:
        return None
    if not (price * n * 0.05 <= total < price * n - 0.005):
        return None
    bundles = int((cap + 1e-9) // n)
    if bundles < 1:
        return None
    return bundles * total + (qty - bundles * n) * price
