"""Run: .venv/bin/python tests/test_history.py"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.indexer import record_history  # noqa: E402
from app.server import _at  # noqa: E402

c = sqlite3.connect(":memory:")
c.executescript("""
CREATE TABLE stores(chain_id TEXT, store_id INTEGER, price_full_ts INTEGER, last_price_ts INTEGER);
CREATE TABLE price_flags(key TEXT, chain_id TEXT, store_id INTEGER, median REAL);
CREATE TABLE name_flags(key TEXT, chain_id TEXT, name TEXT);
CREATE TABLE prices(key TEXT, chain_id TEXT, store_id INTEGER, price REAL, ts INTEGER);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE price_history(key TEXT, chain_id TEXT, day INTEGER, mn REAL, mx REAL, n INTEGER, PRIMARY KEY(key, chain_id, day));
CREATE TABLE price_last(key TEXT, chain_id TEXT, mn REAL, mx REAL, day INTEGER, PRIMARY KEY(key, chain_id));
INSERT INTO stores VALUES('A', 1, 100, 299912312359), ('A', 2, 100, 299912312359), ('B', 1, 100, 299912312359),
                          ('C', 1, 100, 202001010000);  -- C's prices are years old: not current
INSERT INTO prices VALUES('milk', 'C', 1, 5.00, 100), ('milk', 'B', 2, 69.00, 100), ('soap', 'B', 1, 9.90, 100);
INSERT INTO stores VALUES('B', 2, 100, 299912312359);
INSERT INTO price_flags VALUES('milk', 'B', 2, 7.20);  -- an outlier: not counted
INSERT INTO name_flags VALUES('soap', 'B', 'something else');  -- B's "soap" is another product
INSERT INTO prices VALUES('milk', 'A', 1, 7.20, 100), ('milk', 'A', 2, 7.90, 100), ('milk', 'B', 1, 6.90, 100),
                         ('milk', 'A', 3, 1.00, 50);  -- older than its store's full file: stale, ignored
""")
rows = lambda: c.execute("SELECT key, chain_id, day, mn, mx, n FROM price_history ORDER BY chain_id, day").fetchall()

record_history(c, 20261105)                       # C (old prices), B's outlier and B's other "soap" are left out
assert rows() == [("milk", "A", 20261105, 7.2, 7.9, 2), ("milk", "B", 20261105, 6.9, 6.9, 1)], rows()
record_history(c, 20261106)                       # nothing changed: no new rows
assert len(rows()) == 2
c.execute("UPDATE prices SET price=7.50 WHERE chain_id='A' AND store_id=1")
record_history(c, 20261107)                       # chain A's cheapest went up: one new row, for A only
assert rows()[1] == ("milk", "A", 20261107, 7.5, 7.9, 2) and len(rows()) == 3, rows()
c.execute("UPDATE prices SET price=7.60 WHERE chain_id='A' AND store_id=1")
record_history(c, 20261107)                       # same day again: today's row is rewritten, not duplicated
assert rows()[1] == ("milk", "A", 20261107, 7.6, 7.9, 2) and len(rows()) == 3, rows()
assert c.execute("SELECT value FROM settings WHERE key='history_since'").fetchone() == ("20261105",)

# price on a day from change-only points
pts = [[20261105, 7.2], [20261107, 7.6]]
assert _at(pts, 20261101) == 7.2   # before history: the first known price
assert _at(pts, 20261106) == 7.2 and _at(pts, 20261107) == 7.6 and _at(pts, 20261231) == 7.6
print("ok")
