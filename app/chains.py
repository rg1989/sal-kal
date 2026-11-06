"""Chain names, delivery-fee defaults, and what kind of store each Stores-file entry is.

Chain IDs come from the official Price Transparency files (same IDs the
OpenIsraeliSupermarkets scraper uses). Delivery fees are not published in
those files, so the defaults below come from press reports (ynet, Calcalist,
ice.co.il, nativ.org.il, June-July 2026) and are editable in the app.
"""
import re

CHAIN_NAMES = {
    "7290027600007": "Shufersal",
    "7290058140886": "Rami Levy",
    "7290696200003": "Victory",
    "7290058103393": "Victory",
    "7290803800003": "Yohananof",
    "7290103152017": "Osher Ad",
    "7290873255550": "Tiv Taam",
    "7290700100008": "Hazi Hinam",
    "7290785400000": "Keshet Teamim",
    "7290172900007": "Super-Pharm",
    "7290058108879": "King Store",
    "7290055700007": "Carrefour",
    "7290725900003": "Yeinot Bitan",
    "7290661400001": "Mahsanei HaShuk",
    "7290633800006": "Mahsanei HaShuk",
    "7290644700005": "Yellow",
    "7290058249350": "Wolt Market",
    "7290876100000": "Fresh Market",
    "7290058197699": "Good Pharm",
    "7290526500006": "Salach Dabach",
    "7290058173198": "Zol Ubegadol",
    "7290492000005": "Dor Alon",
    "7290875100001": "Bareket",
    "7291059100008": "Polizer",
    "7290058198450": "Super Yuda",
    "7290058177776": "Super Yuda",
    "7290058156016": "Super Sapir",
    "7290058148776": "Shuk HaIr",
    "7290058160839": "Netiv HaHesed",
    "7290058159628": "Maayan 2000",
    "7290058134977": "Shefa Birkat Hashem",
    "7290455000004": "H. Cohen",
    "7290000000003": "City Market",
    "0000000000000": "City Market",            # City Market Shops also publishes under an all-zero id
    "7290058266241": "City Market (main company)",  # ChainName "סיטי מרקט חברה ראשית": branches across the country
    "7290058288090": "City Market (main company)",  # same store ids as 7290058266241
    "5144744100002": "Mishnat Yosef",
    "7290058289400": "Mishnat Yosef",           # its KT Market branches (קי טי יבוא ושיווק)
    "7290639000004": "Stop Market",
}

# fee: standard delivery fee (ILS)
# reduced_fee / reduced_above: lower fee once the basket passes a threshold
# free_above: free delivery from this basket total
# min_order: minimum basket for delivery (None = unknown)
# delivers: False = chain offers pickup only
DELIVERY_DEFAULTS = {
    "7290027600007": {"fee": 35.90, "note": "Raised to 35.90 in July 2026"},
    "7290058140886": {"fee": 35.90, "note": "Raised to 35.90 in 2026"},
    "7290696200003": {"fee": 35.90, "min_order": 250, "note": "Raised to 35.90 in June 2026"},
    "7290058103393": {"fee": 35.90, "min_order": 250, "note": "Raised to 35.90 in June 2026"},
    "7290055700007": {"fee": 29.90, "note": "29.90 as of July 2026"},
    "7290785400000": {"fee": 29.90, "reduced_fee": 14.90, "reduced_above": 750,
                      "note": "14.90 above 750 (July 2026)"},
    "7290873255550": {"fee": 29.90, "free_above": 750, "note": "Free above 750 (July 2026)"},
    "7290700100008": {"fee": 29.90, "note": "29.90 as of June 2026"},
    "7290803800003": {"delivers": False, "note": "Pickup only as of June 2026"},
}

# Store names that indicate an online/delivery store when StoreType is missing.
ONLINE_HINTS = ("אונליין", "online", "אינטרנט", "משלוחים", "ONLINE", "Online", "e-commerce", "דיגיטל")

WOLT_MARKET = "7290058249350"
# Some chains publish their branches' listings on the Wolt app as StoreType 1 stores named "וולט …" (Super Sapir 130
# "וולט ויצמן חולון" sits at the address of its shop 190 and is dearer on 1,145 of 1,188 shared items, 1.42x on
# average): the app's prices, not a door you walk into, nor the chain's own web shop
APP_LISTING = re.compile(r"וולט|wolt", re.I)
ONLINE_NAME = re.compile(r"אונליין|און[\s-]*ליין|online|e-commerce", re.I)  # an online store whatever its StoreType
WAREHOUSE = re.compile(r"מחסן")  # with ONLINE_NAME: Super Sapir's "מחסן אונליין שדרות" warehouses
NOT_A_SHOP = re.compile(r"\btest\b|\bclosed\b|\(סגור\)", re.I)  # Wolt Market's "Test Venue", "Marlog Atlas (CLOSED)"
WEB_WORDS = {"אונליין", "און", "ליין", "online", "אינטרנט", "משלוחים", "משלוחי", "אתר", "סחר", "www", "co", "il",
             "e", "commerce", "דיגיטל", "ליקוט", "shop"}


def store_kind(chain_id, name, store_type, sub_chain=None):
    """What a Stores-file entry is: "shop" (a branch you can walk into; StoreType 3 also takes online orders),
    "online" (the chain's web shop or an online warehouse), "listing" (a delivery app's listing of a branch: Wolt's
    prices, not the chain's), "other" (another brand's shop in the chain's file: Carrefour's "@ יהלומים ביתן" is
    Yeinot Bitan's web shop, "כפר סבא @ קוויק" Quik's), "none" (a test or closed venue)."""
    n = name or ""
    if NOT_A_SHOP.search(n):
        return "none"
    if str(chain_id) != WOLT_MARKET and APP_LISTING.search(n):
        return "listing"
    if "@" in n:
        after = n.split("@", 1)[1]
        own = [w for w in (BRAND_HE.get(chain_name(chain_id), "") or "").split() if len(w) > 1]
        if own and not any(w in after for w in own):
            return "other"
    if store_type == 2 or ONLINE_NAME.search(n):
        return "online"
    if store_type is None and any(h in f"{n} {sub_chain or ''}" for h in ONLINE_HINTS):
        return "online"
    return "shop"


PLACEHOLDER = {"unknown", "0", "-", "לא ידוע", "none", "null", "h"}


def clean_place(text):
    """An address or town as a store card can show it: nothing for "unknown", "0" or a lone letter, no URL fragments
    ("דרך הדרום http:// 1" is dropped whole; "חורב 15 | www.kulinarik.co.il/|" keeps "חורב 15"), no house number 0."""
    parts = [p.strip() for p in str(text or "").split("|")]
    parts = [p for p in parts if p and not re.search(r"https?:|www\.|\.co\.il|\.com\b", p, re.I)]
    out = " ".join(" ".join(parts).split())
    out = re.sub(r"\s+0$", "", out).strip(" ,")
    return "" if out.lower() in PLACEHOLDER or len(out) < 2 else out


def sub_brand(name):
    """A SubChainName that names a brand: not empty, a number ("1", "000") or a placeholder."""
    b = " ".join(str(name or "").split())
    return b if b and not b.isdigit() and b.lower() not in PLACEHOLDER else ""


def walk_in(chain_id, name, store_type, sub_chain=None):
    """A branch you can walk into: on the map, ranked in store, priced on cards, along the way."""
    return store_kind(chain_id, name, store_type, sub_chain) == "shop"


def general_web_shop(chain_id, name):
    """A web shop whose name says no place: only its brand and words like אונליין / אתר סחר ("97 אינטרנט",
    "שופרסל ONLINE", "סופר-פארם אתר סחר"), unlike "96 אינטרנט אילת" or "אונליין - בני ברק"."""
    brand = f"{BRAND_HE.get(chain_name(chain_id), '')} {chain_name(chain_id)}".lower()
    words = [w for w in re.split(r"[^\wא-ת]+", (name or "").lower()) if w and not w.isdigit()]
    own = set(re.split(r"[^\wא-ת]+", brand)) | {"סופר", "פארם", "super", "pharm"}
    return all(w in WEB_WORDS or w in own for w in words)


# brand names as written on the shop sign (and in OpenStreetMap), to find a branch's opening hours there
BRAND_HE = {
    "Shufersal": "שופרסל", "Rami Levy": "רמי לוי", "Victory": "ויקטורי", "Yohananof": "יוחננוף", "Osher Ad": "אושר עד",
    "Tiv Taam": "טיב טעם", "Hazi Hinam": "חצי חינם", "Keshet Teamim": "קשת טעמים", "Super-Pharm": "סופר פארם",
    "King Store": "קינג סטור", "Carrefour": "קרפור", "Yeinot Bitan": "יינות ביתן", "Mahsanei HaShuk": "מחסני השוק",
    "Yellow": "יילו", "Wolt Market": "וולט", "Fresh Market": "פרש מרקט", "Good Pharm": "גוד פארם", "Dor Alon": "אלונית",
    "Bareket": "ברקת", "Polizer": "פוליצר", "Super Yuda": "סופר יודה", "Super Sapir": "ספיר", "Shuk HaIr": "שוק העיר",
    "Netiv HaHesed": "נתיב החסד", "Maayan 2000": "מעיין 2000", "Shefa Birkat Hashem": "שפע ברכת השם",
    "Zol Ubegadol": "זול ובגדול", "Salach Dabach": "דבאח", "City Market": "סיטי מרקט",
    "City Market (main company)": "סיטי מרקט", "Stop Market": "סטופ מרקט",
}


def chain_name(chain_id, fallback=None):
    return CHAIN_NAMES.get(str(chain_id)) or fallback or str(chain_id)
