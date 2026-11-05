"""Run: .venv/bin/python tests/test_deal_terms.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.deal_terms import cost_with, promo_terms, text_limit  # noqa: E402

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

# ---- review findings, real descriptions from the live files (2026-11-05)
# F42 spend thresholds: Shufersal's "-מות150" / "מות-75" suffix (מותנה בקנייה מעל), "בקנייה מעל/ב/של"
assert promo_terms("19.90 ממרח קקאו עם אגוזי לוז-מות150", 1, 19.9, 47, price=37.9) is None
assert promo_terms("3ב100 חיתולי פמפרס בייבי דרי-מות75", 3, 100, None, price=39.9) is None
assert promo_terms("2ב120 נוטרילון שלבים 1-3 -800מות75", 2, 120, None, price=74.9) is None
assert promo_terms("3.90 מ.שיניים קולגייט -260מות150", 1, 3.9, 70, price=12.9) is None
assert promo_terms("3ב110 חיתולי פמפרס פרימיום -1מות-75ישיר", 3, 110, None, price=37.9) is None
assert promo_terms("בי 2ב44 חיתולי האגיס פרידום -מות-99ס198", 2, 44, None, price=44.9) is None
assert promo_terms("ישח 2.90 מנה חמה/פירה/קוסקוס מות 150", 1, 2.9, 47, price=5.5) is None
assert promo_terms('2ב30 בקניה ב-150 ש"ח', 2, 30, None) is None
assert promo_terms('20% הנחה בקנייה של 200 ש"ח ומעלה', 1, None, 20) is None
assert promo_terms("האגיס פרידום דריי 3ב100 מעל 70 מו6", 3, 100, None, price=49.9) is None
# ... words that only contain the letters: melted cheese, compatible capsules, palm leaves
assert promo_terms("גבינה מותכת 70 גרם PRESIDENT", 3, 20, 15.61, price=7.9) == ("bundle", 3, 20.0)
assert promo_terms("קפסולות תואמות נספרסו 4ב59", 4, 59, None, price=19.9) == ("bundle", 4, 59.0)
assert promo_terms("2ב20 10מרקיות מעלי דקל", 2, 20, 8, price=10.9) == ("bundle", 2, 20.0)
# F43 paying with vouchers (Shufersal's Tav HaZahav gift vouchers)
assert promo_terms("תו זהב 5% הנחה מותג שופרסל", 1, 13.21, 5, price=9.9) is None
assert promo_terms("תו זהב 5% הנחה מותג שופרסל", 0.01, 51.3, 5, price=9.3) is None
assert promo_terms("תו הזהב 5% הנחה", 1, None, 5) is None
assert promo_terms("10% הנחה בתשלום בתווי קנייה", 1, None, 10) is None
assert promo_terms("מדבקות תוויות לבית ספר 2ב10", 2, 10, None) == ("bundle", 2, 10.0)   # labels, not vouchers
assert promo_terms("2ב16 פירורית זהב 350ג אסם", 2, 16, 38, price=12.9) == ("bundle", 2, 16.0)  # gold, the product
# F45 n+m only when the numbers agree; a pack's "3+3" is not a deal
assert promo_terms("ישח 12.90 סט מגשי הגשה 3+3", 1, 12.9, 35, price=19.9) == ("unit", 12.9)
assert round(cost_with(19.9, 6, promo_terms("ישח 12.90 סט מגשי הגשה 3+3", 1, 12.9, 35, price=19.9)), 2) == 77.4
assert promo_terms("סוכריות פז בטעמים שונים 3+1 ב10.9שח", 1, 10.9, 15.5, price=12.9) == ("unit", 10.9)
assert promo_terms("סט צלחות  8+8  דבסט  - 10", 1, 10.0, 32.89, price=14.9) == ("unit", 10.0)
assert promo_terms("ישח 2ב25 סט צלחות אייס 8+8 \"7.5+\"10", 2, 25, None, price=19.9) == ("bundle", 2, 25.0)
assert promo_terms("1+1 ב-20", 2, 20, None, price=12.9) is None                     # nothing confirms 2 for 20
assert promo_terms("1+1 ב-20", 2, 20, 22.48, price=12.9) == ("bundle", 2, 20.0)
assert promo_terms("ב1+1- המבורגר/קבב/נקנקיוות 500גרם", 1, 35.9, None, price=35.9) == ("free", 1, 1)
assert promo_terms("ב1+1- המבורגר/קבב/נקנקיוות 500גרם", 1, 35.9, None, price=39.9) is None
assert promo_terms("1+1 נרות שבת 4-20 יחידות", 1, 4.9, None, price=5.9) is None
assert promo_terms("רטבי עגבניות יכין 240 גר 3+1", 4, None, 10000, price=6.5) == ("free", 3, 1)
assert promo_terms('סנסודיין משחת שינים רגישות 75 מ"ל 1+1', 2, 24.9, 50, price=24.9) == ("free", 1, 1)
assert promo_terms("שמפו 1+1", 2, None, 30) is None                                  # 1+1 is 50%, not 30%
assert promo_terms("השני ב-50% על כל המגוון", 2, None, 40) is None
# F46 the text states other terms than the file: not counted, and no price / % fallback then
assert promo_terms("שטראוס משקאות חלב 1ליטר 2ב20שח", 2, 16.0, 39.85, price=13.3) is None
assert promo_terms("שטראוס משקאות חלב 1ליטר 2ב20שח", 2, 20.0, 24.81, price=13.3) == ("bundle", 2, 20.0)
assert promo_terms("טמפו קפסולות מאורו לאספרסו 10 יח 2 ב30שח", 2, 34, 10.05, price=18.9) is None
assert promo_terms("טמפו קפסולות מאורו לאספרסו 10 יח 2 ב34שח", 2, 30, 20.63, price=18.9) is None
assert promo_terms("האגיס אקסטרה קר ניו בורן 2ב85", 3, 85, 1.96, price=28.9) is None
assert promo_terms("2ב45 עגב. חתוכות", 3, 45, 3.23, price=15.5) is None
assert promo_terms("מנקה/מפיץ פיניש למדיח 2ב24", 2, 20, 44.13, price=17.9) is None
assert promo_terms("אטריות ביצים 400 גרם רמי לוי 2ב10", 2, 13, 18.75, price=8.0) is None
assert promo_terms("חמאה ב 21.9", 1, 22.9, 8.0, price=24.9) is None
assert promo_terms("גרוייר ב 34.9", 1, 39.9, 19, price=49.5) is None
assert promo_terms("בי 39.90 מוצצי מאמ מארז זוג", 1, 33.81, 11, price=38.0) is None
assert promo_terms('מלבי 5ב20ש', 1, 4.0, 42.03, price=6.9) is None                    # 5 for 20, not one for 4
assert promo_terms("משחת שיניים אופטיק וויט 75 מ\"ל-ב2ב22", 1, 11.9, 45.66, price=21.9) is None
# ... a bottle deposit the text names (פקדון / כולל) is the only difference allowed
assert promo_terms('2ב129.40 פקדון וויסקי גרנטס 700 מ"ל', 2, 130, 38, price=104.9) == ("bundle", 2, 130.0)
assert promo_terms("2ב13.40 פקדון ספרינג/לייט 1.5 ליטר", 2, 14.0, None, price=8.9) == ("bundle", 2, 14.0)
assert promo_terms("2ב36.40 פקדון פחיות קוקה קולה/זירו/ספריי", 2, 40.0, None, price=25.9) == ("bundle", 2, 40.0)
assert promo_terms('99.70 פקדון וויסקי גק דניאלס 700 מ"ל', 1, 100, 23, price=129.9) == ("unit", 100.0)
assert promo_terms("פרוט אנג ווג קרטונית 2ב20 כולל", 2, 19.4, 11.01, price=10.9) == ("bundle", 2, 19.4)
assert promo_terms("מבצע עין גדי פקק ספורט 750מל 2 ב 14שח", 2, 15, 15.73, price=8.9) is None   # no deposit word
assert promo_terms("יין סגל 750 מ\"ל ב22.9 שח", 1, 22.6, 9.24, price=24.9) is None
# ... "3 ב 1" is 3-in-1, a pack's count is not a bundle, Super Sapir writes "++2ב16"
assert promo_terms("טוסטר לחיצה  3 ב 1 -99.9", 1, 99.9, 23.09, price=129.9) == ("unit", 99.9)
assert promo_terms("29.90 5 ב1- גל כביסה 2 ליטר LU", 1, 29.9, 27, price=40.9) == ("unit", 29.9)
assert promo_terms("מארז חטיפים 10 יח' ב 9.90", 1, 9.9, 41.42, price=16.9) == ("unit", 9.9)
assert promo_terms("נרות 72 ב 17.90", 1, 17.9, 18.26, price=21.9) == ("unit", 17.9)
assert promo_terms("טבלאות שוקולד פרה 100 גר' ++2ב16", 2, 16, None, price=9.9) == ("bundle", 2, 16.0)
assert promo_terms("אבקת אפיה/סוכר וניל/אבקת סוכר הנמל +5ב10", 5, 10, None, price=2.5) == ("bundle", 5, 10.0)
assert promo_terms("ק/2ב45 מגוון פירות קפואים 400-500גר *", 2, 45, 5.06, price=24.9) == ("bundle", 2, 45.0)
assert promo_terms("חציליים קלויים/פלפל כבוש/חריף-SERA2ב22", 2, 22, None, price=13.9) == ("bundle", 2, 22.0)
assert promo_terms("שוקולד ג'יאנדויה 150 ג' DG2 ב16.9", 1, 16.9, 15.08, price=19.9) == ("unit", 16.9)
# F48 card holders and groups of shoppers; "מוע-" is the club
assert promo_terms("W CARD/מלבי 5ב20ש מוגבל 2מנות מוגבל 2מנות", 1, 4.0, 42.03, price=6.9) is None
assert promo_terms('*W CARD/ציפס קפוא 1.5ק"ג ב15ש מוגבל 2 מנות', 1, 15, 31.51, price=21.9) is None
assert promo_terms("מבצע 7% הנחה דיור מוגן", 1, 38.97, 7, price=14.9) is None
assert promo_terms("קפה ב5 לחיילים / תחנת בת ים 186", 1, 5, 64.29, price=12.0) is None
assert promo_terms("10% הנחה לאזרחים ותיקים", 1, None, 10) is None
assert promo_terms("מוע-פיטנס קרקר דק 140 גרם 7.9 מוגבל 3", 1, 7.9, 43.17, price=13.9) is None
assert promo_terms("מוע-פיטנס קרקר דק 140 גרם 7.9 מוגבל 3", 1, 7.9, 43.17, price=13.9, club=True) == ("unit", 7.9)
assert promo_terms("7.90תפוצ'יפס 50 גר' מארז-למשלמים בכ.מקס ויקטורי מוגבל", 1, 7.9, 20, price=9.9) is None
assert promo_terms("קינדר קארדס/קאנטרי 3 ב 10", 3, 10, None, price=3.9) == ("bundle", 3, 10.0)   # the product
assert promo_terms("יין גמלא 750 מל ב 54.90", 1, 54.9, None, price=59.9) == ("unit", 54.9)        # the winery
assert promo_terms("מזון לבעלי חיים 2ב30", 2, 30, None) == ("bundle", 2, 30.0)

# ---- per-customer limits stated in the text
assert text_limit("*מנה חמה אסם ב3.90ש מוגבל 4מנות") == 4
assert text_limit("טחינה בארכה 500 גרם ב 8.90 מוגבל3") == 3
assert text_limit("W CARD/מלבי 5ב20ש מוגבל 2מנות מוגבל 2מנות") == 2
assert text_limit("פרסיל ג'ל 2.5 ליטר עד 2 יח") == 2
assert text_limit("דאו אקס ספריי ב6.90ש עד4יח*") == 4
assert text_limit("רבע לשבע עלית ב-10 ש ח מו2") == 2
assert text_limit('אונליין-סילאן לחיץ שווה-470 ג ב9.90 ש"ח מוגבל ל2 יח') == 2
assert text_limit('אונליין בלבד-קמח שטיבל מנופה כחול 2 ב10 ש"ח-מוגבל ל4 מימוש') == 4
assert text_limit("59.90צלעות טרי יבוא  מוגבל ל5 קג") == 5
assert text_limit("64.90 דג דניס טרי שלם מוגבל 3קג'") == 3
assert text_limit("לימון ב4.9 מוגבל ל3") == 3
assert text_limit("מוגבל 3 יח':") == 3
assert text_limit("*קמח לבן נפלאות מנופה 1 ק\"ג 4ב22ש מוגבל מנה") == 1
assert text_limit("2ב30 גל כביסה כחול 3 ליטר טאצ` מוגבל מימוש") == 1
assert text_limit("אונ-כרעיים/ירכיים עוף טרי 5 ב99.90 מוגבל") == 1                # cut off: the cautious 1
assert text_limit("W CARD/פרחי ברוקולי קפוא 700ג ב7.90ש 2מנות") == 2
assert text_limit("מוגבל 3מנות ללקוח:") == 3
assert text_limit("מוגבל 1.5 ק\"ג") == 1
assert text_limit("שוקולד 2ב10") is None
assert text_limit("בתוקף עד 31.12") is None
assert text_limit("עד 50% הנחה") is None
assert text_limit("מוגבל במלאי") is None
assert text_limit(None) is None

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
