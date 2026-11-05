import os
import sqlite3

DB_PATH = os.environ.get("BASKET_DB", "/srv/sal/data/basket.db")

ITEM_NAMES_COLS = "key TEXT, chain_id TEXT, name TEXT, ts INTEGER DEFAULT 0, PRIMARY KEY(key, chain_id, name)"

# Price data: written by the indexer in long transactions.
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS chains(
  chain_id TEXT PRIMARY KEY, name_he TEXT, folder TEXT);
CREATE TABLE IF NOT EXISTS stores(
  chain_id TEXT, store_id INTEGER, name TEXT, address TEXT, city TEXT,
  store_type INTEGER, sub_chain TEXT,
  price_full_ts INTEGER DEFAULT 0, promo_full_ts INTEGER DEFAULT 0,
  last_price_ts INTEGER DEFAULT 0,
  PRIMARY KEY(chain_id, store_id));
CREATE TABLE IF NOT EXISTS products(
  key TEXT PRIMARY KEY, code TEXT, name TEXT, manufacturer TEXT,
  quantity TEXT, unit TEXT, is_weighted INTEGER, scope TEXT,
  store_count INTEGER DEFAULT 0, chain_count INTEGER DEFAULT 0, min_price REAL, max_price REAL, norm TEXT);
CREATE INDEX IF NOT EXISTS products_norm ON products(norm) WHERE scope='chain';
CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
  key UNINDEXED, name, manufacturer, code, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS prices(
  key TEXT, chain_id TEXT, store_id INTEGER, price REAL, ts INTEGER,
  PRIMARY KEY(key, chain_id, store_id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS promos(
  chain_id TEXT, store_id INTEGER, promo_id TEXT, description TEXT,
  start_ts INTEGER, end_ts INTEGER, min_qty REAL, discounted_price REAL,
  discount_rate REAL, club_only INTEGER, ts INTEGER,
  PRIMARY KEY(chain_id, store_id, promo_id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS promo_items(
  key TEXT, chain_id TEXT, store_id INTEGER, promo_id TEXT, ts INTEGER,
  PRIMARY KEY(key, chain_id, store_id, promo_id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS files(
  path TEXT PRIMARY KEY, size INTEGER, kind TEXT, rows INTEGER, done_at INTEGER);
CREATE TABLE IF NOT EXISTS geocache(q TEXT PRIMARY KEY, lat REAL, lon REAL, at INTEGER);
-- price history: each product's cheapest and dearest shelf price per chain, one row per day it changes
CREATE TABLE IF NOT EXISTS price_history(key TEXT, chain_id TEXT, day INTEGER, mn REAL, mx REAL, n INTEGER,
  PRIMARY KEY(key, chain_id, day)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS price_last(key TEXT, chain_id TEXT, mn REAL, mx REAL, day INTEGER,
  PRIMARY KEY(key, chain_id)) WITHOUT ROWID;
-- every name a chain uses for a barcode product (the names on its shelves); ts = newest file (YYYYMMDDHHMM) it was in
CREATE TABLE IF NOT EXISTS item_names(ITEM_NAMES_COLS) WITHOUT ROWID;
-- barcode products a chain names differently from the other chains (app.names.odd_names): not the same product.
-- name = the chain's odd name (its newest one when several disagree)
CREATE TABLE IF NOT EXISTS name_flags(key TEXT, chain_id TEXT, name TEXT, PRIMARY KEY(key, chain_id)) WITHOUT ROWID;
-- shelf prices far from the product's median price at other stores (app.trust.OUTLIER): not trusted
CREATE TABLE IF NOT EXISTS price_flags(key TEXT, chain_id TEXT, store_id INTEGER, median REAL,
  PRIMARY KEY(key, chain_id, store_id)) WITHOUT ROWID;
""".replace("ITEM_NAMES_COLS", ITEM_NAMES_COLS)

# columns added after the first release: (table, column, type)
# promos.club_ids: the ClubId values as published, comma-separated ("0", "1,2"); NULL on rows indexed before it existed
ADDED_COLUMNS = (("products", "median", "REAL"), ("promos", "max_qty", "REAL"), ("promos", "remarks", "TEXT"),
                 ("promos", "club_ids", "TEXT"))

# The household's own data lives in a separate file (attached as `u`), so saving a cart never
# waits behind the indexer's long write locks on the price data. Unqualified names resolve here.
USER_TABLES = ("carts", "cart_items", "snapshots", "settings")
USER_SCHEMA = """
PRAGMA u.journal_mode=WAL;
CREATE TABLE IF NOT EXISTS u.carts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, created INTEGER, updated INTEGER);
CREATE TABLE IF NOT EXISTS u.cart_items(
  cart_id INTEGER, key TEXT, qty REAL, name TEXT, position INTEGER,
  PRIMARY KEY(cart_id, key));
CREATE TABLE IF NOT EXISTS u.snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, cart_id INTEGER, taken_at INTEGER,
  data_ts INTEGER, kind TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS u.snapshots_cart ON snapshots(cart_id, taken_at);
CREATE TABLE IF NOT EXISTS u.settings(key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path, timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA cache_size=-200000")
    user = os.environ.get("BASKET_USER_DB") or os.path.join(os.path.dirname(os.path.abspath(path)), "user.db")
    con.execute("ATTACH DATABASE ? AS u", (user,))
    con.executescript(SCHEMA + USER_SCHEMA)
    _migrate(con)
    return con


def _cols(con, table):
    return {r[1] for r in con.execute(f"PRAGMA main.table_info({table})")}


def _migrate(con):
    """Columns added since the tables were created, item_names' move from one name per (barcode, chain) to every
    name with a timestamp, and a one-time move of user tables from the price DB into user.db (else main's copies
    would shadow them). Each step re-checks inside its write lock: the web server's threads connect concurrently."""
    for table, col, typ in ADDED_COLUMNS:
        if col not in _cols(con, table):
            try:
                con.execute(f"ALTER TABLE main.{table} ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                if col not in _cols(con, table):  # another connection added it in between
                    raise
    if "ts" not in _cols(con, "item_names"):
        _migrate_item_names(con)
    old = [t for t in USER_TABLES
           if con.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()]
    if not old:
        return
    with con:
        for t in old:
            if not con.execute(f"SELECT 1 FROM u.{t} LIMIT 1").fetchone():
                con.execute(f"INSERT INTO u.{t} SELECT * FROM main.{t}")
            con.execute(f"DROP TABLE main.{t}")


def _migrate_item_names(con):
    """item_names(key, chain_id, name) PK(key, chain_id) -> every distinct name, PK(key, chain_id, name), with ts.
    The old rows' file times weren't kept: each gets its chain's newest PriceFull time (the names were read from
    the chain's current files), so they vote in the name check until newer files replace them (indexer.NAMES_DAYS).
    ~0.5 M rows: a few seconds."""
    con.execute("BEGIN IMMEDIATE")
    try:
        if "ts" not in _cols(con, "item_names"):
            con.execute("ALTER TABLE main.item_names RENAME TO item_names_v1")
            con.execute(f"CREATE TABLE main.item_names({ITEM_NAMES_COLS}) WITHOUT ROWID")
            con.execute("""CREATE TEMP TABLE chain_ts AS SELECT chain_id, max(price_full_ts) ts FROM main.stores
                           GROUP BY chain_id""")
            con.execute("""INSERT OR IGNORE INTO main.item_names(key, chain_id, name, ts)
                           SELECT i.key, i.chain_id, i.name, coalesce(c.ts, 0) FROM main.item_names_v1 i
                           LEFT JOIN temp.chain_ts c ON c.chain_id=i.chain_id WHERE coalesce(i.name, '') != ''""")
            con.execute("DROP TABLE temp.chain_ts")
            con.execute("DROP TABLE main.item_names_v1")
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
