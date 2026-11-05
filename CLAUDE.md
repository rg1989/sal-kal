# Sal — Israeli grocery basket price comparison

Handoff from a Claude cloud session (2026-11-04). Read this first, then continue from "What's left".

## What the user wants
A premium web UI, self-hosted on their always-on **Omarchy (Arch Linux) home server** in Israel, where they can:
- Build a basket: search products (name or barcode), add, set quantities.
- See what the full basket costs at every supermarket chain. **Cheapest first and largest/most visible; the most expensive at the bottom, collapsed.**
- Save carts with names; edit them; compare specific carts and compare prices **over time**.
- See whether a price is the **online store price or the physical-store price**, and what **home delivery** would cost (delivery fee, thresholds, minimum order).
- The user is reachable from their MacBook Air, which already has SSH access to the server. They use Hebrew product names; UI copy is English.

## Data source (no API — legally mandated XML files)
Israel's Food Act (2014, in force since 2015) requires chains with 3+ stores to publish Stores, PriceFull/Price
and PromoFull/Promo files publicly, updated through the day. Portals differ (Shufersal's own site, Cerberus/
publishedprices for Rami Levy & others, Carrefour/publishprice, Bina, Laibcatalog API for Victory/H. Cohen/Mahsanei HaShuk).
Some portals block non-Israeli IPs — that's why everything runs on the home server.

Downloader: OpenIsraeliSupermarkets/israeli-supermarket-scarpers (maintained; the old erlichsefi repo just points there).
33 active chains (see `il_supermarket_scarper/scrappers_factory.py`). Deprecated: Mega, Quik, Cofix (folded into Rami Levy), Victory old source, etc.
Python must be <3.13 (setup.py says `>=3.8,<3.13`), and it needs Playwright Chromium for some chains.

### Step 1 — downloader install (given to the user; may or may not have been run yet)
`install-downloader.sh` in this folder. It installs to `~/il-prices` (src/, .venv/, run.sh), dumps go to
`~/il-prices/dumps/<ChainFolder>/`, a systemd **user** timer `il-prices.timer` runs every 3h at :17, linger enabled,
files older than 2 days are pruned. Check with `systemctl --user status il-prices.timer` and `ls ~/il-prices/dumps`.

## File formats learned (from real samples in OpenIsraeliSupermarkets/israeli-supermarket-parsers/resources/xml)
- Names: `PriceFull<chainid13>-<store>-<YYYYMMDDHHMM>.gz|.xml`, also `Stores…`, `PromoFull…`, `Price…`, `Promo…`;
  some prefixed `NULL`; Wolt uses `PromoFull<chain>-000-038-20260217-000027`. Files may be gzip, zip or plain; some ISO-8859-8/UTF-16.
- Prices: `<Root><Items><Item>` (Cerberus/Bina/most), Super-Pharm `<OrderXml><Envelope><Header><Details><Line>`.
  Fields: ItemCode, ItemName, ManufacturerName, Quantity, UnitQty, bIsWeighted / blsWeighted (Super-Pharm typo), ItemPrice.
- Stores: Cerberus `<Root><SubChains><SubChain><Stores><Store>` with StoreType (1=physical, 2=online, 3=both);
  Shufersal SAP-style uppercase `<asx:abap>…<STORES><STORE><STOREID>…<STORETYPE>`. Shufersal online store has STORETYPE 2.
- Promos: `<Promotions><Promotion>` with PromotionId, PromotionDescription, Start/End date+hour, MinQty, DiscountedPrice,
  DiscountRate, Clubs/ClubId (0 = everyone), AdditionalIsCoupon, AdditionalIsActive, MinPurchaseAmnt, PromotionItems/Item(ItemCode, IsGiftItem).
  Bina chains use PromotionID/ChainID casing and sometimes `<Sales>`. Super-Pharm promos are `<Line>` per item with nested PromotionDetails.
- DiscountedPrice is usually the **bundle price** for MinQty ("3 ב 8.90"). DiscountRate is often the saving % on top
  of it, not a "1+1" signal (reading it as one overpriced 99.8% of such bundles before 2026-11-05). RewardType codes
  differ per chain (Shufersal 0, Rami Levy 10/3, Carrefour 12): not used. MaxQty and Remarks (coupons) are indexed.

## Confirmed-only comparison (the user's rule, 2026-11-05 — overrides everything below)
Accuracy over coverage: a price that can't be confirmed is shown greyed with its reason and never counted; no estimates.
Rules live in `app/trust.py` (tests: tests/test_trust.py). A cart line counts only when:
1. the store is fresh: `stores.last_price_ts` within `FRESH_HOURS` (26 h); older stores aren't ranked (`stale` group);
2. same product: a barcode with a valid GS1 check digit (`is_global_code`), and the chain's own name (`item_names`)
   agrees with the other chains' (`odd_names`: shared word after prefix/plural/construct folding, one-letter typos,
   English↔Hebrew consonants) — disagreeing chains go in `name_flags` (~0.7% of chain names, mostly real mix-ups);
   chain-code items (produce, deli) are never compared, not even at their own chain ("Not compared" block);
3. the price is within ⅓×–3× of the product's median at current stores (`price_flags`, needs 4+ stores; `products.median`);
4. a deal counts only if its description states the same terms as its numbers, or its price and % agree at the shelf
   price (`promo_terms`; ~82% of deal lines); coupons/vouchers/club sign-up/spend thresholds never; MaxQty respected.
The indexer's `confirm()` rebuilds flags, then stats (store/chain counts, min/max) and price history from confirmed
prices only. Stores missing items are listed as "N of M" with no estimate; complete stores rank first.
Delivery areas are not in the price files (Super Sapir's terms: "defined delivery areas", checked per address on its
site, min ₪200 varying by area). So "Delivered home" ranks a chain only when the user ticked "Delivers to me" for it in
the Delivery fees dialog (`delivers_here` in the `delivery` setting); other chains are listed apart as "Delivery area
not confirmed" (`no_area` in the compare response) and in-store mode says so instead of a delivered total.

## Product matching
Packaged goods use the manufacturer EAN barcode as ItemCode → identical across chains → exact match.
Exceptions: weighed produce/deli (chain-internal short codes, or 13-digit codes starting with 2) and store brands.
Implementation: `product_key()` → global key = barcode; otherwise `"<chainid>:<code>"`. Chain-scoped items are grouped
in search by a normalized name (`products.norm`) and shown per store as similar items, but never counted in totals.

## Delivery fees (not in the price files — editable defaults in `app/chains.py`, user-overridable in the UI)
Sources: ice.co.il (5 Jul 2026), nativ.org.il (5 Jun 2026), ynet/Calcalist/Bizportal (2026).
Shufersal 35.90 (raised Jul 2026); Rami Levy 35.90; Victory 35.90 (orders over 250); Carrefour 29.90; Keshet Teamim
29.90, 14.90 above 750; Tiv Taam 29.90, free above 750 (valid to 31 Dec 2027); Hazi Hinam 29.90; Yohananof pickup only.
Online minimum orders across sites are 250–350 per ice.co.il, not per chain.

## Code layout (in this folder)
- `app/db.py` — SQLite schema (WAL). DB at `$BASKET_DB` or `~/il-prices/basket.db`.
- `app/indexer.py` — `python -m app.indexer [--dumps ~/il-prices/dumps] [--db ...]`. Incremental (tracks files by path+size),
  latest full file per store wins, deltas applied after it, daily cleanup of superseded rows, FTS5 product search index,
  per-product stats (store_count, chain_count, min/max), and auto price snapshots for saved carts after each run. **Tested.**
- `app/engine.py` — `compare(con, items, prefs)`: per store pricing with best promo per line (`line_cost`), club-only promos
  excluded unless prefs.club, physical vs online stores (StoreType or name hints), city filter, delivery fee rules,
  ranking by mode `store` (in-store total) or `delivery` (online total + fee). Returns per chain: `in_store` (cheapest branch,
  top branches, lines) and `online` (total, fee, fee_note, delivered, price_vs_store). Also cart snapshots. **Tested with fixtures.**
- `app/server.py` — FastAPI. `GET /`, `/api/status`, `POST /api/reindex`, `/api/search?q=`, `/api/cities`, `POST /api/compare`,
  `/api/settings` (GET/PUT prefs + delivery overrides), carts CRUD `/api/carts[/{id}]`, `/duplicate`, `/snapshot`, `/history`,
  `DELETE /api/snapshots/{id}`. Not yet run end-to-end.
- `app/static/index.html`, `app/static/styles.css` — **done**. Design: "shelf-tag" identity — cool paper `#EEF0EC`, ink `#1C2321`,
  Israeli shelf-label yellow `#FFD23F` used only for the winning store's price tag, promo red `#C8102E`. Fonts: IBM Plex Sans (+Hebrew)
  for UI, Barlow Condensed for prices. Dark mode via prefers-color-scheme. Mobile: Basket/Prices tabs (body[data-tab]).
- `tests/make_fixtures.py` — generates multi-chain synthetic dumps in the real dialects (7 chains, online stores, weighed tomatoes,
  promos incl. a club-only one). `python tests/make_fixtures.py /tmp/fx/dumps && python -m app.indexer --dumps /tmp/fx/dumps --db /tmp/fx/basket.db`.

## Status (2026-11-04) — live on Omarchy
Canonical copy: `omarchy:~/Projects/il-prices-app` (Tailscale host `omarchy`). URL: http://omarchy:8080.
Git repo since 2026-11-05 (branch main, local only). Sessions run on omarchy itself: edit here, `./install-app.sh`
(idempotent; restarts `sal-web`). The indexer timer runs code from this folder: stop `sal-index.timer` while editing.
Services (systemd --user): `il-prices.timer` (downloader, every 3h), `sal-index.timer` (every 30 min:
`app.indexer` then `app.geo`), `sal-web` (uvicorn :8080). DB `~/il-prices/basket.db`.
Built since handoff: `app.js`; `install-app.sh`; address + max-distance filter and drive times (`app/geo.py`:
Nominatim geocoding cached in `geocache`, CBS town codes in `towns`, OSRM typical drive times, Google Routes
live traffic if the user saves a key); search merges per-chain produce and falls back to best partial match.
(The old "≈" full-basket estimate was removed on 2026-11-05: see Confirmed-only comparison.) Home preset: the owner's address, 5 km.
Real-data fixes: City Market "spend ₪X" promos (`MinPurchaseAmount`, thousands of items) were attached to
every product — now skipped. City is often a numeric CBS code. Many stores lack names ("Branch N").
Shop UI (2026-11-04, per user): header search + results grid, home = category tiles + deal/pick/classic shelves,
product cards with Add/−qty+, slide-in cart, "Checkout" = the comparison page (nothing is ever ordered).
`app/catalog.py`: categories = name-START words (FTS `^word*`; "word!" = exact). `app/deals.py` → `deals` table
(savings vs TYPICAL avg price; no club/staff/"spend ₪X"), run in sal-index after geo. Photos: Open Food Facts by
barcode via `/api/img/{code}` (in-memory cache). User data (carts, snapshots, settings) lives in `user.db`, attached
as `u`, so saves never wait on the indexer's write locks (backup of the pre-split tables: ~/il-prices/user-backup-20261104.sql).
Photos (2026-11-05): Rami Levy catalogue shots by barcode (img.rami-levy.co.il/product/<code>/medium.jpg),
white background flood-filled to transparent, saved as ~/il-prices/photos/<code>.webp, served at /photos/. `app.images`
runs nightly (sal-photos.timer), deal products first then by popularity; misses remembered 30 days (missing.json).
Open Food Facts was tried and dropped (crowd phone photos; API throttles). Photos are for private household use only.
Category icons: duotone line art generated with Gemini (one 4x4 grid), cut by tests/slice_icons.py into <slug>-line.png +
<slug>-accent.png masks in app/static/cat, painted via CSS masks (ink + --tag) so they follow light/dark.
Browse/filter: /api/browse?terms=a|b&cat=&size=&deal=&sort= — each word is a separate AND filter chip; "narrow down"
chips are frequent words in result names (brands are in names: 95% of manufacturer fields are blank); sizes parsed
from names. URL: #/s/<terms>?size=&deal=1&sort= or #/c/<slug>?q=<terms>; Back undoes the last filter.
Carts vs basket (user's terms): a CART is a named collection (several, switchable in the slide-in cart: tabs, rename,
duplicate, delete; Add goes to the active cart). The BASKET is every product across all carts — "Deals in your
baskets" (/api/deals?kind=basket) and the "Your basket" shelf use it. "Started this week" = chains' own promo start dates.
Home map: MapLibre (app/static/vendor) + OpenFreeMap positron restyled in Sal colours (roads/stores/home yellow), RTL
plugin for Hebrew labels, /api/stores/near; overlays re-added on every style.load (theme switches use diff:false).
MapLibre only draws on animation frames: in a hidden browser pane it waits (not a bug).
Live-cart shop (2026-11-05): header = brand | search centred | address pill + theme. Desktop (>=1100px): the cart is a
sticky right column (<aside id="cart">) on every page: cart tabs, rows with photo/stepper/line price at the pinned store
(default cheapest; tap a store in the cart or a map pin to re-price), "Not at X" for missing items (no estimate),
top 4 stores. Phones: bottom total bar + the same aside as a slide-in sheet (body.cart-open). Cards in the cart get a
yellow ring + "In cart" badge (CSS :has). Checkout (#/basket) = preview only: map with price pins (cheapest yellow),
"Save these prices" (manual snapshot now stores per-store per-product [cost, qty] + names/qty) and "What changed"
vs the last manual save (same branch only), ranked stores with drive times, and a store-by-store product table
(pick up to 5 chains; cheapest per product in yellow; split-shop saving). Map roads are dimmed on purpose.
Search (2026-11-05): no dropdown. Typing in the header box (2+ chars, 180 ms debounce) opens #/s/<text> once (one history
entry; Esc or clearing returns to where you were) and then updates the product grid in place (history.replaceState);
/api/browse also takes a barcode (digits, 4+). Category rail: a sticky left column at >=1280px (it hides the home category
tiles and the browse category chips); below that the tiles/chips stay.
Branches + store card (2026-11-05): compare() also returns `branches` (every nearby physical branch priced for the cart,
full-coverage first, then cheapest). One "All branches" switch (cart column + map pill, localStorage 'branches'): off =
map dots are each chain's cheapest branch and the cart lists the top chains; on = all branches on the map and a scrolling
list of all of them, cheapest first. The selected store (pinned chain's cheapest branch, or a tapped branch: S.sel) gets
a card under the map (#store-card, moves with the map): address (CBS town codes resolved), road km + drive time, route
drawn in yellow (OSRM /route, GET /api/store/{chain}/{store}), Waze + Google Maps links, opening hours from
OpenStreetMap (GET .../hours; one Overpass query for the whole area, cached in ~/il-prices/hours.json for 14 days,
10-min retry when Overpass is busy; only shops of the same chain within 300 m count). OSM has hours for ~17 of 250
nearby branches; otherwise the card links to Google Maps. Tests: tests/test_store.py.
Map as backdrop (2026-11-05): no frame, no zoom buttons, the cooperative-gesture hint hidden (plain scroll scrolls the
page; pinch / ⌘+scroll zoom); the canvas is masked to fade into the page at every edge (markers stay sharp); at checkout
it opens fitted to the selected store's route (fitRoute; edge() padding keeps it out of the faded margins), else the radius.
Home map (2026-11-05, per user): flush under the header's yellow line with no top fade (sides + bottom fade stay); always
fitted to the distance circle with small padding (fitMap/circlePad; a new radius zooms in/out; tapping a store doesn't
zoom to its route there); the Recent pills sit under the map.
Price history (2026-11-05, replaces the saved-prices/snapshot mechanic, which is removed — the old u.snapshots table
is left unused): indexer.record_history() runs after every index run and writes price_history(key, chain_id, day, mn, mx, n)
= each product's cheapest/dearest current shelf price per chain, one row per day it changes (today's row rewritten until
midnight; price_last = latest row per pair; settings.history_since = first day). ~683k pairs; ~5 s. API: GET /api/history?key=
(per chain, change-only points), POST /api/history/cart {items, chains} -> per-chain cart totals by day + 30-day changes
per product. Page: product page "Price history" chart, checkout "Price history" (chains that have the whole cart, lines
scaled to end at today's price at your branch), ▲/▼ 30-day arrows in Compare Stores. Charts: step lines, fixed colour per
chain (validated categorical palette --s1..--s8), legend + end labels, hover readout, hidden table. Tests: tests/test_history.py.
Copy: the user dislikes filler text ("slop") — no taglines, explanatory notes or subtitles; labels only.
Tailscale: also served at https://omarchy.<tailnet>.ts.net:8443 (tailscale serve, tailnet only; port 443 is another app).
Cards (2026-11-05): server `_enrich()` (browse, picks, deals) adds `near` = cheapest shelf price at a branch within your
distance (15 km without one; nearest of equal prices; weighed produce priced across chains via norm), `pack` and `per`
(price per 100 g / litre, like shelf labels). Pack size: chain fields first; a size in the name only if it agrees with a
unit-less quantity (names carry other numbers: "PRO 20 גרם" = protein). The indexer now fills a product's quantity/unit
from a later chain when the stored one is missing or "לא ידוע", so unit prices fill in as daily PriceFull files arrive.
Test: .venv/bin/python tests/test_cards.py.
Left: git repo (none yet); category ranking could prefer each category's core words; the price files' own
UnitOfMeasurePrice field isn't indexed (would give official unit prices); the shop-from-a-list feature was skipped.
