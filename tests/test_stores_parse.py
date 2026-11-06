"""Stores files with several sub-chains, City Market Shops' nested stores, and files the parser can't read in full.
Run: .venv/bin/python tests/test_stores_parse.py"""
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.TemporaryDirectory()
os.environ["BASKET_USER_DB"] = os.path.join(tmp.name, "user.db")  # never the household's own user.db

from app import db as dbmod  # noqa: E402
from app import indexer as ix  # noqa: E402

logging.getLogger("indexer").setLevel(logging.CRITICAL)  # the broken files below log warnings on purpose
c = dbmod.connect(os.path.join(tmp.name, "basket.db"))
c.execute("CREATE TABLE IF NOT EXISTS towns(code INTEGER PRIMARY KEY, name TEXT)")
c.executemany("INSERT INTO towns VALUES(?,?)", [(6600, "חולון"), (8400, "רחובות"), (2610, "בית שמש")])


def write(name, body, raw=None):
    path = os.path.join(tmp.name, name)
    with open(path, "wb") as f:
        f.write(raw if raw is not None else body.encode("utf-8"))
    return path


def stores(chain):
    return {r[0]: tuple(r[1:]) for r in c.execute(
        "SELECT store_id, name, address, city, sub_chain FROM stores WHERE chain_id=? ORDER BY store_id", (chain,))}


# ---------------------------------------------------------------- each SubChain section names its own stores
SHUF = "7290027600007"
p = write(f"Stores{SHUF}-000-202611060200.xml", f"""<?xml version="1.0" encoding="UTF-8"?><Chain>
<ChainID>{SHUF}</ChainID><ChainName>שופרסל</ChainName><SubChains>
<SubChain><SubChainID>1</SubChainID><SubChainName>שופרסל שלי</SubChainName><Stores>
  <Store><StoreID>756</StoreID><StoreType>1</StoreType><StoreName>שלי באר יעקב</StoreName><Address>יצחק שמיר 17</Address><City>2530</City></Store>
</Stores></SubChain>
<SubChain><SubChainID>2</SubChainID><SubChainName>שופרסל דיל</SubChainName><Stores>
  <Store><StoreID>12</StoreID><StoreType>1</StoreType><StoreName>דיל חולון</StoreName><Address>המרכבה 1</Address><City>6600</City></Store>
  <Store><StoreID>13</StoreID><StoreType>1</StoreType><StoreName>דיל רחובות</StoreName><Address>הרצל 2</Address><City>8400</City></Store>
</Stores></SubChain>
<SubChain><SubChainID>9</SubChainID><SubChainName /><Stores>
  <Store><StoreID>413</StoreID><StoreType>2</StoreType><StoreName>שופרסל ONLINE</StoreName><Address>unknown</Address><City>0</City></Store>
</Stores></SubChain>
</SubChains></Chain>""")
ix.PARSE_ERRORS.clear()
assert ix.index_stores(c, p, ix.parse_filename(p)) == 4
s = stores(SHUF)
assert s[756][3] == "שופרסל שלי" and s[12][3] == s[13][3] == "שופרסל דיל", s
assert s[413] == ("שופרסל ONLINE", "", "", ""), s[413]  # an empty SubChainName, "unknown" and "0" are no value
assert c.execute("SELECT name_he FROM chains WHERE chain_id=?", (SHUF,)).fetchone()[0] == "שופרסל"
assert not ix.PARSE_ERRORS

# ---------------------------------------------------------------- City Market Shops: <Store><SubChainStoreXMLObject>
CM = "7290000000003"
p = write(f"StoresFull{CM}-000-202611041000.xml", f"""<root><ChainId>{CM}</ChainId><ChainName>סיטי מרקט</ChainName>
<SubChains><SubChainsXMLObject><SubChain><SubChainId>1</SubChainId><SubChainName>סיטי מרקט</SubChainName>
<Stores><SubChainStoresXMLObject><Store>
  <SubChainStoreXMLObject><StoreId>003</StoreId><StoreType>1</StoreType>
    <StoreName>סיטי מרקט פינה מתוקה חולון בע"מ, הלוחמים 37 חולון</StoreName><Address>unknown</Address><City>unknown</City></SubChainStoreXMLObject>
  <SubChainStoreXMLObject><StoreId>015</StoreId><StoreType>1</StoreType>
    <StoreName>סיטי מרקט שמש בע"מ, נהר הירדן 5 בית שמש</StoreName><Address>unknown</Address><City>unknown</City></SubChainStoreXMLObject>
  <SubChainStoreXMLObject><StoreId>008</StoreId><StoreType>1</StoreType>
    <StoreName>סיטי מרקט טאוור בע"מ, משה דיין 2 עיר שאינה ברשימה</StoreName><Address>unknown</Address><City>unknown</City></SubChainStoreXMLObject>
  <SubChainStoreXMLObject><StoreId>000</StoreId><StoreType>1</StoreType>
    <StoreName>סיטי מרקט - חנויות כללי</StoreName><Address>unknown</Address><City>unknown</City></SubChainStoreXMLObject>
</Store></SubChainStoresXMLObject></Stores></SubChain></SubChainsXMLObject></SubChains></root>""")
assert ix.index_stores(c, p, ix.parse_filename(p)) == 4
s = stores(CM)
assert s[3] == ('סיטי מרקט פינה מתוקה חולון בע"מ', "הלוחמים 37", "חולון", "סיטי מרקט"), s[3]
assert s[15][1:3] == ("נהר הירדן 5", "בית שמש"), s[15]  # a town of two words
# the text after the comma doesn't end with a known town: nothing is guessed, the store stays unplaced
assert s[8] == ('סיטי מרקט טאוור בע"מ, משה דיין 2 עיר שאינה ברשימה', "", "", "סיטי מרקט"), s[8]
assert s[0][1:3] == ("", "")
assert not ix.PARSE_ERRORS

# a real address is kept as it is, commas in the name or not
p = write(f"Stores{CM}-000-202611051000.xml", f"""<root><ChainId>{CM}</ChainId><SubChains><SubChain><Stores><Store>
  <SubChainStoreXMLObject><StoreId>003</StoreId><StoreName>פינה, מתוקה חולון</StoreName><Address>סוקולוב 9</Address>
    <City>6600</City></SubChainStoreXMLObject></Store></Stores></SubChain></SubChains></root>""")
ix.index_stores(c, p, ix.parse_filename(p))
assert stores(CM)[3][:3] == ("פינה, מתוקה חולון", "סוקולוב 9", "6600")

# a Stores file that gives no stores is reported, not silently taken as an empty chain
p = write(f"Stores{CM}-000-202611061000.xml", "<root><SubChains><SubChain><Stores><Shop><Id>1</Id></Shop></Stores>"
          "</SubChain></SubChains></root>")
assert ix.index_stores(c, p, ix.parse_filename(p)) == 0
assert ix.PARSE_ERRORS and ix.PARSE_ERRORS[-1] == (os.path.basename(p), "no stores read"), ix.PARSE_ERRORS

# ---------------------------------------------------------------- declared ISO-8859-8, written as Windows-1255
ix.PARSE_ERRORS.clear()
SP = "7290172900007"
items = "".join(f"<Line><ItemCode>72900000{i:05d}</ItemCode><ItemName>{n}</ItemName><ItemPrice>{p}</ItemPrice></Line>"
                for i, (n, p) in enumerate([("שמפו", "9.90"), ("קרם גוף ₪", "12.50"), ("סבון", "4.20")]))
xml = f'<?xml version="1.0" encoding="ISO-8859-8"?><OrderXml><Envelope><Header><Details>{items}</Details></Header></Envelope></OrderXml>'
raw = xml.replace("שמפו", "שָמפו").encode("cp1255")  # a qamats (0xC8): not an ISO-8859-8 byte
assert b"\xc8" in raw and b"\xa4" in raw
p = write(f"PriceFull{SP}-001-202611061500.xml", None, raw)
names = [ix.children(el).get("itemname") for el, _ in ix.iter_records(p, {"item", "product", "line"}, "itemcode")]
assert names == ["שָמפו", "קרם גוף ₪", "סבון"], names  # before: nothing at all, and no error
assert not ix.PARSE_ERRORS

# ---------------------------------------------------------------- XML errors that can lose data are reported
p = write("Promo7290700100008-000-203-202611061212.xml", "<Root><Promotions><Promotion><PromotionID>1</PromotionID>"
          "<Groups></PromotionItems></Group></Groups></Promotion><Promotion><PromotionID>2</PromotionID></Promotion>"
          "</Promotions></Root>")
list(ix.iter_records(p, {"promotion"}, "promotionid"))
assert len(ix.PARSE_ERRORS) == 1 and ix.PARSE_ERRORS[0][0] == os.path.basename(p), ix.PARSE_ERRORS
# a stray "&" or a control character loses nothing: not reported
ix.PARSE_ERRORS.clear()
p = write("Price7290058140886-001-001-202611061300.xml", None,
          b"<Root><Items><Item><ItemCode>1</ItemCode><ItemName>a & b\x01</ItemName></Item>"
          b"<Item><ItemCode>2</ItemCode></Item></Items></Root>")
assert len(list(ix.iter_records(p, {"item"}, "itemcode"))) == 2 and not ix.PARSE_ERRORS, ix.PARSE_ERRORS

print("ok")
