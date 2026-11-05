"""Shop categories and curated lists.

The price files carry no categories, so a category is the set of words a product's
name STARTS with ("חלב…", "גבינה…" → Dairy). Matching the first word keeps "שוקולד חלב"
(milk chocolate) out of Dairy. Products whose name starts with a pack size ("12 ביצים")
are matched through `contains` words instead.
"""

CATEGORIES = [
    {"slug": "dairy", "name": "Dairy & eggs", "he": "חלב וביצים", "icon": "🥛", "tint": "#E3EEF8",
     "starts": ["חלב", "גבינה", "גבינת", "קוטג", "יוגורט", "יופלה", "דנונה", "מעדן", "שמנת", "חמאה", "לבן!", "אשל",
                "גיל!", "ביצים", "ביצה", "שוקו!", "מילקי", "בולגרית", "צפתית", "לאבנה", "ריקוטה", "מוצרלה", "פודינג"],
     "contains": ["ביצים"]},
    {"slug": "bakery", "name": "Bread & bakery", "he": "לחם ומאפים", "icon": "🍞", "tint": "#F6EBDD",
     "starts": ["לחם", "לחמני", "פיתה", "פיתות", "חלה!", "חלות", "באגט", "טורטיה", "לאפה", "בגט", "קרואסון", "מאפה",
                "בורקס", "עוגה", "עוגת", "רוגלך", "פרוסות", "כעך", "בייגל"]},
    {"slug": "produce", "name": "Fruit & veg", "he": "פירות וירקות", "icon": "🥬", "tint": "#E4F2E0",
     "starts": ["עגבני", "מלפפון", "בצל", "שום", "תפוח", "תפוחי", "בננה", "בננות", "גזר", "פלפל", "חסה", "תפוז",
                "לימון", "אבוקדו", "קישוא", "חציל", "כרוב", "ענבים", "אבטיח", "מלון", "אגס", "תות", "פטרוזיליה",
                "כוסברה", "שמיר", "נענע", "סלק", "בטטה", "דלעת", "קלמנטינה", "אשכולית", "מנגו", "שזיף", "אפרסק",
                "נקטרינה", "רימון", "קיווי", "פטריות", "ברוקולי", "כרובית", "צנון", "סלרי", "תירס טרי"]},
    {"slug": "meat", "name": "Meat, chicken & fish", "he": "בשר, עוף ודגים", "icon": "🍗", "tint": "#F7E3E0",
     "starts": ["עוף", "חזה", "שניצל", "כרעיים", "שוקיים", "פרגית", "כנפיים", "בשר", "טחון", "סטייק", "אנטריקוט",
                "צלי!", "נקניק", "נקניקיות", "קבב", "המבורגר", "פסטרמה", "דג!", "דגי", "סלמון", "אמנון", "דניס",
                "לברק", "פילה", "הודו", "שווארמה"]},
    {"slug": "pantry", "name": "Pantry", "he": "מזווה ובישול", "icon": "🥫", "tint": "#F3EED9",
     "starts": ["אורז", "פסטה", "ספגטי", "פנה", "קמח", "סוכר", "מלח", "שמן", "רסק", "רוטב", "טונה", "תירס",
                "חומוס", "טחינה", "קטשופ", "מיונז", "חרדל", "אטריות", "פתיתים", "קוסקוס", "עדשים", "שעועית",
                "אפונה", "זיתים", "מלפפונים", "חמוצים", "תבלין", "פפריקה", "כמון", "פלפל שחור", "אבקת מרק", "מרק",
                "שקדי מרק", "פירורי לחם", "שמרים", "אבקת אפייה", "ונילין", "חומץ", "דבש", "ריבה"]},
    {"slug": "breakfast", "name": "Coffee, tea & breakfast", "he": "קפה, תה וארוחת בוקר", "icon": "☕", "tint": "#EFE5DC",
     "starts": ["קפה", "נס קפה", "נסקפה", "תה!", "קורנפלקס", "דגני", "גרנולה", "שיבולת", "קוואקר", "ממרח",
                "נוטלה", "חלבה", "קפסולות", "פריכיות"]},
    {"slug": "snacks", "name": "Snacks & sweets", "he": "חטיפים ומתוקים", "icon": "🍫", "tint": "#F4E4EC",
     "starts": ["במבה", "ביסלי", "צ'יפס", "ציפס", "תפוצ", "דוריטוס", "שוקולד", "עוגיות", "עוגיה", "ופל", "וופל",
                "מסטיק", "סוכריות", "סוכריה", "חטיף", "קרקר", "בייגלה", "פופקורן", "אגוזי", "בוטנים", "גרעינים",
                "פסק זמן", "קליק", "כיף כף", "טעמי", "אפרופו", "מקופלת", "קרמבו", "לקקן", "מארז"]},
    {"slug": "drinks", "name": "Drinks", "he": "משקאות", "icon": "🥤", "tint": "#E0F0F1",
     "starts": ["מים!", "קולה", "קוקה", "ספרייט", "פאנטה", "משקה", "מיץ", "סודה", "בירה", "יין!", "נביעות", "פריגת",
                "תפוזינה", "שוופס", "פפסי", "XL!", "רד בול", "נסטי", "פיוז", "ענבים אדום", "ליקר", "וודקה", "ערק",
                "סיידר", "לימונדה", "תרכיז", "פטל"]},
    {"slug": "frozen", "name": "Frozen", "he": "קפואים", "icon": "🧊", "tint": "#E6ECF8",
     "starts": ["גלידה", "שלגון", "ארטיק", "פיצה", "מלאווח", "ג'חנון", "בצק", "ירקות קפואים", "אפונה קפואה",
                "צ'יפס קפוא", "נאגטס", "קציצות"],
     "contains": ["קפוא", "קפואה", "קפואים"]},
    {"slug": "cleaning", "name": "Cleaning & home", "he": "ניקיון ובית", "icon": "🧽", "tint": "#E2F1EC",
     "starts": ["נוזל כלים", "נוזל לניקוי", "אבקת כביסה", "ג'ל כביסה", "מרכך כביסה", "אקונומיקה", "סנו", "פיירי",
                "נייר טואלט", "נייר סופג", "מגבות נייר", "שקיות", "נייר אלומיניום", "ניילון נצמד", "מטליות",
                "ספוג", "מגבונים לניקוי", "טבליות למדיח", "מבשם", "אנטי קאלק", "מסיר שומנים", "כביסה", "ממחטות",
                "סכו\"ם", "צלחות", "כוסות חד"]},
    {"slug": "personal", "name": "Toiletries & pharm", "he": "טיפוח ופארם", "icon": "🧴", "tint": "#EFE6F6",
     "starts": ["שמפו", "מרכך שיער", "סבון", "תחליב", "משחת שיניים", "מברשת שיניים", "דאודורנט", "קרם", "תחבושות",
                "טמפונים", "גילוח", "סכיני גילוח", "ג'ל רחצה", "מי פה", "צמר גפן", "ויטמין", "אקמול", "נורופן",
                "פלסטר", "קונדישנר", "ספריי"]},
    {"slug": "baby", "name": "Baby", "he": "תינוקות", "icon": "🍼", "tint": "#FBEFE0",
     "starts": ["חיתולים", "חיתול", "האגיס", "פמפרס", "מטרנה", "סימילאק", "נוטרילון", "מגבונים", "דייסה", "בייבי",
                "מוצץ", "בקבוק לתינוק"]},
]

# Well-known Israeli products; each resolves to the most widely sold match in the price files.
CLASSICS = ["במבה קלאסי 80", "ביסלי גריל", "קוטג 5%", "חלב תנובה בקרטון 3%", "שוקו תנובה", "פתיתים אפויים",
            "חומוס אחלה", "טחינה גולמית", "נס קפה עלית", "מילקי", "שוקולד פרה חלב", "קרמבו", "לחם אחיד",
            "גבינה לבנה 5%", "קטשופ", "פסק זמן", "עגבניה", "מלפפון"]


def fts_query(cat):
    """FTS5 query: name starts with any `starts` word, or contains any `contains` word."""
    q = lambda w: '"' + w.rstrip("!").replace('"', '""') + '"'
    # "word!" = that exact word ("שוקו" must not match "שוקולד"); multi-word phrases are exact too
    parts = [f"^{q(w)}" if w.endswith("!") or " " in w else f"^{q(w)}*" for w in cat["starts"]]
    parts += [f"{q(w)}*" for w in cat.get("contains", [])]
    return "name : (" + " OR ".join(parts) + ")"


def public(cat):
    return {k: cat[k] for k in ("slug", "name", "he", "icon", "tint")}


# ---------------------------------------------------------------- refine facets
import re
from collections import Counter

# words that never narrow anything down: Hebrew particles, packaging and unit words
STOP = set("""עם של ב ל ו ה את על או גם בטעם טעם מארז אריזה אריזת יח יח' יחידות יחידה גרם גרמים גר גר' ג ג' מל מ"ל
ליטר ליטרים ל' ק"ג קג קילו ס"מ סמ חב' בע"מ שקית קופסה קופסת בקבוק פחית פחיות זוג סה"כ חדש חדשה מבצע כ ק""".split())
WORD_RX = re.compile(r"[A-Za-zא-ת][A-Za-zא-ת'\"״׳\-]*")
SIZE_RX = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ק\"ג|קג|קילו|גרם|גר'?|ג'|ג(?![א-ת])|מ\"ל|מל(?![א-ת])|ליטר|ל'|ל(?![א-ת])|kg|ml|gr|g|l)(?![A-Za-z])", re.I)
UNITS = {"ק\"ג": "kg", "קג": "kg", "קילו": "kg", "kg": "kg", "קילוגרם": "kg", "גרם": "g", "גרמים": "g", "גר": "g", "גר'": "g",
         "ג'": "g", "ג": "g", "g": "g", "gr": "g", "מ\"ל": "ml", "מל": "ml", "מל'": "ml", "קילוגרמים": "kg", "ml": "ml", "מיליליטר": "ml", "ליטר": "L",
         "ליטרים": "L", "ל'": "L", "ל": "L", "l": "L"}


def size_of(name, quantity=None, unit=None):
    """Package size as a short label ("400 g", "1.5 L"), from the name or the quantity fields; None if unknown."""
    m = SIZE_RX.search(name or "")
    if m:
        n, u = float(m.group(1).replace(",", ".")), UNITS.get(m.group(2).lower())
    else:
        try:
            n = float(quantity)
        except (TypeError, ValueError):
            return None
        u = UNITS.get((unit or "").strip())
    if not u or n <= 0:
        return None
    if u == "g" and n >= 1000:
        n, u = n / 1000, "kg"
    if u == "ml" and n >= 1000:
        n, u = n / 1000, "L"
    return f"{n:g} {u}"


def words_of(name):
    out = set()
    for w in WORD_RX.findall(name or ""):
        w = w.strip("'\"״׳-").lower()
        if len(w) >= 2 and w not in STOP:
            out.add(w)
    return out


def refine_words(items, terms, limit=18):
    """Words that split the current results: brands, flavours, styles. Excludes the user's own words,
    words in (almost) every result, and one-offs."""
    n = len(items)
    if n < 4:
        return []
    have = [t.lower() for t in terms]
    seen = lambda w: any(w.startswith(t) or t.startswith(w) for t in have)
    counts = Counter(w for d in items for w in words_of(d["name"]))
    return [{"word": w, "n": c} for w, c in counts.most_common(80)
            if 2 <= c <= n * 0.85 and not seen(w)][:limit]
