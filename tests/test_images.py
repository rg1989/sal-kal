"""Run: .venv/bin/python tests/test_images.py"""
import io
import os
import shutil
import sys
import tempfile
import threading
import time

TMP = tempfile.mkdtemp()
os.environ["BASKET_DB"] = os.path.join(TMP, "basket.db")
os.environ["BASKET_USER_DB"] = os.path.join(TMP, "user.db")
os.environ["SAL_PHOTOS"] = os.path.join(TMP, "photos")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image  # noqa: E402

from app import db as dbmod, images  # noqa: E402

c = dbmod.connect()
A, B = "7290000000003", "7290027600007"
CODES = [f"72900000{i:05d}" for i in range(10)]
for n, code in enumerate(CODES):
    c.execute("INSERT INTO products(key, code, name, scope, store_count) VALUES(?,?,?,?,?)",
              (code, code, f"p{n}", "global", 100 - n))
for code in CODES[:5]:   # chain A sells the first five, chain B the rest
    c.execute("INSERT INTO item_names(key, chain_id, name, ts) VALUES(?,?,?,?)", (code, A, "x", 1))
for code in CODES[5:]:
    c.execute("INSERT INTO item_names(key, chain_id, name, ts) VALUES(?,?,?,?)", (code, B, "x", 1))
c.execute("INSERT INTO carts(id, name) VALUES(1, 'w')")
c.execute("INSERT INTO cart_items(cart_id, key, qty) VALUES(1, ?, 1)", (CODES[9],))
c.commit()

buf = io.BytesIO()
img = Image.new("RGB", (200, 200), "white")
img.paste((200, 30, 30), (50, 40, 150, 170))   # a product on a white studio background
img.save(buf, "PNG")
PNG = buf.getvalue()
calls = []


class Fake(images.Source):
    interval = 0.0

    def __init__(self, name, chains, has, slow_first=False):
        super().__init__()
        self.name, self.chains, self.has, self.slow_first = name, chains, has, slow_first

    def fetch(self, code):
        calls.append((self.name, code))
        if self.slow_first:
            self.slow_first = False
            return images.SLOW, None
        return (images.OK, PNG) if code in self.has else (images.MISS, None)

    def slow_down(self):  # don't really rest 60 s in a test
        self.rested = True


# the cart product comes first; each source starts with its own chain's products
assert images.queue(c)[0] == CODES[9]
sa = Fake("a", (A,), set(CODES[:5]))
sb = Fake("b", (B,), set(CODES[5:8]), slow_first=True)
st = images.run(c, minutes=0.05, srcs=[sa, sb])
have = {code for code in CODES if os.path.exists(images.photo_path(code))}
assert have == set(CODES[:8]), have                                   # every photo either source has
assert st["a"]["saved"] == 5 and st["b"]["saved"] == 3 and st["b"]["slow"] == 1 and sb.rested
assert [code for name, code in calls if name == "a"][:5] == CODES[:5]  # its own products first
saved = [code for name, code in calls if (name == "a" and code in CODES[:5]) or (name == "b" and code in CODES[5:8])]
assert sorted(saved) == sorted(set(saved))                              # one photo each, never fetched twice
miss = images.load_missing()
assert set(miss[CODES[8]]) == {"a", "b"}            # nobody has it: remembered per source
assert set(miss[CODES[9]]) == {"a"}                 # b was told to slow down on it: b asks again another time
# a second run doesn't ask a source again for what it said it doesn't have
calls.clear()
images.run(c, minutes=0.02, srcs=[Fake("a", (A,), set()), Fake("b", (B,), set())])
assert calls == [("b", CODES[9])], calls
# old missing.json ({barcode: ts}) is read as Rami Levy's
images.save_missing({})
with open(images.MISSING, "w") as fh:
    fh.write('{"123": %d}' % int(time.time()))
assert images.load_missing() == {"123": {"rami_levy": images.load_missing()["123"]["rami_levy"]}}
# a placeholder pixel isn't a photo
assert not images.save_photo("999", b"not an image") and not os.path.exists(images.photo_path("999"))
tiny = io.BytesIO()
Image.new("RGB", (10, 10)).save(tiny, "PNG")
assert not images.save_photo("998", tiny.getvalue())
# a flat colour or fully transparent image is a placeholder, not a photo
flat = io.BytesIO()
Image.new("RGB", (200, 200), (230, 230, 230)).save(flat, "PNG")
assert not images.save_photo("997", flat.getvalue())
clear = io.BytesIO()
Image.new("RGBA", (200, 200), (0, 0, 0, 0)).save(clear, "PNG")
assert not images.save_photo("996", clear.getvalue())
# an already cut-out PNG is laid on white, then cut out like the rest (no black background)
cut = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
cut.paste((30, 120, 30, 255), (60, 60, 140, 140))
b2 = io.BytesIO()
cut.save(b2, "PNG")
assert images.save_photo("995", b2.getvalue())
out = Image.open(images.photo_path("995"))
assert out.mode == "RGBA" and out.getpixel((0, 0))[3] < 40 and out.getpixel((out.width // 2, out.height // 2))[3] == 255

# fallback sources only take products every general source has already missed
images.save_missing({})
for code in CODES:
    if os.path.exists(images.photo_path(code)):
        os.remove(images.photo_path(code))
calls.clear()


class FakeFallback(Fake):
    fallback = True


gen = Fake("gen", (), {CODES[0]})
fb = FakeFallback("fb", (), set(CODES))
images.run(c, minutes=0.05, srcs=[gen, fb])
assert ("fb", CODES[0]) not in calls                            # the general source had it
assert {code for name, code in calls if name == "fb"} == set(CODES) - {CODES[0]}

# Shufersal's own product codes: 729000 + number -> number, other barcodes as they are
assert images.Shufersal.product_code("7290004125400") == "4125400"
assert images.Shufersal.product_code("7290112962546") == "7290112962546"
assert images.Shufersal.product_code("59099710") == "59099710"
shutil.rmtree(TMP)
print("ok")
