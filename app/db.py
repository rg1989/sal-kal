import json
import os
import sqlite3
import time

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
-- a store's shelf price for a barcode product changing, caught as the indexer writes the newer file (was_ts = the
-- file the old price came from). app.board checks and folds them into price_moves, then empties this.
CREATE TABLE IF NOT EXISTS price_moves_raw(key TEXT, chain_id TEXT, store_id INTEGER, was REAL, now REAL,
  was_ts INTEGER, ts INTEGER);
CREATE TRIGGER IF NOT EXISTS prices_moved AFTER UPDATE OF price ON prices
  WHEN abs(new.price - old.price) >= 0.005 AND instr(new.key, ':') = 0
BEGIN
  INSERT INTO price_moves_raw VALUES(new.key, new.chain_id, new.store_id, old.price, new.price, old.ts, new.ts);
END;
-- confirmed shelf-price changes per chain and day (app.board): `stores` branches went from `was` to `now`
CREATE TABLE IF NOT EXISTS price_moves(key TEXT, chain_id TEXT, day INTEGER, was REAL, now REAL, stores INTEGER,
  ts INTEGER, PRIMARY KEY(key, chain_id, day, was, now)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS price_moves_day ON price_moves(day);
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
-- accounts (app.auth): user 1 is the household from before accounts; prefs / delivery are that account's JSON
CREATE TABLE IF NOT EXISTS u.users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT, avatar TEXT, created INTEGER, seen INTEGER,
  lang TEXT, onboarded INTEGER DEFAULT 0, admin INTEGER DEFAULT 0, prefs TEXT, delivery TEXT);
-- one row per Google / Facebook sign-in; verified = the provider confirmed the email address
CREATE TABLE IF NOT EXISTS u.identities(
  provider TEXT, subject TEXT, user_id INTEGER, email TEXT, verified INTEGER DEFAULT 0, created INTEGER,
  PRIMARY KEY(provider, subject));
CREATE INDEX IF NOT EXISTS u.identities_user ON identities(user_id);
CREATE INDEX IF NOT EXISTS u.identities_email ON identities(email);
-- token = SHA-256 of the cookie's random token
CREATE TABLE IF NOT EXISTS u.sessions(token TEXT PRIMARY KEY, user_id INTEGER, created INTEGER, seen INTEGER, expires INTEGER);
CREATE INDEX IF NOT EXISTS u.sessions_user ON sessions(user_id);
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
    _migrate_users(con)
    old = [t for t in USER_TABLES
           if con.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()]
    if not old:
        return
    with con:
        for t in old:
            if not con.execute(f"SELECT 1 FROM u.{t} LIMIT 1").fetchone():
                con.execute(f"INSERT INTO u.{t} SELECT * FROM main.{t}")
            con.execute(f"DROP TABLE main.{t}")


def _migrate_users(con):
    """Accounts: carts get an owner (user_id), and the household from before accounts becomes user 1 with its carts,
    address and delivery settings (the first account to sign in takes it over, see app.auth.sign_in)."""
    ucols = lambda: {r[1] for r in con.execute("PRAGMA u.table_info(carts)")}
    if "user_id" not in ucols():
        try:
            con.execute("ALTER TABLE u.carts ADD COLUMN user_id INTEGER")
        except sqlite3.OperationalError:
            if "user_id" not in ucols():
                raise
    con.execute("CREATE INDEX IF NOT EXISTS u.carts_user ON carts(user_id, updated)")
    if con.execute("SELECT 1 FROM u.users LIMIT 1").fetchone():
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        if not con.execute("SELECT 1 FROM u.users LIMIT 1").fetchone():
            get = lambda k: (con.execute("SELECT value FROM u.settings WHERE key=?", (k,)).fetchone() or [None])[0]
            prefs, delivery = get("prefs"), get("delivery")
            try:
                has_home = bool(json.loads(prefs or "{}").get("home"))
            except ValueError:
                has_home = False
            now = int(time.time())
            con.execute("INSERT INTO u.users(id, name, created, seen, onboarded, admin, prefs, delivery) VALUES(1,?,?,?,?,1,?,?)",
                        ("Household", now, now, int(has_home), prefs, delivery))
            con.execute("UPDATE u.carts SET user_id=1 WHERE user_id IS NULL")
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


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
