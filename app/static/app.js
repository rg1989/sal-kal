'use strict';
// Sal frontend: vanilla JS, no build. Talks to app/server.py.

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = v => v == null ? '—' : '₪' + Number(v).toFixed(2);  // ₪ first in both languages, like the shelf labels
const signed = v => (v > 0 ? '+' : '−') + '₪' + Math.abs(v).toFixed(2);
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const local = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};
const tsDate = t => {  // YYYYMMDDHHMM -> Date
  const s = String(t || '');
  return s.length < 12 ? null : new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8), +s.slice(8, 10), +s.slice(10, 12));
};
const fmtDate = d => d.toLocaleString(locale(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
const plural = (n, w) => tn(n, w);
const kmText = d => d == null ? '' : d < 1 ? t('{n} m', { n: Math.max(100, Math.round(d * 10) * 100) }) : t('{n} km', { n: d < 10 ? d.toFixed(1) : Math.round(d) });
const bdi = v => `<bdi>${esc(v)}</bdi>`;
// chains fill empty fields with placeholders like "לא ידוע" (unknown), "0" or ","
const clean = v => { const t = String(v ?? '').trim(); return /^(לא ידוע|unknown|כללי|[0.,\-\s]*)$/i.test(t) ? '' : t; };
const qtyText = (q, u) => { const n = parseFloat(q); return [n ? String(+n.toFixed(2)) : clean(q), clean(u)].filter(Boolean).join(' '); };

const SVG = {
  x: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  chev: '<svg class="chev" viewBox="0 0 20 20" aria-hidden="true"><path d="M5 8l5 5 5-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  copy: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="7" width="9" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M13 4.5H6A1.5 1.5 0 0 0 4.5 6v7" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  trash: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 6h12M8 6V4.5h4V6M6 6l.7 9.5h6.6L14 6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

async function api(path, opts = {}) {
  const done = opts.quiet ? null : loading();  // the thin bar under the header while anything loads
  try {
    let r;
    try {
      r = await fetch(path, {
        method: opts.method || 'GET',
        headers: { ...(opts.body ? { 'Content-Type': 'application/json' } : {}), 'X-Sal-V': Usage.ids() },
        body: opts.body ? JSON.stringify(opts.body) : undefined,
        credentials: 'same-origin',
      });
    } catch (e) { Usage.apiFail(path, 0); throw e; }  // offline, or the server is down
    if (r.status === 401 && !opts.noAuth) { signedOut(); throw new Error('signed out'); }
    if (!r.ok) { Usage.apiFail(path, r.status); throw new Error(`${r.status} ${path}`); }
    return await r.json();
  } finally { done?.(); }
}

const S = {
  cart: { id: null, name: t('Weekly shop'), items: [] },  // items: [{key, qty, name}]
  meta: {},           // product key -> {name, manufacturer, is_weighted, min, max, stores}
  prefs: { mode: 'store', club: false, home: null, max_km: null },
  result: null,       // last /api/compare response for the current basket + prefs
  status: null,
  lastAdded: null,
  traffic: false,    // a Google Maps key is set on the server -> live drive times
  hits: {},          // product key -> last search hit (name, size, …) for the product page
  product: null,     // {p, res} on the product page
};
const delivery = () => S.prefs.mode === 'delivery';
const view = c => delivery() ? c.online : c.in_store;
// what this chain costs: confirmed lines only (online: with the delivery fee). Nothing is ever estimated.
const paid = c => delivery() ? (c.online.delivered ?? c.online.total) : c.in_store.total;
// every compared (barcode) product has a confirmed price here; produce is never compared
const full = c => !!(view(c)?.complete ?? c.complete);
const nCompared = (r = S.result) => r?.n ?? 0;
// "all 4" / "3 of 4" / "Not compared" (only produce in the cart)
const countText = (covered, isFull, n = nCompared(), noun = '') => !n ? t('Not compared')
  : isFull ? (n === 1 ? t('has it') : noun ? t('all {n} products', { n }) : t('all {n}', { n }))
  : noun ? t('{a} of {n} products', { a: covered, n }) : t('{a} of {n}', { a: covered, n });
const priceUnit = key => S.meta[key]?.is_weighted ? perKg() : '';
function pricesFrom(ts) {  // "Prices from 09:30" today, "Prices from 4 Nov 22:10" before
  const d = tsDate(ts);
  if (!d) return '';
  const hm = d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' });
  return t('Prices from {when}', { when: d.toDateString() === new Date().toDateString() ? hm : `${d.toLocaleDateString(locale(), { day: 'numeric', month: 'short' })} ${hm}` });
}
// why a price isn't counted (engine `reason`)
const unconfText = l => ({ price: t('Price looks wrong'), noname: t('Name not checked'), new: t('Being checked') })[l.reason]
  || (l.reason === 'name' ? t('Named differently') + (l.shelf_name ? `: ${bdi(l.shelf_name)}` : '') : t('Not confirmed'));
// confirmed deals this quantity doesn't reach: "2 for ₪55"
const dealMoreText = d => t('{n} for {price}', { n: d.units, price: '₪' + (Number.isInteger(+d.total) ? +d.total : (+d.total).toFixed(2)) });
const dealsMore = (l, tag = 'span') => (l.deals_more || []).filter(d => d.units > 0 && d.total != null).map(d =>
  `<${tag} ${tag === 'button' ? `type="button" data-dealqty="${d.units}" ` : ''}class="chip more" dir="auto" title="${esc(d.description || '')}">${dealMoreText(d)}</${tag}>`).join(' ');
const notCounted = l => l.deals_not_counted?.length ? `<span class="chip muted" title="${esc(l.deals_not_counted.join(' · '))}">${t('Deal not counted')}</span>` : '';
// produce at one store: its own item, or items with the same name; null when it has neither
const ncPick = l => l.own && l.price != null ? { name: l.shelf_name, price: l.price } : l.similar?.[0] || null;
const radius = () => S.prefs.home && S.prefs.max_km ? S.prefs.max_km : null;

// ---------------------------------------------------------------- toast
let toastTimer;
function toast(msg, action, fn, kind) {
  haptic(kind || (action ? 'warn' : 'tap'));
  if (kind === 'error') Usage.ev('toast', { a: msg });
  const el = $('#toast');
  (document.querySelector('dialog[open]') || document.body).append(el);  // stay above an open dialog's backdrop
  el.innerHTML = `<span>${esc(msg)}</span>` + (action ? `<button type="button">${esc(action)}</button>` : '');
  if (action) el.querySelector('button').onclick = () => { el.hidden = true; fn(); };
  el.hidden = false;
  el.classList.remove('in'); void el.offsetWidth; el.classList.add('in');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, action ? 8000 : 3000);
}

// ---------------------------------------------------------------- basket
function metaHTML(it) {
  const m = S.meta[it.key] || {};
  const parts = [];
  if (clean(m.manufacturer)) parts.push(bdi(clean(m.manufacturer)));
  if (m.min != null) parts.push((m.min === m.max ? money(m.min) : `${money(m.min)}–${money(m.max)}`) + (m.is_weighted ? perKg() : ''));
  if (m.stores === 0) parts.push(t('not on sale anywhere right now'));
  return parts.join(' · ');
}

// The live cart is always on screen (a column on desktop, a sheet + total bar on phones): every product, its price
// at the store the cart is priced at (the cheapest, or the one you tap), and where the whole cart costs least.
// a chain where nothing in the cart counts (all missing or not yet confirmed) has no total to compare
const counted = c => !nCompared() || (view(c)?.covered || 0) > 0;
const pinned = () => { const ch = S.result?.chains || []; return ch.find(c => String(c.chain_id) === S.pin) || ch.find(counted) || ch[0] || null; };
const lineAt = (c, key) => c ? view(c)?.lines.find(l => l.key === key) : null;
const wide = () => matchMedia('(min-width: 1100px)').matches;

function itemNote(it, c, l) {
  const m = S.meta[it.key] || {}, kg = m.is_weighted;
  if (!l) return metaHTML(it);
  const each = p => it.qty !== 1 ? `${fmtQty(it.qty, kg)} × ${money(p)}` : money(p) + (kg ? perKg() : '');
  if (l.status === 'missing') return `<span class="warn">${t('Not at {chain}', { chain: esc(cn(c.name)) })}</span>`;
  if (l.status === 'unconfirmed') return `<span class="unc">${unconfText(l)} · ${t('Not counted')}</span>`;
  if (l.status === 'not_compared') {
    const p = ncPick(l);
    return `<span class="unc">${t('Not compared')}${p ? ` · ${l.own ? '' : t('Similar') + ' · '}${each(p.price)}` : ` · ${t('Not at {chain}', { chain: esc(cn(c.name)) })}`}</span>`;
  }
  return each(l.price)
    + (l.promo ? ` · <span class="promo" dir="auto">${esc(l.promo.description || t('deal'))}</span>` : '')
    + (l.deals_more?.length ? ' ' + dealsMore(l, 'button') : '')
    + (l.deals_not_counted?.length ? ` · <span class="unc" title="${esc(l.deals_not_counted.join(' · '))}">${t('Deal not counted')}</span>` : '');
}
function itemPrice(it, l) {
  if (!l) return '';
  if (l.status === 'ok') return money(l.cost);
  if (l.status === 'unconfirmed') return money(l.full ?? l.price * it.qty);
  if (l.status === 'not_compared') { const p = ncPick(l); return p ? money(p.price * it.qty) : '—'; }
  return '—';
}
const itemClass = l => !l ? '' : l.status === 'missing' ? ' missing' : l.status !== 'ok' ? ' unconfirmed' : l.promo ? ' deal' : '';

function renderBasket() {
  const items = S.cart.items, c = pinned();
  const a = document.activeElement, li = a?.closest?.('#drawer-items .item');
  const keep = li && { i: li.dataset.i, val: a.matches('input') ? a.value : null,
                       sel: a.matches('input') ? 'input' : `[data-step="${a.dataset.step}"]` };
  $('#drawer-items').innerHTML = items.map((it, i) => {
    const m = S.meta[it.key] || {}, kg = m.is_weighted, l = lineAt(c, it.key), last = it.qty <= (kg ? 0.5 : 1);
    return `<li class="item${it.key === S.lastAdded ? ' added' : ''}${itemClass(l)}" data-i="${i}">
      <span class="lc-thumb">${PH}${photo(m)}</span>
      <span class="item-name" dir="auto">${esc(it.name || m.name || it.key)}</span>
      <span class="item-meta">${itemNote(it, c, l)}</span>
      <span class="stepper">
        <button type="button" data-step="-1" aria-label="${last ? t('Remove') : t('One less')}">${last ? SVG.trash : '−'}</button>
        <input type="number" inputmode="decimal" min="0" step="${kg ? 0.5 : 1}" value="${it.qty}" aria-label="${t('Quantity')}">${kg ? `<span class="unit">${t('kg')}</span>` : ''}
        <button type="button" data-step="1" aria-label="${t('One more')}">+</button>
      </span>
      <span class="lc-price num">${itemPrice(it, l)}</span>
    </li>`;
  }).join('');
  const added = S.lastAdded && $('#drawer-items .item.added');
  S.lastAdded = null;
  if (keep) {
    const el = $(`#drawer-items .item[data-i="${keep.i}"] ${keep.sel}`);
    if (el) { if (keep.val != null) el.value = keep.val; el.focus(); }
  }
  if (added && wide()) added.scrollIntoView({ block: 'nearest' });
  $('#drawer-items').hidden = !items.length;
  $('#drawer-empty').hidden = items.length > 0;
  $('#drawer-foot').hidden = !items.length;
  if (document.activeElement !== $('#drawer-cart-name')) $('#drawer-cart-name').value = S.cart.name;
  $('#cart-label').textContent = $('#co-name').textContent = S.cart.name;
  renderCartTabs();
  refreshCards();  // product cards anywhere on the page show Add or −qty+ for what's in the cart
  updateTotals();
}

function updateTotals() {
  const n = S.cart.items.length, c = pinned();
  const units = S.cart.items.reduce((t, i) => t + (S.meta[i.key]?.is_weighted ? 1 : i.qty), 0);
  $('#basket-count').textContent = n || '';
  $('#lc-sum').innerHTML = !n ? '' : `${plural(n, 'product')}${units !== n ? ` · ${plural(units, 'item')}` : ''}`
    + (c ? ` · ${t('prices at {chain}', { chain: `<strong>${esc(cn(c.name))}</strong>` })}` : '');
  $('#cart-bar').hidden = !n;
  $('#cb-n').textContent = n;
  const v = c && view(c);
  $('#cb-txt').innerHTML = !c ? t('Finding the cheapest store…') : !nCompared() ? t('Not compared')
    : t('{price} at {chain}', { price: `<strong class="num">${money(paid(c))}</strong>`, chain: esc(cn(c.name)) }) + (full(c) ? '' : ` · ${t('{a} of {n}', { a: v.covered, n: nCompared() })}`);
  renderStores();
}

// the stores the cart costs least at; tap one to see every line priced there
function renderStores() {
  const el = $('#lc-stores'), ch = S.result?.chains || [], p = pinned();
  if (!S.cart.items.length) { el.innerHTML = ''; return; }
  const n = nCompared();
  if (!S.result) { el.innerHTML = `<p class="lc-wait"><span class="spin" aria-hidden="true"></span>${t('Finding the cheapest store…')}</p>`; return; }
  if (!ch.length) {
    el.innerHTML = `<p class="lc-wait">${radius() && !delivery() ? t('No store within {n} km has these products.', { n: radius() }) : t('No store has these products.')} <button class="linkish" type="button" data-open-loc>${t('Change distance')}</button></p>`;
    return;
  }
  $('#all-branches').checked = S.allBranches;
  $('#all-branches').closest('.switch').hidden = delivery();
  const bs = S.result.branches || [];
  if (S.allBranches && !delivery() && bs.length) {
    $('#lc-label').innerHTML = `${t('All branches')} <span>· ${t('{n}, cheapest first', { n: bs.length })}</span>`;
    let gap = false;
    const sel = selStore();
    el.innerHTML = '<div class="lc-scroll">' + bs.map((b, i) => {
      const sep = n && !gap && !b.complete ? (gap = true, `<p class="lc-sep">${t('Missing some products')}</p>`) : '';
      const on = sel && String(sel.chain_id) === String(b.chain_id) && sel.store_id === b.store_id;
      const best = i === 0 && b.complete && n > 0;
      return sep + `<button type="button" class="lc-store${best ? ' best' : ''}${on ? ' on' : ''}" data-branch="${i}" aria-pressed="${!!on}">
        <span class="ls-name">${esc(cn(b.chain))}${best ? ` <em>${t('Cheapest')}</em>` : ''}</span>
        <span class="ls-sub">${bdi(storeName(b))}${b.distance_km != null ? ` · ${kmText(b.distance_km)}` : ''} · ${countText(b.covered, b.complete, n)}</span>
        <span class="ls-total num">${n ? money(b.total) : ''}</span></button>`;
    }).join('') + '</div>';
    return;
  }
  const list = ch.filter(counted).slice(0, 4);
  if (p && !list.includes(p)) list.push(p);
  // the map counts branches; this ranks chains, each at its cheapest branch near you — say so, with the totals
  $('#lc-label').innerHTML = `${t('Cheapest stores')}${ch.length > list.length ? ` <span>· ${t('top {a} of {n}', { a: list.length, n: ch.length })}</span>` : ''}`;
  el.innerHTML = list.map(c => {
    const v = view(c), best = c === ch[0] && full(c) && n > 0;
    const where = delivery() ? (v.fee == null ? t('Delivered · fee not set') : v.fee === 0 ? t('Free delivery') : t('incl. {fee} delivery', { fee: money(v.fee) }))
      : bdi(storeName(v)) + (v.distance_km != null ? ` · ${kmText(v.distance_km)}` : '');
    return `<button type="button" class="lc-store${c === p ? ' on' : ''}${best ? ' best' : ''}" data-pin="${esc(c.chain_id)}" aria-pressed="${c === p}">
      <span class="ls-name">${esc(cn(c.name))}${best ? ` <em>${t('Cheapest')}</em>` : ''}</span>
      <span class="ls-sub">${where} · ${countText(v.covered, full(c), n, full(c) ? '' : ' products')}</span>
      <span class="ls-total num">${n ? money(paid(c)) : ''}</span></button>`;
  }).join('');
}
function pin(id) { haptic('select'); S.pin = id; S.sel = null; renderBasket(); drawPins(); renderStoreCard(true); }
S.allBranches = local.get('branches') === 'all';
function setAllBranches(on) {
  haptic('select');
  S.allBranches = on;
  local.set('branches', on ? 'all' : 'best');
  renderStores();
  drawDots();
  renderMapInfo();
}
$('#all-branches').addEventListener('change', e => setAllBranches(e.target.checked));
// a branch row: select it — its route, address and hours under the map (the map lives on checkout)
function selectBranch(b) {
  S.sel = { chain_id: String(b.chain_id), store_id: b.store_id };
  renderStores();
  if ($('#map-wrap').closest('[hidden]') || !$('#map-wrap').offsetParent) {
    closeCart();
    go('#/basket');
    setTimeout(() => { renderStoreCard(true); $('#map-wrap').scrollIntoView({ block: 'center', behavior: 'smooth' }); }, 650);
  } else renderStoreCard(true);
}
$('#lc-stores').addEventListener('click', e => {
  const b = e.target.closest('[data-pin]'); if (b) return pin(b.dataset.pin);
  const r = e.target.closest('[data-branch]'); if (r) selectBranch(S.result.branches[+r.dataset.branch]);
});

let saving = Promise.resolve();
async function persist() {
  const body = { name: S.cart.name, items: S.cart.items };
  try {
    if (S.cart.id) await api(`/api/carts/${S.cart.id}`, { method: 'PUT', body });
    else if (body.items.length) {
      const c = await api('/api/carts', { method: 'POST', body });
      S.cart.id = c.id;
      local.set('cart', c.id);
    }
    loadCarts();  // keep the cart tabs' names and counts current
  } catch (e) { if (e.message !== 'signed out') toast(t("Couldn't save the cart"), null, null, 'error'); }
}
const save = debounce(() => { saving = saving.then(persist); }, 400);

function changed() {
  renderBasket(); save(); compareSoon();
  if (document.body.dataset.view === 'product' && S.product) renderBuy();
}

function setQty(i, q) {
  const it = S.cart.items[i];
  if (!it || !Number.isFinite(q)) return renderBasket();
  if (q <= 0) return removeAt(i);
  it.qty = Math.max(S.meta[it.key]?.is_weighted ? 0.5 : 1, Math.round(q * 100) / 100);
  changed();
}

function removeAt(i) {
  const [gone] = S.cart.items.splice(i, 1);
  Usage.ev('remove', { a: gone.key });
  changed();
  toast(t('Removed {name}', { name: iso(gone.name || t('item')) }), t('Undo'), () => { S.cart.items.splice(i, 0, gone); changed(); });
}

$('#drawer-items').addEventListener('click', e => {
  const li = e.target.closest('.item'), step = e.target.closest('[data-step]'), dq = e.target.closest('[data-dealqty]');
  if (li && step) stepCart(S.cart.items[+li.dataset.i].key, +step.dataset.step);
  if (li && dq) setQty(+li.dataset.i, +dq.dataset.dealqty);  // "2 for ₪55": buy the deal's quantity
});
$('#drawer-items').addEventListener('change', e => {
  if (e.target.matches('input')) setQty(+e.target.closest('.item').dataset.i, parseFloat(e.target.value));
});

async function openCart(id) {
  await saving;
  try {
    const c = await api(`/api/carts/${id}`);
    S.cart = { id: c.id, name: c.name, items: c.items };
    local.set('cart', c.id);
  } catch { S.cart = { id: null, name: t('Weekly shop'), items: [] }; }
  S.result = null;
  S.pin = null;
  S.sel = null;
  S.trend = null;
  renderBasket();
  compareNow();
}

// A cart is a named collection you put together; you can keep several and switch between them.
S.carts = [];
async function loadCarts() {
  S.carts = await api('/api/carts').catch(() => S.carts);
  renderCartTabs();
}
function renderCartTabs() {
  const list = S.carts.some(c => c.id === S.cart.id) || !S.cart.items.length ? S.carts
    : [...S.carts, { id: S.cart.id, name: S.cart.name, n: S.cart.items.length }];  // a brand-new cart not saved yet
  $('#cart-tabs').innerHTML = list.map(c => {
    const on = c.id === S.cart.id, n = on ? S.cart.items.length : c.n;
    return `<button class="pill${on ? ' on' : ''}" type="button" role="tab" aria-selected="${on}" data-cartid="${c.id}"><bdi>${esc(on ? S.cart.name : c.name)}</bdi>${n ? ` <span>${n}</span>` : ""}</button>`;
  }).join('') + `<button class="pill add" type="button" data-cart="new">${t('+ New cart')}</button>`;
}
$('#cart-tabs').addEventListener('click', async e => {
  const tab = e.target.closest('[data-cartid]');
  if (tab && +tab.dataset.cartid !== S.cart.id) { haptic('select'); await openCart(+tab.dataset.cartid); return; }
  if (e.target.closest('[data-cart="new"]')) {
    await saving;
    const n = S.carts.length + 1;
    const c = await api('/api/carts', { method: 'POST', body: { name: t('Cart {n}', { n }) } }).catch(() => null);
    if (!c) return toast(t("Couldn't create a cart"), null, null, 'error');
    haptic('success');
    await loadCarts();
    await openCart(c.id);
    $('#drawer-cart-name').focus();
    $('#drawer-cart-name').select();
  }
});
$('#drawer-cart-name').addEventListener('change', e => {
  S.cart.name = e.target.value.trim() || t('Untitled');
  e.target.value = S.cart.name;
  if (S.cart.id) save();
  renderBasket();
});
$('#drawer-cart-name').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); } });
document.querySelector('.cart-edit').addEventListener('click', async e => {
  const b = e.target.closest('[data-cart]'); if (!b) return;
  await saving;
  if (b.dataset.cart === 'cmp') return openCompare();
  if (b.dataset.cart === 'empty') {
    if (!S.cart.items.length) return;
    const old = S.cart.items;
    S.cart.items = [];
    changed();
    return toast(t('Emptied {cart}', { cart: iso(S.cart.name) }), t('Undo'), () => { S.cart.items = old; changed(); });
  }
  if (b.dataset.cart === 'dup' && S.cart.id) {
    const c = await api(`/api/carts/${S.cart.id}/duplicate`, { method: 'POST' });
    await loadCarts();
    await openCart(c.id);
    toast(t('Made “{cart}”', { cart: iso(c.name) }), null, null, 'success');
  }
  if (b.dataset.cart === 'del') {
    if (!S.cart.id) { S.cart.items = []; return changed(); }
    if (!await confirmSheet(t('Delete the cart “{cart}”?', { cart: iso(S.cart.name) }), t('Delete'))) return;
    await api(`/api/carts/${S.cart.id}`, { method: 'DELETE' });
    await loadCarts();
    if (S.carts.length) await openCart(S.carts[0].id);
    else { S.cart = { id: null, name: t('Weekly shop'), items: [] }; S.result = null; renderBasket(); renderResults(); }
    toast(t('Cart deleted'));
  }
});

// ---------------------------------------------------------------- search
// ---------------------------------------------------------------- cart actions
function addToCart(h, qty = 1, quiet = false, from = null) {
  const kg = !!h.is_weighted, fresh = !S.cart.items.length && !S.cart.id;
  haptic('success');
  Usage.add(h.key, qty);
  flyToCart(from);
  S.meta[h.key] = { ...S.meta[h.key], name: h.name, manufacturer: h.manufacturer, is_weighted: kg,
                    min: h.min_price, max: h.max_price, stores: h.store_count, img: h.img || S.meta[h.key]?.img };
  const ex = S.cart.items.find(i => i.key === h.key);
  if (ex) ex.qty = Math.round((ex.qty + qty) * 100) / 100;
  else S.cart.items.push({ key: h.key, qty, name: h.name });
  S.lastAdded = h.key;
  changed();
  for (const b of [$('#basket-btn'), $('#cart-bar')]) { b.classList.remove('bump'); void b.offsetWidth; b.classList.add('bump'); }
  if (wide()) { if (fresh && !quiet) toast(t('Started the cart “{cart}”', { cart: iso(S.cart.name) }), null, null, 'none'); return; }  // the cart column shows the rest
  if (!quiet) toast(fresh ? t('Started the cart “{cart}”', { cart: iso(S.cart.name) }) : qty > 1 ? t('Added {n} to {cart}', { n: qty, cart: iso(S.cart.name) }) : t('Added to {cart}', { cart: iso(S.cart.name) }), t('View cart'), showCart, 'none');
}
function stepCart(key, d) {
  const i = S.cart.items.findIndex(x => x.key === key);
  if (i < 0) return;
  const it = S.cart.items[i], step = S.meta[key]?.is_weighted ? 0.5 : 1;
  const q = Math.round((it.qty + d * step) * 100) / 100;
  haptic('tap');
  if (q < step - 1e-9) removeAt(i);
  else { it.qty = q; changed(); }
}
function showCart() {
  if (wide()) { const c = $('#cart'); c.classList.remove('flash'); void c.offsetWidth; c.classList.add('flash'); return; }
  haptic('tap');
  document.body.classList.add('cart-open');
  $('#cart').focus();
}
const closeCart = () => document.body.classList.remove('cart-open');
$('#basket-btn').onclick = showCart;

const go = h => { if (location.hash !== h) location.hash = h; else route(); };
$('.brand').addEventListener('click', e => { e.preventDefault(); topQ.value = ''; liveFrom = null; go('#/'); });
// Typing in the header search filters the product grid live (no dropdown). The first keystroke opens the results page
// (one history entry, so Back returns to where you were); every later keystroke just updates it in place.
const topQ = $('#top-q');
let liveFrom = null;  // where you were when you started typing
const runLive = debounce(() => {
  const text = topQ.value.trim();
  if (text.length < 2) {  // cleared: back to where you were before searching (home if you came by a link)
    if (!text && (liveFrom != null || document.body.dataset.view === 'browse' && !B.cat)) { const back = liveFrom || '#/'; liveFrom = null; go(back); }
    return;
  }
  B = { cat: '', terms: [text], sizes: [], deal: false, sort: '' };
  const h = browseHash(B);
  if (liveFrom != null && document.body.dataset.view === 'browse') { history.replaceState(null, '', h); openBrowse(true); }
  else { liveFrom = location.hash || '#/'; location.hash = h; }
}, 180);
topQ.addEventListener('input', runLive);
$('#q-clear').addEventListener('click', () => { topQ.value = ''; runLive(); });  // shown only while there's text (CSS)
topQ.addEventListener('change', () => { const t = topQ.value.trim(); if (t.length > 1) remember(t); });
topQ.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); topQ.blur(); }  // results are already on screen; this just drops the keyboard
  else if (e.key === 'Escape') { topQ.value = ''; runLive(); }
});
document.addEventListener('click', e => {
  if (e.target.closest('[data-close-cart]')) closeCart();
  if (e.target.closest('[data-open-loc]')) openLocation();
  const a = e.target.closest('[data-add]');
  if (a) { e.preventDefault(); const h = S.hits[a.dataset.add]; if (h) addToCart(h, +(a.dataset.qty || 1), false, a.closest('.card, .p-head')?.querySelector('.c-thumb')); }
  const st = e.target.closest('[data-cstep]');
  if (st) { e.preventDefault(); stepCart(st.closest('[data-ckey]').dataset.ckey, +st.dataset.cstep); }
  if (e.target.closest('[data-showcart]')) { e.preventDefault(); showCart(); }
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeCart(); });

// ---------------------------------------------------------------- product cards
const PH = '<svg class="ph" viewBox="0 0 24 24" aria-hidden="true"><path class="ph-a" d="M4.6 9.6h14.8l-1.55 7.4a2.1 2.1 0 0 1-2.05 1.66H8.2A2.1 2.1 0 0 1 6.15 17z"/><g fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8.5 9.6 10.5 5.8M15.5 9.6 13.5 5.8"/><path d="M4.6 9.6h14.8l-1.55 7.4a2.1 2.1 0 0 1-2.05 1.66H8.2A2.1 2.1 0 0 1 6.15 17z"/><path d="M9.8 12.6v2.8M14.2 12.6v2.8"/></g></svg>';
// product photos come from Open Food Facts by barcode (through the server, cached); no photo → the placeholder stays
// photos are synced to this server in bulk; every product list says which ones exist, so they load right away
const photo = h => h.img ? `<img src="${esc(h.img)}" alt="" decoding="async" onerror="this.remove()">` : '';
const catIcon = c => c.line
  ? `<span class="ci" aria-hidden="true"><i class="a" style="--m:url('${esc(c.accent)}')"></i><i class="l" style="--m:url('${esc(c.line)}')"></i></span>`
  : `<span class="cat-emoji">${c.icon}</span>`;
function qtyCtl(h, deal) {
  const it = S.cart.items.find(i => i.key === h.key);
  return it
    ? `<span class="qty-ctl" data-ckey="${esc(h.key)}"><button type="button" data-cstep="-1" aria-label="${t('One less')}">−</button><span class="num">${fmtQty(it.qty, h.is_weighted)}</span><button type="button" data-cstep="1" aria-label="${t('One more')}">+</button></span>`
    : `<button class="add" type="button" data-add="${esc(h.key)}"${deal && deal.min_qty > 1 ? ` data-qty="${deal.min_qty}"` : ''}>${deal && deal.min_qty > 1 ? t('Add {n}', { n: deal.min_qty }) : t('Add')}</button>`;
}
const perText = u => u ? `${money(u.v)} / ${sizeText(u.per)}` : '';
// a deal in its last days (end time YYYYMMDDHHMM): "ends today", "ends tomorrow", "ends Sat"
function endsText(ts) {
  const end = tsDate(ts);
  if (!end) return '';
  const day = d => new Date(d.getFullYear(), d.getMonth(), d.getDate()), n = Math.round((day(end) - day(new Date())) / 864e5);
  return n < 0 || n > 2 ? '' : n === 0 ? t('ends today') : n === 1 ? t('ends tomorrow') : t('ends {day}', { day: end.toLocaleDateString(locale(), { weekday: 'short' }) });
}
function card(h) {
  S.hits[h.key] = { ...S.hits[h.key], ...h };
  // produce / deli: one chain's own item, priced at that chain only; other chains' items of the same name aren't compared
  const own = h.scope === 'chain' ? h.chain || '' : null, ownName = own != null ? cn(own) : null;
  const d = h.deal, n = own != null && h.near && h.near.chain !== own ? null : h.near, unit = h.is_weighted ? perKg() : '';
  // the server's pack size (null when the name's number is something else, e.g. grams of protein); no bare numbers
  const facts = [sizeText('pack' in h ? h.pack : h.size), perText(h.per)].filter(Boolean).join(' · ');
  const label = own != null ? esc(ownName) + (!n && h.min_price !== h.max_price ? ` · ${t('from')}` : '')
    : n ? t('near you') : h.min_price === h.max_price ? t('price') : t('from');
  const price = d
    ? `<span class="c-price deal"><small>${d.min_qty > 1 ? t('each, ×{n}', { n: d.min_qty }) : t('deal price')}</small>${money(d.unit_price)}${unit}</span>`
    : (n || h.min_price != null) ? `<span class="c-price"><small>${label}</small>${money(n ? n.price : h.min_price)}${unit}</span>`
    : '<span class="c-price"></span>';
  const where = n && n.km != null ? ` · ${kmText(n.km)}` : '';
  const info = d
    ? `<span class="c-deal" dir="auto">${esc(d.description)}</span>
       <span class="c-meta">${esc(cn(d.chain))}${d.distance_km != null ? ` · ${kmText(d.distance_km)}` : ''} · ${t('usually {price}', { price: money(d.typical) })}${endsText(d.end_ts) ? ` · <b class="c-ends">${endsText(d.end_ts)}</b>` : ''}</span>
       ${facts ? `<span class="c-meta">${bdi(facts)}</span>` : ''}`
    : `<span class="c-meta">${facts ? bdi(facts) : ''}</span>`
      + (own != null
        ? (n ? `<span class="c-near">${t('{price} at {chain}', { price: `<strong>${money(n.price)}${unit}</strong>`, chain: esc(cn(n.chain)) })}${where}</span>` : '')
          + `<span class="c-meta">${h.chain_count > 1 ? t('Similar at {n}', { n: plural(h.chain_count - 1, 'other chain') }) : t('{chain} only', { chain: esc(ownName) })}</span>`
        : n ? `<span class="c-near">${t('Cheapest {price} at {chain}', { price: `<strong>${money(n.price)}${unit}</strong>`, chain: esc(cn(n.chain)) })}${where}</span>`
        : `<span class="c-meta">${h.chain_count ? plural(h.chain_count, 'chain') : ''}</span>`);
  return `<li class="card${d ? ' has-deal' : ''}" data-key="${esc(h.key)}">
    <a class="c-link" href="#/p/${encodeURIComponent(h.key)}">
      <span class="c-thumb">${PH}${photo(h)}${d ? `<span class="c-badge">−${Math.round(d.save_pct * 100)}%</span>` : ''}</span>
      <span class="c-name" dir="auto">${esc(h.name)}</span>
      ${info}
    </a>
    <div class="c-foot">${price}<span class="c-ctl">${qtyCtl(h, d)}</span></div>
  </li>`;
}
function refreshCards() {
  $$('.card[data-key]').forEach(li => {
    const h = S.hits[li.dataset.key];
    if (h) li.querySelector('.c-ctl').innerHTML = qtyCtl(h, li.classList.contains('has-deal') ? h.deal : null);
  });
}
const skeletons = n => '<li class="card skel" aria-hidden="true"><span class="c-thumb"></span><span class="sk-line"></span><span class="sk-line short"></span><span class="sk-foot"></span></li>'.repeat(n);
async function fill(list, wrap, load) {
  let items = [];
  try { items = await load(); } catch { /* section just stays hidden */ }
  wrap.hidden = !items.length;
  list.innerHTML = items.map(card).join('');
}

// ---------------------------------------------------------------- home
const recent = () => { try { return JSON.parse(local.get('recent')) || []; } catch { return []; } };
function remember(text) {
  if (text) local.set('recent', JSON.stringify([text, ...recent().filter(t => t !== text)].slice(0, 8)));
}
const viewed = () => { try { return JSON.parse(local.get('viewed')) || []; } catch { return []; } };
function rememberViewed(p) {
  const keep = ['key', 'name', 'manufacturer', 'quantity', 'unit', 'is_weighted', 'scope', 'chain', 'img'];  // no prices
  const o = Object.fromEntries(keep.map(k => [k, p[k]]));
  local.set('viewed', JSON.stringify([o, ...viewed().filter(v => v.key !== p.key)].slice(0, 12)));
}
let cats = null;
const catTile = c => `<a class="cat" href="#/c/${c.slug}" style="--tint:${c.tint}">
  <span class="cat-icon" aria-hidden="true">${catIcon(c)}</span><span class="cat-name">${esc(catName(c))}</span>${L() === 'en' ? `<span class="cat-he" dir="rtl">${esc(c.he)}</span>` : ''}</a>`;
// home is for finding things: deals near you, never what's already in your carts, and no product twice on the page
const HOME_ROWS = [['biggest', 'biggest'], ['fresh', 'new'], ['ending', 'ending']];
let homeSeq = 0;
async function renderHome() {
  const r = recent(), inCart = new Set(S.cart.items.map(i => i.key)), v = viewed().filter(x => !inCart.has(x.key));
  $('#recent').innerHTML = r.map(t => `<a class="pill" href="#/s/${encodeURIComponent(t)}" dir="auto">${esc(t)}</a>`).join('');
  $('#recent-wrap').hidden = !r.length;
  // what you looked at, with today's confirmed prices (never prices kept in this browser)
  $('#viewed').innerHTML = v.map(x => card({ ...x, min_price: null, max_price: null })).join('');
  $('#viewed-wrap').hidden = !v.length;
  if (v.length) fill($('#viewed'), $('#viewed-wrap'), () => api('/api/cards?keys=' + encodeURIComponent(v.map(x => x.key).join(','))));
  for (const [id] of HOME_ROWS) if (!$('#' + id).children.length) $('#' + id).innerHTML = skeletons(6);
  initMap();
  dealRows();
  cats = cats || await api('/api/categories').catch(() => null);
  $('#cats').innerHTML = (cats || []).map(catTile).join('');
}
async function dealRows() {
  const my = ++homeSeq;
  const got = await Promise.all(HOME_ROWS.map(([, kind]) => api(`/api/deals?kind=${kind}&limit=20&skip_basket=1`).catch(() => [])));
  if (my !== homeSeq) return;
  const seen = new Set();
  HOME_ROWS.forEach(([id], i) => {
    const items = got[i].filter(h => !seen.has(h.key)).slice(0, 12);
    items.forEach(h => seen.add(h.key));
    $(`#${id}-wrap`).hidden = !items.length;
    $('#' + id).innerHTML = items.map(card).join('');
  });
}

function renderRail() {
  $('#rail-list').innerHTML = (cats || []).map(c => `<a class="rail-cat" href="#/c/${c.slug}" data-slug="${c.slug}">${catIcon(c)}<span>${esc(catName(c))}</span></a>`).join('');
  markRail();
}
function markRail() {
  const on = document.body.dataset.view === 'browse' ? B.cat : '';
  $$('#rail-list .rail-cat').forEach(a => { a.classList.toggle('on', a.dataset.slug === on); a.toggleAttribute('aria-current', a.dataset.slug === on); });
}

// ---------------------------------------------------------------- map: home, distance limit, stores
// Vector map (OpenFreeMap tiles, MapLibre) restyled in Sal's colours: roads, home and stores in the brand yellow.
let map = null, homeMarker = null, mapData = null, mapBase = null, mapLib = null;
const isDark = () => document.documentElement.dataset.theme
  ? document.documentElement.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
const mapColors = () => isDark()
  ? { bg: '#121715', land: '#141a18', park: '#16201b', water: '#0b1210', bld: '#1a211f', text: '#7f8a85', road: '#FFD23F', dot: '#FFD23F' }
  : { bg: '#EEF0EC', land: '#EEF0EC', park: '#e3e9df', water: '#d6e0dd', bld: '#e2e5df', text: '#6B7470', road: '#E8B800', dot: '#E8B800' };
function loadMapLib() {
  return mapLib = mapLib || new Promise((ok, fail) => {
    const css = document.createElement('link');
    css.rel = 'stylesheet'; css.href = '/static/vendor/maplibre-gl.css';
    document.head.append(css);
    const js = document.createElement('script');
    js.src = '/static/vendor/maplibre-gl.js'; js.onload = ok; js.onerror = fail;
    document.head.append(js);
  });
}
async function mapStyle() {
  mapBase = mapBase || await fetch('https://tiles.openfreemap.org/styles/positron').then(r => r.json());
  const st = structuredClone(mapBase), C = mapColors(), dark = isDark();
  st.layers = st.layers.filter(l => !/shield|airport|label_other|housenumber|poi|aeroway|railway|boundary|casing|pier|ice|glacier/.test(l.id) && l.type !== 'raster');
  for (const l of st.layers) {
    const width = l.paint?.['line-width'];
    if (l.type === 'background') l.paint = { 'background-color': C.bg };
    else if (l.type === 'fill') {
      const col = /water/.test(l.id) ? C.water : /building/.test(l.id) ? C.bld : /park|wood|landcover/.test(l.id) ? C.park : C.land;
      l.paint = { 'fill-color': col, 'fill-opacity': 1 };
    } else if (l.type === 'line') {
      if (/waterway/.test(l.id)) l.paint = { 'line-color': C.water, ...(width ? { 'line-width': width } : {}) };
      else {  // roads: the bigger the road, the stronger the yellow
        const op = /motorway/.test(l.id) ? .15 : /major/.test(l.id) ? .09 : /minor/.test(l.id) ? .045 : .02;  // faint: home and stores are the point
        l.paint = { 'line-color': C.road, 'line-opacity': dark ? op : op * 2.2, ...(width ? { 'line-width': width } : {}) };  // yellow on light paper needs more to show
      }
    } else if (l.type === 'symbol') {
      l.paint = { 'text-color': C.text, 'text-halo-color': C.bg, 'text-halo-width': 1.2 };
    }
  }
  return st;
}
const circle = (lat, lon, km, n = 96) => ({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [Array.from({ length: n + 1 }, (_, i) => {
  const a = i / n * 2 * Math.PI;
  return [lon + km / (111.32 * Math.cos(lat * Math.PI / 180)) * Math.cos(a), lat + km / 110.574 * Math.sin(a)];
})] } });
function addOverlays() {
  if (map.getSource('stores')) return drawMapData(false);
  const C = mapColors(), empty = { type: 'FeatureCollection', features: [] };
  map.addSource('radius', { type: 'geojson', data: empty });
  map.addLayer({ id: 'radius-fill', type: 'fill', source: 'radius', paint: { 'fill-color': C.dot, 'fill-opacity': .06 } });
  map.addLayer({ id: 'radius-line', type: 'line', source: 'radius', paint: { 'line-color': C.dot, 'line-width': 1.6, 'line-opacity': .85, 'line-dasharray': [2, 2] } });
  map.addSource('route', { type: 'geojson', data: empty });
  const round = { 'line-cap': 'round', 'line-join': 'round' };
  map.addLayer({ id: 'route-casing', type: 'line', source: 'route', layout: round, paint: { 'line-color': C.bg, 'line-width': 10, 'line-opacity': .9 } });
  map.addLayer({ id: 'route', type: 'line', source: 'route', layout: round, paint: { 'line-color': C.dot, 'line-width': 5.5 } });
  map.addSource('stores', { type: 'geojson', data: empty });
  map.addLayer({ id: 'stores', type: 'circle', source: 'stores', paint: {
    'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 3, 14, 5.5, 17, 8],
    'circle-color': C.dot, 'circle-stroke-color': C.bg, 'circle-stroke-width': 2 } });
  drawMapData(false);
  drawRoute(false);
}
function drawMapData(animate = true) {
  if (!map?.getSource('stores') || !mapData?.home) return;
  const { home, max_km, stores } = mapData, r = max_km || 15;
  map.getSource('radius').setData(circle(home.lat, home.lon, r));
  drawDots();
  homeMarker.setLngLat([home.lon, home.lat]);
  fitMap(animate);
}
// home shows the whole distance circle (a new radius zooms in or out); checkout shows the route to the selected store
function fitMap(animate) {
  if (!map || !mapData?.home) return;
  if (!onHome() && fitRoute(animate)) return;
  const { home, max_km } = mapData, r = max_km || 15;
  const dx = r / (111.32 * Math.cos(home.lat * Math.PI / 180)), dy = r / 110.574;
  map.fitBounds([[home.lon - dx, home.lat - dy], [home.lon + dx, home.lat + dy]],
                { padding: onHome() ? circlePad() : edge(), duration: animate ? 900 : 0 });
}
const bestOnly = () => !S.allBranches && !delivery() && !!S.result?.chains?.length;
function drawDots() {
  if (!map?.getSource('stores') || !mapData?.home) return;
  const dots = bestOnly()
    ? S.result.chains.map(c => ({ ...c.in_store, chain: c.name })).filter(v => v.lat != null)
      .map(v => ({ lat: v.lat, lon: v.lon, chain: v.chain, name: storeName(v), km: v.distance_km }))
    : mapData.stores;
  map.getSource('stores').setData({ type: 'FeatureCollection', features: dots.map(x => ({ type: 'Feature',
    properties: { chain: x.chain, name: x.name, km: x.km }, geometry: { type: 'Point', coordinates: [x.lon, x.lat] } })) });
}
function renderMapInfo() {
  const info = $('#map-info');
  if (!mapData?.home) {
    info.innerHTML = `<button class="linkish" type="button" data-open-loc>${t('Set your address')}</button>&nbsp;${t('to see the stores around you')}`;
    return;
  }
  const chains = new Set(mapData.stores.map(x => x.chain)).size;
  const where = mapData.max_km ? t('within {n} km', { n: mapData.max_km }) : t('nearby');
  info.innerHTML = !delivery() && S.result?.chains?.length
    ? `<span class="mi-dot"></span><span class="mi-seg" role="group" aria-label="${t('Branches on the map')}">
        <button type="button" data-branches="best" aria-pressed="${!S.allBranches}">${t('Cheapest per chain')} <b>${S.result.chains.length}</b></button>
        <button type="button" data-branches="all" aria-pressed="${S.allBranches}">${t('All branches')} <b>${mapData.total}</b></button></span>`
    : `<span class="mi-dot"></span><span>${t('{branches} of {chains} {where}', { branches: `<strong>${plural(mapData.total, 'branch')}</strong>`, chains: `<strong>${plural(chains, 'chain')}</strong>`, where })}</span>`;
}
$('#map-info').addEventListener('click', e => { const b = e.target.closest('[data-branches]'); if (b) setAllBranches(b.dataset.branches === 'all'); });
async function updateMap() {
  if (!map) return;
  mapData = await api('/api/stores/near').catch(() => null);
  renderMapInfo();
  if (mapData?.home) drawMapData();
}
async function initMap() {
  if (map) { map.resize(); return updateMap(); }
  try { await loadMapLib(); } catch { $('#map-wrap').hidden = true; return; }
  // Hebrew (right-to-left) labels need this shaping plugin, or the letters come out reversed
  if (maplibregl.getRTLTextPluginStatus() === 'unavailable') maplibregl.setRTLTextPlugin('/static/vendor/mapbox-gl-rtl-text.js', false);
  const style = await mapStyle().catch(() => null);
  if (!style) { $('#map-wrap').hidden = true; return; }
  map = new maplibregl.Map({ container: 'map', style, center: [34.83, 32.07], zoom: 12, attributionControl: { compact: true },
                             cooperativeGestures: true, dragRotate: false, pitchWithRotate: false, touchPitch: false });
  const pin = document.createElement('div');
  pin.className = 'home-pin';
  pin.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3.5 9.2 10 4l6.5 5.2M5.5 8v7.5h9V8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  pin.title = t('Home');
  homeMarker = new maplibregl.Marker({ element: pin }).setLngLat([34.83, 32.07]).addTo(map);
  map.on('style.load', addOverlays);  // every style load (first one and theme switches) gets the overlays back
  map.on('moveend', () => requestAnimationFrame(tuckPins));
  updateMap();  // data doesn't wait for the map; it's drawn as soon as the overlays exist
  drawPins();
  const pop = new maplibregl.Popup({ closeButton: false, offset: 10, className: 'sal-pop' });
  map.on('click', 'stores', e => {
    const f = e.features[0].properties;
    pop.setLngLat(e.lngLat).setHTML(`<strong>${esc(cn(f.chain))}</strong><br><bdi>${esc(f.name)}</bdi> · ${kmText(+f.km)}`).addTo(map);
  });
  map.on('mouseenter', 'stores', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'stores', () => { map.getCanvas().style.cursor = ''; });
}
// the map lives on the home page and at checkout; it moves with you rather than loading twice
function placeMap(slot) {
  const w = $('#map-wrap');
  const moved = w.parentElement !== slot;
  if (moved) slot.append(w, $('#store-card'));
  map?.resize();
  if (moved) fitMap(false);
}
// the cheapest stores for this cart, with their totals, right on the map; the cheapest one in the brand yellow
let pins = [];
function drawPins() {
  if (!map) return;
  pins.forEach(m => m.remove());
  pins = [];
  if (delivery() || !S.cart.items.length || !nCompared()) return;
  const ch = (S.result?.chains || []).filter(counted).slice(0, 4), p = pinned(), n = nCompared();
  [...ch].reverse().forEach(c => {  // the cheapest last, so it's drawn on top
    const v = c.in_store;
    if (v?.lat == null) return;
    const best = c === ch[0] && full(c), el = document.createElement('button');
    el.type = 'button';
    el.className = `price-pin${best ? ' best' : ''}${c === p ? ' on' : ''}`;
    el.innerHTML = `${best ? `<em>${t('Cheapest')}</em>` : ''}<b class="num">${money(v.total)}</b><span>${esc(cn(c.name))}${full(c) ? '' : ` · ${v.covered}/${n}`}</span>`;
    el.title = `${cn(c.name)} · ${storeName(v)}${v.distance_km != null ? ' · ' + kmText(v.distance_km) : ''} — ${t('price the cart here')}`;
    el.addEventListener('click', e => { e.stopPropagation(); pin(String(c.chain_id)); });
    pins.push(new maplibregl.Marker({ element: el, anchor: 'bottom', offset: [0, -4] }).setLngLat([v.lon, v.lat]).addTo(map));
  });
  requestAnimationFrame(tuckPins);
}
// pins are added pricey-first; walking them cheapest-first, any pin that would cover a kept one becomes a dot
function tuckPins() {
  const kept = [];
  [...pins].reverse().forEach(m => {
    const el = m.getElement();
    el.classList.remove('tucked');
    const r = el.getBoundingClientRect();
    if (kept.some(k => r.left < k.right - 2 && r.right > k.left + 2 && r.top < k.bottom - 2 && r.bottom > k.top + 2)) el.classList.add('tucked');
    else kept.push(r);
  });
}
// ---------------------------------------------------------------- the selected store: route, address, hours, directions
S.sel = null;  // a branch tapped in "All branches"; otherwise the store the cart is priced at
const storeCache = new Map();
const DAYNAMES = () => [0, 1, 2, 3, 4, 5, 6].map(i => new Date(2026, 10, 1 + i).toLocaleDateString(locale(), { weekday: 'long' }));  // 1 Nov 2026 is a Sunday
const ICO = {
  pin: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 17.6s5.6-5.1 5.6-9.4a5.6 5.6 0 0 0-11.2 0c0 4.3 5.6 9.4 5.6 9.4z" fill="none" stroke="currentColor" stroke-width="1.7"/><circle cx="10" cy="8.2" r="2" fill="currentColor"/></svg>',
  car: '<svg viewBox="0 0 20 20" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M3.5 12.5V10l1.7-4.2a1.5 1.5 0 0 1 1.4-.9h6.8a1.5 1.5 0 0 1 1.4.9L16.5 10v2.5a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1zM3.5 10h13"/><path d="M5.5 13.5V15M14.5 13.5V15" stroke-linecap="round"/></g></svg>',
  clock: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="6.6" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M10 6.4V10l2.4 1.6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  go: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10.2 16 4.5l-5.6 12-1.5-4.8z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>',
};
function selStore() {
  if (delivery() || !S.result?.chains?.length) return null;
  const b = S.sel && (S.result.branches || []).find(x => String(x.chain_id) === S.sel.chain_id && x.store_id === S.sel.store_id);
  if (b) return b;
  const c = pinned();
  return c?.in_store ? { ...c.in_store, chain: c.name, missing: c.in_store.missing.length, complete: full(c) } : null;
}
const mins = t => { const [h, m] = t.split(':'); return +h * 60 + +m; };
function openNow(day) {  // "07:00–23:00, 16:00–20:00" | "Closed" | "Open 24 hours"
  if (!day || day === 'Closed') return false;
  if (day === 'Open 24 hours') return true;
  const d = new Date(), now = d.getHours() * 60 + d.getMinutes();
  return day.split(', ').some(r => { const [a, b] = r.split('–').map(mins); return b > a ? now >= a && now < b : now >= a || now < b; });
}
function hoursHTML(h, gsearch) {
  if (!h) return `<span class="muted"><span class="spin" aria-hidden="true"></span>${t('Looking up opening hours…')}</span>`;
  if (h.week) {
    const today = new Date().getDay(), td = h.week[today], days = DAYNAMES();
    return `<span>${t('Today {hours}', { hours: `<bdi dir="ltr">${esc(hoursText(td))}</bdi>` })}</span> ${openNow(td) ? `<b class="open">${t('Open now')}</b>` : `<b class="shut">${t('Closed now')}</b>`}
      <details class="sc-week"><summary>${t('All week')}</summary><table>${h.week.map((x, i) =>
        `<tr${i === today ? ' class="today"' : ''}><th>${days[i]}</th><td><bdi dir="ltr">${esc(hoursText(x))}</bdi></td></tr>`).join('')}</table></details>`;
  }
  if (h.hours) return `<span dir="ltr">${esc(h.hours)}</span>`;
  return `<span class="muted">${h.checked === false ? t('Couldn’t check the opening hours just now') : t('Opening hours aren’t listed for this branch')} —
    <a href="${gsearch}" target="_blank" rel="noopener">${t('see them on Google Maps')}</a></span>`;
}
function storeCardHTML(v, d, h) {
  const top = S.result.chains[0], best = top.in_store, n = nCompared();
  const isBest = n > 0 && !!top.in_store?.complete && String(best.chain_id) === String(v.chain_id) && best.store_id === v.store_id;
  const addr = d ? [d.address, d.city].filter(Boolean).join(', ') : '';
  const home = S.prefs.home, dest = d?.lat != null ? `${d.lat},${d.lon}` : '';
  const gsearch = 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent([d?.brand_he || v.chain, addr].filter(Boolean).join(' '));
  const gdir = `https://www.google.com/maps/dir/?api=1${home ? `&origin=${home.lat},${home.lon}` : ''}&destination=${dest}&travelmode=driving`;
  return `<div class="sc-head">
      <div><p class="sc-chain">${esc(cn(v.chain))}${isBest ? ` <em>${t('Cheapest')}</em>` : ''}</p><p class="sc-branch">${bdi(storeName(v))}${v.last_price_ts ? ` · <span class="muted">${pricesFrom(v.last_price_ts)}</span>` : ''}</p></div>
      ${n ? `<p class="sc-total"><span class="num">${money(v.total)}</span>
        <small>${!v.complete ? t('{a} of {n} products here', { a: v.covered, n }) : n === 1 ? t('for your product') : t('all {n} products', { n })}</small></p>` : ''}
    </div>
    <ul class="sc-rows">
      <li>${ICO.pin}<span>${d ? (addr ? bdi(addr) : `<span class="muted">${t('Address not listed')}</span>`) : `<span class="muted">${t('Finding the address…')}</span>`}</span></li>
      <li>${ICO.car}<span>${d?.route ? t('{n} km by road', { n: d.route.km }) : t('{d} away', { d: kmText(v.distance_km) })}<span class="drive" data-sk="${esc(v.chain_id)}:${v.store_id}"></span></span></li>
      <li title="${t('Opening hours: OpenStreetMap')}">${ICO.clock}<span class="sc-hours">${hoursHTML(h, gsearch)}</span></li>
    </ul>
    ${dest ? `<div class="sc-actions">
      <a class="sc-btn waze" href="https://waze.com/ul?ll=${dest}&navigate=yes" target="_blank" rel="noopener" aria-label="${t('Open in Waze')}">${ICO.go}Waze</a>
      <a class="sc-btn" href="${gdir}" target="_blank" rel="noopener" aria-label="${t('Open in Google Maps')}">${ICO.go}Google Maps</a></div>` : ''}
`;
}
let storeSeq = 0;
async function renderStoreCard(fit) {
  const el = $('#store-card'), v = selStore();
  if (!v || !S.prefs.home || document.body.dataset.view !== 'basket') {
    el.hidden = true; el.innerHTML = ''; S.routeGeo = null; fittedTo = null; drawRoute(false); return;
  }
  el.hidden = false;
  const key = `${v.chain_id}:${v.store_id}`, my = ++storeSeq;
  const c = storeCache.get(key) || {};
  storeCache.set(key, c);
  const paint = () => { el.innerHTML = storeCardHTML(v, c.d, c.h); fillDrive(el); };
  paint();
  if (!c.d) {
    try { c.d = await api(`/api/store/${encodeURIComponent(v.chain_id)}/${v.store_id}`); } catch { storeCache.delete(key); return; }
    if (my !== storeSeq) return;
    paint();
  }
  S.routeGeo = c.d.route;
  drawRoute(fit);
  if (!c.h) {
    c.h = await api(`/api/store/${encodeURIComponent(v.chain_id)}/${v.store_id}/hours`).catch(() => ({ checked: false }));
    if (my === storeSeq) paint();
  }
}
// the route home -> selected store, in the brand yellow over the dimmed roads; a ring marks the store
let selMarker = null;
function drawRoute(fit) {
  if (!map?.getSource('route')) return;
  const g = S.routeGeo, v = selStore();
  map.getSource('route').setData(g ? { type: 'Feature', geometry: { type: 'LineString', coordinates: g.coords } } : { type: 'FeatureCollection', features: [] });
  if (g && v?.lat != null) {
    selMarker = selMarker || new maplibregl.Marker({ element: Object.assign(document.createElement('div'), { className: 'sel-ring' }) });
    selMarker.setLngLat([v.lon, v.lat]).addTo(map);
  } else selMarker?.remove();
  // a new store (first load, a cart change that moves the cheapest, a tap) gets the view; re-renders of the same one don't
  const key = g && v ? `${v.chain_id}:${v.store_id}` : null;
  if (g && !onHome() && (fit || key !== fittedTo)) fitRoute(true);
}
// the map fades out at its edges, so keep what matters in the clear middle (and room above for the price pins)
const edge = () => innerWidth < 600 ? { top: 105, bottom: 45, left: 64, right: 64 } : { top: 130, bottom: 70, left: 90, right: 90 };
// home: the distance circle fills the map's height (no top fade there; the bottom fade takes a little more room).
// Where the map is narrow the circle's top would run under the map-info pill, so it starts below it.
const onHome = () => !!$('#map-wrap').closest('#home-map');
const circlePad = () => {
  const w = map.getContainer().clientWidth;
  return { top: w < 900 ? 54 : 18, bottom: w < 600 ? 30 : 40, left: 24, right: 24 };
};
let fittedTo = null;
function fitRoute(animate) {
  const g = S.routeGeo, v = selStore();
  if (!map || !g?.coords?.length || !v) return false;
  const xs = g.coords.map(p => p[0]), ys = g.coords.map(p => p[1]);
  map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]], { padding: edge(), maxZoom: 15, duration: animate ? 900 : 0 });
  fittedTo = `${v.chain_id}:${v.store_id}`;
  return true;
}
async function restyleMap() {
  if (!map) return;
  map.setStyle(await mapStyle(), { diff: false });  // full reload: 'style.load' re-adds the overlays in the new colours
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', restyleMap);

// ---------------------------------------------------------------- browse: stacked filters
// Every word is its own filter chip; each one narrows the results further. Back undoes the last one.
let B = { cat: '', terms: [], sizes: [], deal: false, sort: '' };
const splitBar = v => (v || '').split('|').map(x => x.trim()).filter(Boolean);
function browseHash(st) {
  if (!st.cat && !st.terms.length) return '#/';
  const qs = new URLSearchParams();
  if (st.cat && st.terms.length) qs.set('q', st.terms.join('|'));
  if (st.sizes.length) qs.set('size', st.sizes.join('|'));
  if (st.deal) qs.set('deal', '1');
  if (st.sort) qs.set('sort', st.sort);
  const q = qs.toString();
  return (st.cat ? `#/c/${st.cat}` : `#/s/${encodeURIComponent(st.terms.join('|'))}`) + (q ? '?' + q : '');
}
const setBrowse = patch => go(browseHash({ ...B, ...patch }));

function renderTerms() {
  const c = cats?.find(x => x.slug === B.cat);
  $('#b-pills').innerHTML = (c ? `<span class="term cat">${catIcon(c)} ${esc(catName(c))}<button type="button" data-rmcat aria-label="${esc(t('Remove {x}', { x: catName(c) }))}">×</button></span>` : '')
    + B.terms.map((w, i) => `<span class="term"><bdi>${esc(w)}</bdi><button type="button" data-rmterm="${i}" aria-label="${esc(t('Remove {x}', { x: w }))}">×</button></span>`).join('');
  $('#b-add').placeholder = B.terms.length || B.cat ? t('Narrow down…') : t('Type a product…');
  $('#b-add').value = '';
}

let listSeq = 0;
async function openBrowse(live) {
  const my = ++listSeq, grid = $('#b-cards');
  cats = cats || await api('/api/categories').catch(() => []);
  $('#b-cats').innerHTML = cats.map(c => `<a class="pill${c.slug === B.cat ? ' on' : ''}" href="${browseHash({ ...B, cat: c.slug === B.cat ? '' : c.slug, sizes: [] })}">${catIcon(c)} ${esc(catName(c))}</a>`).join('');
  renderTerms();
  markRail();
  if (live && grid.querySelector('.card:not(.skel)')) grid.classList.add('busy');
  else { $('#b-title').textContent = t('Searching…'); grid.innerHTML = skeletons(12); }
  const qs = new URLSearchParams({ terms: B.terms.join('|'), cat: B.cat, size: B.sizes.join('|'), deal: B.deal ? 1 : 0, sort: B.sort, limit: 120 });
  const r = await api('/api/browse?' + qs).catch(() => null);
  if (my !== listSeq) return;
  grid.classList.remove('busy');
  if (!r) { $('#b-title').textContent = t('Couldn’t load products — try again in a moment'); $('#b-cards').innerHTML = ''; return; }
  $('#b-title').innerHTML = r.total
    ? (r.total === 1 ? t('1 product') : t('{n} products', { n: r.total.toLocaleString(locale()) + (r.more ? '+' : '') }))
      + (r.total > r.products.length ? ` <small>${t('top {n} shown', { n: r.products.length })}</small>` : '')
    : t('Nothing matches all of these filters');
  $('#b-refine').innerHTML = r.refine.length ? `<span class="facet-label">${t('Narrow down')}</span>`
    + r.refine.map(w => `<button class="pill sug" type="button" data-term="${esc(w.word)}"><bdi>${esc(w.word)}</bdi> <span>${w.n}</span></button>`).join('') : '';
  $('#b-sizes').innerHTML = r.sizes.length ? `<span class="facet-label">${t('Size')}</span>`
    + r.sizes.map(z => `<button class="pill sug${B.sizes.includes(z.size) ? ' on' : ''}" type="button" data-size="${esc(z.size)}"><bdi>${esc(sizeText(z.size))}</bdi> <span>${z.n}</span></button>`).join('') : '';
  $('#b-deal').setAttribute('aria-pressed', B.deal);
  $('#b-deal').innerHTML = `${t('On deal now')} <span>${r.deal_n}</span>`;
  $('#b-sort').value = B.sort;
  $('#b-cards').innerHTML = r.products.length ? r.products.map(card).join('')
    : '';
  Usage.results(B.terms, r.total, B.cat);
}

$('#b-add').addEventListener('keydown', e => {
  const v = e.target.value.trim();
  if (e.key === 'Enter' && v) { e.preventDefault(); remember(v); Usage.ev('filter', { a: 'typed word', b: v }); if (!B.terms.includes(v)) setBrowse({ terms: [...B.terms, v] }); }
  else if (e.key === 'Backspace' && !e.target.value) {
    if (B.terms.length) setBrowse({ terms: B.terms.slice(0, -1) });
    else if (B.cat) setBrowse({ cat: '' });
  }
});
$('#view-browse').addEventListener('click', e => {
  const tm = e.target.closest('[data-term]');
  if (tm) { haptic('select'); Usage.ev('filter', { a: 'narrow down', b: tm.dataset.term }); return setBrowse({ terms: [...B.terms, tm.dataset.term] }); }
  const rt = e.target.closest('[data-rmterm]');
  if (rt) { Usage.ev('filter', { a: 'remove word', b: B.terms[+rt.dataset.rmterm] }); return setBrowse({ terms: B.terms.filter((_, i) => i !== +rt.dataset.rmterm) }); }
  if (e.target.closest('[data-rmcat]')) { Usage.ev('filter', { a: 'remove category', b: B.cat }); return setBrowse({ cat: '', sizes: [] }); }
  const z = e.target.closest('[data-size]');
  if (z) { haptic('select'); const v = z.dataset.size; Usage.ev('filter', { a: B.sizes.includes(v) ? 'size off' : 'size', b: v }); return setBrowse({ sizes: B.sizes.includes(v) ? B.sizes.filter(x => x !== v) : [...B.sizes, v] }); }
  if (e.target.closest('#b-deal')) { haptic('select'); Usage.ev('filter', { a: B.deal ? 'deals off' : 'deals only' }); return setBrowse({ deal: !B.deal }); }
  if (e.target.closest('#b-terms') && !e.target.closest('button')) $('#b-add').focus();
});
$('#b-sort').onchange = e => { Usage.ev('filter', { a: 'sort', b: e.target.value || 'default' }); setBrowse({ sort: e.target.value }); };

// ---------------------------------------------------------------- product page
let prodSeq = 0;
async function openProduct(key) {
  const el = $('#view-product'), my = ++prodSeq;
  if (S.product?.p.key !== key) el.innerHTML = productSkeleton();
  let p, res;
  try {
    [p, res] = await Promise.all([
      S.hits[key] || api('/api/product?key=' + encodeURIComponent(key)),
      comparePost({ items: [{ key, qty: 1 }], prefs: { ...S.prefs, mode: 'store' } }),
    ]);
  } catch {
    el.innerHTML = backLink() + emptyState(t('Couldn’t load this product'), '');
    return;
  }
  if (my !== prodSeq) return;
  S.hits[key] = { ...p, ...S.hits[key] };
  S.product = { p: S.hits[key], res };
  rememberViewed(S.product.p);
  renderProduct();
  const deal = (await api(`/api/deals?kind=mine&limit=1&keys=${encodeURIComponent(key)}`).catch(() => []))[0];
  if (my !== prodSeq || !deal) return;
  const d = deal.deal;
  $('#p-deal').innerHTML = `<span class="c-badge">−${Math.round(d.save_pct * 100)}%</span>
    <span>${t('{deal} at {chain}', { deal: `<strong dir="auto">${esc(d.description)}</strong>`, chain: esc(cn(d.chain)) })}${d.distance_km != null ? ` (${kmText(d.distance_km)})` : ''}:
    ${d.min_qty > 1 ? t('{price} each when you buy {n}', { price: money(d.unit_price), n: d.min_qty }) : t('{price} each', { price: money(d.unit_price) })}, ${t('usually {price}', { price: money(d.typical) })}.</span>
    ${d.min_qty > 1 && !S.cart.items.some(i => i.key === key) ? `<button class="add" type="button" data-add="${esc(key)}" data-qty="${d.min_qty}">${t('Add {n}', { n: d.min_qty })}</button>` : ''}`;
  $('#p-deal').hidden = false;
}

// the product page prices one unit: a counted line costs `cost`; produce at its own chain costs its shelf price
const offerLine = c => c.in_store?.lines?.[0];
const offerPrice = l => l?.status === 'ok' ? l.cost : l?.status === 'not_compared' && l.own ? l.price : null;
function offer(c, best) {
  const s = c.in_store, l = offerLine(c), kg = S.product.p.is_weighted, ol = c.online?.lines?.[0];
  const flags = [
    l?.promo && `<span class="chip promo" dir="auto">${esc(l.promo.description || t('Promotion'))}</span>`,
    l && dealsMore(l),
    l && notCounted(l),
  ].filter(Boolean).join(' ');
  const online = ol && offerPrice(ol) != null ? `<small>${t('Online {price}', { price: money(offerPrice(ol)) })}</small>` : '';
  // branches are ranked by the cart total; with nothing compared (produce) that is no price order
  const of = s.branch_count > 1 ? ' · ' + (S.product.res.n ? t('cheapest of {n} branches', { n: s.branch_count }) : plural(s.branch_count, 'branch')) : '';
  return `<li class="offer${best ? ' best' : ''}">
    <span class="o-chain">${esc(cn(c.name))}${best ? ` <span class="o-badge">${t('Cheapest')}</span>` : ''}</span>
    <span class="o-store">${storePlace(s)}${of}${l?.shelf_name ? ` · ${bdi(l.shelf_name)}` : ''}</span>
    ${flags ? `<span class="o-flags">${flags}</span>` : ''}
    <span class="o-price">${l?.promo ? `<s>${money(l.full)}</s>` : ''}${money(offerPrice(l))}${kg ? `<em>${perKg()}</em>` : ''}${online}</span>
  </li>`;
}
// a chain whose price can't be confirmed (or produce matched only by name): greyed, below the offers
function offerOther(c) {
  const s = c.in_store, l = offerLine(c), kg = S.product.p.is_weighted, own = l.status === 'not_compared' && l.own && l.price != null;
  const what = l.status === 'unconfirmed' ? `${unconfText(l)} · ${t('Not counted')}`
    : own ? t('Not compared') + (l.shelf_name ? ` · ${bdi(l.shelf_name)}` : '')
    : (l.similar || []).map(x => `${t('Similar')}: ${bdi(x.name)} ${money(x.price)}${kg ? perKg() : ''}`).join(' · ');
  const price = l.status === 'unconfirmed' || own ? l.price : l.similar?.[0]?.price;
  return `<li class="offer unconfirmed">
    <span class="o-chain">${esc(cn(c.name))}</span>
    <span class="o-store">${storePlace(s)}</span>
    <span class="o-flags">${what}</span>
    <span class="o-price">${money(price)}${kg ? `<em>${perKg()}</em>` : ''}</span>
  </li>`;
}

function renderBuy() {
  const { p } = S.product, it = S.cart.items.find(i => i.key === p.key);
  $('#p-buy').innerHTML = it
    ? `${qtyCtl(p)}<span class="in-cart">${t('In your cart')} · <button class="linkish" type="button" data-showcart>${t('View cart')}</button></span>`
    : `<button class="primary" type="button" data-add="${esc(p.key)}">${t('Add to cart')}</button>`;
}

function renderProduct() {
  const { p, res } = S.product, el = $('#view-product'), kg = !!p.is_weighted, r = radius();
  // counted prices first (barcode: confirmed lines, ranked by the engine; produce: its own chain's branches). A bare
  // code that isn't a real barcode is a different item at each chain: every chain's item is listed as not compared
  const ownItem = res.n > 0 || p.key.includes(':');
  const chains = !ownItem ? [] : res.chains.filter(c => offerPrice(offerLine(c)) != null)
    .sort((a, b) => res.n ? 0 : offerPrice(offerLine(a)) - offerPrice(offerLine(b)));
  const others = res.chains.filter(c => { const l = offerLine(c); return l && !chains.includes(c)
    && (l.status === 'unconfirmed' || (l.status === 'not_compared' && (l.similar?.length || (l.own && l.price != null)))); });
  const meta = [clean(p.manufacturer), qtyText(p.quantity, p.unit), kg ? t('sold by weight') : ''].filter(Boolean);
  const shown = chains.slice(0, 6), more = chains.slice(6);
  el.innerHTML = `${backLink()}
    <div class="p-head">
      <span class="c-thumb p-thumb">${PH}${photo(p)}</span>
      <div class="p-info">
        <h1 dir="auto">${esc(p.name)}</h1>
        ${meta.length ? `<p class="p-meta">${meta.map(bdi).join(' · ')}</p>` : ''}
        ${chains.length ? `<p class="p-from">${t(res.n ? 'From {price} at {chain}' : '{price} at {chain}', { price: `<strong class="num">${money(offerPrice(offerLine(chains[0])))}${kg ? perKg() : ''}</strong>`, chain: esc(cn(chains[0].name)) })}</p>` : ''}
        <div class="buy" id="p-buy"></div>
      </div>
    </div>
    <p class="p-deal" id="p-deal" hidden></p>
    ${chains.length ? `<h2 class="p-h">${t('Price at {chains}', { chains: plural(chains.length, 'chain') })}${r ? ' ' + t('within {n} km', { n: r }) : ''}${res.n ? ` · ${t('cheapest first')}` : ''}</h2>
      <ol class="offers">${shown.map((c, i) => offer(c, i === 0 && res.n > 0 && !!c.in_store?.complete)).join('')}</ol>
      ${more.length ? `<details class="pricier"><summary>${t('More expensive')} (${more.length})
        <span class="range">${money(offerPrice(offerLine(more[0])))}${more.length > 1 ? ' – ' + money(offerPrice(offerLine(more.at(-1)))) : ''}</span></summary>
        <ol class="offers">${more.map(c => offer(c, false)).join('')}</ol></details>` : ''}`
    : others.length ? '' : emptyState(r ? t('Not sold within {n} km', { n: r }) : t('Not on sale right now'), '',
        r ? `<div class="actions"><button class="primary" type="button" data-open-loc>${t('Change distance')}</button></div>` : '')}
    ${others.length ? `<details class="pricier"${chains.length ? '' : ' open'}><summary>${res.n ? t('Not counted') : t('Not compared')} (${others.length})</summary>
      <ol class="offers">${others.map(offerOther).join('')}</ol></details>` : ''}
    <section class="p-history"></section>`;
  renderBuy();
  fillDrive(el);
  productHistory(p.key, chains.slice(0, 4).map(c => c.name));
}
async function productHistory(key, names) {
  const hist = await api('/api/history?key=' + encodeURIComponent(key), { quiet: true }).catch(() => null);
  const el = $('#view-product .p-history');
  if (!hist || !el || S.product?.p.key !== key) return;
  const series = hist.chains.filter(c => names.includes(c.chain))
    .map(c => ({ name: c.chain, points: withGaps(c.points.map(p => [p[0], p[1]]), c.ends) })).filter(sr => sr.points.some(p => p[1] != null));
  const days = new Set(series.flatMap(sr => sr.points.map(p => p[0])));
  el.innerHTML = !series.length ? '' : `<h2 class="p-h">${t('Price history')}</h2>` + (days.size < 2 && (hist.since || 0) >= todayNum()
    ? `<p class="trend-note">${t('Tracking since {day}.', { day: fmtDay(hist.since || todayNum()) })}</p>`
    : lineChart(series, t('Cheapest shelf price over time at each chain')));
}

$('#view-product').addEventListener('click', e => {
  if (e.target.closest('[data-back]')) { e.preventDefault(); navs > 1 ? history.back() : (location.hash = '#/'); }
});

// ---------------------------------------------------------------- views
let navs = 0;
const VIEWS = { '': 'home', s: 'browse', c: 'browse', p: 'product', basket: 'basket' };
function route() {
  navs++;
  const m = location.hash.match(/^#\/([a-z]*)\/?([^?]*)(?:\?(.*))?$/) || [];
  const v = VIEWS[m[1] || ''] || 'home', arg = decodeURIComponent(m[2] || ''), qs = new URLSearchParams(m[3] || '');
  const before = document.body.dataset.view;
  for (const id of ['home', 'browse', 'product', 'basket']) $(`#view-${id}`).hidden = id !== v;
  document.body.dataset.view = v;
  if (before && before !== v) enter($(`#view-${v}`));  // a gentle fade-in for a new page (not for live search updates)
  renderStoreCard(false);  // shows at checkout, hides (with its route) everywhere else
  if (document.activeElement !== topQ) {  // the box shows what a search page is filtered by, and is empty elsewhere
    topQ.value = m[1] === 's' ? splitBar(arg).join(' ') : '';
    if (v !== 'browse') liveFrom = null;
  }
  if (v === 'home') renderHome();
  if (v === 'browse') {
    const rest = { sizes: splitBar(qs.get('size')), deal: qs.get('deal') === '1', sort: qs.get('sort') || '' };
    B = m[1] === 'c' ? { cat: arg, terms: splitBar(qs.get('q')), ...rest } : { cat: '', terms: splitBar(arg), ...rest };
    openBrowse();
  }
  if (v === 'product') openProduct(arg);
  markRail();
  if (v === 'basket') { placeMap($('#co-map')); initMap(); renderBasket(); compareNow(); }
  else if (v === 'home') placeMap($('#home-map'));
  closeCart();
  window.scrollTo(0, 0);
  Usage.view(v, v === 'browse' ? B.cat || undefined : v === 'product' ? arg : undefined);
}
window.addEventListener('hashchange', route);

// ---------------------------------------------------------------- compare
const comparePost = body => api('/api/compare', { method: 'POST', body });
let compareSeq = 0;
async function compareNow() {
  const my = ++compareSeq;
  if (!S.cart.items.length) { S.result = null; drawPins(); drawDots(); if (mapData) renderMapInfo(); renderStoreCard(false); updateTotals(); return renderResults(); }
  if (!S.result) renderResults(true);
  try {
    const r = await comparePost({ items: S.cart.items, prefs: S.prefs });
    if (my !== compareSeq) return;
    S.result = r;
    for (const it of r.items) {
      const m = S.meta[it.key] || {};
      S.meta[it.key] = { ...m, name: m.name || it.name, manufacturer: it.manufacturer, is_weighted: it.is_weighted,
                         min: it.min, max: it.max, stores: it.stores, compared: it.compared, img: it.img || m.img };
    }
    renderBasket();
    renderResults();
    drawPins();
    drawDots();
    renderMapInfo();
    renderStoreCard(false);
    loadTrend();
    if (document.body.dataset.view === 'basket') compared(r);
  } catch (e) { if (my === compareSeq && e.message !== 'signed out') toast(t("Couldn't compare prices — is the server up?"), null, null, 'error'); }
}
const compareSoon = debounce(compareNow, 250);
// statistics: what checkout showed (how many stores had the whole cart, which chain was cheapest), once per cart state
let comparedKey = '';
const compared = debounce(r => {
  const ok = r.chains.filter(c => full(c) && counted(c)), best = ok[0] || null;
  const k = [S.cart.id, S.cart.items.length, S.prefs.mode, best?.chain_id].join('|');
  if (k === comparedKey) return;
  comparedKey = k;
  Usage.ev('compare', { n: S.cart.items.length, d: { complete: ok.length, chains: r.chains.length, best: best?.name || null,
                                                     total: best ? Math.round(paid(best)) : null, mode: S.prefs.mode, km: S.prefs.max_km } });
}, 2500);

function emptyState(title, text, extra = '') {
  return `<div class="empty-results"><div class="ghost-tag" aria-hidden="true"></div><h2>${title}</h2><p>${text}</p>${extra}</div>`;
}

const realCity = c => c && !/^\d+$/.test(c.trim()) ? c.trim() : '';
const storeName = v => clean(v.name) || t('Branch {n}', { n: v.store_id });
function storePlace(v) {
  const city = realCity(v.city), n = storeName(v);
  const name = city && n.includes(city) ? n : [n, city].filter(Boolean).join(', ');
  return bdi(name) + (v.distance_km != null ? ` · ${t('{d} away', { d: kmText(v.distance_km) })}` : '') + driveSlot(v);
}
const place = v => delivery() ? `${t('Online store')} · ${bdi(storeName(v))}` : storePlace(v);
const driveSlot = v => S.prefs.home ? `<span class="drive" data-sk="${esc(v.chain_id)}:${v.store_id}"></span>` : '';

// drive times load after the results so pricing never waits on the routing service
async function fillDrive(root = $('#results')) {
  const els = [...root.querySelectorAll('.drive[data-sk]')];
  if (!els.length) return;
  const my = root._driveSeq = (root._driveSeq || 0) + 1;
  const stores = [...new Set(els.map(e => e.dataset.sk))].map(k => [k.slice(0, k.lastIndexOf(':')), +k.slice(k.lastIndexOf(':') + 1)]);
  let r;
  try { r = await api('/api/drive', { method: 'POST', body: { home: S.prefs.home, stores }, quiet: true }); } catch { return; }
  if (my !== root._driveSeq) return;
  els.forEach(e => {
    const m = r.times[e.dataset.sk];
    if (m == null) return;
    e.textContent = ' · ' + t(r.live ? '{n} min drive now' : '{n} min drive', { n: m < 1 ? '<1' : m });
    e.title = r.live ? t('Driving time in current traffic (Google Maps)') : t('Typical driving time, without live traffic');
  });
}

// What a chain costs in store, online, delivered — shared by the hero and the rows.
function facts(c, skipHeadline) {
  const o = c.online, s = c.in_store, out = [], n = nCompared();
  const of = v => v.complete || !n ? '' : ` · ${t('{a} of {n}', { a: v.covered, n })}`;  // a total over only some products says so
  if (s && !(skipHeadline && !delivery())) out.push([t('In store'), money(s.total) + of(s)]);
  if (!o) { out.push(['', t('No online shop in the price files'), true]); return out; }
  out.push([t('Groceries online'), money(o.total) + of(o)]);
  if (!o.area_confirmed) out.push(['', t('Delivery area not confirmed'), true]);
  else if (o.fee == null) out.push(['', serverText(o.fee_note) || t('Delivery fee not set'), true]);
  else {
    out.push([t('Delivery'), o.fee === 0 ? t('Free') : money(o.fee)]);
    if (!(skipHeadline && delivery())) out.push([t('Delivered total'), money(o.delivered)]);
    if (o.fee_note) out.push(['', serverText(o.fee_note), true]);
  }
  if (o.price_vs_store != null) {
    const d = o.price_vs_store;
    out.push([t('Online vs in store'), Math.abs(d) < 0.005 ? t('Same prices') : t(d > 0 ? '{price} dearer' : '{price} cheaper', { price: '₪' + Math.abs(d).toFixed(2) })]);
  }
  return out;
}

function fmtQty(q, kg) { return kg ? t('{n} kg', { n: q }) : `${q}`; }

// The name on this store's shelf (when it isn't the cart's name) and the barcode, under the product name
const shelfLine = l => [l.shelf_name && bdi(l.shelf_name), l.code && `<span class="code">${esc(l.code)}</span>`].filter(Boolean).join(' · ');

function lineRow(l) {
  const it = S.cart.items.find(i => i.key === l.key), m = S.meta[l.key] || {}, kg = m.is_weighted;
  const name = `<span dir="auto">${esc(it?.name || m.name || l.key)}</span>`, qty = it?.qty ?? l.qty ?? 1;
  if (l.status === 'missing')
    return `<tr class="missing"><td class="l-name">${name}<small>${t('Not sold here')}</small></td><td class="l-cost">${t('Missing')}</td></tr>`;
  if (l.status === 'unconfirmed')
    return `<tr class="unconfirmed"><td class="l-name">${name}<small>${unconfText(l)} · ${t('Not counted')}</small>${l.code ? `<small>${shelfLine({ code: l.code })}</small>` : ''}</td>
      <td class="l-cost">${money(l.full ?? l.price * qty)}</td></tr>`;
  const shelf = shelfLine(l);
  const chips = (l.promo ? ` <span class="chip promo" dir="auto">${esc(l.promo.description || t('Promotion'))}</span>`
      + (l.promo.club ? ` <span class="chip">${t('Club')}</span>` : '') : '')
    + (l.deals_more?.length ? ' ' + dealsMore(l) : '') + (l.deals_not_counted?.length ? ' ' + notCounted(l) : '');
  return `<tr><td class="l-name">${name}<small>${fmtQty(l.qty, kg)} × ${money(l.price)}${chips}</small>${shelf ? `<small class="shelf">${shelf}</small>` : ''}</td>
    <td class="l-cost${l.promo ? ' promo' : ''}">${l.promo ? `<s>${money(l.full)}</s>` : ''}${money(l.cost)}</td></tr>`;
}
// produce: never in a total; this store's own item, or items with the same name, for you to judge
function ncRow(l) {
  const it = S.cart.items.find(i => i.key === l.key), m = S.meta[l.key] || {}, kg = m.is_weighted, u = kg ? perKg() : '';
  const name = `<span dir="auto">${esc(it?.name || m.name || l.key)}</span>`, qty = it?.qty ?? l.qty ?? 1;
  if (l.own && l.price != null)
    return `<tr><td class="l-name">${name}<small>${fmtQty(qty, kg)} × ${money(l.price)}${l.shelf_name ? ` · ${bdi(l.shelf_name)}` : ''}</small></td>
      <td class="l-cost">${money(l.price * qty)}</td></tr>`;
  if (l.similar?.length)
    return `<tr><td class="l-name">${name}${l.similar.map(x => `<small>${t('Similar')}: ${bdi(x.name)} ${money(x.price)}${u}</small>`).join('')}</td>
      <td class="l-cost">${money(l.similar[0].price * qty)}</td></tr>`;
  return `<tr class="missing"><td class="l-name">${name}<small>${t('Not sold here')}</small></td><td class="l-cost">—</td></tr>`;
}
function linesTable(ls) {
  const counted = ls.filter(l => l.status !== 'not_compared'), nc = ls.filter(l => l.status === 'not_compared');
  return (counted.length ? `<table class="lines"><tbody>${counted.map(lineRow).join('')}</tbody></table>` : '')
    + (nc.length ? `<div class="nc"><p class="nc-h">${t('Not compared')}</p><table class="lines"><tbody>${nc.map(ncRow).join('')}</tbody></table></div>` : '');
}

function winner(c) {
  const v = view(c), total = paid(c), n = nCompared();
  const [whole, ag] = total.toFixed(2).split('.');
  const next = S.result.chains.find(x => x !== c && full(x));  // the runner-up with everything too
  const lead = full(c) && next ? paid(next) - paid(c) : 0;
  const sub = !full(c)
    ? t('{part} items here', { part: `<strong>${t('{a} of {n}', { a: v.covered, n })}</strong>` })
    : (n === 1 ? t('Your item here') : t('All {n} items here', { n }))
      + (lead > 0.005 ? ` · ${t('{price} less than {chain}', { price: `<strong>${money(lead)}</strong>`, chain: esc(cn(next.name)) })}` : '');
  const foot = facts(c, true).map(([k, val, warn]) => warn
    ? `<div>${k ? esc(k) + ': ' : ''}${esc(val)}</div>`
    : `<div class="alt"><span>${esc(k)}</span><strong>${esc(val)}</strong></div>`).join('');
  const others = !delivery() && c.in_store.branches.length > 1
    ? `<p class="note">${t('Other branches:')} ${c.in_store.branches.slice(1, 4).map(b =>
        `${bdi(storeName(b))} ${money(b.total)}${b.complete === false ? ` (${t('{a} of {n}', { a: b.covered, n })})` : ''}${b.distance_km != null ? ` (${kmText(b.distance_km)}${driveSlot(b)})` : ''}`).join(' · ')}</p>` : '';
  const r = radius();
  const branches = [delivery() ? t('Prices on the chain’s website')
    : c.in_store.branch_count > 1 ? t('Cheapest of {n} branches', { n: c.in_store.branch_count }) + (r ? ' ' + t('within {n} km', { n: r }) : '')
    : r ? t('Only branch within {n} km', { n: r }) : t('Only branch with prices'), pricesFrom(v.last_price_ts)].filter(Boolean).join(' · ');
  return `<article class="winner">
    <div class="tag-face">
      <p class="tag-chain">${esc(cn(c.name))}</p>
      <p class="tag-store">${place(v)}</p>
      <p class="tag-total" aria-label="${money(total)}"><span class="cur">₪</span>${whole}<span class="agorot">.${ag}</span></p>
      <p class="tag-sub">${sub}</p>
      <div class="tag-foot">${foot}</div>
    </div>
    <div class="winner-detail">
      <div class="detail-head"><h3>${delivery() ? t('Online prices') : t('In-store prices')}</h3><span class="branches">${branches}</span></div>
      ${linesTable(v.lines)}${others}
    </div>
  </article>`;
}

function row(c, hero) {
  const v = view(c), n = nCompared();
  const d = n && full(c) && hero && full(hero) ? paid(c) - paid(hero) : null;
  const promos = v.lines.filter(l => l.promo).length;
  const flags = [
    n && !full(c) && `<span class="chip warn">${v.covered ? t('{a} of {n}', { a: v.covered, n }) : t('Not counted')}</span>`,
    promos && `<span class="chip promo">${t('{n} on promotion', { n: promos })}</span>`,
    delivery() && c.online.fee_note && `<span class="chip warn">${esc(serverText(c.online.fee_note))}</span>`,
  ].filter(Boolean).join('');
  const alt = (n ? facts(c) : []).map(([k, val, warn]) => warn
    ? `<span class="chip warn">${k ? esc(k) + ': ' : ''}${esc(val)}</span>`
    : `<span>${esc(k)} <strong class="num">${esc(val)}</strong></span>`).join('')
    + (v.last_price_ts ? `<span class="muted">${pricesFrom(v.last_price_ts)}</span>` : '');
  return `<details class="row">
    <summary>
      <span class="row-chain">${esc(cn(c.name))}</span>
      <span class="row-store">${place(v)}</span>
      ${flags ? `<span class="flags">${flags}</span>` : ''}
      <span class="row-total">${n && v.covered ? money(paid(c)) : n ? '—' : ''}</span>
      <span class="row-delta">${d == null ? '' : d < 0.005 ? t('same') : '+' + money(d)}</span>
      ${SVG.chev}
    </summary>
    <div class="row-body"><div class="row-alt">${alt}</div>${linesTable(v.lines)}</div>
  </details>`;
}

// stores whose newest price file is over a day old: listed, never ranked
function staleHTML() {
  const st = S.result?.stale || [];
  if (!st.length) return '';
  return `<details class="pricier stale"><summary>${t('Prices over a day old')} (${st.length})</summary>
    <ul class="stale-list">${st.map(s => `<li><span class="row-chain">${esc(cn(s.chain))}</span>
      <span class="row-store">${bdi(storeName(s))}${s.distance_km != null ? ` · ${kmText(s.distance_km)}` : ''}</span>
      <span class="st-ts">${s.online ? t('Online') + ' · ' : ''}${pricesFrom(s.last_price_ts) || t('No current prices')}</span></li>`).join('')}</ul></details>`;
}
// delivery: chains whose online shop may not deliver to this address (the user hasn't confirmed it): never ranked
function noAreaHTML() {
  const na = delivery() ? S.result?.no_area || [] : [];
  if (!na.length) return '';
  const n = nCompared();
  return `<details class="pricier stale"><summary>${t('Delivery area not confirmed')} (${na.length})</summary>
    <ul class="stale-list">${na.map(a => `<li><span class="row-chain">${esc(cn(a.chain))}</span>
      <span class="row-store">${bdi(storeName(a))}</span>
      <span class="st-ts">${money(a.total)}${a.complete || !n ? '' : ` · ${t('{a} of {n}', { a: a.covered, n })}`}</span></li>`).join('')}</ul>
    <p class="note"><button class="linkish" type="button" data-open-fees>${t('Delivery fees')}</button></p></details>`;
}
// up to 4 rows open; the rest (always including the dearest) collapsed under `label`
function groupHTML(list, hero, open, label) {
  if (!list.length) return '';
  const cut = list.length > 1 ? Math.min(4, list.length - 1) : list.length;
  const rows = list.slice(0, cut), more = list.slice(cut);
  return (open ? `<div class="group-label">${open}</div>` : '')
    + (rows.length ? `<div class="ranked">${rows.map(c => row(c, hero)).join('')}</div>` : '')
    + (more.length ? `<details class="pricier"><summary>${label} (${more.length})
        <span class="range">${money(paid(more[0]))}${more.length > 1 ? ' – ' + money(paid(more.at(-1))) : ''}</span></summary>
        <div class="ranked">${more.map(c => row(c, hero)).join('')}</div></details>` : '');
}

function renderResults(loading) {
  const el = $('#results'), st = S.status;
  updateTotals();
  renderTrend();
  renderSbs();
  if (st && !st.products) {
    const ix = st.index || {};
    const bar = ix.running && ix.total ? `<div class="progress"><span style="width:${Math.round(100 * ix.processed / ix.total)}%"></span></div>` : '';
    el.innerHTML = emptyState(t('Prices are still downloading'), '', bar);
    return;
  }
  if (!S.cart.items.length) {
    el.innerHTML = emptyState(t('Your cart is empty'), '');
    return;
  }
  if (loading || !S.result) { el.innerHTML = resultsSkeleton(); return; }
  const chains = S.result.chains, n = nCompared();
  if (!chains.length) {
    const r = radius();
    el.innerHTML = (delivery()
      ? emptyState(S.result.no_area?.length ? t('No confirmed delivery') : t('No online shop has these products'), '') + noAreaHTML()
      : r ? emptyState(t('No stores within {n} km', { n: r }), '',
          `<div class="actions"><button class="primary" type="button" data-open-loc>${t('Change distance')}</button></div>`)
      : emptyState(t('No store has these products'), '')) + staleHTML();
    return;
  }
  const branches = chains.reduce((t, c) => t + (c.in_store?.branch_count || 0), 0);
  const where = delivery() ? plural(chains.length, 'online shop')
    : `${plural(branches, 'branch')} · ${plural(chains.length, 'chain')}`;
  if (!n) {  // only produce: nothing to rank; each store's own items, not compared
    el.innerHTML = `<p class="co-sum">${where}</p><div class="group-label">${t('Not compared')}</div>
      <div class="ranked">${chains.map(c => row(c, null)).join('')}</div>` + staleHTML();
    fillDrive();
    return;
  }
  const done = chains.filter(full), partial = chains.filter(c => !full(c) && counted(c));
  const none = chains.filter(c => !counted(c));  // nothing counts here (missing, or not confirmed yet): no total
  const sum = `<p class="co-sum">${where} · ${n === 1 ? t('{a} with your product', { a: done.length }) : t('{a} with all {n} products', { a: done.length, n })}</p>`;
  const hero = done[0] || partial[0];
  const noneHTML = none.length ? `<details class="pricier"${hero ? '' : ' open'}><summary>${t('Not counted')} (${none.length})</summary>
      <div class="ranked">${none.map(c => row(c, null)).join('')}</div></details>` : '';
  el.innerHTML = sum + (hero ? winner(hero) : '')
    + groupHTML(done.filter(c => c !== hero), hero, t('Next cheapest'), t('More expensive stores'))
    + (done.length
      ? (partial.length ? `<details class="pricier"><summary>${t('Missing some products')} (${partial.length})</summary>
          <div class="ranked">${partial.map(c => row(c, hero)).join('')}</div></details>` : '')
      : groupHTML(partial.filter(c => c !== hero), hero, t('Missing some products'), t('More')))
    + noneHTML + noAreaHTML() + staleHTML();
  fillDrive();
}

// ---------------------------------------------------------------- price history: every product, every chain, daily
// The indexer records each product's cheapest and dearest shelf price per chain on every day it changes.
S.trend = null;
let trendSeq = 0;
const fullChains = () => nCompared() ? (S.result?.chains || []).filter(full).slice(0, 4) : [];
async function loadTrend() {
  const ids = [...new Set([...fullChains(), ...sbsCols()].map(c => String(c.chain_id)))];
  if (!S.cart.items.length || !ids.length) { S.trend = null; renderTrend(); return; }
  const my = ++trendSeq;
  const hist = await api('/api/history/cart', { method: 'POST', body: { items: S.cart.items, chains: ids }, quiet: true }).catch(() => null);
  if (my !== trendSeq) return;
  S.trend = hist;
  renderTrend();
  renderSbs();
}
// a product's price change at a chain over the last 30 days (its cheapest branch), as a small arrow
function moveTag(c, l) {
  const m = S.trend?.changes?.[String(c.chain_id)]?.[l.key];
  if (!m) return '';
  const d = m[1] - m[0];
  return ` <small class="mv ${d > 0 ? 'up' : 'down'}" title="${t('30 days ago {price}', { price: money(m[0]) })}">${d > 0 ? '▲' : '▼'}${money(Math.abs(d))}</small>`;
}
const dayDate = d => new Date(Math.floor(d / 10000), Math.floor(d / 100) % 100 - 1, d % 100);
const fmtDay = d => dayDate(d).toLocaleDateString(locale(), { day: 'numeric', month: 'short' });
const todayNum = () => { const t = new Date(); return t.getFullYear() * 10000 + (t.getMonth() + 1) * 100 + t.getDate(); };
// fixed colour per chain (validated categorical palette, light + dark): a chain keeps its colour whatever else is shown
const SLOT = ['Rami Levy', 'Yohananof', 'Victory', 'Bareket', 'Osher Ad', 'Shufersal', 'Carrefour', 'Tiv Taam'];
const hue = name => SLOT.includes(name) ? `var(--s${SLOT.indexOf(name) + 1})` : 'var(--muted)';

const charts = new Map();
let chartId = 0;
// change-only points + the days a series stopped having a confirmed price -> points where null starts a gap
const withGaps = (pts, ends = []) => [...pts, ...(ends || []).map(d => [d, null])].sort((a, b) => a[0] - b[0]);
// step lines over time (prices change in steps), one per series, carried to today unless it stopped (a null point
// starts a gap: no confirmed price until the next point); legend + direct end labels, crosshair readout on hover,
// and a hidden table for screen readers
function lineChart(series, title) {
  const W = 760, H = 230, L = 50, R = 118, T = 12, B = 26, today = todayNum();
  const all = series.flatMap(x => x.points), vals = all.filter(p => p[1] != null);
  const t0 = dayDate(Math.min(...vals.map(p => p[0]))).getTime(), t1 = dayDate(today).getTime();
  let lo = Math.min(...vals.map(p => p[1])), hi = Math.max(...vals.map(p => p[1]));
  const pad = (hi - lo) * 0.2 || hi * 0.04 || 1;
  lo = Math.max(0, lo - pad); hi += pad;
  const x = d => L + (t1 === t0 ? 1 : (dayDate(d).getTime() - t0) / (t1 - t0)) * (W - L - R);
  const y = v => T + (hi - v) * (H - T - B) / (hi - lo);
  // one path per series, broken at gaps; a stretch too short to see as a line (e.g. back since today) gets a dot
  const segs = pts => {
    const out = [];
    for (const [day, v] of pts) {
      if (day < dayNum(t0)) continue;
      const cur = out.at(-1);
      if (v == null) { if (cur && cur.open) { cur.d += `H${x(day).toFixed(1)}`; cur.x1 = x(day); cur.open = false; } continue; }
      if (cur?.open) { cur.d += `H${x(day).toFixed(1)}V${y(v).toFixed(1)}`; continue; }
      out.push({ d: `M${x(day).toFixed(1)} ${y(v).toFixed(1)}`, x0: x(day), y0: y(v), open: true });
    }
    const last = out.at(-1);
    if (last?.open) { last.d += `H${x(today).toFixed(1)}`; last.x1 = x(today); }
    return out;
  };
  const path = pts => segs(pts).map(sg => sg.d).join('');
  const dots = (pts, col) => segs(pts).filter(sg => sg.x1 - sg.x0 < 6)
    .map(sg => `<circle class="pt" cx="${sg.x0.toFixed(1)}" cy="${sg.y0.toFixed(1)}" r="3" style="fill:${col}"/>`).join('');
  const ticks = [0, 1, 2].map(k => lo + (k + 0.5) * (hi - lo) / 3);
  const days = [...new Set(all.map(p => p[0]).filter(d => d >= dayNum(t0)))].sort();
  const xt = [days[0], ...days.filter((_, i) => i && i % Math.ceil(days.length / 4) === 0), today].filter((d, i, a) => a.indexOf(d) === i);
  // end labels for the lines that reach today, nudged apart to stay legible, each tied to its line with a short leader
  const ends = series.filter(sr => sr.points.at(-1)[1] != null).map(sr => ({ sr, y: y(sr.points.at(-1)[1]) })).sort((a, b) => a.y - b.y);
  ends.forEach((e, i) => { e.ly = i ? Math.max(e.y, ends[i - 1].ly + 15) : e.y; });
  const id = ++chartId;
  charts.set(id, { series, x, y, days, t0, t1, L, R, W, today });
  return `<div class="lchart" data-chart="${id}">
    ${series.length > 1 || ends.length < series.length ? `<div class="lc-legend">${series.map(sr => `<span><i style="background:${hue(sr.name)}"></i>${esc(cn(sr.name))}</span>`).join('')}</div>` : ''}
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}">
      ${ticks.map(v => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"/><text class="axis" x="${L - 8}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end">₪${v.toFixed(v < 20 ? 2 : 0)}</text>`).join('')}
      ${xt.map(d => `<text class="axis" x="${x(d).toFixed(1)}" y="${H - 6}" text-anchor="middle">${fmtDay(d)}</text>`).join('')}
      ${series.map(sr => `<path class="ln" d="${path(sr.points)}" style="stroke:${hue(sr.name)}"/>${dots(sr.points, hue(sr.name))}`).join('')}
      ${ends.map(e => `<path class="lead" d="M${x(today) + 3} ${e.y.toFixed(1)}L${x(today) + 9} ${e.ly.toFixed(1)}"/><text class="end" x="${x(today) + 12}" y="${(e.ly + 4).toFixed(1)}">${esc(cn(e.sr.name))}</text>`).join('')}
      <line class="xhair" y1="${T}" y2="${H - B}" hidden/>
    </svg>
    <div class="lc-tip" hidden></div>
    <table class="sr-only"><caption>${esc(title)}</caption><tr><th>${t('Date')}</th>${series.map(sr => `<th>${esc(cn(sr.name))}</th>`).join('')}</tr>
      ${days.map(d => `<tr><td>${fmtDay(d)}</td>${series.map(sr => `<td>${money(atDay(sr.points, d))}</td>`).join('')}</tr>`).join('')}</table>
  </div>`;
}
// the price on a day: the last point on or before it; none before the first point or in a gap (never a later price)
const atDay = (pts, d) => { let v = null; for (const [pd, pv] of pts) { if (pd > d) break; v = pv; } return v; };
const dayNum = t => { const d = new Date(t); return d.getFullYear() * 10000 + (d.getMonth() + 1) * 100 + d.getDate(); };
document.addEventListener('pointermove', e => {
  const box = e.target.closest?.('.lchart'); if (!box) return;
  const c = charts.get(+box.dataset.chart), svg = box.querySelector('svg'); if (!c) return;
  const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * c.W / r.width;
  const hair = svg.querySelector('.xhair'), tip = box.querySelector('.lc-tip');
  if (px < c.L || px > c.W - c.R) { hair.setAttribute('hidden', ''); tip.hidden = true; return; }
  const t = c.t0 + (px - c.L) / (c.W - c.L - c.R) * (c.t1 - c.t0), dt = new Date(t);
  const d = Math.min(c.today, dt.getFullYear() * 10000 + (dt.getMonth() + 1) * 100 + dt.getDate());
  hair.removeAttribute('hidden'); hair.setAttribute('x1', px); hair.setAttribute('x2', px);
  const rows = c.series.map(sr => [sr.name, atDay(sr.points, d)]).sort((a, b) => (a[1] ?? 1e9) - (b[1] ?? 1e9));
  tip.innerHTML = `<b>${fmtDay(d)}</b>${rows.map(([n, v]) => `<span><i style="background:${hue(n)}"></i>${esc(cn(n))}<em>${money(v)}</em></span>`).join('')}`;
  tip.hidden = false;
  tip.style.left = `${Math.max(4, Math.min(e.clientX - box.getBoundingClientRect().left + (rtl() ? -tip.offsetWidth - 14 : 14), box.clientWidth - tip.offsetWidth - 4))}px`;
});
document.addEventListener('pointerleave', e => {
  if (e.target.classList?.contains('lchart')) { e.target.querySelector('.xhair')?.setAttribute('hidden', ''); e.target.querySelector('.lc-tip').hidden = true; }
}, true);

// checkout: the whole cart over time at the chains that have all of it. The history is chain-wide (each product at
// the chain's cheapest branch), so each line is scaled to end at today's price at your branch.
function renderTrend() {
  const el = $('#trend'), hist = S.trend, ch = fullChains();
  if (!el || !S.cart.items.length || !hist || !ch.length || delivery()) { if (el) el.hidden = true; return; }
  const series = ch.map(c => {
    const id = String(c.chain_id), pts = withGaps(hist.series[id] || [], hist.ends?.[id]), last = pts.at(-1)?.[1];
    // a chain whose history stopped (a product without a confirmed price) isn't drawn as if it ran to today
    return { name: c.name, points: last ? pts.map(([d, v]) => [d, v == null ? null : v * paid(c) / last]) : [] };
  }).filter(sr => sr.points.length);
  if (!series.length) { el.hidden = true; return; }
  el.hidden = false;
  const days = new Set(series.flatMap(sr => sr.points.map(p => p[0])));
  el.innerHTML = `<h2 id="trend-title">${t('Price history')}</h2>` + (days.size < 2 && (hist.since || 0) >= todayNum()
    ? `<p class="trend-note">${t('Tracking since {day}.', { day: fmtDay(hist.since || todayNum()) })}</p>`
    : lineChart(series, t('This cart over time at {chains}', { chains: series.map(x => cn(x.name)).join(', ') })));
}

// ---------------------------------------------------------------- stores side by side, product by product
let sbsPick = (() => { try { return JSON.parse(local.get('sbs')) || []; } catch { return []; } })();
function sbsCols() {
  const ch = S.result?.chains || [];
  const pick = sbsPick.map(id => ch.find(c => String(c.chain_id) === id)).filter(Boolean);
  return pick.length >= 2 ? pick : ch.slice(0, 3);
}
$('#sbs-picks').addEventListener('click', e => {
  const b = e.target.closest('[data-sbs]'); if (!b) return;
  const cur = sbsCols().map(c => String(c.chain_id)), id = b.dataset.sbs;
  if (cur.includes(id)) {
    if (cur.length <= 2) return toast(t('Compare at least two stores'), null, null, 'warn');
    sbsPick = cur.filter(x => x !== id);
  } else {
    if (cur.length >= 5) return toast(t('Up to 5 stores side by side'), null, null, 'warn');
    sbsPick = [...cur, id];
  }
  haptic('select');
  local.set('sbs', JSON.stringify(sbsPick));
  renderSbs();
  loadTrend();
});
function renderSbs() {
  const box = $('#sbs'), ch = S.result?.chains || [], items = S.cart.items, n = nCompared();
  box.hidden = !items.length || ch.length < 2 || !n;
  if (box.hidden) return;
  const cols = sbsCols(), on = new Set(cols.map(c => String(c.chain_id)));
  $('#sbs-picks').innerHTML = ch.map(c => { const k = String(c.chain_id);
    return `<button type="button" class="pill${on.has(k) ? ' on' : ''}" data-sbs="${esc(k)}" aria-pressed="${on.has(k)}">${esc(cn(c.name))} <span>${money(paid(c))}</span></button>`; }).join('');
  const wins = cols.map(() => 0);
  let split = 0, everywhere = true;
  const body = items.map(it => {
    const m = S.meta[it.key] || {}, ls = cols.map(c => lineAt(c, it.key));
    const produce = ls.some(l => l?.status === 'not_compared');
    const have = ls.filter(l => l?.status === 'ok').map(l => l.cost), lo = have.length ? Math.min(...have) : null;
    const differ = have.length > 1 && Math.max(...have) - lo > 0.005;
    if (!produce) { if (lo == null) everywhere = false; else split += lo; }
    return `<tr${produce ? ' class="unconfirmed"' : ''}><th scope="row"><div class="sb-prod"><span class="sb-thumb">${PH}${photo(m)}</span><span class="sb-name" dir="auto">${esc(it.name || m.name || it.key)}</span><small>${fmtQty(it.qty, m.is_weighted)}${m.is_weighted ? '' : ' ×'}${produce ? ` · ${t('Not compared')}` : ''}</small></div></th>${ls.map((l, j) => {
      if (!l || l.status === 'missing') return `<td class="n miss">—<small>${t('not sold here')}</small></td>`;
      if (l.status === 'not_compared') {
        const p = ncPick(l);
        return p ? `<td class="n unc">${money(p.price * it.qty)}<small>${l.own ? '' : t('Similar') + ' · '}${bdi(p.name || '')}</small></td>` : `<td class="n miss">—<small>${t('not sold here')}</small></td>`;
      }
      if (l.status === 'unconfirmed') return `<td class="n unc">${money(l.full ?? l.price * it.qty)}<small>${unconfText(l)} · ${t('Not counted')}</small></td>`;
      const best = differ && l.cost - lo < 0.005;
      if (best) wins[j]++;
      return `<td class="n${best ? ' best' : ''}">${money(l.cost)}${moveTag(cols[j], l)}${l.promo ? `<small class="promo" dir="auto">${esc(l.promo.description || t('deal'))}</small>` : ''}${l.deals_more?.length ? `<small class="more">${l.deals_more.map(dealMoreText).join(' · ')}</small>` : ''}${l.deals_not_counted?.length ? `<small title="${esc(l.deals_not_counted.join(' · '))}">${t('Deal not counted')}</small>` : ''}</td>`;
    }).join('')}</tr>`;
  }).join('');
  const doneCols = cols.filter(full), low = doneCols.length ? Math.min(...doneCols.map(paid)) : null;
  $('#sbs-table').innerHTML = `<table class="sbs-table"><thead><tr><th scope="col">${t('Product')}</th>${cols.map(c => { const v = view(c);
      return `<th scope="col" class="n"><span class="sb-chain">${esc(cn(c.name))}</span><small>${delivery() ? t('Online store') : bdi(storeName(v)) + (v.distance_km != null ? ` · ${kmText(v.distance_km)}` : '') + driveSlot(v)}</small></th>`; }).join('')}</tr></thead>
    <tbody>${body}</tbody>
    <tfoot><tr><th scope="row">${t('Cheapest for')}</th>${wins.map(w => `<td class="n">${plural(w, 'product')}</td>`).join('')}</tr>
      ${delivery() ? `<tr><th scope="row">${t('Delivery')}</th>${cols.map(c => `<td class="n">${c.online.fee == null ? '—' : c.online.fee === 0 ? t('Free') : money(c.online.fee)}</td>`).join('')}</tr>` : ''}
      <tr class="total"><th scope="row">${t('Whole cart')}</th>${cols.map(c => { const v = view(c);
        return `<td class="n${full(c) && paid(c) - low < 0.005 ? ' best' : ''}">${money(paid(c))}${full(c) ? '' : `<small>${t('{a} of {n}', { a: v.covered, n })}</small>`}</td>`; }).join('')}</tr></tfoot></table>`;
  const one = doneCols.length ? doneCols.reduce((a, b) => paid(a) <= paid(b) ? a : b) : null, save = one ? paid(one) - split : 0;
  $('#sbs-split').innerHTML = !delivery() && one && everywhere && save >= 1
    ? t('Buying each product where it’s cheapest among these stores comes to {total} — {save} less than all of it at {chain}, if the extra stop is worth it to you.', { total: `<strong>${money(split)}</strong>`, save: money(save), chain: esc(cn(one.name)) })
    : '';
  fillDrive($('#sbs-table'));
}

// ---------------------------------------------------------------- prefs
function applyPrefsUI() {
  $$('[data-mode]').forEach(b => b.setAttribute('aria-checked', b.dataset.mode === S.prefs.mode));
  $('#club').checked = !!S.prefs.club;
  const h = S.prefs.home, btn = $('#near-btn');
  btn.classList.toggle('set', !!h);
  const place = h ? bdi(h.label.split(',')[0]) : '';
  $('#near-label').innerHTML = !h ? t('Set your location')
    : `<span class="nl-long">${S.prefs.max_km ? t('Within {n} km of {place}', { n: S.prefs.max_km, place }) : t('Near {place}', { place })}</span>`
      + `<span class="nl-short"><bdi class="nl-place">${esc(h.label.split(',')[0])}</bdi>${S.prefs.max_km ? `<span class="nl-km">${t('{n} km', { n: S.prefs.max_km })}</span>` : ''}</span>`;
  btn.title = h ? h.label : '';
}
function setPrefs(p) {
  Object.assign(S.prefs, p);
  applyPrefsUI();
  S.result = null;
  compareNow();
  if (document.body.dataset.view === 'product' && S.product) openProduct(S.product.p.key);
  const where = 'home' in p || 'max_km' in p;
  return api('/api/settings', { method: 'PUT', body: { prefs: p } })
    .then(() => { if (where) { updateMap(); if (document.body.dataset.view === 'home') dealRows(); } })
    .catch(e => { if (e.message !== 'signed out') toast(t("Couldn't save your settings"), null, null, 'error'); });
}
$$('[data-mode]').forEach(b => { b.onclick = () => { haptic('select'); setPrefs({ mode: b.dataset.mode }); }; });
$('#club').onchange = e => { haptic('select'); setPrefs({ club: e.target.checked }); };
$('#near-btn').onclick = () => openLocation();  // the address picker lives in account.js

// ---------------------------------------------------------------- status / freshness
function renderFreshness() {
  const st = S.status, el = $('#freshness');
  if (!st) return;
  const ix = st.index || {};
  const pct = ix.running && ix.total ? Math.round(100 * ix.processed / ix.total) : 0;
  const updating = ix.running ? ' · ' + (pct ? t('updating {n}%', { n: pct }) : t('updating…')) : '';
  const d = tsDate(st.data_ts);
  if (!d) { el.innerHTML = `<span class="dot stale"></span>${t('No prices yet')}${updating}`; return; }
  const stale = Date.now() - d > 12 * 3600e3;
  el.innerHTML = `<span class="dot${stale ? ' stale' : ''}"></span>${t('Prices from {when}', { when: fmtDate(d) })} · ${plural(st.chains, 'chain')} · ${plural(st.stores, 'store')}${updating}`;
}

let lastStatusCompare = 0;
async function pollStatus() {
  try {
    const st = await api('/api/status', { quiet: true }), prev = S.status;
    S.status = st;
    renderFreshness();
    const changed = !prev || prev.data_ts !== st.data_ts || prev.products !== st.products || (S.prefs.home && prev.located !== st.located);
    // while indexing, counts change every poll: re-price at most once a minute, keeping the current view until then
    if (changed && (!prev || Date.now() - lastStatusCompare > 60000)) {
      lastStatusCompare = Date.now();
      compareNow();
    } else if (!st.products) renderResults();
  } catch { /* server restarting; try again */ }
  setTimeout(pollStatus, S.status?.index?.running || !S.status?.products ? 4000 : 60000);
}

// ---------------------------------------------------------------- dialogs
$$('dialog').forEach(d => d.addEventListener('click', e => {
  if (e.target === d || e.target.closest('[data-close]')) closeSheet(d);
}));

// delivery fees
const FEES = () => [['fee', t('Delivery fee')], ['reduced_fee', t('Lower fee')], ['reduced_above', t('…for baskets over')],
                    ['free_above', t('Free over')], ['min_order', t('Minimum order')]];
const RESET = () => `<button class="ghost small reset" type="button" data-reset>${t('Reset')}</button>`;
const feeVal = v => v == null ? '' : Number.isInteger(v) ? v : Number(v).toFixed(2);
function renderFees(rows) {
  const fees = FEES();
  $('#fee-table').innerHTML = `<thead><tr><th>${t('Chain')}</th>${fees.map(f => `<th>${f[1]}</th>`).join('')}<th>${t('Delivers to me')}</th><th></th></tr></thead><tbody>`
    + rows.map(r => `<tr data-cid="${esc(r.chain_id)}">
        <td>${esc(cn(r.name))}${r.note ? `<small>${esc(serverText(r.note))}</small>` : ''}</td>
        ${fees.map(([k, label]) => `<td data-label="${esc(label)}"><input type="number" inputmode="decimal" min="0" step="0.01" data-f="${k}" value="${feeVal(r[k])}" aria-label="${esc(cn(r.name))} ${label}"></td>`).join('')}
        <td data-label="${esc(t('Delivers to me'))}"><label class="fee-check"><input type="checkbox" data-f="delivers_here" ${r.delivers_here === true ? 'checked' : ''} aria-label="${esc(t('{chain} delivers to me', { chain: cn(r.name) }))}"></label></td>
        <td>${r.custom ? RESET() : ''}</td></tr>`).join('') + '</tbody>';
}
async function saveFees(delivery) {
  try {
    const d = await api('/api/settings', { method: 'PUT', body: { delivery } });
    S.result = null;
    compareNow();
    return d;
  } catch { toast(t("Couldn't save delivery fees"), null, null, 'error'); }
}
document.addEventListener('click', e => { if (e.target.closest('[data-open-fees]')) $('#open-settings').click(); });
$('#open-settings').onclick = async () => {
  const d = await api('/api/settings').catch(() => null);
  if (!d) return toast(t("Couldn't load delivery fees"), null, null, 'error');
  renderFees(d.delivery);
  openSheet($('#settings'));
};
$('#fee-table').addEventListener('change', e => {
  const tr = e.target.closest('tr[data-cid]'); if (!tr) return;
  const rule = {};
  for (const [k] of FEES()) { const v = tr.querySelector(`[data-f="${k}"]`).value; rule[k] = v === '' ? null : +v; }
  rule.delivers_here = tr.querySelector('[data-f="delivers_here"]').checked ? true : null;
  if (e.target.type === 'checkbox') haptic('select');
  tr.lastElementChild.innerHTML = RESET();  // don't re-render: keeps focus while tabbing through fields
  saveFees({ [tr.dataset.cid]: rule });
});
$('#fee-table').addEventListener('click', async e => {
  if (!e.target.matches('[data-reset]')) return;
  const d = await saveFees({ [e.target.closest('tr').dataset.cid]: { reset: true } });
  if (d) renderFees(d.delivery);
});

// compare baskets
let cmpSeq = 0;
async function openCompare() {
  await saving;
  const carts = await api('/api/carts').catch(() => []);
  const body = $('#compare-body');
  if (carts.length < 2) {
    body.innerHTML = `<p class="sheet-note">${t('Make another cart to compare.')}</p>`;
    return openSheet($('#compare'));
  }
  const pre = new Set([...new Set([S.cart.id, ...carts.map(c => c.id)])].filter(Boolean).slice(0, 2));
  body.innerHTML = `    <div class="cmp-picks">${carts.map(c => `<label><input type="checkbox" value="${c.id}" ${pre.has(c.id) ? 'checked' : ''}><span dir="auto">${esc(c.name)}</span></label>`).join('')}</div>
    <div id="cmp-table" class="table-scroll"></div>`;
  openSheet($('#compare'));
  runCompare();
}
$('#compare-body').addEventListener('change', e => {
  if ($$('#compare-body input:checked').length > 4) { e.target.checked = false; return toast(t('Up to 4 carts'), null, null, 'warn'); }
  haptic('select');
  runCompare();
});
async function runCompare() {
  const ids = $$('#compare-body input:checked').map(i => +i.value), out = $('#cmp-table'), my = ++cmpSeq;
  if (ids.length < 2) { out.innerHTML = `<p class="note">${t('Pick two or more.')}</p>`; return; }
  out.innerHTML = '<div class="skeleton short"></div>';
  let carts, res;
  try {
    carts = await Promise.all(ids.map(id => api(`/api/carts/${id}`)));
    res = await Promise.all(carts.map(c => comparePost({ items: c.items, prefs: S.prefs })));
  } catch { out.innerHTML = `<p class="note">${t('Couldn’t price these carts.')}</p>`; return; }
  if (my !== cmpSeq) return;
  const names = [...new Set(res.flatMap(r => r.chains.map(c => c.name)))];
  const cell = (r, n) => r.chains.find(c => c.name === n);
  // "together": what all the picked baskets cost at one chain; only chains that sell every basket count
  // only chains with a confirmed price for every compared product of every basket count
  const together = n => res.every(r => cell(r, n) && full(cell(r, n))) ? res.reduce((s, r) => s + paid(cell(r, n)), 0) : null;
  names.sort((a, b) => (together(a) ?? 1e9) - (together(b) ?? 1e9));
  const bestTogether = names[0] && together(names[0]) != null ? names[0] : null;
  out.innerHTML = `<table class="cmp-table"><thead><tr><th>${t('Chain')}</th>${carts.map(c => `<th class="n"><bdi>${esc(c.name)}</bdi><br><small>${plural(c.items.length, 'product')}</small></th>`).join('')}
      <th class="n">${t('All together')}</th></tr></thead>
    <tbody>${names.map(n => `<tr><td>${esc(cn(n))}</td>${res.map(r => {
      const c = cell(r, n);
      if (!c) return '<td class="n">—</td>';
      const isBest = r.chains[0].name === n && full(c) && nCompared(r) > 0;
      return `<td class="n${isBest ? ' best' : ''}">${nCompared(r) ? money(paid(c)) : '—'}${nCompared(r) && !full(c) ? `<br><span class="chip warn">${t('{a} of {n}', { a: view(c).covered, n: nCompared(r) })}</span>` : ''}</td>`;
    }).join('')}<td class="n${n === bestTogether ? ' best' : ''}">${money(together(n))}</td></tr>`).join('')}</tbody></table>`;
}

// ---------------------------------------------------------------- theme
// auto follows the device; the choice is per browser (localStorage)
const THEMES = {
  auto: ['Match device', '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="6.2" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M10 3.8a6.2 6.2 0 0 1 0 12.4z" fill="currentColor"/></svg>'],
  light: ['Light', '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="3.4" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>'],
  dark: ['Dark', '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M16 12.6A6.5 6.5 0 0 1 7.4 4a6.5 6.5 0 1 0 8.6 8.6z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>'],
};
let theme = THEMES[local.get('theme')] ? local.get('theme') : 'auto';
function applyTheme() {
  if (theme === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  const b = $('#theme-btn'), [name, icon] = THEMES[theme];
  b.innerHTML = icon;
  b.title = b.ariaLabel = t('Theme: {name} (click to change)', { name: t(name) });
  $$('[data-theme-pick]').forEach(x => x.setAttribute('aria-checked', x.dataset.themePick === theme));
}
function setTheme(name) {
  theme = name;
  local.set('theme', theme);
  haptic('select');
  applyTheme();
  restyleMap();
}
$('#theme-btn').onclick = () => {
  const order = Object.keys(THEMES);
  setTheme(order[(order.indexOf(theme) + 1) % order.length]);
};
applyTheme();

// ---------------------------------------------------------------- language: everything on screen in the other language
function relabel() {
  applyTheme();
  applyPrefsUI();
  renderFreshness();
  renderRail();
  if (mapData) renderMapInfo();
  renderBasket();
  renderResults();
  drawPins();
  if (document.body.dataset.view === 'basket') renderStoreCard(false);
  if (document.body.dataset.view === 'product' && S.product) { renderProduct(); }
  else route();
}

// ---------------------------------------------------------------- start (account.js calls this once you're signed in)
async function startApp() {
  const settings = await api('/api/settings').catch(() => null);
  if (settings) { S.prefs = { ...S.prefs, ...settings.prefs }; S.traffic = settings.traffic; }
  applyPrefsUI();
  route();
  api('/api/categories', { quiet: true }).then(c => { cats = cats || c; renderRail(); }).catch(() => {});
  await loadCarts();
  const carts = S.carts;
  const want = carts.find(c => c.id === +local.get('cart')) || carts[0];
  if (want) await openCart(want.id);
  else { renderBasket(); renderResults(); }
  pollStatus();
}
