"""Run: .venv/bin/python tests/test_deal_terms.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.deal_terms import cost_with, promo_terms  # noqa: E402

# ---- deals: counted only when the description states the same terms as the numbers
assert promo_terms('מגוון ירק טרי מהשורש 2 יח ב15.90 ש"ח', 2, 15.9, 10.92) == ("bundle", 2, 15.9)
assert promo_terms("פרסידנט גבינת שמנת קלאסית 180גרם-2ב30", 2, 30, 23.08) == ("bundle", 2, 30.0)
assert promo_terms("אולוויז דיסקריט 2- ב 26", 2, 26, 12.75) == ("bundle", 2, 26.0)
assert promo_terms("שערות סבתא תות 15 גרם 3ב11", 3, 11, 8.33) == ("bundle", 3, 11.0)
assert promo_terms("נשנושים/איירביס 2 ב30", 2, 15, None) == ("bundle", 2, 30.0)   # per-unit price in the file
assert promo_terms("שלגוני קראץ/טילון אקסטרים/סנדוויץ אוראו/", 3, 30, 15.97) is None  # terms not in the text
assert promo_terms("חטיף חלבון  -בתוקף מבצע  21.11.202ב25WIN", 2, 25, None) is None
assert promo_terms("2ב45 עגב. חתוכות", 3, 45, None) is None                        # text and MinQty disagree
assert promo_terms("במבצע 11.90", 1, 11.9, 14.39) == ("unit", 11.9)
assert promo_terms("לחם הארץ -13.9", 1, 13.9, 19.19) == ("unit", 13.9)
assert promo_terms("גאודה ב 119", 0.01, 119, 14) == ("unit", 119)
assert promo_terms("32.10 פקדון סבן אפ רגיל/מירינדה 1.5*6ליט", 1, 33.9, 15) is None
assert promo_terms("שמפו 1+1", 2, None, 50) == ("free", 1, 1)
assert promo_terms("השני ב-50% על כל המגוון", 2, None, 25) == ("second", 50.0)
assert promo_terms("20% הנחה", 1, None, 20) == ("pct", 20.0)
assert promo_terms("20% הנחה", 1, None, 30) is None
assert promo_terms("קופון שובר מגוון תלמה בקניית 8 יחידות", 1, 10, None) is None
assert promo_terms("במבצע 11.90", 1, 11.9, None, remarks="הצטרפות מועדון קרפור 100% הנחה") is None
assert promo_terms("מבצע מועדון 11.90", 1, 11.9, None) is None
assert promo_terms("מבצע מועדון 11.90", 1, 11.9, None, club=True) == ("unit", 11.9)
assert promo_terms("2ב30 בקנייה מעל 100 ש\"ח", 2, 30, None) is None
# chains that write only the product name: the price and the percentage must agree at the shelf price
assert promo_terms("קרפור סטייליס בטעם טבעי 500 גרם", 2, 22, 26.17, price=14.90) == ("bundle", 2, 22.0)
assert promo_terms("קרפור סטייליס בטעם טבעי 500 גרם", 2, 22, 26.17, price=12.90) is None
assert promo_terms("קרפור סטייליס בטעם טבעי 500 גרם", 2, 22, 26.17) is None   # no shelf price, nothing to check
assert promo_terms("אבוקדו", 0.01, 5.9, 14.49, price=6.90) == ("unit", 5.9)
assert promo_terms("טבליות פיניש 30 יח", 1, 19.9, 50.0, price=39.80) == ("unit", 19.9)
assert promo_terms("שוקולד רושן", 3, 20, None, price=9.90) is None

# ---- cost: the bundle that used to be priced at 18.72
assert cost_with(9.90, 2, ("bundle", 2, 15.9)) == 15.9
assert round(cost_with(9.90, 3, ("bundle", 2, 15.9)), 2) == 25.8           # one bundle + one at shelf price
assert cost_with(9.90, 1, ("bundle", 2, 15.9)) is None                     # not enough units
assert cost_with(10, 4, ("free", 1, 1)) == 20
assert cost_with(10, 2, ("second", 50.0)) == 15
assert cost_with(10, 3, ("unit", 8), max_qty=2) == 26                      # deal covers 2 units only
assert cost_with(10, 1, ("unit", 12)) is None                              # no saving
assert cost_with(10, 2, ("bundle", 2, 25)) is None
print("ok")
