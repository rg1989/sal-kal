"""Run: .venv/bin/python tests/test_names.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.names import Generic, generic_words, name_words, names_agree, odd_names, words_match  # noqa: E402

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

# ---- words: real words that only look alike never match; spellings of one word do
for a, b in [("תפוזים", "תפוחים"), ("מילקה", "מילקי"), ("קוקוס", "קוסקוס"), ("עוגיה", "עוגה"), ("pro", "פרה"),
             ("zero", "שרי"), ("free", "פרי"), ("שמנת", "שמן"), ("קרמל", "קרם"), ("ספרייט", "ספריי")]:
    assert not words_match(*[next(iter(w(x))) for x in (a, b)]), (a, b)
for a, b in [("סאוור", "סוואר"), ("גיאוטו", "גיטו"), ("נתחי", "נטחי"), ("קדבורי", "קטבורי"), ("פוקפורן", "פופקורן"),
             ("red", "רד"), ("bull", "בול"), ("leon", "לאון"), ("zero", "זירו"), ("ecolove", "אקולאב")]:
    assert words_match(*[next(iter(w(x))) for x in (a, b)]), (a, b)

# ---- generic words: in the names of many barcodes
rows = [(f"k{i}", f"עוגת {x}") for i, x in enumerate(["סוזנה", "דנדי", "שוקולד", "גזר", "תפוזים"])]
rows += [("k0", "עוגת סוזנה 500 גרם"), ("k9", "חסה אייסברג")]
g = generic_words(rows, min_keys=3)
assert isinstance(g, frozenset) and g == {"עוגת"} and g.df["עוגת"] == 5 and g.df["סוזנה"] == 1, (g, g.df)

# word counts as on the live data (2026-11-05), for the rules that weigh how common a word is
DF = {"עוגת": 1142, "מילקה": 601, "מיני": 1928, "אוראו": 348, "מומנטס": 20, "חסה": 302, "לקט": 143, "לסלט": 167,
      "אייסברג": 26, "סוזנה": 9, "דנדי": 4, "חלב": 2601, "תנובה": 335, "טרי": 281, "בקרטונ": 92, "ריבה": 156,
      "ריבת": 491, "תות": 1911, "שדה": 511, "פרינגלס": 198, "שמנת": 777, "בצל": 520, "מסטיק": 501, "מנטוס": 257,
      "ענבימ": 578, "דובדבנ": 400, "ללס": 459, "פיור": 247, "פרש": 609}
G = Generic(x for x, n in DF.items() if n >= 150)
G.df = DF


def odd(names, card="", voter=None):
    return odd_names(names, card, G, voter)


# ---- odd names
mentos = {"A": "מסטיק מנטוס פיור בטעם ענבים 60 ג'", "B": "מנטוס מסטיק ללא סוכר", "C": "אורביט",
          "D": "מסטיק פיורפרש ענבים60גר"}
o, better = odd_names(mentos, "מסטיק מנטוס פיור בטעם ענבים 60 ג'")
assert o == {"C"} and better is None, (o, better)
o, better = odd_names(mentos, "אורביט")                                  # the card's own name is the odd one
assert o == {"C"} and better and "מנטוס" in better, (o, better)
o, _ = odd_names({"A": "חטיף שוקולד", "B": "סבון כביסה"}, "חטיף שוקולד")  # no consensus: the card decides
assert o == {"B"}, o
assert odd_names({"A": "קפה עלית", "B": "קפה טורקי עלית"}, "") == (set(), None)

# another flavour of the same brand is another product (F24)
o, better = odd({"A": "ריבת תות שדה 820 גר", "B": "ריבה תות שדה", "C": "ריבת תפוז"}, "ריבת תפוז")
assert o == {"C"} and better and "תות" in better, (o, better)
o, _ = odd({**mentos, "E": "מסטיק ללס בטעם דובדבן 60 גרם"}, mentos["A"])  # Bareket's cherry gum among grape
assert o == {"C", "E"}, o
o, _ = odd({"A": "פרינגלס שום", "B": "פרינגלס שמנת בצל", "K": "פרינגלס שמנת בצל 165 ג'"}, "פרינגלס שמנת בצל")
assert o == {"A"}, o
o, better = odd({"A": "שוקו וניל", "B": "שוקו בננה", "C": "משקה חלב בננה", "D": "משקה חלב בטעם בננה"}, "שוקו וניל")
assert o == {"A"} and "בננה" in better, (o, better)                   # Tara banana milk under a vanilla card
# synonyms are one flavour
assert odd({"A": "קינדר בואנו דארק 43 גרם", "B": "בואנו דארק", "C": "קינדר בואנו מריר 43"}, "בואנו דארק")[0] == set()
assert odd({"A": "סוכריות טופי 200 גר", "B": "סוכריות קרמל"}, "סוכריות קרמל")[0] == set()

# one shared generic word (cake, lettuce, a big brand) is not enough when each name adds its own (F25)
assert odd({"A": "עוגת סוזנה", "B": "עוגת דנדי"}, "עוגת דנדי")[0] == {"A"}
assert odd({"A": "מילקה מומנטס", "B": "מילקה מיני אוראו"}, "מילקה מומנטס")[0] == {"B"}
cm = {"0000000000000": "CityMarketShops", "7290000000003": "CityMarketShops"}
o, better = odd({"0000000000000": "חסה לקט לסלט", "7290000000003": "חסה לקט לסלט", "C": "חסה אייסברג  350 גר'",
                 "H": "חסה אייסברג 350  גר` עלי הבשור מהדרין", "M": "חסה  אייסברג 350גר עלי הבשור מהדרין"},
                "חסה לקט לסלט", cm)
assert o == {"0000000000000", "7290000000003"} and "אייסברג" in better, (o, better)
assert odd({"A": "מרס", "B": "חטיף שוקולד בר טוויקס"}, "מרס")[0] == {"B"}
# ... but a name of generic words only, inside the other, is fine and links nobody
assert odd({"A": "ריבת תות שדה 820 גר", "B": "ריבה תות שדה", "P": "ריבה 820 גר'"}, "ריבת תות שדה")[0] == set()

# one retailer under two chain ids votes once (F28): its two names don't outvote two other chains
o, better = odd({"0000000000000": "בהרט", "7290000000003": "בהרט", "K": "קינמון טחון", "T": "קינמון טחון 100 גר"},
                "בהרט", cm)
assert o == {"0000000000000", "7290000000003"} and "קינמון" in better, (o, better)
o, _ = odd({"0000000000000": "בהרט", "7290000000003": "בהרט", "K": "קינמון טחון", "T": "קינמון טחון 100 גר"}, "בהרט")
assert o == {"K", "T"}, o              # (without the voter map: a 2-2 tie, and City Market's card decides)

# several names per chain: the chain is odd if any of its names is
o, _ = odd({"A": ["מסטיק מנטוס פיור בטעם ענבים", "אורביט"], "B": "מנטוס מסטיק ענבים", "C": "מנטוס פיור ענבים"},
           "מנטוס פיור ענבים")
assert o == {"A"}, o

# the same product, named differently
assert odd({"A": "רד בול משקה אנרגיה פח250", "B": "Red Bull משקה אנרגיה", "C": "RED-BULL", "D": "משקה אנרגיה רדבול",
            "E": "רד בול קלאסי"}, "משקה אנרגיה רד בול")[0] == set()
assert odd({"A": "גבינה לואש קירי ממרח 200 ג'", "B": "לה וואש קירי ממרח 32", "C": "ממרח גבינת לואש קירי",
            "D": "גבינה לוואש קירי ממר", "E": "גבינה לה וואש קירי ספרידי ממרח"}, "גבינה לוואש קירי ממר")[0] == set()
assert odd({"A": "חלב תנובה 3% 1 ליטר", "B": "חלב טרי 3% בקרטון 1 ל"}, "חלב תנובה 3% 1 ליטר")[0] == set()
assert odd({"A": "KNORR", "B": "מרק פטריות קנור"}, "מרק פטריות קנור")[0] == set()
assert odd({"A": "Pampers", "B": "חיתולי פמפרס פרימיום"}, "Pampers")[0] == set()
assert odd({"A": "קאשיו 200", "B": "קשיו מטוגן"}, "קשיו מטוגן")[0] == set()
# fat percentages and model numbers: another number is another product
assert odd({"A": "חלב 3% 1 ליטר", "B": "חלב 1% 1 ליטר", "C": "חלב טרי 3%"}, "חלב 3% 1 ליטר")[0] == {"B"}
k2000 = {"A": "סנוK 2000", "B": "K 2000 סנו ללא ריח", "C": "קוטל חרקים K2000 ללא ריח 750 מל סנו",
         "D": "2000-K אינסקטל 750 מ\"ל", "E": "ללא ר K-1100"}
assert odd(k2000, "סנוK 2000")[0] == {"E"}
# a shelf name that only describes the product by the brand's flavour (Rami Levy for 7UP and Sprite)
seven_up = {"A": "7 UP", "B": "סבן אפ 500 מ'ל", "C": "סבן אפ קול 500 מ\"ל", "D": "גזוז בטעם לימון ליים"}
assert odd(seven_up, "משקה סבן אפ קוול 500") == (set(), None)
assert odd({**seven_up, "E": "גזוז בטעם תפוז"}, "סבן אפ")[0] == {"E"}
# a name cut at 20 characters: its last word is the start of a word
assert odd({"A": "מסטיק מנטוס פיור בטע", "B": "מסטיק מנטוס פיור בטעם ענבים", "C": "מנטוס פיור ענבים 60 גר"},
           "מנטוס פיור ענבים")[0] == set()

# size grades: M eggs aren't L eggs (7290001201589: Super Sapir's M among everyone's L), even when a name with no
# grade ("ביצים") joins them into one group
from app.names import _fold, _sizes  # noqa: E402
eggs = {"SS": "ביצים 12יח' M", "SH": "12 ביצי משק טריות L לסר", "CA": "12 ביצים L  מ.לסר", "RL": "ביצים 12 יח L",
        "CM": ["ביצים", "ביצים L"], "YO": "ביצים 12 יח גדול", "TT": "ביצים אריזון 12 גדול"}
assert odd(eggs, "12 ביצים L  מ.לסר")[0] == {"SS"}, odd(eggs, "12 ביצים L  מ.לסר")
assert odd({k: v for k, v in eggs.items() if k != "SS"}, "12 ביצים L")[0] == set()  # גדול is an L
assert odd({"A": "שקיות אשפה L 100 יח", "B": "שקיות אשפה XL 100 יח", "C": "שקיות אשפה XL"}, "שקיות אשפה XL")[0] == {"A"}
assert odd({"A": "דלי לפופקורן גדול", "B": "דלי לפופקורן בינוני", "C": "דלי לפופקורן בינוני"}, "דלי לפופקורן")[0] == {"A"}
# loose Hebrew words: גדול and ענק are one size; "אקסטרה לארג'" is XL; X L is XL; S/M is both
assert odd({"A": "גפרור ענק", "B": "גפרורים גדול.", "C": "גפרור ענק שמאי"}, "גפרור ענק")[0] == set()
assert odd({"A": "תחתונים סופגים אקסטרה לארג' 12 יח'", "B": "תחתונים XL סופגים 12 יח", "C": "תחתונים סופגים XL"},
           "תחתונים XL")[0] == set()
assert _sizes(_fold("המבורגר 500 גר`X L")) == {"xl"} and _sizes(_fold("תחתון S/M")) == {"s", "m"}
# not grades: a unit after a number, M&M's, Shikma's S+ line, a set of two sizes
for name in ("קוקה קולה 1.5L", "מים 2 L", "M&M'S בוטנים", "M M'S שוקולד חלב", "סופגן שיקמה אקסטרא S+",
             "סט צלחות ענק ובינוני", "סט צלחות סטון עמק+בינוני", "טיטולים מידה 4"):
    assert _sizes(_fold(name)) == frozenset(), (name, _sizes(_fold(name)))
print("ok")
