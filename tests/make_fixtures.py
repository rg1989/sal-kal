"""Generate synthetic price-transparency dumps in the real XML dialects, for testing.

Dialects covered: Cerberus-style <Root><Items><Item>, Shufersal SAP uppercase stores file,
Super-Pharm <OrderXml><Line> price file, Bina/Cerberus <Promotions><Promotion> and a weighed item.
"""
import gzip
import os
import random
import sys
import time

random.seed(7)
OUT = sys.argv[1] if len(sys.argv) > 1 else "fixtures/dumps"
NOW = time.strftime("%Y%m%d%H%M")
TODAY = time.strftime("%Y-%m-%d")

PRODUCTS = [  # code, name, manufacturer, base price
    ("7290004131074", "חלב תנובה 3% 1 ליטר", "תנובה", 7.12),
    ("7290000042015", "חלב טרה 3% 1 ליטר בקבוק", "טרה", 7.12),
    ("7290004127336", "גבינה לבנה 5% 250 גרם", "תנובה", 5.41),
    ("7290102398065", "קוטג' 5% 250 גרם", "תנובה", 6.39),
    ("7290000066318", "במבה אסם 80 גרם", "אסם", 5.90),
    ("7290000068428", "ביסלי גריל 70 גרם", "אסם", 5.90),
    ("7290000171463", "פתיתים אפויים אסם 500 גרם", "אסם", 7.90),
    ("7290011194246", "שוקולד פרה חלב 100 גרם", "עלית", 6.90),
    ("7290000207841", "קפה נמס עלית 200 גרם", "עלית", 32.90),
    ("7290008757034", "שמן קנולה 1 ליטר", "מגדים", 11.90),
    ("7290000560213", "סוכר לבן 1 ק\"ג", "סוגת", 5.90),
    ("7290110115852", "אורז פרסי 1 ק\"ג", "סוגת", 11.90),
    ("7290000058456", "פסטה ספגטי 500 גרם", "אסם", 6.50),
    ("7290107935579", "רסק עגבניות 100 גרם", "פרי ניר", 2.40),
    ("7290016048162", "טחינה גולמית 500 גרם", "הנסיך", 15.90),
    ("7290000287478", "ביצים L 12 יחידות", "תנובה", 15.30),
    ("7290102392308", "יוגורט דנונה 1.5% 200 גרם", "שטראוס", 3.90),
    ("7290000698046", "נייר טואלט לילי 32 גלילים", "חוגלה", 49.90),
    ("7290106528529", "אבקת כביסה אריאל 3 ק\"ג", "P&G", 54.90),
    ("7290002331124", "נוזל כלים פיירי 750 מ\"ל", "P&G", 12.90),
    ("7290000164571", "קורנפלקס תלמה 750 גרם", "תלמה", 19.90),
    ("7290011498832", "קולה 1.5 ליטר", "החברה המרכזית", 7.90),
    ("7290015350075", "מים מינרליים נביעות 6x1.5", "טמפו", 14.90),
    ("7290000072753", "לחם אחיד פרוס", "אנג'ל", 7.60),
    ("7290005429071", "חומוס אחלה 400 גרם", "שטראוס", 9.90),
]
WEIGHED = ("7000035", "עגבניות", "", 6.90)  # chain-internal code, sold per kg

CHAINS = [  # chain id, folder, name, price factor, stores [(id, name, city, type)]
    ("7290027600007", "Shufersal", "שופרסל", 1.06, [(1, "שלי ת\"א- בן יהודה", "תל אביב", 1),
                                                   (215, "דיל רמת גן", "רמת גן", 1),
                                                   (413, "שופרסל אונליין", "", 2)]),
    ("7290058140886", "RamiLevy", "רמי לוי", 0.94, [(10, "רמי לוי תלפיות", "ירושלים", 1),
                                                    (39, "רמי לוי רמת גן", "רמת גן", 1),
                                                    (98, "רמי לוי אינטרנט", "", 2)]),
    ("7290696200003", "VictoryNewSource", "ויקטורי", 0.97, [(22, "ויקטורי גבעתיים", "גבעתיים", 1),
                                                           (90, "ויקטורי אונליין", "", 2)]),
    ("7290803800003", "Yohananof", "יוחננוף", 0.95, [(5, "יוחננוף תל אביב", "תל אביב", 1)]),
    ("7290873255550", "TivTaam", "טיב טעם", 1.10, [(3, "טיב טעם רמת גן", "רמת גן", 1),
                                                    (99, "טיב טעם אונליין", "", 2)]),
    ("7290055700007", "YaynotBitanAndCarrefour", "קרפור", 1.00, [(400, "קרפור מרקט תל אביב", "תל אביב", 1),
                                                                  (900, "קרפור אונליין", "", 2)]),
    ("7290172900007", "SuperPharm", "סופר-פארם", 1.18, [(83, "סופר-פארם דיזנגוף", "תל אביב", 1)]),
]


def write_gz(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def stores_xml(cid, name, stores):
    if cid == "7290027600007":  # Shufersal SAP-style uppercase
        body = "".join(f"<STORE><SUBCHAINID>1</SUBCHAINID><STOREID>{s}</STOREID><BIKORETNO>7</BIKORETNO>"
                       f"<STORETYPE>{t}</STORETYPE><CHAINNAME>{name}</CHAINNAME><SUBCHAINNAME>{name}</SUBCHAINNAME>"
                       f"<STORENAME>{esc(n)}</STORENAME><ADDRESS>רחוב {s}</ADDRESS><CITY>{c}</CITY></STORE>"
                       for s, n, c, t in stores)
        return (f'<?xml version="1.0" encoding="UTF-8"?><asx:abap xmlns:asx="http://www.sap.com/abapxml" version="1.0">'
                f"<asx:values><CHAINID>{cid}</CHAINID><STORES>{body}</STORES></asx:values></asx:abap>")
    body = "".join(f"<Store><StoreId>{s}</StoreId><BikoretNo>1</BikoretNo><StoreType>{t}</StoreType>"
                   f"<StoreName>{esc(n)}</StoreName><Address>רחוב {s}</Address><City>{c}</City></Store>"
                   for s, n, c, t in stores)
    return (f'<?xml version="1.0" encoding="utf-8"?><Root><ChainId>{cid}</ChainId><ChainName>{name}</ChainName>'
            f"<SubChains><SubChain><SubChainId>1</SubChainId><SubChainName>{name}</SubChainName>"
            f"<Stores>{body}</Stores></SubChain></SubChains></Root>")


def price_xml(cid, sid, items):
    if cid == "7290172900007":  # Super-Pharm OrderXml/Line dialect
        lines = "".join(f"<Line><PriceUpdateDate>{TODAY}</PriceUpdateDate><ItemCode>{c}</ItemCode><ItemName>{esc(n)}</ItemName>"
                        f"<ManufacturerName>{esc(m)}</ManufacturerName><blsWeighted>{w}</blsWeighted>"
                        f"<ItemPrice>{p:.2f}</ItemPrice></Line>" for c, n, m, p, w in items)
        return (f'<?xml version="1.0" encoding="UTF-8"?><OrderXml><Envelope><ChainId>{cid}</ChainId><StoreId>{sid}</StoreId>'
                f"<Header><Details>{lines}</Details></Header></Envelope></OrderXml>")
    rows = "".join(f"<Item><PriceUpdateDate>{TODAY} 08:00</PriceUpdateDate><ItemCode>{c}</ItemCode><ItemType>1</ItemType>"
                   f"<ItemName>{esc(n)}</ItemName><ManufacturerName>{esc(m)}</ManufacturerName>"
                   f"<Quantity>1</Quantity><UnitQty>יחידה</UnitQty><bIsWeighted>{w}</bIsWeighted>"
                   f"<ItemPrice>{p:.2f}</ItemPrice><ItemStatus>1</ItemStatus></Item>" for c, n, m, p, w in items)
    return (f'<?xml version="1.0" encoding="utf-8"?><Root><XmlDocVersion>19</XmlDocVersion><ChainId>{cid}</ChainId>'
            f'<SubChainId>1</SubChainId><StoreId>{sid:03d}</StoreId><BikoretNo>1</BikoretNo><Items Count="{len(items)}">{rows}</Items></Root>')


def promo_xml(cid, sid, promos):
    body = ""
    for pid, desc, mq, dp, codes, club in promos:
        items = "".join(f"<Item><ItemCode>{c}</ItemCode><ItemType>1</ItemType><IsGiftItem>0</IsGiftItem></Item>" for c in codes)
        body += (f"<Promotion><PromotionId>{pid}</PromotionId><PromotionDescription>{esc(desc)}</PromotionDescription>"
                 f"<PromotionUpdateDate>{TODAY} 00:00</PromotionUpdateDate><PromotionStartDate>2026-01-01</PromotionStartDate>"
                 f"<PromotionStartHour>00:00:00</PromotionStartHour><PromotionEndDate>2027-12-31</PromotionEndDate>"
                 f"<PromotionEndHour>23:59:00</PromotionEndHour><RewardType>1</RewardType>"
                 f"<AdditionalRestrictions><AdditionalIsCoupon>0</AdditionalIsCoupon><AdditionalIsActive>1</AdditionalIsActive></AdditionalRestrictions>"
                 f"<MinQty>{mq:.2f}</MinQty><DiscountedPrice>{dp:.2f}</DiscountedPrice>"
                 f"<Clubs><ClubId>{club}</ClubId></Clubs><PromotionItems>{items}</PromotionItems></Promotion>")
    return (f'<?xml version="1.0" encoding="utf-8"?><Root><ChainId>{cid}</ChainId><SubChainId>1</SubChainId>'
            f'<StoreId>{sid:03d}</StoreId><Promotions Count="{len(promos)}">{body}</Promotions></Root>')


def main():
    for cid, folder, name, factor, stores in CHAINS:
        d = os.path.join(OUT, folder)
        write_gz(f"{d}/Stores{cid}-000-{NOW}.gz", stores_xml(cid, name, stores))
        for sid, sname, city, stype in stores:
            items = []
            for code, pname, manu, base in PRODUCTS:
                if random.random() < 0.06:  # not every store carries everything
                    continue
                p = base * factor * random.uniform(0.96, 1.05) * (1.03 if stype == 2 else 1)
                items.append((code, pname, manu, round(p, 1) - 0.01 if p > 3 else round(p, 2), 0))
            items.append((WEIGHED[0], WEIGHED[1], WEIGHED[2], round(WEIGHED[3] * factor, 2), 1))
            write_gz(f"{d}/PriceFull{cid}-{sid:03d}-{NOW}.gz", price_xml(cid, sid, items))
            promos = [(f"{sid}01", "במבה 3 ב 12", 3, 12.0, ["7290000066318"], "0"),
                      (f"{sid}02", "קוטג' 2 ב 10 למועדון", 2, 10.0, ["7290102398065"], "1"),
                      (f"{sid}03", "קפה עלית ב 24.90", 1, 24.90 * factor, ["7290000207841"], "0")]
            if cid != "7290172900007":
                write_gz(f"{d}/PromoFull{cid}-{sid:03d}-{NOW}.gz", promo_xml(cid, sid, promos))
    print("fixtures written to", OUT)


if __name__ == "__main__":
    main()
