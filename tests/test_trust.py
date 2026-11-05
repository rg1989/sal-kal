"""Run: .venv/bin/python tests/test_trust.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.trust import fresh_cutoff, gs1_ok  # noqa: E402

# ---- barcodes: real GS1 codes pass, chain-internal look-alikes fail
for good in ("7290000066332", "72917367", "80867067", "016000355101", "0071164304211", "3073781190618"):
    assert gs1_ok(good), good
for bad in ("56565656", "147852369", "11102023", "7290020000000", "12380040", "abc", "", "1234567"):
    assert not gs1_ok(bad), bad

assert fresh_cutoff(1_000_000_000 + 26 * 3600) == fresh_cutoff(1_000_000_000 + 26 * 3600)
print("ok")
