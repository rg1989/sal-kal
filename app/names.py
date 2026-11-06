"""Do two chains mean the same product by one barcode? Their own names for it must agree (see odd_names)."""
import re
from collections import Counter, defaultdict
from functools import lru_cache

FINALS = str.maketrans("ךםןףץ", "כמנפצ")
STOP = {
    # units and pack words
    "גרם", "גר", "גרמים", "ג", "מל", "מיליליטר", "ליטר", "ליטרים", "ל", "קג", "קילו", "קילוגרם", "יח", "יחי", "יחידות",
    "יחידה", "מארז", "מארזים", "בקבוק", "בקבוקים", "פחית", "פחיות", "שקית", "קופסא", "קופסה", "מטר", "סמ", "oz",
    "ml", "gr", "g", "kg", "l", "x",
    # filler
    "של", "עם", "ללא", "בטעם", "טעם", "כשר", "חדש", "מבצע", "גדול", "קטן", "בודד", "ארוז", "ארוזה", "במשקל",
}
STOP = {w.translate(FINALS) for w in STOP}
PREFIXES = "והבלמשכ"
_FOLD = (("ך", "כ"), ("ם", "מ"), ("ן", "נ"), ("ף", "פ"), ("ץ", "צ"), ('"', ""), ("'", ""), ("`", ""), ("׳", ""),
         ("״", ""))
_TOKEN = re.compile(r"[א-ת]+|[a-zà-öø-ÿ]+|\d+|[^\W\d_a-zà-öø-ÿא-ת]+")  # 198גרם -> 198 גרם, BLUEארוך -> blue ארוך
_SEVEN_UP = re.compile(r"(?<![a-z\d])(?:7|seven) ?up(?![a-z])")


def _fold(name):
    """Lower case, Hebrew final letters folded, geresh and quotes dropped (ג'וי = גוי), 7UP spelled סבנ אפ."""
    s = (name or "").lower()
    for a, b in _FOLD:  # (str.replace is several times faster than str.translate here)
        if a in s:
            s = s.replace(a, b)
    return _SEVEN_UP.sub(" סבנ אפ ", s) if "up" in s else s


def _tokens(folded):
    return [w for w in _TOKEN.findall(folded) if len(w) >= 2 and not w.isdigit() and w not in STOP]


def name_words(name):
    """The words of a product name that say what it is: no punctuation, numbers, units or filler.
    Geresh and quotes are dropped inside words ("ג'וי" = "גוי"), Hebrew final letters folded."""
    return set(_tokens(_fold(name)))


class Generic(frozenset):
    """Words too common to say which product a name means; .df = in how many barcodes' names each word is."""
    df = {}


def generic_words(rows, min_keys=150):
    """Words (as name_words folds them) used in the names of at least min_keys different barcodes: categories,
    big brands, colours, flavours. One of them shared by two names doesn't make them one product.
    rows: (key, name) pairs. The result also carries every word's count (.df), which odd_names uses."""
    seen, df = defaultdict(set), Counter()
    for key, name in rows:
        seen[key].update(_tokens(_fold(name)))
    for ws in seen.values():
        df.update(ws)
    out = Generic(w for w, n in df.items() if n >= min_keys)
    out.df = dict(df)
    return out


@lru_cache(maxsize=200_000)
def _variants(w):
    """The word, without a glued prefix (ו/ה/ב/ל/מ/ש/כ), and in its base form: construct state (ריבת -> ריבה)
    and plurals (עגבניות -> עגבניה, ענבים -> ענב)."""
    out = {w}
    if len(w) >= 4 and w[0] in PREFIXES:
        out.add(w[1:])
    for v in list(out):
        if len(v) >= 4 and v.endswith("ות"):
            out.add(v[:-2] + "ה")
        elif len(v) >= 4 and v.endswith("ת"):
            out.add(v[:-1] + "ה")
        if len(v) >= 5 and v.endswith("ימ"):  # ים with the final letter folded
            out.add(v[:-2])
    return frozenset(out)


# ---------------------------------------------------------------- flavours
# A closed list: two names of one barcode that name different flavours (or scents) are different products.
# The words on a line are one flavour (synonyms: דארק = מריר, טופי = קרמל, ליים = לימון, נענע = מנטה,
# לוז = אגוזי לוז).
_FLAVOURS = """
strawberry: תות תותים strawberry
banana: בננה בננות banana
apple: תפוח תפוחים תפוחי apple
orange: תפוז תפוזים תפוזי orange
lemon: לימון לימונים ליים lemon lime
grape: ענב ענבים grape
watermelon: אבטיח watermelon
melon: מלון
peach: אפרסק אפרסקים peach
apricot: משמש
pineapple: אננס
mango: מנגו mango
passionfruit: פסיפלורה
cherry: דובדבן דובדבנים cherry
raspberry: פטל raspberry
blueberry: אוכמניות אוכמנית אוכמניה blueberry
forest: יער
pomegranate: רימון רימונים
grapefruit: אשכולית אשכוליות
mandarin: מנדרינה מנדרינות מנדרינים קלמנטינה קלמנטינות
plum: שזיף שזיפים
pear: אגס אגסים
fig: תאנה תאנים
coconut: קוקוס coconut
vanilla: וניל vanilla
dark: מריר דארק
caramel: קרמל טופי caramel toffee
mint: מנטה נענע mint
nut: אגוז אגוזים אגוזי לוז פקאן hazelnut
almond: שקד שקדים almond
pistachio: פיסטוק pistachio
peanut: בוטן בוטנים
cashew: קשיו קאשיו
sesame: שומשום
honey: דבש honey
cinnamon: קינמון cinnamon
garlic: שום garlic
onion: בצל onion
barbecue: ברביקיו בארבקיו
paprika: פפריקה
chilli: צילי
mushroom: פטריות פטריה
ginger: גינגר
maple: מייפל
lavender: לבנדר
"""
FLAVOUR = {}
for _line in _FLAVOURS.strip().splitlines():
    _f, _ws = _line.split(":")
    for _w in _ws.split():
        FLAVOUR[_w.translate(FINALS)] = _f.strip()
_NOT_FLAVOUR = re.compile(r"תפוחי?\s*אדמה|תפוא|שקד תבור")  # potato; a brand
# brands that are a flavour: a shelf name that only says the flavour ("גזוז בטעם לימון ליים") is that product
_BRAND_FLAVOUR = [(re.compile(r"ספרייט|sprite|סבנ ?אפ"), frozenset({"lemon"}))]
# brands that name a variant of another brand's product (מילקה אוראו): an own word, but not a flavour
_VARIANT = {"אוראו", "oreo", "לוטוס", "lotus", "נוטלה", "nutella"}
# category words under the generic threshold that describe rather than name a product
_DESCRIPTIVE = {"גזוז", "משקה", "מוגז", "תרכיז", "נקטר"}


@lru_cache(maxsize=200_000)
def _flavour(w):
    for v in _variants(w):
        f = FLAVOUR.get(v)
        if f:
            return f
    return None


# ---------------------------------------------------------------- word matching
# real words that a loose rule (one letter apart, a vowel letter, a prefix, two-consonant English) would join
_DISTINCT = (
    ("תפוזים", "תפוחים"), ("תפוז", "תפוח"), ("תפוזי", "תפוחי"), ("מילקה", "מילקי"), ("קוקוס", "קוסקוס"),
    ("עוגיה", "עוגה"), ("עוגיות", "עוגות"), ("עוגית", "עוגת"), ("ספריי", "ספרייט"), ("לבנה", "לבן"),
    ("חלבה", "חלב"), ("שמנת", "שמן"), ("קרמל", "קרם"), ("שומן", "שום"), ("שומשום", "שום"), ("פריכיות", "פרי"),
    ("לימונית", "לימון"), ("קרמבו", "קרם"), ("pro", "פרה"), ("free", "פרי"), ("fit", "פטה"), ("lite", "לאטה"),
    ("max", "מיקס"), ("mix", "מקס"), ("zero", "שרי"), ("tea", "טיה"),
)
_DISTINCT = {frozenset(w.translate(FINALS) for w in p) for p in _DISTINCT}
# short English brands with too few consonants to compare (Dove / דאב)
_SHORT_BRANDS = {"dove": "דאב", "axe": "אקס", "bic": "ביק", "nivea": "ניואה", "oreo": "אוראו", "omo": "אומו",
                 "fa": "פא", "lux": "לוקס", "ola": "אולה", "up": "אפ", "kit": "קיט", "kat": "קט"}
_SHORT_BRANDS = {k: v.translate(FINALS) for k, v in _SHORT_BRANDS.items()}

# brand names written in English at one chain and in Hebrew at another (KNORR / קנור): compare consonants
_HE = dict(zip("בגדזחטכלמנסעפצקרשת", "-GDZKTKLMNS-PSKRST"))
_LAT = [("sh", "S"), ("ch", "K"), ("ph", "P"), ("th", "T"), ("ck", "K"), ("qu", "K"), ("tz", "S"), ("ts", "S"),
        ("ce", "S"), ("ci", "S"), ("cy", "S"), ("x", "KS")]
_LAT1 = dict(zip("cdfgjklmnpqrstz", "KDPGGKLMNPKRSTZ"))
_LATIN = re.compile(r"[a-z]+")
_HEBREW = re.compile(r"[א-ת]+")


@lru_cache(maxsize=200_000)
def _skeleton(w, shape=False):
    """Consonant classes of an all-Latin or all-Hebrew word (vowel letters, b/v/w and ב/ו dropped: they swap
    across spellings), or None. shape=True keeps b/v/w and ב as B and marks vowels (Latin vowels, א/ו/י, a final
    ה) as V, for short words."""
    if _LATIN.fullmatch(w):
        for a, b in _LAT:
            w = w.replace(a, b.lower() if len(b) == 1 else b)
        out = []
        for ch in w:
            if ch.isupper():
                out.append(ch)
            elif ch in _LAT1:
                out.append(_LAT1[ch])
            elif shape and ch in "bvw":
                out.append("B")
            elif shape and ch in "aeiouy":
                out.append("V")
    elif _HEBREW.fullmatch(w):
        out = []
        for i, ch in enumerate(w):
            c = _HE.get(ch, "")
            if shape and ch == "ב":
                c = "B"
            elif shape and (ch in "אוי" or (ch == "ה" and i == len(w) - 1)):
                c = "V"
            out.append(c)
    else:
        return None
    s = "".join(c for c in out if c and c != "-")
    return re.sub(r"(.)\1+", r"\1", s)


def _cross_script(a, b):
    """True when a Latin and a Hebrew word spell the same consonants (PAMPERS / פמפרס, ELECTRORICE / אלקטרו).
    Three consonants at least; with two, the vowels must line up too (LEON / לאון, RED / רד, but not PRO / פרה)."""
    if a.isascii() == b.isascii():
        return False
    lat, heb = (a, b) if a.isascii() else (b, a)
    if _SHORT_BRANDS.get(lat) in _variants(heb):
        return True
    if len(lat) < 3 or len(heb) < 2:
        return False
    x, y = _skeleton(lat), _skeleton(heb)
    if not x or not y:
        return False
    if min(len(x), len(y)) >= 3:
        return x == y or (min(len(x), len(y)) >= 4 and (x.startswith(y) or y.startswith(x)))
    xs, ys = _skeleton(lat, True), _skeleton(heb, True)
    if not xs or not ys or len(xs.replace("V", "")) < 2 or xs.replace("V", "") != ys.replace("V", ""):
        return False
    it = iter(xs)  # the Hebrew shape is the English one with some vowels left out
    return all(ch in it for ch in ys)


def _same_sound(a, b):
    """Two Hebrew spellings of one word: the same consonants, first and last letters alike, vowel letters
    (א/ו/י) anywhere (סאוור / סוואר, גיאוטו / גיטו, נטחי / נתחי)."""
    if min(len(a), len(b)) < 4 or a.isascii() or b.isascii():
        return False
    x = _skeleton(a)
    if not x or len(x) < 2 or x != _skeleton(b):
        return False
    first = a[0] == b[0] or _HE.get(a[0], a[0]) == _HE.get(b[0], b[0]) != "-"
    last = a[-1] == b[-1] or (a[-1] in "אויה" and b[-1] in "אויה") or _HE.get(a[-1], 1) == _HE.get(b[-1], 2) != "-"
    return first and last


def _one_edit(a, b):
    """True when a and b differ by at most one inserted, deleted or changed letter, or two swapped neighbours."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    if a[i + (len(a) == len(b)):] == b[i + 1:]:
        return True
    return len(a) == len(b) and i + 1 < len(a) and a[i] == b[i + 1] and a[i + 1] == b[i] and a[i + 2:] == b[i + 2:]


def _vowel_letter(a, b):
    """True when the longer word is the shorter one with one more vowel letter (א/ו/י) after its first letter:
    קאשיו / קשיו, מאנר / מנר."""
    if abs(len(a) - len(b)) != 1:
        return False
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    if len(short) < 3 or (len(short) == 3 and not _HEBREW.fullmatch(short)):
        return False
    last = len(long_) - (len(short) == 3)  # מנר / מאנר, but not תות / תותי
    return any(long_[i] in "אוי" and long_[:i] + long_[i + 1:] == short for i in range(1, last))


_wm_cache = {}


def words_match(a, b):
    """Two words are one word: equal after prefix/plural/construct folding, a longer form of a 4+ letter word
    (טוויזלר / טוויזלרס), a one-letter typo in 5+ letters, other vowel letters, or one name in two scripts.
    Real words that only look alike (תפוזים / תפוחים) never match."""
    if a == b:
        return True
    k = (a, b) if a < b else (b, a)
    r = _wm_cache.get(k)
    if r is None:
        if len(_wm_cache) > 2_000_000:
            _wm_cache.clear()
        r = _wm_cache[k] = _words_match(a, b)
    return r


def _words_match(a, b):
    if frozenset((a, b)) in _DISTINCT:
        return False
    if a.isascii() != b.isascii():
        return _cross_script(a, b)
    va, vb = _variants(a), _variants(b)
    if va & vb:
        return True
    for x in va:
        for y in vb:
            if frozenset((x, y)) in _DISTINCT:
                continue
            if min(len(x), len(y)) >= 4 and (x.startswith(y) or y.startswith(x)):
                return True
            if min(len(x), len(y)) >= 5 and _one_edit(x, y):
                return True  # typos: אבוקדו / אבקדו
            if _vowel_letter(x, y) or _same_sound(x, y):
                return True
    return False


@lru_cache(maxsize=200_000)
def _buckets(w):
    """Keys two words must share to have a chance to match (words_match): the first two or last three letters of
    a form, the first two consonant classes, the flavour."""
    out = {"p" + v[:2] for v in _variants(w)} | {"e" + v[-3:] for v in _variants(w) if len(v) >= 5}
    for v in _variants(w):
        sk = _skeleton(v)
        if sk:
            out.add("s" + sk[:2])
    f = _flavour(w)
    if f:
        out.add("f" + f)
    return out


def names_agree(a, b):
    """Two word sets share at least one word. None when either has no word."""
    if not a or not b:
        return None
    return any(words_match(x, y) for x in a for y in b)


# ---------------------------------------------------------------- one name, parsed once
_PCT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
# a number that is not a pack size, count or percentage (a shade, a model, a size in a series): 105, 1003, 731
_BARE = re.compile(r"(?<![\d.,*x×%])(\d{2,5})(?![\d.,%*x×])(?!\s*(?:גר|ג\b|גרם|מל|ל\b|ליטר|קג|יח|ml|gr?\b|kg|l\b|cm|"
                   r"מ\b|סמ|מטר|שקיקים|כמוסות|טבליות|דף|דפים|זוג))")
_UNITS = {"x", "ml", "gr", "g", "kg", "l", "oz", "cm", "mm", "m", "mg", "cc", "lt", "ltr", "gb", "mah", "w", "v"}
# a size grade: eggs, diapers, gloves, bags. M eggs aren't L eggs (7290001201589: "ביצים 12יח' M" at one chain,
# "12 ביצי משק טריות L" at the rest). A letter right after a number is a unit (1.5L, 2 M), not a size.
_SIZE_LAT = re.compile(r"(?<![a-z\d.,&])(?<!\d )(xxl|xl|xs|s|m|l)(?![a-z&+])")  # not M&M, not Shikma's S+ line
_NOT_GRADE = re.compile(r"(?<![a-z])m\s*&?\s*m\s*s?(?![a-z])")  # M&M's, "M M'S"
_SPACED = re.compile(r"(?<![a-z])(x{1,2})\s+l(?![a-z])")        # "X L" = XL
_EXTRA = re.compile(r"(?<![א-ת])(?:אקסטר[הא][\s-]+(אקסטר[הא][\s-]+)?|א\.\s*)לא?רג(?![א-ת])")  # אקסטרה לארג' = XL
_SIZE_HE = re.compile(r"(?<![א-ת])(?:ה|ו)?(קטנ(?:ה|ות|ימ)?|בינוני(?:ת|ות|ימ|יימ)?|גדול(?:ה|ות|ימ)?|ענק(?:ית|יות|יימ|ימ)?|גמבו"
                      r"|מדיומ|לארג|לרג)"
                      r"(?:\s+(מאוד|מאד|במיוחד))?(?![א-ת])")
# The letters are grades; the Hebrew words are looser (shops call one size גדול and ענק alike), so each stands for
# the grades it can mean, and two names clash only when no grade fits both
_SIZE_OF = {"xs": {"xs"}, "s": {"s"}, "m": {"m"}, "l": {"l"}, "xl": {"xl"}, "xxl": {"xxl"},
            "קטנ": {"xs", "s"}, "בינוני": {"m"}, "מדיומ": {"m"}, "לארג": {"l"}, "לרג": {"l"},
            "גדול": {"l", "xl", "xxl"}, "ענק": {"l", "xl", "xxl"}, "גמבו": {"l", "xl", "xxl"}}


def _sizes(low):
    """The size grades a folded name states: S/M/L/XL/XXL ("X L" too), or in Hebrew קטן (XS/S), בינוני / מדיום (M),
    לארג' (L), אקסטרה לארג' (XL), גדול / ענק / ג'מבו (L-XXL), גדול מאוד / במיוחד (XL/XXL). None for a name of two
    sizes ("ענק ובינוני",
    "עמק+בינוני")."""
    out = set()
    if "רג" in low:
        low = _EXTRA.sub(lambda m: " xxl " if m.group(1) else " xl ", low)
    if _SIZE_LAT_ANY.search(low):
        low = _SPACED.sub(r"\1l", _NOT_GRADE.sub(" ", low))
        for m in _SIZE_LAT.finditer(low):
            out |= _SIZE_OF[m.group(1)]
    for m in _SIZE_HE.finditer(low):
        w = m.group(1)
        if m.group(0).startswith("ו") and not w.startswith("ו") or low[max(0, m.start() - 1):m.start()] == "+":
            return frozenset()  # "X and Y": a set of two sizes
        stem = next(k for k in _HE_STEMS if w.startswith(k))
        out |= {"xl", "xxl"} if stem == "גדול" and m.group(2) else _SIZE_OF[stem]
    return frozenset(out)


_HE_STEMS = ("קטנ", "בינוני", "גדול", "ענק", "גמבו", "מדיומ", "לארג", "לרג")


_SIZE_LAT_ANY = re.compile(r"[xslm]")
_CODE = re.compile(r"(?<![a-z\d])([a-z]{1,3})\s*-?\s*(\d{2,5})(?![\d.,])|(?<![\d.,])(\d{2,5})\s*-?\s*([a-z]{1,3})(?![a-z])")
_DIGIT = re.compile(r"\d")
_CUT = {19, 20, 21, 22, 39, 40}  # many chains cut names at 20 or 40 characters: the last word may be cut short


def _num(s):
    s = s.replace(",", ".")
    return s.rstrip("0").rstrip(".") if "." in s else s


class _Name:
    """One shelf name, parsed once: its words, the last word when the name looks cut short, the words that say
    which product it is (spec: not generic, not a flavour), flavours, fat percentages, model codes (K-300), other
    numbers, two neighbouring words run together (רד בול = רדבול)."""
    __slots__ = ("raw", "words", "cut", "size", "spec", "flav", "flav_any", "fl", "bk", "pct", "codes", "nums",
                 "joined", "implied", "grades")

    def __init__(self, raw, generic=frozenset()):
        self.raw = raw
        low = _fold(raw)
        order = _tokens(low)
        self.words = set(order)
        self.cut = None
        s = raw.strip()
        if len(s) in _CUT and order and _tokens(_fold(s[s.rfind(" ") + 1:])) == order[-1:]:
            self.cut = order[-1]
        self.size = len(self.words - {self.cut})
        self.joined = {}
        for i in range(len(order) - 1):
            if order[i].isascii() == order[i + 1].isascii():
                self.joined[order[i] + order[i + 1]] = (order[i], order[i + 1])
        fw = set(_tokens(_NOT_FLAVOUR.sub(" ", low))) if ("תפו" in low or "שקד" in low) else self.words
        self.fl = {w: f for w in fw if (f := _flavour(w))}
        self.flav = frozenset(f for w, f in self.fl.items() if w != self.cut)  # a cut word may be another
        self.flav_any = frozenset(self.fl.values())                             # flavour cut short
        self.bk = defaultdict(list)
        for w in self.words:
            for k in _buckets(w):
                self.bk[k].append(w)
        digits = _DIGIT.search(low)
        self.pct = frozenset(_num(m.group(1)) for m in _PCT.finditer(low)) if "%" in low else frozenset()
        self.grades = _sizes(low)
        codes = set()
        if digits and _LATIN.search(low):
            for m in _CODE.finditer(low):
                letters, num = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
                if letters not in _UNITS:
                    codes.add((letters, num))
        self.codes = frozenset(codes)
        self.nums = frozenset(_BARE.findall(low)) | {c[1] for c in codes} if digits else frozenset()
        self.spec = frozenset(w for w in self.words if w != self.cut and w not in generic and w not in _DESCRIPTIVE
                              and w not in self.fl)
        self.implied = frozenset().union(*(f for r, f in _BRAND_FLAVOUR if r.search(low))) if (
            "ספרייט" in low or "sprite" in low or "סבנ" in low) else frozenset()


_parsed, _parsed_for = {}, [None]


def _parse(raw, generic):
    """_Name(raw), remembered while the generic word set stays the same (names repeat across barcodes)."""
    if _parsed_for[0] is not generic or len(_parsed) > 300_000:
        _parsed.clear()
        _parsed_for[0] = generic
    nm = _parsed.get(raw)
    if nm is None:
        nm = _parsed[raw] = _Name(raw, generic)
    return nm


AGREE, WEAK, NONE, CLASH = 3, 2, 1, 0


def _link(a, b, generic=frozenset()):
    """How two names of one barcode relate:
    CLASH  they name different flavours, fat percentages, size grades (M / L, בינוני / גדול) or model numbers
           (K-300 / K-400);
    AGREE  the same words, or two shared words (a shared number that isn't a pack size counts), a model code or a
           fat percentage, or one says nothing the other doesn't, or one shared word unless the next rule holds;
    NONE   nothing in common, or two short names sharing one word that each add their own rarer word: two variants
           of a brand or a category (עוגת סוזנה / עוגת דנדי, מילקה מומנטס / מילקה מיני אוראו);
    WEAK   one name is only generic words (ריבה) the other has: compatible, but it links nobody else."""
    if a.flav and b.flav and not (a.flav_any & b.flav_any):
        return CLASH
    if a.pct and b.pct and not (a.pct & b.pct):
        return CLASH
    if a.grades and b.grades and not (a.grades & b.grades):  # M eggs / L eggs, size 4 / size 5 bags
        return CLASH
    codes = a.codes & b.codes
    if a.codes and b.codes and not codes and {c[0] for c in a.codes} & {c[0] for c in b.codes}:
        return CLASH
    ma, mb, pairs = set(), set(), []
    for k in a.bk.keys() & b.bk.keys():
        for x in a.bk[k]:
            for y in b.bk[k]:
                if (x not in ma or y not in mb) and (
                        words_match(x, y) or (k[0] == "f" and a.fl.get(x) == b.fl.get(y))):
                    ma.add(x)
                    mb.add(y)
                    pairs.append((x, y))
    for p, q, mp, mq in ((a, b, ma, mb), (b, a, mb, ma)):
        if p.cut and p.cut not in mp:  # a word cut short matches the words it starts
            heads = (p.cut, p.cut[1:]) if len(p.cut) >= 3 and p.cut[0] in PREFIXES else (p.cut,)
            hit = [y for y in q.words if any(v.startswith(h) for v in _variants(y) for h in heads)]
            if hit:
                mp.add(p.cut)
                mq.update(hit)
                pairs.extend((p.cut, y) for y in hit)
        if q.joined:
            for x in p.words - mp:  # רדבול / רד בול, PALL MALL / פלמאל
                for j, two in q.joined.items():
                    if words_match(x, j):
                        mp.add(x)
                        mq.update(two)
                        pairs.append((x, two[0]))
                        break
    if not ma and not codes:
        return NONE  # a shared number or percentage alone says nothing
    strong = codes or (a.pct & b.pct)
    n = min(len(ma), len(mb)) + len(codes) + len(a.pct & b.pct)
    ua = a.words - ma - {a.cut}
    ub = b.words - mb - {b.cut}
    if not ua and not ub:
        return AGREE
    if not ua or not ub:
        small = a if not ua else b
        return AGREE if (small.spec or strong or n >= 2) else WEAK
    if strong or n + len(a.nums & b.nums) >= 2 or min(a.size, b.size) > 3:
        return AGREE
    df = getattr(generic, "df", None)
    if df:
        common = min(max(df.get(x, 0), df.get(y, 0)) for x, y in pairs)
        rare = lambda w: w not in generic and df.get(w, 0) * 2 < common  # noqa: E731
    else:
        common = all(x in generic and y in generic or x in a.fl for x, y in pairs)
        rare = lambda w: common and w not in generic  # noqa: E731
    own_a = [w for w in ua if _own(w, b, rare)]
    own_b = [w for w in ub if _own(w, a, rare)]
    if own_a and own_b and any(x.isascii() == y.isascii() for x in own_a for y in own_b):
        return NONE  # (an English word against a Hebrew one may be the same brand spelled apart: not enough)
    return AGREE


_PACK = {"חבילה", "חבילת", "שלישיה", "שלישייה", "שלישיית", "רביעיה", "רביעייה", "רביעיית", "שישיה", "שישייה",
         "שישיית", "שמיניה", "שמיניית", "זוג", "צמד", "סט", "ערכת", "ערכה"}


@lru_cache(maxsize=200_000)
def _near(a, b):
    """Two words of one script at most two letters apart (גמביט / גביטה, אמרט / אמרנט): maybe one word misspelt."""
    if a.isascii() != b.isascii() or min(len(a), len(b)) < 3 or abs(len(a) - len(b)) > 2 or (
            a[0] != b[0] and a[-1] != b[-1]):
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > 2:
            return False
        prev = cur
    return prev[-1] <= 2


def _own(w, other, rare):
    """A word of one name that names its own variant against the other name: a flavour, a variant brand, or a
    rare word that isn't a pack word, a shortening of the other name's words (תפו / תפוציפס) or a misspelling
    of one (גאלאקסי / גלקסי)."""
    if not (_flavour(w) is not None or w in _VARIANT or (len(w) >= 3 and rare(w))) or w in _PACK:
        return False
    return not any(y.startswith(w) or w.startswith(y) or _near(w, y) for y in other.words)


def odd_names(names, card_name="", generic=frozenset(), voter=None):
    """Chains whose name for one barcode doesn't agree with the rest.

    names: {chain_id: name or list of names}. voter: {chain_id: retailer}; a retailer is one vote however many
    chain ids it publishes under. generic: words too common to identify a product (generic_words).
    Names are linked pairwise (_link); the consensus is the largest group, in retailers, of names linked by AGREE.
    Inside it, the flavour most of its retailers name wins. A name outside it is still fine when it is only
    generic words that a fine name has (WEAK), or only describes the product by a flavour the consensus has. With
    no consensus (no two retailers agree, or two equal groups) the product card's name decides. A chain is odd
    when any of its names is. Names with no usable words are not judged.
    Returns (odd chain ids, a name from the consensus to show on the card, or None to keep card_name)."""
    voter = voter or {}
    nodes, by_raw = [], {}  # one node per distinct name: [parsed name, chain ids, voters]
    for ch, ns in names.items():
        for raw in ([ns] if isinstance(ns, str) else ns):
            raw = raw or ""
            nd = by_raw.get(raw)
            if nd is None:
                nm = _parse(raw, generic)
                if not nm.words and not nm.codes:
                    continue
                nd = by_raw[raw] = [nm, set(), set()]
                nodes.append(nd)
            nd[1].add(ch)
            nd[2].add(voter.get(ch, ch))
    if len(nodes) < 2:
        return set(), None
    n = len(nodes)
    rel = {}

    def link(i, j):
        k = (i, j) if i < j else (j, i)
        r = rel.get(k)
        if r is None:
            r = rel[k] = _link(nodes[k[0]][0], nodes[k[1]][0], generic)
        return r

    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if root(i) != root(j) and link(i, j) == AGREE:
                parent[root(i)] = root(j)
    by_root = defaultdict(list)
    for i in range(n):
        by_root[root(i)].append(i)
    votes = lambda g: len(set().union(*(nodes[i][2] for i in g)))  # noqa: E731
    groups = sorted(by_root.values(), key=votes, reverse=True)
    card = _Name(card_name or "", generic)
    has_card = bool(card.words or card.codes)
    top = votes(groups[0])
    if len(groups) > 1 and (top < 2 or votes(groups[1]) == top):  # no consensus: the card's name decides
        if not has_card:
            return set(), None
        return _chains(nodes, {i for i in range(n) if _link(nodes[i][0], card, generic) >= WEAK}), None
    cons = set(groups[0])
    win = _winning_flavours([nodes[i] for i in cons], card)
    if win is not None:
        cons = {i for i in cons if not nodes[i][0].flav or nodes[i][0].flav_any & win}
    # a name with no grade ("ביצים") can join M and L names into one group: the grade most of it names wins
    grade = _winning_grades([nodes[i] for i in cons], card)
    if grade is not None:
        cons = {i for i in cons if not nodes[i][0].grades or nodes[i][0].grades & grade}
    good = set(cons)
    known = set(win or ()).union(*(nodes[i][0].implied | nodes[i][0].flav for i in cons))
    rest = [i for i in range(n) if i not in groups[0]
            and not (nodes[i][0].flav and win is not None and not nodes[i][0].flav_any & win)
            and not (nodes[i][0].grades and grade is not None and not nodes[i][0].grades & grade)]
    for i in rest:
        nm = nodes[i][0]
        if any(link(i, j) == WEAK for j in cons):
            good.add(i)  # "ריבה" next to "ריבת תות שדה"
        elif nm.flav and not nm.spec and not nm.codes and nm.flav <= known:
            good.add(i)  # "גזוז בטעם לימון ליים" next to "ספרייט"
    grew = True
    while grew:  # a name of generic words only is fine next to a name found fine ("נקניק" / "בלונים נקניק")
        grew = False
        for i in rest:
            if i not in good and not nodes[i][0].spec and not nodes[i][0].codes and any(
                    link(i, j) >= WEAK for j in good):
                good.add(i)
                grew = True
    better = None
    if has_card and cons and (not any(_link(nodes[i][0], card, generic) >= WEAK for i in cons)
                              or (card.flav and win is not None and not card.flav_any & win)):
        better = _best(nodes, cons)
    return _chains(nodes, good), better


def _best(nodes, ids):
    """The name to show on the card: the one most retailers use, not cut short, with the most words."""
    return nodes[max(ids, key=lambda i: (len(nodes[i][2]), nodes[i][0].cut is None, len(nodes[i][0].words)))][0].raw


def _winning_flavours(group, card):
    """The flavours that win inside a consensus group: those named by more than half of the retailers that name
    one; with no majority, the card's; else those more than one retailer names. None: no flavour named."""
    fv = defaultdict(set)
    for nm, _, voters in group:
        for f in nm.flav_any:
            fv[f] |= voters
    if not any(nm.flav for nm, _, _ in group):
        return None
    flavoured = set().union(*(voters for nm, _, voters in group if nm.flav_any))
    win = {f for f, v in fv.items() if len(v) * 2 > len(flavoured)}
    return frozenset(win or (card.flav & set(fv)) or {f for f, v in fv.items() if len(v) > 1})


def _winning_grades(group, card):
    """The size grades that win inside a consensus group: those named by more than half of the retailers that name
    one; with no majority, the card's; else those more than one retailer names. None: no grade named."""
    gv = defaultdict(set)
    for nm, _, voters in group:
        for g in nm.grades:
            gv[g] |= voters
    if not gv:
        return None
    graded = set().union(*(voters for nm, _, voters in group if nm.grades))
    win = {g for g, v in gv.items() if len(v) * 2 > len(graded)}
    return frozenset(win or (card.grades & set(gv)) or {g for g, v in gv.items() if len(v) > 1}) or None


def _chains(nodes, good):
    """Chains with a name outside good."""
    return set().union(*(nodes[i][1] for i in range(len(nodes)) if i not in good))
