"""Chain names and delivery-fee defaults.

Chain IDs come from the official Price Transparency files (same IDs the
OpenIsraeliSupermarkets scraper uses). Delivery fees are not published in
those files, so the defaults below come from press reports (ynet, Calcalist,
ice.co.il, nativ.org.il, June-July 2026) and are editable in the app.
"""

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
    "5144744100002": "Mishnat Yosef",
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


# brand names as written on the shop sign (and in OpenStreetMap), to find a branch's opening hours there
BRAND_HE = {
    "Shufersal": "שופרסל", "Rami Levy": "רמי לוי", "Victory": "ויקטורי", "Yohananof": "יוחננוף", "Osher Ad": "אושר עד",
    "Tiv Taam": "טיב טעם", "Hazi Hinam": "חצי חינם", "Keshet Teamim": "קשת טעמים", "Super-Pharm": "סופר פארם",
    "King Store": "קינג סטור", "Carrefour": "קרפור", "Yeinot Bitan": "יינות ביתן", "Mahsanei HaShuk": "מחסני השוק",
    "Yellow": "יילו", "Wolt Market": "וולט", "Fresh Market": "פרש מרקט", "Good Pharm": "גוד פארם", "Dor Alon": "אלונית",
    "Bareket": "ברקת", "Polizer": "פוליצר", "Super Yuda": "סופר יודה", "Super Sapir": "ספיר", "Shuk HaIr": "שוק העיר",
    "Netiv HaHesed": "נתיב החסד", "Maayan 2000": "מעיין 2000", "Shefa Birkat Hashem": "שפע ברכת השם",
    "Zol Ubegadol": "זול ובגדול", "Salach Dabach": "דבאח",
}


def chain_name(chain_id, fallback=None):
    return CHAIN_NAMES.get(str(chain_id)) or fallback or str(chain_id)
