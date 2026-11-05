# Sal-Kal (סל קל, "light basket"; formerly Sal) — Israeli grocery basket price comparison

Handoff from a Claude cloud session (2026-11-04). Read this first, then continue from "What's left".

## What the user wants
A premium web UI, self-hosted on their always-on **Omarchy (Arch Linux) home server** in Israel, where they can:
- Build a basket: search products (name or barcode), add, set quantities.
- See what the full basket costs at every supermarket chain. **Cheapest first and largest/most visible; the most expensive at the bottom, collapsed.**
- Save carts with names; edit them; compare specific carts and compare prices **over time**.
- See whether a price is the **online store price or the physical-store price**, and what **home delivery** would cost (delivery fee, thresholds, minimum order).
- The user is reachable from their MacBook Air, which already has SSH access to the server. They use Hebrew product names; UI copy is English.

## Data source (no API — legally mandated XML files)
Israel's Food Act (חוק קידום התחרות בענפי המזון והפארם, 2014; price publishing in force since May 2015, chapter 3,
§30) requires a "large retailer" (3+ stores with combined yearly sales over ₪250M, indexed: ₪294.3M in 2026, or an
online store over that figure) to publish Stores, PriceFull/Price and PromoFull/Promo files publicly, each price
updated within 1 h of the till. §30(ה): anyone may use the data, free, privately or commercially. The files don't
bind the till price; the shelf tag does (Consumer Protection Law §17ב(ד)). Portals differ (Shufersal's own site, Cerberus/
publishedprices for Rami Levy & others, Carrefour/publishprice, Bina, Laibcatalog API for Victory/H. Cohen/Mahsanei HaShuk).
Some portals block non-Israeli IPs — that's why everything runs on the home server.

Downloader: OpenIsraeliSupermarkets/israeli-supermarket-scarpers (maintained; the old erlichsefi repo just points there).
33 active chains (see `il_supermarket_scarper/scrappers_factory.py`). Deprecated: Mega, Quik, Cofix (folded into Rami Levy), Victory old source, etc.
Python must be <3.13 (setup.py says `>=3.8,<3.13`), and it needs Playwright Chromium for some chains.

### Step 1 — downloader install
`install-downloader.sh` in this folder (sudo). It installs to `/srv/sal/downloader` (src/, venv/), dumps go to
`/srv/sal/data/dumps/<ChainFolder>/`, the system timer `sal-download.timer` runs every 3h at :17 as user `sal`,
files older than 2 days are pruned (ExecStartPre; the old run.sh's prune never worked). Check with
`systemctl status sal-download` and `journalctl -u sal-download -f`.

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
Prices newer than the last confirm (`confirmed_through` setting) count at once when within OUTLIER of
`products.median`, else "Being checked". `item_names` keeps every distinct name per (key, chain) with ts; only names
from the last `trust.NAMES_DAYS` (3) count. One-off indexer flags: --rekey, --names, --promos-again, --rebuild-history.
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
- `app/db.py` — SQLite schema (WAL). DB at `$BASKET_DB` or `/srv/sal/data/basket.db`.
- `app/indexer.py` — `python -m app.indexer [--dumps /srv/sal/data/dumps] [--db ...]`. Incremental (tracks files by path+size),
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
Git repo since 2026-11-05 (branch main), pushed to the PUBLIC GitHub repo rg1989/sal-kal (history scrubbed 2026-11-05: no home address, owner email, tailnet name or other home services; keep it that way, and never commit auth.json, keys or user data). Sessions run on omarchy itself: edit here, `./install-app.sh`
(sudo, idempotent) copies app/ to /srv/sal/code and restarts `sal-web`; services run that copy, never this folder.
Sandboxed (2026-11-05, per user: omarchy also runs other private home services, and Sal will face the internet): everything
runs as system user `sal` from `/srv/sal` (own btrfs subvolume: python/, code/, venv/, downloader/, ms-playwright/
root-owned; data/ = basket.db, user.db, photos/, hours.json, dumps/, the only writable place). System units in
deploy/systemd: `sal-web` (uvicorn on /run/sal/web.sock, socket-activated), `sal-lan` (port 8080 → the socket, for the
LAN and `tailscale serve`), `sal-index.timer` (every 30 min: indexer, geo, deals), `sal-photos.timer`, `sal-download.timer`,
`sal-tunnel` (Cloudflare, off until /etc/cloudflared/sal.yml exists: deploy/cloudflared-sal.yml.example).
`deploy/systemd/sal-.service.d/sandbox.conf` applies to every sal-*.service: no /home, /run/user, devices or D-Bus;
read-only except /srv/sal/data; IPv4 to public addresses only (loopback/LAN/Docker/Tailscale dropped, DNS stub allowed,
no IPv6). Prove it: `sudo systemctl start sal-check; journalctl -u sal-check` (deploy/sandbox-check.py).
Logs: `journalctl -u sal-web` (system units, not --user). Inspect data: `sudo -u sal sqlite3 /srv/sal/data/basket.db`.
One-off indexer runs: `sudo -u sal env BASKET_DB=/srv/sal/data/basket.db /srv/sal/venv/bin/python -m app.indexer ...`
from /srv/sal/code. `~/il-prices` is the pre-move copy (2026-11-05 18:20), unused.
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
as `u`, so saves never wait on the indexer's write locks (backup of the pre-split tables: /srv/sal/data/user-backup-20261104.sql).
Photos (2026-11-05, multi-source): app.images runs every catalogue at once, one worker per source at its own adaptive pace (several for CDNs): Pricez CDN (m.pricez.co.il/ProductPictures/<bc>.jpg, best coverage), GS1 photos (d226b0iufwcjmj.cloudfront.net/gs1-products/1062/large/<bc>.jpg), Super-Pharm blob store, Rami Levy, Tiv Taam stor.ai API (4 s crawl delay, rests at the first 403: its Radware WAF blocked this IP at Yochananof), Shufersal search JSON (1 req/10 s per robots.txt) and chp.co.il (140 px, fallback only after general sources miss). Each source starts with its own chain's products; one photo per product; misses per source in photos/missing.json; bot checks are never worked around (Carrefour, Victory, Super Sapir, Keshet, Mahsanei HaShuk, Quik are Cloudflare-blocked). Runs last 50 min (hourly timer); cart products first; the web server fetches photos at once for products you open or add to a cart. ~7,900 photos/hour. Fresh Market API (robots Disallow), Savy (private backend) and Open Food Facts (crowd photos) are deliberately not used.
White backgrounds are flood-filled to transparent; saved as /srv/sal/data/photos/<code>.webp, served at /photos/.
Photos are for private household use only.
Category icons: duotone line art generated with Gemini (one 4x4 grid), cut by tests/slice_icons.py into <slug>-line.png +
<slug>-accent.png masks in app/static/cat, painted via CSS masks (ink + --tag) so they follow light/dark.
Browse/filter: /api/browse?terms=a|b&cat=&size=&deal=&sort= — each word is a separate AND filter chip; "narrow down"
chips are frequent words in result names (brands are in names: 95% of manufacturer fields are blank); sizes parsed
from names. URL: #/s/<terms>?size=&deal=1&sort= or #/c/<slug>?q=<terms>; Back undoes the last filter.
Carts vs basket (user's terms): a CART is a named collection (several, switchable in the slide-in cart: tabs, rename,
duplicate, delete; Add goes to the active cart). The BASKET is every product across all carts.
Home = discovery (2026-11-05, per user): map, Recent pills, then deal rows that never repeat the basket: "Biggest
savings near you", "Started this week" (chains' own promo start dates), "Ending soon" (ends today..day after;
/api/deals?kind=ending) — all with skip_basket=1, no product twice on the page — then Recently viewed (minus the
active cart). The "Deals in your baskets" and "Your basket" shelves were removed (/api/picks, kind=basket unused).
Promo start/end dates were NULL for all chains but Wolt/Keshet until the PromotionStartDateTime fix (d75e2da); the
indexer reads files once, so old promos get dates as new PromoFull files arrive, or at once with --promos-again.
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
entry; Esc, the × button or clearing returns to where you were, home if you came by a link) and then updates the product
grid in place (history.replaceState); on #/s/ pages the box shows the search terms; the logo clears it and goes home;
/api/browse also takes a barcode (digits, 4+). Category rail: a sticky left column at >=1280px (it hides the home category
tiles and the browse category chips); below that the tiles/chips stay.
Branches + store card (2026-11-05): compare() also returns `branches` (every nearby physical branch priced for the cart,
full-coverage first, then cheapest). One "All branches" switch (cart column + map pill, localStorage 'branches'): off =
map dots are each chain's cheapest branch and the cart lists the top chains; on = all branches on the map and a scrolling
list of all of them, cheapest first. The selected store (pinned chain's cheapest branch, or a tapped branch: S.sel) gets
a card under the map (#store-card, moves with the map): address (CBS town codes resolved), road km + drive time, route
drawn in yellow (OSRM /route, GET /api/store/{chain}/{store}), Waze + Google Maps links, opening hours from
OpenStreetMap (GET .../hours; one Overpass query for the whole area, cached in /srv/sal/data/hours.json for 14 days,
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
Accounts (2026-11-05, per user): sign in with Google or Facebook (`app/auth.py`, OAuth code flow on the standard library,
PKCE for Google, appsecret_proof for Facebook). Config `/srv/sal/data/auth.json` (0600, re-read on change, no restart), set
with `./set-login.sh google <client id>` / `facebook <app id>` (asks for the secret), `base-url https://<domain>`,
`owner-email`, `status`. Redirect URIs to register: `<base>/auth/google/callback`, `<base>/auth/facebook/callback`; Meta also
needs `/privacy` and `/data-deletion` (both served, he/en). No provider configured = "local" mode: everyone is user 1, no
sign-in (as before). With one, every /api call except /api/me, /api/status, /api/logout needs the `sal_session` cookie
(HttpOnly, SameSite=Lax, 180 days rolling; only its SHA-256 in u.sessions). user.db: `users` (prefs + delivery JSON per
account, lang, onboarded, admin), `identities` (provider, subject, email, verified), `sessions`, `carts.user_id`. The
household's pre-account carts/prefs/delivery became user 1; the first sign-in takes it over (only `owner_email` if set)
and is admin (reindex, Google Maps key). Accounts join by email only through a verified address (Google);
Facebook never joins by email. server.py: `me()` / `prefs()` / `overrides()` read a contextvar set by the `who`
middleware; every cart query filters `user_id`; per-account rate limits on outside lookups (`limit()`).
Tests: tests/test_accounts.py. Local testing: `SAL_DEV_LOGIN=1` adds a "Dev" provider (`/auth/dev?who=name`) — never on
the server; a test copy of the DB can be made with `cp --reflink` (btrfs, no extra space).
Address (onboarding + header pill): picked, never typed free-form: town and street from Israel's official lists
(data.gov.il towns 5c78e9fa…, streets 9ad3862c…, kept in places.db, refreshed every 90 days, Hebrew + CBS English
names, any word order, קרית/קריית folded, English matched by consonant skeleton) + a house-number stepper, or the phone's
location (reverse-geocoded, matched to the lists). `app/places.py` locates the pick once via Nominatim (1 req/s server-wide,
official name → OSM spellings, surname-first swap, then street, then town centre) and caches it. Endpoints
/api/places/{towns,streets,locate,reverse}. Prefs.home keeps label, lat, lon, town, street, number, precision.
Street names (2026-11-05, after "דרך בגין" couldn't be found as "מנחם בגין" and was saved at the town centre): the list
drops first names and abbreviates types (שד, רח, דרך), so a loose second pass (`_loose`) matches the name's distinctive
words, one left-over typed word allowed only when nothing fits exactly; locate() also tries the name without its type
word (the road found must carry its words), then the nearest number on the same side (precision "near", `near`: n).
The town centre (`centre:<town>`) and the spelling that found each street (`via:<town>:<street>`) are cached, so a new
number costs 1-2 requests. A town-centre point for a picked street is an error in the picker (never saved); "street"
and "near" pins say so in red, and saved places that aren't exact are flagged. Map pins: the house is the Home place,
the briefcase any other, whichever end of the trip. Tests: tests/test_places.py.
First run (account not onboarded): 3 steps — address, distance (map + live branch count), what carts are + name the
first cart. Hebrew is the default language (he/en): `app/static/i18n.js` (`t('English text', vars)`, `tn(n, noun)`,
`cn(chain)` Hebrew chain names, `serverText` for engine/chains notes, data-i18n* attributes in index.html), the account's
`lang` follows it across devices; `<html dir=rtl>` mirrors the page (CSS uses logical properties: rail on the right,
cart on the left). Language: globe menu in the header (wide screens), the account sheet, a toggle on sign-in/onboarding.
`app/static/ux.js`: haptics (Vibration API on Android, the hidden `<input switch>` tick on iOS 18, only after a first
tap), the loading bar (every api() call), page fade-in, fly-to-cart, bottom sheets on phones with drag-to-close,
skeletons, confirmSheet(). Script order: i18n.js, ux.js, app.js, account.js (account.js boots and calls startApp()).
Public (2026-11-05): https://sal-kal.com (and www.) via the locally managed Cloudflare Tunnel `sal-kal`
(id c3e83818-4866-4cd7-a919-22095cb88731; /etc/cloudflared/sal.yml + its credentials; `sal-tunnel.service`, sandboxed;
DNS CNAMEs made with `cloudflared tunnel route dns`). The Cloudflare account has other tunnels:
not Sal's, leave them. auth.json base_url = https://sal-kal.com. Requests through the tunnel carry Cf-Connecting-IP:
they always need sign-in, so with no provider set up they get only the sign-in page ("sign-in opens soon") and /privacy,
/data-deletion; the LAN and Tailscale (no such header) stay in local mode. Signing in works on sal-kal.com only (the
redirect URIs and Secure cookies are https://sal-kal.com).
Landing (2026-11-05, per user): signed-out visitors get app/static/landing.html at "/" (signed-in: the app; /app always
the app and its sign-in; /about always the landing). Bilingual by `<span lang>` pairs, flags in every language picker,
autoplaying walkthrough video + phone/laptop mockups in app/static/landing/ (recorded with Playwright from a test server
and a demo account at Tel Aviv City Hall, never a real home), live counts from /api/status, the law (section 30,
price-transparency regulations) linked. Credit: "Built by Roman Grinevich" (LinkedIn) + GitHub (rg1989/sal-kal) in the
landing footer, the app footer and the account sheet. Mobile audit script (overflow, clipped text, overlaps, small targets)
lives in the session scratchpad; fixes landed in styles.css (fee cards on phones, onboarding map height, radius row, pins).
Phones (styles.css "made for a thumb" block, <=640px): every control >=44px tall, or 40px with a ::after hit area past its
edges; labels never wrap (short labels: "Club prices", "Cheapest per chain", "Waze" / "Google Maps" with full aria-labels);
control rows stay one row (checkout mode switch full width; deal + sort share a row; cards get a full-width Add); cart rows
put the stepper under the price; checkboxes are custom (yellow, ink tick), switches keep [switch]. Check at 320/360/390/430 in he and en.
Copy: the user dislikes filler text ("slop") — no taglines, explanatory notes or subtitles; labels only.
Tailscale: also served at https://omarchy.<tailnet>.ts.net:8443 (tailscale serve, tailnet only; port 443 is another app).
Cards (2026-11-05): server `_enrich()` (browse, picks, deals) adds `near` = cheapest shelf price at a branch within your
distance (15 km without one; nearest of equal prices; weighed produce priced across chains via norm), `pack` and `per`
(price per 100 g / litre, like shelf labels). Pack size: chain fields first; a size in the name only if it agrees with a
unit-less quantity (names carry other numbers: "PRO 20 גרם" = protein). The indexer now fills a product's quantity/unit
from a later chain when the stored one is missing or "לא ידוע", so unit prices fill in as daily PriceFull files arrive.
Test: .venv/bin/python tests/test_cards.py.
Installable app (2026-11-05, PWA, no app stores): `manifest-he.webmanifest` / `manifest-en.webmanifest` (id "/",
standalone; pwa.js switches the <link id="manifest"> and the iOS home-screen title with the page language; icons
pwa-192/512 rounded, icon-512 + pwa-maskable-192 full-bleed for Android's mask). `sw.js` is served at /sw.js (server
route, no-cache): it stores /static only (?v= URLs as is, other files refreshed in the background; never /api, /auth,
/admin, photos or /static/landing). "/" and "/app" always come from the network, with a "No connection" page offline.
Kill switch: deploy a sw.js that calls self.registration.unregister(). Install buttons: any [data-install] element
(the sign-in page's button, shown signed out) and a row in the account sheet. Chrome/Edge/Samsung open their own install
dialog once Chrome offers it (beforeinstallprompt, held by pwa.js; Chrome waits for a tap and ~30 s on the page).
Before that, Android gets the steps sheet (#install: browser menu ⋮ › Install app). iPhone/iPad and Mac Safari always
get the steps. /app?install opens the steps (for pages without #install, e.g. the landing page). Hidden once installed
(display-mode, or getInstalledRelatedApps via related_applications in the manifests). Needs https (sal-kal.com or the
Tailscale URL); http://omarchy:8080 can't install.
Not yet tested on a real iPhone: Google/Facebook sign-in inside the home-screen app (iOS keeps its cookies apart
from Safari's).
Admin + usage statistics (2026-11-05, per user): https://sal-kal.com/admin, only for the owner's Google sign-in with
the address verified by Google (`auth.OWNER_EMAIL_SHA256`, hard-coded as a hash so the public repo doesn't carry the
address; `auth.is_owner`); everyone else, and every /api/admin/* call, gets 404. `app/stats.py` keeps first-party
stats in /srv/sal/data/stats.db (own file, one writer thread, queue: requests never wait): visits (device/OS/browser
parsed from the UA, in-app browser, Cloudflare country/city, screen, referrer host, utm), events (page views + time,
searches with result count and outcome, adds, filters, clicks by control label, rage/dead clicks, JS errors, failed
requests, error toasts, load times; server side: sign-in start/done/fail, setup, address/distance, carts), and per-
route request counts with a latency histogram (middleware). No IPs, no UA strings, no typed values; 180 days (requests
400); account deletion calls `stats.forget` (its events and its browsers' anonymous ones). Browser side:
`app/static/usage.js` (also on landing.html) posts to /api/ux (open, size/rate capped); app.js `api()` sends
`X-Sal-V: <browser id>.<visit id>` so server events join the visit; the sign-in link carries `v=` through OAuth state.
The owner's own visits are flagged (admin=1, back to before sign-in) and hidden unless "Include my visits".
Page: app/admin/{admin.html,admin.js,admin.css}, served only through the gated routes (CSP: no inline script).
Tabs: overview (KPIs vs the period before, visitors/day, new-browser funnel, weekday×hour heatmap, here now), where
people get stuck, usage, audience, users (+ per-user timeline, carts, visits), server, price data.
Tests: tests/test_admin.py (drives the ASGI app directly; httpx isn't installed). City needs Cloudflare's "Add visitor
location headers" managed transform.
Reports to sell (2026-11-05, per user: the data is the monetizable part): /admin#reports, `app/reports.py` +
app/admin/{reports.js,reports.css}, GET /api/admin/reports (what we hold) and /api/admin/reports/{name} (JSON, or
?format=csv[&part=] with a BOM for Excel; print view for PDF). Built ONLY from the public price layer (price_last,
price_history, deals, prices under indexer.CONFIRMED_ROW): never carts, accounts, searches or stats.db, so the privacy
page's "no sale of information" stays true. Reports: chain price index (geometric mean vs each product's median across
chains, per category), basket over time (household basket: top products per category that every broad chain sells, or
pasted barcodes), price changes, deals by chain, price gaps (across chains / across a chain's branches: needs 4+ stores
and 2+ chains so carton-vs-pack and internal codes stay out), product tracker (words, barcode prefix = a maker, or
barcodes). Cached 15 min per data version. History began 2026-11-05: time reports grow more valuable daily.
Tests: tests/test_reports.py.
Sign-in only on the base address (2026-11-05): a first Google sign-in started on http://omarchy:8080 failed ("no state
cookie": the cookie was on the LAN host, Google returns to sal-kal.com). With accounts on, GET /, /app, /about, /admin
and /auth/* from any other host get a 307 to auth.json base_url (/api stays); failures land on /app#/signin?signin=…
(the landing page at / can't show them). Tests: tests/test_signin.py.
Loading (2026-11-05, per user): never "empty" before data: S.cartsLoaded gates the cart (cartSkeleton rows, storeSkeleton)
and checkout (resultsSkeleton); S.comparing shows a shimmer for each cart line price; showView() switches to the page in
the address with its skeleton before settings/carts arrive; the home's first deal shelf and category tiles show skeletons.
Two yellows (per user, 2026-11-05): --tag (bright #FFD23F) only for what matters: Go to comparison, the chosen store
(cart row, store card, selection ring, route: mapColors().pick), the cheapest (map pin, results winner, cheapest cells),
the product page's Add to cart, the logo, and deals. --accent/--accent-edge (muted gold; dark #BFA14A) for decoration:
header line, heading bars, scrollbars, loading bar, pills and segments, rail selection, in-cart steppers, everyday price
tags, switches, focus rings, thumbnails, the map's roads, dots and circle. Deals (red read as an error; green was tried,
then back to yellow): --deal = the bright yellow with --deal-ink; text and icons on paper use --deal-text (#8A6400 light,
the yellow on dark). --promo stays for errors, price rises, "closed", danger. A deal's own wording is a circled (i)
(INFO_I, the usual info mark, in --deal-text) beside a cart line's price (dealTip) and before a card's name (cardDealTip; the green
description line on cards is gone): ux.js [data-tip] tooltip on hover, focus or tap. Any text cut by an ellipsis or a
line clamp shows in full in the same tooltip on hover (clipped()). Card discount badge: big "69%", no minus (saveBadge).
Card Add is a round "+" (an SVG plus; "+2"/"+3" keep the number at text size) for a deal's quantity (addBtn), beside the
price on phones too; in the cart it's a compact − n + beside the price (cards 196px, 172px on phone shelves; slim buttons
so "125" fits at 360px wide; it wraps under only if a long price leaves no room). Map price pins (drawPins) never shrink
to dots: overlapping pins stack, the picked store on top, then cheapest to dearest; the picked store always has a pin
even outside the top four. "Go to comparison"
(מעבר להשוואה, cart icon, yellow) in the cart and at the home map's cart-side bottom corner (#map-go, hidden when empty).
The home map's extra dark strip (--map-ext) is measured by sizeMapExt() to end where the first list (shelf, or category
tiles) starts; the button sits in that corner beside the first heading; the sections over the map let clicks through
their empty parts (pointer-events) so the button and the map still take them.
Places + on the way (2026-11-05, per user): an account has named places (prefs.places [{id, name, address}], up to 12;
older accounts' single `home` becomes place "home", and the first-run setup's `home` sets the place you're around).
Two modes, a bar above the map on home and checkout (#where): "Around a place" (prefs.at + max_km) and "On the way"
(prefs.area "route", from -> to, travel car/bike/foot, detour_km from that way's choices: commute.DETOURS car 0.2-3 km,
bike 0.2-1, foot 0.1-0.2 (per user: a walk is 200 m off the way at most); default 1/0.5/0.2; a saved value from another
way snaps to the nearest choice, commute.detour_km(), the same in app.js detour()). Compare never takes places from the
request, only picks among the saved ones (at/from/to): a page opened before Work's address was fixed elsewhere kept the
old town-centre point and its stops followed a route the map didn't show (2026-11-05); the page also adopts the saved
places from each compare (syncPlaces), and a places edit is compared once saved. server.effective()
(and derive() in app.js, the same rules) works out `home` (the place you're around, or where the trip starts) and
`work` (where it ends), so all the older code reads prefs.home. app/commute.py: OSRM route (project-osrm.org for cars,
routing.openstreetmap.de routed-bike/routed-foot), cached a week; down = straight line, never cached as the route (the
service is left alone for that trip for a minute: commute._down). corridor() = branches
within the detour of the route ({sk: (off_km, at_km)}), only branches geo.exact_stores() places at their own address (a
town-centre point is a few km off: ~1,260 of 2,300 qualify; better address cleanup in geo.queries would add more).
engine.compare(way=...) prices those instead of the radius (distance_km = off the route) and returns way.plans:
commute.plans() = the cheapest split of the cart over 1..5 stops, each product at its cheapest stop, confirmed lines
only, each extra stop must save >= ₪1 (exact for 1-2 stops, a beam search beyond; shortlist of 60 branches). Cards'
"near" price, deals, the map's stores and opening hours (per branch area) follow the same corridor. POST /api/way/trip
= the trip through the stops and what it adds over going straight. Checkout: #way-plan (stop counts, stops in order with
what to buy, +min/+km, Google Maps through the stops); the map draws the band, the trip and numbered stop pins.
Landing: the "On the way" section right after the hero (phone-way-*.webp, demo account at Tel Aviv City Hall -> the
Ramat Gan diamond exchange towers by bike). Tests: tests/test_commute.py, tests/test_way_server.py.
Attack surface (2026-11-05, per user: the only way in is the public URL). `app/guard.py` + checks in server.py:
`guard.Door` (outermost ASGI) caps bodies at 256 KB (read in full, 30 s) and counts each internet visitor
(Cf-Connecting-IP; the LAN/Tailscale aren't counted) in token buckets (API 10/s burst 300, files 40/s, 12 API calls
open at once); signed-in calls also per account (5/s). Every metered API call (all but OPEN, the owner's, and
guard.WAITS that mostly wait on outside services) is charged the seconds it took to a compute budget per address
(0.5 s/s, 120 s burst) and per account (0.35 s/s, 90 s): the one Python process can't be monopolised. Heavy pages
(guard.HEAVY: compare is ~20 s for 400 items without an address, browse 1-3 s) queue one at a time per account,
4 at once site-wide. Outside services go through `guard.outside(name)`: site-wide rate + concurrency (OSRM 60/min,
FOSSGIS 30/min, Overpass 30/h, Google Routes 1500/day then OSRM, Nominatim 2 waiting): no visitor can get this IP
banned there or pile up threads. Opening hours: the Overpass query runs outside the lock, 6 new areas/h per account,
radius ≤ 30 km. Inputs: result limits capped, search ≤ 12 words/200 chars, prefs/delivery validated (delivery: 13-digit
chain ids, numbers, ≤ 80), compare's request prefs cleaned (an address abroad is dropped), carts' photo fetches only
for real products. Writes (POST/PUT/DELETE) with a foreign Origin get 403. stats.db: site-wide daily caps and a 2 GB
ceiling (it shares the root disk with every home service). Headers: CSP on every HTML page (script-src 'self' + the
pages' inline scripts by hash + the two handlers `this.remove()` / `location.reload()` via 'unsafe-hashes'; any new
inline script or onX= attribute is blocked: add a .js file or a listener), HSTS on the internet, Permissions-Policy,
COOP. No /docs or /openapi.json. sal-web: MemoryMax 3G, TasksMax 256, CPUQuota 400%, uvicorn --limit-concurrency 400;
sal-tunnel MemoryMax 1G. Tests: tests/test_security.py. Several sessions work in this folder at once and deploy
selected files: before deploying, compare /srv/sal/code/app with HEAD and your working copy, and install only your
own files.
Left: category ranking could prefer each category's core words; the price files' own
UnitOfMeasurePrice field isn't indexed (would give official unit prices); the shop-from-a-list feature was skipped.
