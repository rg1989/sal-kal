"""Which deals Sal counts: only when the deal's own description states the same terms as its numbers
(or its price and percentage agree at the shelf price), with no condition the price files can't express.

Accuracy over coverage: a deal is counted only when nothing in it disagrees. Terms the text states that differ
from the file's numbers, an "n+m" whose numbers say something else, a spend threshold, a payment method or a
group of shoppers in the text: the deal is shown, not counted."""
import math
import re

NUM = r"(\d+(?:\.\d{1,2})?)"
UNIT_WORD = r"(?:יח|ק\"ג|ק״ג|קג|ק'|ק\b|ליטר|ל'|גרם|גר'|גר\b|ג'|מ\"ל|מ״ל|מל\b|%|אחוז)"
# "2ב15", "2 ב-15.90", "2 יח' ב15.90", "3 יחידות ב 20", "2- ב 26"
BUNDLE_RE = re.compile(r"(?<![\d.])(\d{1,2})\s*-?\s*(?:יח(?:ידות|י|')?\.?\s*)?-?\s*ב\s*-?\s*" + NUM + r"(?![\d.])")
# the same, only where the count is a count: not glued to a size, range or model ("210-12ב", "21/3 ב", "250*70ב",
# "DG2 ב", "1+1 ב"). Group 2 is set when a unit word sits between the count and the price ("10 יח' ב 9.90").
STATED_RE = re.compile(r"(?<![A-Za-z\d])(?<!\d[.*/+])(?<!\d-)(\d{1,2})\s*-?\s*(יח(?:ידות|י|'|׳)?\.?\s*)?-?\s*ב\s*-?\s*"
                       + NUM + r"(?![\d.%+])")
# a price after a standalone "ב": "ב 21.9", "ב-9.90" (not "50%", not a quantity, not a word ending in ב)
BET_PRICE_RE = re.compile(r"(?<![א-ת\d])ב\s*-?\s*" + NUM + r"(?![\d.%+])(?!\s*" + UNIT_WORD + ")")
# Shufersal / Victory / Wolt write the deal price first: "19.90 ממרח...", "ישח 2.90 ...", "בי 26.90 ..."
LEAD_PRICE_RE = re.compile(r"^\s*(?:[א-ת]{1,4}\.?\s+)?(\d+\.\d{1,2})(?![\d.])(?!\s*" + UNIT_WORD + ")")
FREE_RE = re.compile(r"(?<![\d.])(\d)\s*\+\s*(\d)(?![\d.%])")              # 1+1, 2+1
# "3+3יח", "8+8 יחידות": a pack's contents, not a deal
PACK_AFTER_FREE = re.compile(r"\s*(?:יח|גליל|ליטר|ל'|ק\"ג|גרם|מ\"ל|קפסול|כביס|זוג|\"|״)")
SECOND_RE = re.compile(r"(?:ה)?שני(?:יה|ה)?\s*ב\s*-?\s*(\d{1,2})\s*%")     # השני ב-50%
HALF_RE = re.compile(r"(?:ה)?שני(?:יה|ה)?\s*ב?\s*חצי\s*מחיר")              # השני בחצי מחיר
PCT_RE = re.compile(r"(\d{1,2})\s*%")
NUMS_RE = re.compile(r"(?<![\d.])" + NUM + r"(?![\d])")
# the text names the bottle deposit: "פקדון", or Rami Levy's "2ב20 כולל" (deposit included) at the very end
DEPOSIT_RE = re.compile(r"פקדון|פיקדון|(?<!לא )כולל\W*$")
HEB = r"(?<![א-ת])"
# conditions the price files can't express
CONDITION_RE = re.compile("|".join([
    # coupons, vouchers, club sign-up, card / app only, staff
    r"קופון|שובר|הצטרפ|כרטיס|אשראי|אפליקצי|עובד",
    # spend thresholds: "מעל 100", Shufersal's "-מות150" / "מות-75" (מותנה בקנייה מעל), "בקנייה מעל/של/ב 200",
    # "100 ש"ח ומעלה", "מינימום"
    r"מעל\s*-?\s*₪?\s*\d", HEB + r"מות\s*-?\s*\d", r"ב?קני(?:י)?ה\s*(?:מעל|של|ב)\s*-?\s*₪?\s*\d",
    r"\d\s*(?:₪|ש\"ח|ש״ח|שח|ש)?\s*ומעלה", r"מינימום",
    # paying with vouchers or a given method: Shufersal's Tav HaZahav, meal cards, payment apps, cash only
    HEB + r"[בלו]?תו\s*ה?זהב", HEB + r"[בלו]?תווי(?!ת|ות)", HEB + r"[בלו]?תו\s*(?:קני|שי)",
    r"סיבוס|סודקסו|תן\s*ביס|פייבוקס",
    r"(?i:(?<![a-z])pay\s*box)", r"למשלמים", HEB + r"בכ\.", r"במזומן",
    # card holders and groups of shoppers: W CARD, assisted living, soldiers, seniors, students, members of ...
    r"(?i:(?<![a-z])card(?![a-z]))", r"קארד(?!ס)", r"כארד", r"דיור\s*מוגן", HEB + r"ל?חיילי(?:ם|\s)", r"ותיקים",
    r"גמלאים", r"סטודנט", HEB + r"ל?נכים", r"גיל\s*ה?זהב", r"בהצגת", r"לבעלי(?!\s*חיים)", r"לחברי", r"למנויי",
]))
CLUB_RE = re.compile(r"מועדון|" + HEB + r"מוע[-.׳']")
# a per-customer limit in the text: "מוגבל 4", "מוגבל ל-3 יח'", "מוגבל 4מנות", "עד 2 יח'", "עד3ק", Rami Levy's "מו4"
LIMIT_RES = (
    re.compile(r"מוגבל\s*(?:ל|עד)?\s*-?\s*" + NUM + r"(?![\d])"),
    re.compile(HEB + r"עד\s*-?\s*" + NUM + r"\s*(?:יח|ק|מנ|מימוש)"),
    re.compile(HEB + r"מו\s?(\d{1,2})(?![\d.])"),
    re.compile(r"(?<![\d.])(\d{1,2})\s*(?:יח(?:ידות|'|׳)?\.?\s*)?ללקוח"),
    re.compile(r"(?<![\d.])(\d{1,2})\s*מנות\W*$"),
)
LIMIT_ONE_RE = re.compile(r"מוגבל\s*(?:ל)?\s*(?:מנה|מימוש|יחידה)|מוגבל\W*$")


def _eq(a, b, tol=0.011):
    return a is not None and b is not None and abs(a - b) <= tol


def _deposit(stated, counted, units):
    """True when stated and counted differ by a bottle deposit (₪0.30 a bottle) on `units` units."""
    d = abs(stated - counted)
    if d < 0.005 or units <= 0:
        return False
    per = d / (0.30 * units)
    return round(per) >= 1 and abs(per - round(per)) * 0.30 * units <= 0.011 and round(per) <= 24


def _free_ok(buy, get, mq_int, dp, rate, price):
    """An n+m the numbers agree with: no price or the shelf price (Shufersal), the paid share or the effective
    unit price; and no percentage, or the free share (1+1 = 50%), or 100% on the free unit (Keshet)."""
    n = buy + get
    if rate:
        if not (_eq(rate, 100 * get / n, 1.0) or (_eq(rate, 100, 0.5) and mq_int == n)):
            return False
    if dp and dp > 0:
        if not price:
            return False
        if not (_eq(dp, price, 0.02) or _eq(dp, price * buy, 0.02 * buy) or _eq(dp, price * buy / n, 0.02)):
            return False
    return True


def text_limit(desc):
    """A per-customer limit stated in the description ("מוגבל 4", "מוגבל 3 יח", "עד 2 יח'", "מו4"), else None.
    The number is as written: a count of units, or of deals when the text says מנות/מימושים; callers that cap
    units at it take the cautious reading. A bare "מוגבל" (cut off) or "מוגבל מנה" gives 1. A fractional limit
    (kg) is rounded down."""
    text = desc or ""
    found = []
    for rx in LIMIT_RES:
        for m in rx.finditer(text):
            v = float(m.group(1))
            if v > 0:
                found.append(max(1, int(math.floor(v + 1e-9))))
    if LIMIT_ONE_RE.search(text):
        found.append(1)
    return min(found) if found else None


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
    deposit = bool(DEPOSIT_RE.search(text))

    m = FREE_RE.search(text)
    if m and not PACK_AFTER_FREE.match(text, m.end()):
        buy, get = int(m.group(1)), int(m.group(2))
        if not (buy >= 1 and get >= 1 and mq_int in (None, 1, buy + get)):
            return None
        if _free_ok(buy, get, mq_int, dp, rate, price):
            return ("free", buy, get)
        # the numbers say something else ("3+3" is the pack, the price is per unit): read the deal as if the
        # n+m weren't there; it counts only if the price itself is stated
    m = SECOND_RE.search(text)
    second = float(m.group(1)) if m else (50.0 if HALF_RE.search(text) else None)
    if second is not None:
        if mq_int in (None, 1, 2) and (not rate or _eq(rate, second, 0.6) or _eq(rate, second / 2, 0.6)):
            return ("second", second)
        return None
    # the file's percentage is the saving its price gives at this shelf price: two numbers that confirm each other
    agrees = lambda paid, units: bool(price and rate and 0 < rate < 100 and price * units > 0
                                      and _eq(100 * (1 - paid / (price * units)), rate, 0.6))
    stated = [(int(n), float(t), bool(u)) for n, u, t in STATED_RE.findall(text) if float(t) > 1]  # "2ב1" = 2-in-1
    bet = [float(x) for x in BET_PRICE_RE.findall(STATED_RE.sub(" ", text))]  # "3 ב 1" isn't a price of 1
    if mq_int and mq_int > 1:
        if not (dp and dp > 0):
            return None
        # the text states its bundle: it must be this one (size and total), or the deal isn't counted
        for n, total in [(n, t) for n, t, _ in stated] + [(int(n), float(t)) for n, t in BUNDLE_RE.findall(text)]:
            if n != mq_int:
                continue
            if _eq(total, dp, 0.021):
                return ("bundle", n, round(max(total, dp), 2))
            if _eq(total, dp * n, 0.02 * n):  # the file's price is per unit
                return ("bundle", n, round(total, 2))
            if deposit and (not rate or agrees(dp, n) or agrees(dp, 1)):
                if _deposit(total, dp, n):
                    return ("bundle", n, round(dp, 2))
                if _deposit(total, dp * n, n):
                    return ("bundle", n, round(dp * n, 2))
        if stated:
            return None
        if bet and not any(_eq(x, dp, 0.021) or _eq(x, dp * mq_int, 0.02 * mq_int) for x in bet):
            return None  # "ב 25" in the text, another price in the file
        if agrees(dp, mq_int):
            return ("bundle", mq_int, round(dp, 2))
        if agrees(dp, 1):  # the price is per unit
            return ("bundle", mq_int, round(dp * mq_int, 2))
        return None
    # one unit. A bundle in the text ("5ב20") while the file says one unit: not counted. A count with a unit word
    # or of 10 and more is the pack ("10 יח' ב 9.90", "נרות 72 ב 17.90"): its price is the stated price
    if any(2 <= n < 10 and not unit for n, _, unit in stated):
        return None
    said_prices = bet + [float(x) for x in LEAD_PRICE_RE.findall(text)]
    said_prices += [t for n, t, unit in stated if unit or n == 1 or n >= 10]
    if said_prices:
        # the text states the price: the file's price must be it (or it plus / minus the bottle deposit)
        if not (dp and dp > 0):
            return None
        if any(_eq(x, dp) for x in said_prices):
            return ("unit", round(dp, 2))
        if deposit and (not rate or agrees(dp, 1)) and any(_deposit(x, dp, 1) for x in said_prices):
            return ("unit", round(dp, 2))
        return None
    # a sale price written in the description, or a percentage off written as one
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
