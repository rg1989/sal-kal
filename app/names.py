"""Do two chains mean the same product by one barcode? Their own names for it must agree (see odd_names)."""
import re

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


def name_words(name):
    """The words of a product name that say what it is: no punctuation, numbers, units or filler.
    Geresh and quotes are dropped inside words ("ג'וי" = "גוי"), Hebrew final letters folded."""
    s = (name or "").lower().translate(FINALS)
    s = re.sub(r"[\"'`׳״]", "", s)
    s = re.sub(r"(?<=\d)(?=[^\W\d_])|(?<=[^\W\d_])(?=\d)", " ", s)  # 198גרם -> 198 גרם
    s = re.sub(r"(?<=[a-z])(?=[א-ת])|(?<=[א-ת])(?=[a-z])", " ", s)   # BLUEארוך -> blue ארוך
    s = re.sub(r"[^\w\s]|_", " ", s)
    return {w for w in s.split() if not w.isdigit() and len(w) >= 2 and w not in STOP}


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
    return out


# brand names written in English at one chain and in Hebrew at another (KNORR / קנור): compare consonants
_HE = dict(zip("בגדזחטכלמנסעפצקרשת", "-GDZKTKLMNS-PSKRST"))
_LAT = [("sh", "S"), ("ch", "K"), ("ph", "P"), ("th", "T"), ("ck", "K"), ("qu", "K"), ("tz", "S"), ("ts", "S"),
        ("ce", "S"), ("ci", "S"), ("cy", "S"), ("x", "KS")]
_LAT1 = dict(zip("cdfgjklmnpqrstz", "KDPGGKLMNPKRSTS"))


def _skeleton(w):
    """Consonant classes of an all-Latin or all-Hebrew word (vowel letters, b/v/w and ב/ו dropped: they swap
    across spellings), or None."""
    if re.fullmatch(r"[a-z]+", w):
        for a, b in _LAT:
            w = w.replace(a, b.lower() if len(b) == 1 else b)
        out = [_LAT1.get(ch, ch.upper() if ch.isupper() else "") if ch.islower() else ch for ch in w]
    elif re.fullmatch(r"[א-ת]+", w):
        out = [_HE.get(ch, "") for ch in w]
    else:
        return None
    s = "".join(c for c in out if c and c != "-")
    return re.sub(r"(.)\1+", r"\1", s)


def _cross_script(a, b):
    """True when a Latin and a Hebrew word spell the same consonants (PAMPERS / פמפרס, ELECTRORICE / אלקטרו)."""
    if len(a) < 3 or len(b) < 3 or (a.isascii() == b.isascii()):
        return False
    x, y = _skeleton(a), _skeleton(b)
    if not x or not y or min(len(x), len(y)) < 2:
        return False
    return x == y or (min(len(x), len(y)) >= 4 and (x.startswith(y) or y.startswith(x)))


def _one_edit(a, b):
    """True when a and b differ by at most one inserted, deleted or changed letter."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i + (len(a) == len(b)):] == b[i + 1:]


def _vowel_letter(a, b):
    """True when the longer word is the shorter one with one more vowel letter (א/ו/י): קאשיו / קשיו."""
    if len(a) == len(b) or abs(len(a) - len(b)) != 1:
        return False
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return len(short) >= 4 and any(long_[i] in "אוי" and long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def words_match(a, b):
    if _cross_script(a, b):
        return True
    for x in _variants(a):
        for y in _variants(b):
            if x == y:
                return True
            if min(len(x), len(y)) >= 3 and (x.startswith(y) or y.startswith(x)):
                return True  # טוויזלר / טוויזלרס
            if min(len(x), len(y)) >= 5 and _one_edit(x, y):
                return True  # typos: אבוקדו / אבקדו
            if _vowel_letter(x, y):
                return True
    return False


def names_agree(a, b):
    """Two names share at least one word that says what the product is. None when either has no such word."""
    if not a or not b:
        return None
    return any(words_match(x, y) for x in a for y in b)


def odd_names(names, card_name=""):
    """Chains whose name for one barcode doesn't agree with the rest.

    names: {chain_id: name}. The consensus is the largest group of chains linked by agreeing names. When no two
    chains agree, the name shown on the product card decides. Chains whose name has no usable words are not judged.
    Returns (odd chain ids, a name from the consensus to show on the card or None to keep card_name)."""
    words = {c: name_words(n) for c, n in names.items()}
    judged = [c for c, w in words.items() if w]
    if len(judged) < 2:
        return set(), None
    parent = {c: c for c in judged}

    def root(c):
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    def join(a, b):
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[ra] = rb

    # exact words first (one pass over a word -> chain index), then the looser match between what's left apart
    owner = {}
    for c in judged:
        for w in words[c]:
            for v in _variants(w):
                if v in owner:
                    join(c, owner[v])
                else:
                    owner[v] = c

    def grouped():
        g = {}
        for c in judged:
            g.setdefault(root(c), []).append(c)
        return list(g.values())

    groups = grouped()
    if len(groups) > 1:
        gw = [set().union(*(words[c] for c in g)) for g in groups]
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if root(groups[i][0]) != root(groups[j][0]) and names_agree(gw[i], gw[j]):
                    join(groups[i][0], groups[j][0])
        groups = grouped()
    biggest = max(groups, key=len)
    card = name_words(card_name)
    if len(biggest) < 2:  # nobody agrees with anybody: trust the chains that agree with the card's name
        if not card:
            return set(), None
        keep = {c for c in judged if names_agree(words[c], card)}
        return set(judged) - keep, None
    others = [g for g in groups if g is not biggest]
    if any(len(g) == len(biggest) for g in others):  # two equal camps: no consensus; the card decides
        keep = {c for c in judged if card and names_agree(words[c], card)}
        return set(judged) - keep, None
    odd = set(judged) - set(biggest)
    better = None
    if card and not any(names_agree(card, words[c]) for c in biggest):
        better = max((names[c] for c in biggest), key=lambda n: len(name_words(n)))
    return odd, better


