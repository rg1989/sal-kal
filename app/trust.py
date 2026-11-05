"""What Sal can confirm about a price.

A cart line counts only when the store's prices are current, the product is the same one (a real barcode whose
name at this chain agrees with the other chains'), the price is in line with the same product elsewhere, and any
deal applied says the same thing in its description as in its numbers. Everything else is shown but not counted.
"""
import re
import time

FRESH_HOURS = 26          # chains publish a full price file daily; the downloader runs every 3 h
OUTLIER = (1 / 3, 3.0)    # a price outside this share of the product's median price is not trusted
OUTLIER_MIN_STORES = 4    # with fewer stores there is nothing to compare against: the published price stands
NAMES_DAYS = 3            # a chain's shelf name for a barcode counts this long after the newest file it was seen in


def names_cutoff(now=None):
    """Shelf names last seen before this (YYYYMMDDHHMM) no longer confirm anything."""
    return int(time.strftime("%Y%m%d%H%M", time.localtime((now or time.time()) - NAMES_DAYS * 86400)))


def fresh_cutoff(now=None):
    """Store price files older than this (YYYYMMDDHHMM) are out of date."""
    return int(time.strftime("%Y%m%d%H%M", time.localtime((now or time.time()) - FRESH_HOURS * 3600)))


# ---------------------------------------------------------------- barcodes
def gs1_ok(code):
    """True when the last digit is the GS1 check digit (EAN-8, UPC-A, EAN-13, GTIN-14, or one with leading zeros
    dropped). Chain-internal codes that only look like barcodes (56565656, 147852369) usually fail."""
    if not code or not code.isdigit() or len(code) < 8:
        return False
    digits = [int(c) for c in code]
    body, check = digits[:-1], digits[-1]
    s = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - s % 10) % 10 == check


# name and deal rules live in their own modules; re-exported here for callers that import them from trust
from .names import name_words, names_agree, odd_names, words_match  # noqa: E402,F401
from .deal_terms import cost_with, promo_terms  # noqa: E402,F401
