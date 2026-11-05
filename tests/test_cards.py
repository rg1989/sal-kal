"""Run: .venv/bin/python tests/test_cards.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.server import _pack, _per_measure  # noqa: E402

# shelf-label unit prices: per 100 g for weight, per litre for liquids
assert _per_measure(6.60, "250 g") == {"v": 2.64, "per": "100 g"}
assert _per_measure(10.80, "1.5 L") == {"v": 7.2, "per": "L"}
assert _per_measure(5.0, "500 ml") == {"v": 10.0, "per": "L"}
assert _per_measure(20.0, "2 kg") == {"v": 1.0, "per": "100 g"}
assert _per_measure(None, "1 L") is None and _per_measure(3.0, None) is None
# pack size: the chain's fields win; a size in the name counts only when it agrees with a unit-less quantity
assert _pack({"size": "20 g", "quantity": "200", "unit": "גרם"}) == "200 g"
assert _pack({"size": "20 g", "quantity": "200.00", "unit": "לא ידוע"}) is None   # "PRO 20 גרם" = protein
assert _pack({"size": "200 g", "quantity": "200.00", "unit": "לא ידוע"}) == "200 g"
assert _pack({"size": "1.5 L", "quantity": "1.5", "unit": "לא ידוע"}) == "1.5 L"
assert _pack({"size": "250 g", "quantity": "", "unit": ""}) == "250 g"
assert _pack({"size": None, "quantity": "60.00", "unit": "לא ידוע"}) is None
print("ok")
