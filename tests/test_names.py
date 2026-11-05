"""Run: .venv/bin/python tests/test_names.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.names import name_words, names_agree, odd_names  # noqa: E402

# ---- names: the same barcode at different chains (real names from the price files)
w = name_words
assert w("גבינה לואשקירי 120 ג") == {"גבינה", "לואשקירי"}
assert w("ביצת קינדר ג'וי 20 ג'") == {"ביצת", "קינדר", "גוי"}
assert w("רד בול משקה אנרגיה פח250") == {"רד", "בול", "משקה", "אנרגיה", "פח"}
assert names_agree(w("קינדר בואנו דארק 43 גרם"), w("בואנו דארק"))
assert names_agree(w("טוויזלרס 198גרם"), w("סוכריות טוויזלר תות"))        # plural / prefix of a word
assert names_agree(w("אבוקדו בשל"), w("אבקדו"))                          # one-letter typo
assert names_agree(w("ביצת הפתעה עם צעצוע"), w("ביצת קינדר הפתעה 20 גרם"))
assert not names_agree(w("אורביט"), w("מסטיק מנטוס פיור פרש"))
assert names_agree(w("קאשיו 200"), w("קשיו מטוגן 200גר"))                 # one more vowel letter
assert names_agree(w("אל.די ארוך"), w("LD BLUEארוך"))                    # English glued to Hebrew
assert names_agree(w("KNORR"), w("מרק פטריות קנור"))                     # brand in English and in Hebrew
assert names_agree(w("Pampers"), w("חיתולי פמפרס פרימיום"))
assert names_agree(w("צמד חסות בשקית"), w("לבבות חסה"))                  # plural
assert names_agree(w("דלי משחה-סמדר 3.5 קג"), w("משחת קלין"))            # construct state
assert not names_agree(w("מרס"), w("חטיף שוקולד בר טוויקס"))
assert not names_agree(w("קילו תמר מגהול"), w("אננס טבעי 150 גר`"))
assert not names_agree(w("כיסא ים פסים"), w("כוסברה ירק הארץ גוש"))
assert names_agree(w("60 גרם"), w("מסטיק")) is None                      # nothing to judge by

mentos = {"A": "מסטיק מנטוס פיור בטעם ענבים 60 ג'", "B": "מנטוס מסטיק ללא סוכר", "C": "אורביט",
          "D": "מסטיק פיורפרש ענבים60גר"}
odd, better = odd_names(mentos, "מסטיק מנטוס פיור בטעם ענבים 60 ג'")
assert odd == {"C"} and better is None, (odd, better)
odd, better = odd_names(mentos, "אורביט")                                # the card's own name is the odd one
assert odd == {"C"} and better and "מנטוס" in better, (odd, better)
odd, _ = odd_names({"A": "חטיף שוקולד", "B": "סבון כביסה"}, "חטיף שוקולד")  # no consensus: the card decides
assert odd == {"B"}, odd
assert odd_names({"A": "קפה עלית", "B": "קפה טורקי עלית"}, "") == (set(), None)
print("ok")
