"""Deals that must not be shown as open to everyone, or at another item's price.
Run: .venv/bin/python tests/test_promo_parse.py"""
import logging
import os
import sys
import tempfile
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.TemporaryDirectory()
os.environ["BASKET_USER_DB"] = os.path.join(tmp.name, "user.db")  # never the household's own user.db

from app import db as dbmod  # noqa: E402
from app import deal_terms  # noqa: E402
from app import indexer as ix  # noqa: E402

logging.getLogger("indexer").setLevel(logging.CRITICAL)
c = dbmod.connect(os.path.join(tmp.name, "basket.db"))
NOW = 202611061200


def promos(xml, name):
    path = os.path.join(tmp.name, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)
    ix.index_promos(c, path, ix.parse_filename(path), defaultdict(set), NOW)
    return {r[0]: r[1:] for r in c.execute("SELECT promo_id, club_only, club_ids, discounted_price FROM promos")}


def items(pid):
    return sorted(r[0] for r in c.execute("SELECT key FROM promo_items WHERE promo_id=?", (pid,)))


# ---------------------------------------------------------------- ClubID in every form chains write it
assert ix._club_codes("0") == ["0"] and ix._club_codes("0 - כלל הלקוחות") == ["0"] and ix._club_codes("2") == ["2"]
assert set(ix._club_codes("(2=מועדון קרפור אשראי|2=מועדון אפליקציה)")) == {"2"}
assert set(ix._club_codes('("לקוחות הקפה=3" | "מועדון לקוחות=3")')) == {"3"}
assert ix._club_codes("מועדון") == ["?"] and ix._club_codes("") == []
assert "staff" in ix._club_codes("1=עובדים") and "staff" in ix._club_codes("(1=עובדים|1=יגור עובדים ותיקים)")

CARREFOUR = "7290055700007"
p = promos(f"""<Root><ChainId>{CARREFOUR}</ChainId><StoreId>108</StoreId><Promotions>
<Promotion><PromotionId>0011453899</PromotionId><PromotionDescription>חומוס גדול 500 גרם 3 ב 12</PromotionDescription>
  <PromotionStartDate>2026-11-01</PromotionStartDate><PromotionEndDate>2026-11-30</PromotionEndDate>
  <MinQty>3</MinQty><DiscountedPrice>12</DiscountedPrice>
  <Clubs><ClubId>(2=מועדון קרפור אשראי|2=מועדון אפליקציה)</ClubId></Clubs>
  <PromotionItems><Item><ItemCode>7290000066318</ItemCode><IsGiftItem>0</IsGiftItem></Item></PromotionItems></Promotion>
<Promotion><PromotionId>0011453900</PromotionId><PromotionDescription>במבה 3 ב 10</PromotionDescription>
  <PromotionStartDate>2026-11-01</PromotionStartDate><PromotionEndDate>2026-11-30</PromotionEndDate>
  <MinQty>3</MinQty><DiscountedPrice>10</DiscountedPrice><Clubs><ClubId>0</ClubId></Clubs>
  <PromotionItems><Item><ItemCode>7290000066319</ItemCode><IsGiftItem>0</IsGiftItem></Item></PromotionItems></Promotion>
</Promotions></Root>""", f"PromoFull{CARREFOUR}-001-108-202611060511.xml")
assert p["0011453899"][:2] == (1, "2"), p  # before: club_only 0, club_ids '': shown to everyone
assert p["0011453900"][:2] == (0, "0"), p

# ---------------------------------------------------------------- items with their own price: Shufersal BE 50% off
SHUF = "7290027600007"
p = promos(f"""<root><ChainId>{SHUF}</ChainId><StoreId>776</StoreId><Promotions><Promotion>
<PromotionID>4685836</PromotionID><PromotionDescription>בי 50% הנחה תכשיטים</PromotionDescription>
<PromotionStartDateTime>2026-11-02T00:00:00.000</PromotionStartDateTime>
<PromotionEndDateTime>2026-12-01T02:59:00.000</PromotionEndDateTime><ClubID>0 - כלל הלקוחות</ClubID>
<Groups><Group><GroupID>1</GroupID><PromotionItems>
  <PromotionItem><ItemCode>7290117877227</ItemCode><MinQty>1.00</MinQty><DiscountRate>50</DiscountRate><DiscountedPrice>50.00</DiscountedPrice></PromotionItem>
  <PromotionItem><ItemCode>7290117877234</ItemCode><MinQty>1.00</MinQty><DiscountRate>50</DiscountRate><DiscountedPrice>60.00</DiscountedPrice></PromotionItem>
  <PromotionItem><ItemCode>7290117877364</ItemCode><MinQty>1.00</MinQty><DiscountRate>50</DiscountRate><DiscountedPrice>345.00</DiscountedPrice></PromotionItem>
  <PromotionItem><ItemCode>7290117877395</ItemCode><MinQty>1.00</MinQty><DiscountRate>50</DiscountRate><DiscountedPrice>50.00</DiscountedPrice></PromotionItem>
</PromotionItems></Group></Groups></Promotion></Promotions></root>""", f"PromoFull{SHUF}-005-776-202611060431.xml")
assert p["4685836"][0] == 0 and p["4685836"][2] == 50.0
# only the pieces whose own price is the stored 50.00; the ₪690 piece (345.00) is no longer priced at ₪50
assert items("4685836") == ["7290117877227", "7290117877395"], items("4685836")

# ---------------------------------------------------------------- only on some weekdays: not stored
STOP = "7290639000004"
p = promos(f"""<Root><ChainId>{STOP}</ChainId><StoreId>2</StoreId><Promotions>
<Promotion><PromotionId>0001138026</PromotionId><PromotionDescription>שמפו 2ב22</PromotionDescription>
  <PromotionStartDate>2026-11-01</PromotionStartDate><PromotionEndDate>2026-11-30</PromotionEndDate>
  <PromotionDays>1,3,4,5,6,7</PromotionDays><MinQty>2</MinQty><DiscountedPrice>22</DiscountedPrice><ClubId>0</ClubId>
  <PromotionItems><Item><ItemCode>7290000066320</ItemCode></Item></PromotionItems></Promotion>
<Promotion><PromotionId>0001138027</PromotionId><PromotionDescription>סבון 2ב12</PromotionDescription>
  <PromotionStartDate>2026-11-01</PromotionStartDate><PromotionEndDate>2026-11-30</PromotionEndDate>
  <PromotionDays>1,2,3,4,5,6,7</PromotionDays><MinQty>2</MinQty><DiscountedPrice>12</DiscountedPrice><ClubId>0</ClubId>
  <PromotionItems><Item><ItemCode>7290000066321</ItemCode></Item></PromotionItems></Promotion>
</Promotions></Root>""", f"PromoFull{STOP}-001-002-202611060109.xml")
assert "0001138026" not in p and "0001138027" in p, p

# ---------------------------------------------------------------- a percentage in the text is not a price
assert deal_terms.NUMS_RE.findall("בי 50% הנחה תכשיטים") == []
assert deal_terms.NUMS_RE.findall("3 ב 12.90") == ["3", "12.90"]

print("ok")
