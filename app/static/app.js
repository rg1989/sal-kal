'use strict';
// Sal frontend: vanilla JS, no build. Talks to app/server.py.

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = v => v == null ? '—' : '₪' + Number(v).toFixed(2);
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
const fmtDate = d => d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
const kmText = d => d == null ? '' : d < 1 ? `${Math.max(100, Math.round(d * 10) * 100)} m` : `${d < 10 ? d.toFixed(1) : Math.round(d)} km`;
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
  const r = await fetch(path, {
    method: opts.method || 'GET',
    headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

const S = {
  cart: { id: null, name: 'Weekly shop', items: [] },  // items: [{key, qty, name}]
  meta: {},           // product key -> {name, manufacturer, is_weighted, min, max, median, stores}
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
// what this chain costs here, before estimating missing items
const paid = c => delivery() ? (c.online.delivered ?? c.online.total) : c.in_store.total;
// the number chains are compared on: when items are missing, add their typical price elsewhere ("≈")
const approx = c => view(c).missing.length > 0;
const head = c => !approx(c) ? paid(c) : delivery() ? (c.online.delivered_comparable ?? c.online.comparable) : c.in_store.comparable;
const headText = c => (approx(c) ? '≈' : '') + money(head(c));
const comparable = c => delivery() ? c.online.delivered_comparable : c.in_store.comparable;
const radius = () => S.prefs.home && S.prefs.max_km ? S.prefs.max_km : null;

// ---------------------------------------------------------------- toast
let toastTimer;
function toast(msg, action, fn) {
  const t = $('#toast');
  (document.querySelector('dialog[open]') || document.body).append(t);  // stay above an open dialog's backdrop
  t.innerHTML = `<span>${esc(msg)}</span>` + (action ? `<button type="button">${esc(action)}</button>` : '');
  if (action) t.querySelector('button').onclick = () => { t.hidden = true; fn(); };
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, action ? 8000 : 3000);
}

// ---------------------------------------------------------------- basket
function metaHTML(it) {
  const m = S.meta[it.key] || {};
  const parts = [];
  if (clean(m.manufacturer)) parts.push(bdi(clean(m.manufacturer)));
  if (m.min != null) parts.push((m.min === m.max ? money(m.min) : `${money(m.min)}–${money(m.max)}`) + (m.is_weighted ? '/kg' : ''));
  if (m.stores === 0) parts.push('not on sale anywhere right now');
  return parts.join(' · ');
}

// The live cart is always on screen (a column on desktop, a sheet + total bar on phones): every product, its price
// at the store the cart is priced at (the cheapest, or the one you tap), and where the whole cart costs least.
const pinned = () => { const ch = S.result?.chains || []; return ch.find(c => String(c.chain_id) === S.pin) || ch[0] || null; };
const lineAt = (c, key) => c ? view(c)?.lines.find(l => l.key === key) : null;
const wide = () => matchMedia('(min-width: 1100px)').matches;

function itemNote(it, c, l) {
  const m = S.meta[it.key] || {}, kg = m.is_weighted;
  if (!l) return metaHTML(it);
  if (!l.available) return `<span class="warn">Not at ${esc(c.name)}${m.median != null ? ` · usually ≈${money(m.median * it.qty)}` : ''}</span>`;
  return (it.qty !== 1 ? `${fmtQty(it.qty, kg)} × ${money(l.price)}` : money(l.price) + (kg ? '/kg' : ''))
    + (l.promo ? ` · <span class="promo" dir="auto">${esc(l.promo.description || 'deal')}</span>` : '');
}
function itemPrice(it, l) {
  const m = S.meta[it.key] || {};
  if (!l) return '';
  if (!l.available) return m.median != null ? '≈' + money(m.median * it.qty) : '—';
  return money(l.cost);
}

function renderBasket() {
  const items = S.cart.items, c = pinned();
  const a = document.activeElement, li = a?.closest?.('#drawer-items .item');
  const keep = li && { i: li.dataset.i, val: a.matches('input') ? a.value : null,
                       sel: a.matches('input') ? 'input' : `[data-step="${a.dataset.step}"]` };
  $('#drawer-items').innerHTML = items.map((it, i) => {
    const m = S.meta[it.key] || {}, kg = m.is_weighted, l = lineAt(c, it.key), last = it.qty <= (kg ? 0.5 : 1);
    return `<li class="item${it.key === S.lastAdded ? ' added' : ''}${l && !l.available ? ' missing' : ''}${l?.promo ? ' deal' : ''}" data-i="${i}">
      <span class="lc-thumb">${PH}${photo(m)}</span>
      <span class="item-name" dir="auto">${esc(it.name || m.name || it.key)}</span>
      <span class="item-meta">${itemNote(it, c, l)}</span>
      <span class="stepper">
        <button type="button" data-step="-1" aria-label="${last ? 'Remove' : 'One less'}">${last ? SVG.trash : '−'}</button>
        <input type="number" inputmode="decimal" min="0" step="${kg ? 0.5 : 1}" value="${it.qty}" aria-label="Quantity">${kg ? '<span class="unit">kg</span>' : ''}
        <button type="button" data-step="1" aria-label="One more">+</button>
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
  $('#lc-sum').innerHTML = !n ? '' : `${plural(n, 'product')}${units !== n ? ` · ${units} items` : ''}`
    + (c ? ` · prices at <strong>${esc(c.name)}</strong>` : '');
  $('#cart-bar').hidden = !n;
  $('#cb-n').textContent = n;
  $('#cb-txt').innerHTML = c ? `<strong class="num">${headText(c)}</strong> at ${esc(c.name)}` : 'Finding the cheapest store…';
  renderStores();
}

// the stores the cart costs least at; tap one to see every line priced there
function renderStores() {
  const el = $('#lc-stores'), ch = S.result?.chains || [], n = S.cart.items.length, p = pinned();
  if (!n) { el.innerHTML = ''; return; }
  if (!S.result) { el.innerHTML = '<p class="lc-wait">Finding the cheapest store…</p>'; return; }
  if (!ch.length) {
    el.innerHTML = `<p class="lc-wait">No store${radius() && !delivery() ? ` within ${radius()} km` : ''} has these products. <button class="linkish" type="button" data-open-loc>Change distance</button></p>`;
    return;
  }
  $('#all-branches').checked = S.allBranches;
  $('#all-branches').closest('.switch').hidden = delivery();
  const bs = S.result.branches || [];
  if (S.allBranches && !delivery() && bs.length) {
    $('#lc-label').innerHTML = `All branches <span>· ${bs.length}, cheapest first</span>`;
    let gap = false;
    const sel = selStore();
    el.innerHTML = '<div class="lc-scroll">' + bs.map((b, i) => {
      const sep = !gap && b.missing ? (gap = true, '<p class="lc-sep">Missing some products</p>') : '';
      const on = sel && String(sel.chain_id) === String(b.chain_id) && sel.store_id === b.store_id;
      return sep + `<button type="button" class="lc-store${i === 0 && !b.missing ? ' best' : ''}${on ? ' on' : ''}" data-branch="${i}" aria-pressed="${!!on}">
        <span class="ls-name">${esc(b.chain)}${i === 0 && !b.missing ? ' <em>Cheapest</em>' : ''}</span>
        <span class="ls-sub">${bdi(storeName(b))}${b.distance_km != null ? ` · ${kmText(b.distance_km)}` : ''} · ${b.missing ? `${b.covered} of ${n}` : n === 1 ? 'has it' : `all ${n}`}</span>
        <span class="ls-total num">${b.missing ? '≈' : ''}${money(b.missing ? b.comparable : b.total)}</span></button>`;
    }).join('') + '</div>';
    return;
  }
  const list = ch.slice(0, 4);
  if (p && !list.includes(p)) list.push(p);
  // the map counts branches; this ranks chains, each at its cheapest branch near you — say so, with the totals
  $('#lc-label').innerHTML = `Cheapest stores${ch.length > list.length ? ` <span>· top ${list.length} of ${ch.length}</span>` : ''}`;
  el.innerHTML = list.map(c => {
    const v = view(c), best = c === ch[0];
    const where = delivery() ? (v.fee == null ? 'Delivered · fee not set' : v.fee === 0 ? 'Free delivery' : `incl. ${money(v.fee)} delivery`)
      : bdi(storeName(v)) + (v.distance_km != null ? ` · ${kmText(v.distance_km)}` : '');
    return `<button type="button" class="lc-store${c === p ? ' on' : ''}${best ? ' best' : ''}" data-pin="${esc(c.chain_id)}" aria-pressed="${c === p}">
      <span class="ls-name">${esc(c.name)}${best ? ' <em>Cheapest</em>' : ''}</span>
      <span class="ls-sub">${where} · ${v.missing.length ? `${v.covered} of ${n} products` : n === 1 ? 'has it' : `all ${n}`}</span>
      <span class="ls-total num">${headText(c)}</span></button>`;
  }).join('');
}
function pin(id) { S.pin = id; S.sel = null; renderBasket(); drawPins(); renderStoreCard(true); }
S.allBranches = local.get('branches') === 'all';
function setAllBranches(on) {
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
  } catch { toast("Couldn't save the cart"); }
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
  changed();
  toast(`Removed ${gone.name || 'item'}`, 'Undo', () => { S.cart.items.splice(i, 0, gone); changed(); });
}

$('#drawer-items').addEventListener('click', e => {
  const li = e.target.closest('.item'), step = e.target.closest('[data-step]');
  if (li && step) stepCart(S.cart.items[+li.dataset.i].key, +step.dataset.step);
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
  } catch { S.cart = { id: null, name: 'Weekly shop', items: [] }; }
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
  }).join('') + '<button class="pill add" type="button" data-cart="new">+ New cart</button>';
}
$('#cart-tabs').addEventListener('click', async e => {
  const t = e.target.closest('[data-cartid]');
  if (t && +t.dataset.cartid !== S.cart.id) { await openCart(+t.dataset.cartid); return; }
  if (e.target.closest('[data-cart="new"]')) {
    await saving;
    const n = S.carts.length + 1;
    const c = await api('/api/carts', { method: 'POST', body: { name: `Cart ${n}` } }).catch(() => null);
    if (!c) return toast("Couldn't create a cart");
    await loadCarts();
    await openCart(c.id);
    $('#drawer-cart-name').focus();
    $('#drawer-cart-name').select();
  }
});
$('#drawer-cart-name').addEventListener('change', e => {
  S.cart.name = e.target.value.trim() || 'Untitled';
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
    return toast(`Emptied ${S.cart.name}`, 'Undo', () => { S.cart.items = old; changed(); });
  }
  if (b.dataset.cart === 'dup' && S.cart.id) {
    const c = await api(`/api/carts/${S.cart.id}/duplicate`, { method: 'POST' });
    await loadCarts();
    await openCart(c.id);
    toast(`Made “${c.name}”`);
  }
  if (b.dataset.cart === 'del') {
    if (!S.cart.id) { S.cart.items = []; return changed(); }
    if (!confirm(`Delete the cart “${S.cart.name}”?`)) return;
    await api(`/api/carts/${S.cart.id}`, { method: 'DELETE' });
    await loadCarts();
    if (S.carts.length) await openCart(S.carts[0].id);
    else { S.cart = { id: null, name: 'Weekly shop', items: [] }; S.result = null; renderBasket(); renderResults(); }
    toast('Cart deleted');
  }
});

// ---------------------------------------------------------------- search
// ---------------------------------------------------------------- cart actions
function addToCart(h, qty = 1, quiet = false) {
  const kg = !!h.is_weighted, fresh = !S.cart.items.length && !S.cart.id;
  S.meta[h.key] = { ...S.meta[h.key], name: h.name, manufacturer: h.manufacturer, is_weighted: kg,
                    min: h.min_price, max: h.max_price, stores: h.store_count, img: h.img || S.meta[h.key]?.img };
  const ex = S.cart.items.find(i => i.key === h.key);
  if (ex) ex.qty = Math.round((ex.qty + qty) * 100) / 100;
  else S.cart.items.push({ key: h.key, qty, name: h.name });
  S.lastAdded = h.key;
  changed();
  for (const b of [$('#basket-btn'), $('#cart-bar')]) { b.classList.remove('bump'); void b.offsetWidth; b.classList.add('bump'); }
  if (wide()) { if (fresh && !quiet) toast(`Started the cart “${S.cart.name}”`); return; }  // the cart column shows the rest
  if (!quiet) toast(fresh ? `Started the cart “${S.cart.name}”` : qty > 1 ? `Added ${qty} to ${S.cart.name}` : `Added to ${S.cart.name}`, 'View cart', showCart);
}
function stepCart(key, d) {
  const i = S.cart.items.findIndex(x => x.key === key);
  if (i < 0) return;
  const it = S.cart.items[i], step = S.meta[key]?.is_weighted ? 0.5 : 1;
  const q = Math.round((it.qty + d * step) * 100) / 100;
  if (q < step - 1e-9) removeAt(i);
  else { it.qty = q; changed(); }
}
function showCart() {
  if (wide()) { const c = $('#cart'); c.classList.remove('flash'); void c.offsetWidth; c.classList.add('flash'); return; }
  document.body.classList.add('cart-open');
  $('#cart').focus();
}
const closeCart = () => document.body.classList.remove('cart-open');
$('#basket-btn').onclick = showCart;

const go = h => { if (location.hash !== h) location.hash = h; else route(); };
$('.brand').addEventListener('click', e => { e.preventDefault(); go('#/'); });
// Typing in the header search filters the product grid live (no dropdown). The first keystroke opens the results page
// (one history entry, so Back returns to where you were); every later keystroke just updates it in place.
const topQ = $('#top-q');
let liveFrom = null;  // where you were when you started typing
const runLive = debounce(() => {
  const text = topQ.value.trim();
  if (text.length < 2) {
    if (!text && liveFrom != null) { const back = liveFrom; liveFrom = null; go(back); }
    return;
  }
  B = { cat: '', terms: [text], sizes: [], deal: false, sort: '' };
  const h = browseHash(B);
  if (liveFrom != null && document.body.dataset.view === 'browse') { history.replaceState(null, '', h); openBrowse(true); }
  else { liveFrom = location.hash || '#/'; location.hash = h; }
}, 180);
topQ.addEventListener('input', runLive);
topQ.addEventListener('change', () => { const t = topQ.value.trim(); if (t.length > 1) remember(t); });
topQ.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); topQ.blur(); }  // results are already on screen; this just drops the keyboard
  else if (e.key === 'Escape') { topQ.value = ''; runLive(); }
});
document.addEventListener('click', e => {
  if (e.target.closest('[data-close-cart]')) closeCart();
  if (e.target.closest('[data-open-loc]')) openLocation();
  const a = e.target.closest('[data-add]');
  if (a) { e.preventDefault(); const h = S.hits[a.dataset.add]; if (h) addToCart(h, +(a.dataset.qty || 1)); }
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
    ? `<span class="qty-ctl" data-ckey="${esc(h.key)}"><button type="button" data-cstep="-1" aria-label="One less">−</button><span class="num">${fmtQty(it.qty, h.is_weighted)}</span><button type="button" data-cstep="1" aria-label="One more">+</button></span>`
    : `<button class="add" type="button" data-add="${esc(h.key)}"${deal && deal.min_qty > 1 ? ` data-qty="${deal.min_qty}"` : ''}>${deal && deal.min_qty > 1 ? `Add ${deal.min_qty}` : 'Add'}</button>`;
}
const perText = u => u ? `${money(u.v)} / ${u.per}` : '';
function card(h) {
  S.hits[h.key] = { ...S.hits[h.key], ...h };
  const d = h.deal, n = h.near, unit = h.is_weighted ? '/kg' : '';
  // the server's pack size (null when the name's number is something else, e.g. grams of protein); no bare numbers
  const facts = ['pack' in h ? h.pack : h.size, perText(h.per)].filter(Boolean).join(' · ');
  const price = d
    ? `<span class="c-price deal"><small>${d.min_qty > 1 ? `each, ×${d.min_qty}` : 'deal price'}</small>${money(d.unit_price)}${unit}</span>`
    : n ? `<span class="c-price"><small>near you</small>${money(n.price)}${unit}</span>`
    : h.min_price == null ? '<span class="c-price"></span>'
    : `<span class="c-price"><small>${h.min_price === h.max_price ? 'price' : 'from'}</small>${money(h.min_price)}${unit}</span>`;
  const info = d
    ? `<span class="c-deal" dir="auto">${esc(d.description)}</span>
       <span class="c-meta">${esc(d.chain)}${d.distance_km != null ? ` · ${kmText(d.distance_km)}` : ''} · usually ${money(d.typical)}</span>
       ${facts ? `<span class="c-meta">${bdi(facts)}</span>` : ''}`
    : `<span class="c-meta">${facts ? bdi(facts) : ''}</span>`
      + (n ? `<span class="c-near">Cheapest <strong>${money(n.price)}${unit}</strong> at ${esc(n.chain)}${n.km != null ? ` · ${kmText(n.km)}` : ''}</span>`
           : `<span class="c-meta">${h.scope === 'chain' && h.chain_count <= 1 ? esc(h.chain || '') + ' only' : plural(h.chain_count, 'chain')}</span>`);
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
const skeletons = n => '<li class="card skel"><span class="c-thumb"></span></li>'.repeat(n);
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
  const keep = ['key', 'name', 'manufacturer', 'quantity', 'unit', 'is_weighted', 'min_price', 'max_price',
                'store_count', 'chain_count', 'scope', 'chain', 'img'];
  const o = Object.fromEntries(keep.map(k => [k, p[k]]));
  local.set('viewed', JSON.stringify([o, ...viewed().filter(v => v.key !== p.key)].slice(0, 12)));
}
let cats = null;
const catTile = c => `<a class="cat" href="#/c/${c.slug}" style="--tint:${c.tint}">
  <span class="cat-icon" aria-hidden="true">${catIcon(c)}</span><span class="cat-name">${esc(c.name)}</span><span class="cat-he" dir="rtl">${esc(c.he)}</span></a>`;
async function renderHome() {
  const r = recent(), v = viewed();
  $('#recent').innerHTML = r.map(t => `<a class="pill" href="#/s/${encodeURIComponent(t)}" dir="auto">${esc(t)}</a>`).join('');
  $('#recent-wrap').hidden = !r.length;
  $('#viewed').innerHTML = v.map(card).join('');
  $('#viewed-wrap').hidden = !v.length;
  for (const id of ['biggest', 'fresh']) if (!$('#' + id).children.length) $('#' + id).innerHTML = skeletons(6);
  initMap();
  // your basket = every product in any of your carts; nothing to show until a cart has items
  fill($('#mine'), $('#mine-wrap'), () => api('/api/deals?kind=basket&limit=12'));
  fill($('#biggest'), $('#biggest-wrap'), () => api('/api/deals?kind=biggest&limit=12'));
  fill($('#fresh'), $('#fresh-wrap'), () => api('/api/deals?kind=new&limit=12'));
  fill($('#picks'), $('#picks-wrap'), () => api('/api/picks?limit=12'));
  cats = cats || await api('/api/categories').catch(() => null);
  $('#cats').innerHTML = (cats || []).map(catTile).join('');
}

function renderRail() {
  $('#rail-list').innerHTML = (cats || []).map(c => `<a class="rail-cat" href="#/c/${c.slug}" data-slug="${c.slug}">${catIcon(c)}<span>${esc(c.name)}</span></a>`).join('');
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
  const dx = r / (111.32 * Math.cos(home.lat * Math.PI / 180)), dy = r / 110.574;
  if (!fitRoute(animate)) map.fitBounds([[home.lon - dx, home.lat - dy], [home.lon + dx, home.lat + dy]], { padding: edge(), duration: animate ? 900 : 0 });
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
    info.innerHTML = `<button class="linkish" type="button" data-open-loc>Set your address</button>&nbsp;to see the stores around you`;
    return;
  }
  const chains = new Set(mapData.stores.map(x => x.chain)).size, where = mapData.max_km ? `within ${mapData.max_km} km` : 'nearby';
  info.innerHTML = !delivery() && S.result?.chains?.length
    ? `<span class="mi-dot"></span><span class="mi-seg" role="group" aria-label="Branches on the map">
        <button type="button" data-branches="best" aria-pressed="${!S.allBranches}">Cheapest of each chain <b>${S.result.chains.length}</b></button>
        <button type="button" data-branches="all" aria-pressed="${S.allBranches}">All branches <b>${mapData.total}</b></button></span>`
    : `<span class="mi-dot"></span><span><strong>${mapData.total}</strong> ${mapData.total === 1 ? 'branch' : 'branches'} of <strong>${chains}</strong> ${chains === 1 ? 'chain' : 'chains'} ${where}</span>`;
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
  pin.title = 'Home';
  homeMarker = new maplibregl.Marker({ element: pin }).setLngLat([34.83, 32.07]).addTo(map);
  map.on('style.load', addOverlays);  // every style load (first one and theme switches) gets the overlays back
  updateMap();  // data doesn't wait for the map; it's drawn as soon as the overlays exist
  drawPins();
  const pop = new maplibregl.Popup({ closeButton: false, offset: 10, className: 'sal-pop' });
  map.on('click', 'stores', e => {
    const f = e.features[0].properties;
    pop.setLngLat(e.lngLat).setHTML(`<strong>${esc(f.chain)}</strong><br><bdi>${esc(f.name)}</bdi> · ${kmText(+f.km)}`).addTo(map);
  });
  map.on('mouseenter', 'stores', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'stores', () => { map.getCanvas().style.cursor = ''; });
}
// the map lives on the home page and at checkout; it moves with you rather than loading twice
function placeMap(slot) {
  const w = $('#map-wrap');
  if (w.parentElement !== slot) slot.append(w, $('#store-card'));
  map?.resize();
}
// the cheapest stores for this cart, with their totals, right on the map; the cheapest one in the brand yellow
let pins = [];
function drawPins() {
  if (!map) return;
  pins.forEach(m => m.remove());
  pins = [];
  if (delivery() || !S.cart.items.length) return;
  const ch = (S.result?.chains || []).slice(0, 4), p = pinned();
  [...ch].reverse().forEach(c => {  // the cheapest last, so it's drawn on top
    const v = c.in_store;
    if (v?.lat == null) return;
    const best = c === ch[0], el = document.createElement('button');
    el.type = 'button';
    el.className = `price-pin${best ? ' best' : ''}${c === p ? ' on' : ''}`;
    el.innerHTML = `${best ? '<em>Cheapest</em>' : ''}<b class="num">${headText(c)}</b><span>${esc(c.name)}</span>`;
    el.title = `${c.name} · ${storeName(v)}${v.distance_km != null ? ' · ' + kmText(v.distance_km) : ''} — price the cart here`;
    el.addEventListener('click', e => { e.stopPropagation(); pin(String(c.chain_id)); });
    pins.push(new maplibregl.Marker({ element: el, anchor: 'bottom', offset: [0, -4] }).setLngLat([v.lon, v.lat]).addTo(map));
  });
}
// ---------------------------------------------------------------- the selected store: route, address, hours, directions
S.sel = null;  // a branch tapped in "All branches"; otherwise the store the cart is priced at
const storeCache = new Map();
const DAYNAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
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
  return c?.in_store ? { ...c.in_store, chain: c.name, missing: c.in_store.missing.length } : null;
}
const mins = t => { const [h, m] = t.split(':'); return +h * 60 + +m; };
function openNow(day) {  // "07:00–23:00, 16:00–20:00" | "Closed" | "Open 24 hours"
  if (!day || day === 'Closed') return false;
  if (day === 'Open 24 hours') return true;
  const d = new Date(), now = d.getHours() * 60 + d.getMinutes();
  return day.split(', ').some(r => { const [a, b] = r.split('–').map(mins); return b > a ? now >= a && now < b : now >= a || now < b; });
}
function hoursHTML(h, gsearch) {
  if (!h) return '<span class="muted">Looking up opening hours…</span>';
  if (h.week) {
    const today = new Date().getDay(), t = h.week[today];
    return `<span>Today ${esc(t)}</span> ${openNow(t) ? '<b class="open">Open now</b>' : '<b class="shut">Closed now</b>'}
      <details class="sc-week"><summary>All week</summary><table>${h.week.map((x, i) =>
        `<tr${i === today ? ' class="today"' : ''}><th>${DAYNAMES[i]}</th><td>${esc(x)}</td></tr>`).join('')}</table></details>`;
  }
  if (h.hours) return `<span dir="ltr">${esc(h.hours)}</span>`;
  return `<span class="muted">${h.checked === false ? 'Couldn’t check the opening hours just now' : 'Opening hours aren’t listed for this branch'} —
    <a href="${gsearch}" target="_blank" rel="noopener">see them on Google Maps</a></span>`;
}
function storeCardHTML(v, d, h) {
  const best = S.result.chains[0].in_store, n = S.cart.items.length;
  const isBest = String(best.chain_id) === String(v.chain_id) && best.store_id === v.store_id;
  const addr = d ? [d.address, d.city].filter(Boolean).join(', ') : '';
  const home = S.prefs.home, dest = d?.lat != null ? `${d.lat},${d.lon}` : '';
  const gsearch = 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent([d?.brand_he || v.chain, addr].filter(Boolean).join(' '));
  const gdir = `https://www.google.com/maps/dir/?api=1${home ? `&origin=${home.lat},${home.lon}` : ''}&destination=${dest}&travelmode=driving`;
  return `<div class="sc-head">
      <div><p class="sc-chain">${esc(v.chain)}${isBest ? ' <em>Cheapest for this cart</em>' : ''}</p><p class="sc-branch">${bdi(storeName(v))}</p></div>
      <p class="sc-total"><span class="num">${v.missing ? '≈' : ''}${money(v.missing ? v.comparable : v.total)}</span>
        <small>${v.missing ? `${v.covered} of ${n} products here` : n === 1 ? 'for your product' : `all ${n} products`}</small></p>
    </div>
    <ul class="sc-rows">
      <li>${ICO.pin}<span>${d ? (addr ? bdi(addr) : '<span class="muted">Address not listed</span>') : '<span class="muted">Finding the address…</span>'}</span></li>
      <li>${ICO.car}<span>${d?.route ? `${d.route.km} km by road` : `${kmText(v.distance_km)} away`}<span class="drive" data-sk="${esc(v.chain_id)}:${v.store_id}"></span></span></li>
      <li title="Opening hours: OpenStreetMap">${ICO.clock}<span class="sc-hours">${hoursHTML(h, gsearch)}</span></li>
    </ul>
    ${dest ? `<div class="sc-actions">
      <a class="sc-btn waze" href="https://waze.com/ul?ll=${dest}&navigate=yes" target="_blank" rel="noopener">${ICO.go}Open in Waze</a>
      <a class="sc-btn" href="${gdir}" target="_blank" rel="noopener">${ICO.go}Open in Google Maps</a></div>` : ''}
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
  if (g && (fit || key !== fittedTo)) fitRoute(true);
}
// the map fades out at its edges, so keep what matters in the clear middle (and room above for the price pins)
const edge = () => innerWidth < 600 ? { top: 105, bottom: 45, left: 64, right: 64 } : { top: 130, bottom: 70, left: 90, right: 90 };
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
  $('#b-pills').innerHTML = (c ? `<span class="term cat">${catIcon(c)} ${esc(c.name)}<button type="button" data-rmcat aria-label="Remove ${esc(c.name)}">×</button></span>` : '')
    + B.terms.map((t, i) => `<span class="term"><bdi>${esc(t)}</bdi><button type="button" data-rmterm="${i}" aria-label="Remove ${esc(t)}">×</button></span>`).join('');
  $('#b-add').placeholder = B.terms.length || B.cat ? 'Narrow down…' : 'Type a product…';
  $('#b-add').value = '';
}

let listSeq = 0;
async function openBrowse(live) {
  const my = ++listSeq, grid = $('#b-cards');
  cats = cats || await api('/api/categories').catch(() => []);
  $('#b-cats').innerHTML = cats.map(c => `<a class="pill${c.slug === B.cat ? ' on' : ''}" href="${browseHash({ ...B, cat: c.slug === B.cat ? '' : c.slug, sizes: [] })}">${catIcon(c)} ${esc(c.name)}</a>`).join('');
  renderTerms();
  markRail();
  if (live && grid.querySelector('.card:not(.skel)')) grid.classList.add('busy');
  else { $('#b-title').textContent = 'Searching…'; grid.innerHTML = skeletons(12); }
  const qs = new URLSearchParams({ terms: B.terms.join('|'), cat: B.cat, size: B.sizes.join('|'), deal: B.deal ? 1 : 0, sort: B.sort, limit: 120 });
  const r = await api('/api/browse?' + qs).catch(() => null);
  if (my !== listSeq) return;
  grid.classList.remove('busy');
  if (!r) { $('#b-title').textContent = 'Couldn’t load products — try again in a moment'; $('#b-cards').innerHTML = ''; return; }
  $('#b-title').innerHTML = r.total
    ? `${r.total.toLocaleString()}${r.more ? '+' : ''} ${r.total === 1 ? 'product' : 'products'}${r.total > r.products.length ? ` <small>top ${r.products.length} shown</small>` : ''}`
    : 'Nothing matches all of these filters';
  $('#b-refine').innerHTML = r.refine.length ? '<span class="facet-label">Narrow down</span>'
    + r.refine.map(w => `<button class="pill sug" type="button" data-term="${esc(w.word)}"><bdi>${esc(w.word)}</bdi> <span>${w.n}</span></button>`).join('') : '';
  $('#b-sizes').innerHTML = r.sizes.length ? '<span class="facet-label">Size</span>'
    + r.sizes.map(z => `<button class="pill sug${B.sizes.includes(z.size) ? ' on' : ''}" type="button" data-size="${esc(z.size)}">${esc(z.size)} <span>${z.n}</span></button>`).join('') : '';
  $('#b-deal').setAttribute('aria-pressed', B.deal);
  $('#b-deal').innerHTML = `On deal now <span>${r.deal_n}</span>`;
  $('#b-sort').value = B.sort;
  $('#b-cards').innerHTML = r.products.length ? r.products.map(card).join('')
    : '';
}

$('#b-add').addEventListener('keydown', e => {
  const v = e.target.value.trim();
  if (e.key === 'Enter' && v) { e.preventDefault(); remember(v); if (!B.terms.includes(v)) setBrowse({ terms: [...B.terms, v] }); }
  else if (e.key === 'Backspace' && !e.target.value) {
    if (B.terms.length) setBrowse({ terms: B.terms.slice(0, -1) });
    else if (B.cat) setBrowse({ cat: '' });
  }
});
$('#view-browse').addEventListener('click', e => {
  const t = e.target.closest('[data-term]');
  if (t) return setBrowse({ terms: [...B.terms, t.dataset.term] });
  const rt = e.target.closest('[data-rmterm]');
  if (rt) return setBrowse({ terms: B.terms.filter((_, i) => i !== +rt.dataset.rmterm) });
  if (e.target.closest('[data-rmcat]')) return setBrowse({ cat: '', sizes: [] });
  const z = e.target.closest('[data-size]');
  if (z) { const v = z.dataset.size; return setBrowse({ sizes: B.sizes.includes(v) ? B.sizes.filter(x => x !== v) : [...B.sizes, v] }); }
  if (e.target.closest('#b-deal')) return setBrowse({ deal: !B.deal });
  if (e.target.closest('#b-terms') && !e.target.closest('button')) $('#b-add').focus();
});
$('#b-sort').onchange = e => setBrowse({ sort: e.target.value });

// ---------------------------------------------------------------- product page
let prodSeq = 0;
async function openProduct(key) {
  const el = $('#view-product'), my = ++prodSeq;
  if (S.product?.p.key !== key) el.innerHTML = '<div class="skeleton"></div>';
  let p, res;
  try {
    [p, res] = await Promise.all([
      S.hits[key] || api('/api/product?key=' + encodeURIComponent(key)),
      api('/api/compare', { method: 'POST', body: { items: [{ key, qty: 1 }], prefs: { ...S.prefs, mode: 'store' } } }),
    ]);
  } catch {
    el.innerHTML = '<a class="back" href="#/" data-back>← Back</a>' + emptyState('Couldn’t load this product', '');
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
    <span><strong dir="auto">${esc(d.description)}</strong> at ${esc(d.chain)}${d.distance_km != null ? ` (${kmText(d.distance_km)})` : ''}:
    ${money(d.unit_price)} each${d.min_qty > 1 ? ` when you buy ${d.min_qty}` : ''}, usually ${money(d.typical)}.</span>
    ${d.min_qty > 1 && !S.cart.items.some(i => i.key === key) ? `<button class="add" type="button" data-add="${esc(key)}" data-qty="${d.min_qty}">Add ${d.min_qty}</button>` : ''}`;
  $('#p-deal').hidden = false;
}

function offer(c, best) {
  const s = c.in_store, l = s.lines[0], kg = S.product.p.is_weighted;
  const promo = l?.promo ? `<span class="chip promo" dir="auto">${esc(l.promo.description || 'Promotion')}</span>` : '';
  return `<li class="offer${best ? ' best' : ''}">
    <span class="o-chain">${esc(c.name)}${best ? ' <span class="o-badge">Cheapest</span>' : ''}</span>
    <span class="o-store">${storePlace(s)}${s.branch_count > 1 ? ` · cheapest of ${s.branch_count} branches` : ''}</span>
    ${promo ? `<span class="o-flags">${promo}</span>` : ''}
    <span class="o-price">${l?.promo ? `<s>${money(l.full)}</s>` : ''}${money(s.total)}${kg ? '<em>/kg</em>' : ''}
      ${c.online ? `<small>Online ${money(c.online.total)}</small>` : ''}</span>
  </li>`;
}

function renderBuy() {
  const { p } = S.product, it = S.cart.items.find(i => i.key === p.key);
  $('#p-buy').innerHTML = it
    ? `${qtyCtl(p)}<span class="in-cart">In your cart · <button class="linkish" type="button" data-showcart>View cart</button></span>`
    : `<button class="primary" type="button" data-add="${esc(p.key)}">Add to cart</button>`;
}

function renderProduct() {
  const { p, res } = S.product, el = $('#view-product'), kg = !!p.is_weighted;
  const chains = res.chains, r = radius();
  const meta = [clean(p.manufacturer), qtyText(p.quantity, p.unit), kg ? 'sold by weight' : ''].filter(Boolean);
  const shown = chains.slice(0, 6), more = chains.slice(6);
  el.innerHTML = `<a class="back" href="#/" data-back>← Back</a>
    <div class="p-head">
      <span class="c-thumb p-thumb">${PH}${photo(p)}</span>
      <div class="p-info">
        <h1 dir="auto">${esc(p.name)}</h1>
        ${meta.length ? `<p class="p-meta">${meta.map(bdi).join(' · ')}</p>` : ''}
        ${chains.length ? `<p class="p-from">From <strong class="num">${money(chains[0].in_store.total)}${kg ? '/kg' : ''}</strong> at ${esc(chains[0].name)}</p>` : ''}
        <div class="buy" id="p-buy"></div>
      </div>
    </div>
    <p class="p-deal" id="p-deal" hidden></p>
    ${chains.length ? `<h2 class="p-h">Price at ${plural(chains.length, 'chain')}${r ? ` within ${r} km` : ''} · cheapest first</h2>
      <ol class="offers">${shown.map((c, i) => offer(c, i === 0)).join('')}</ol>
      ${more.length ? `<details class="pricier"><summary>More expensive (${more.length})
        <span class="range">${money(more[0].in_store.total)}${more.length > 1 ? ' – ' + money(more.at(-1).in_store.total) : ''}</span></summary>
        <ol class="offers">${more.map(c => offer(c, false)).join('')}</ol></details>` : ''}`
    : emptyState(r ? `Not sold within ${r} km` : 'Not on sale right now', '',
        r ? '<div class="actions"><button class="primary" type="button" data-open-loc>Change distance</button></div>' : '')}
    <section class="p-history"></section>`;
  renderBuy();
  fillDrive(el);
  productHistory(p.key, chains.slice(0, 4).map(c => c.name));
}
async function productHistory(key, names) {
  const t = await api('/api/history?key=' + encodeURIComponent(key)).catch(() => null);
  const el = $('#view-product .p-history');
  if (!t || !el || S.product?.p.key !== key) return;
  const series = t.chains.filter(c => names.includes(c.chain)).map(c => ({ name: c.chain, points: c.points.map(p => [p[0], p[1]]) }));
  const days = new Set(series.flatMap(sr => sr.points.map(p => p[0])));
  el.innerHTML = !series.length ? '' : '<h2 class="p-h">Price history</h2>' + (days.size < 2 && (t.since || 0) >= todayNum()
    ? `<p class="trend-note">Tracking since ${fmtDay(t.since || todayNum())}.</p>`
    : lineChart(series, 'Cheapest shelf price over time at each chain'));
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
  for (const id of ['home', 'browse', 'product', 'basket']) $(`#view-${id}`).hidden = id !== v;
  document.body.dataset.view = v;
  renderStoreCard(false);  // shows at checkout, hides (with its route) everywhere else
  if (document.activeElement !== topQ) { topQ.value = ''; if (v !== 'browse') liveFrom = null; }
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
}
window.addEventListener('hashchange', route);

// ---------------------------------------------------------------- compare
let compareSeq = 0;
async function compareNow() {
  const my = ++compareSeq;
  if (!S.cart.items.length) { S.result = null; drawPins(); drawDots(); if (mapData) renderMapInfo(); renderStoreCard(false); updateTotals(); return renderResults(); }
  if (!S.result) renderResults(true);
  try {
    const r = await api('/api/compare', { method: 'POST', body: { items: S.cart.items, prefs: S.prefs } });
    if (my !== compareSeq) return;
    S.result = r;
    for (const it of r.items) {
      const m = S.meta[it.key] || {};
      S.meta[it.key] = { ...m, name: m.name || it.name, manufacturer: it.manufacturer, is_weighted: it.is_weighted,
                         min: it.min, max: it.max, median: it.median, stores: it.stores, img: it.img || m.img };
    }
    renderBasket();
    renderResults();
    drawPins();
    drawDots();
    renderMapInfo();
    renderStoreCard(false);
    loadTrend();
  } catch { if (my === compareSeq) toast("Couldn't compare prices — is the server up?"); }
}
const compareSoon = debounce(compareNow, 250);

function emptyState(title, text, extra = '') {
  return `<div class="empty-results"><div class="ghost-tag" aria-hidden="true"></div><h2>${title}</h2><p>${text}</p>${extra}</div>`;
}

const realCity = c => c && !/^\d+$/.test(c.trim()) ? c.trim() : '';
const storeName = v => clean(v.name) || `Branch ${v.store_id}`;
function storePlace(v) {
  const city = realCity(v.city), n = storeName(v);
  const name = city && n.includes(city) ? n : [n, city].filter(Boolean).join(', ');
  return bdi(name) + (v.distance_km != null ? ` · ${kmText(v.distance_km)} away` : '') + driveSlot(v);
}
const place = v => delivery() ? `Online store · ${bdi(storeName(v))}` : storePlace(v);
const driveSlot = v => S.prefs.home ? `<span class="drive" data-sk="${esc(v.chain_id)}:${v.store_id}"></span>` : '';

// drive times load after the results so pricing never waits on the routing service
async function fillDrive(root = $('#results')) {
  const els = [...root.querySelectorAll('.drive[data-sk]')];
  if (!els.length) return;
  const my = root._driveSeq = (root._driveSeq || 0) + 1;
  const stores = [...new Set(els.map(e => e.dataset.sk))].map(k => [k.slice(0, k.lastIndexOf(':')), +k.slice(k.lastIndexOf(':') + 1)]);
  let r;
  try { r = await api('/api/drive', { method: 'POST', body: { home: S.prefs.home, stores } }); } catch { return; }
  if (my !== root._driveSeq) return;
  els.forEach(e => {
    const m = r.times[e.dataset.sk];
    if (m == null) return;
    e.textContent = ` · ${m < 1 ? '<1' : m} min ${r.live ? 'drive now' : 'drive'}`;
    e.title = r.live ? 'Driving time in current traffic (Google Maps)' : 'Typical driving time, without live traffic';
  });
}

// What a chain costs in store, online, delivered — shared by the hero and the rows.
function facts(c, skipHeadline) {
  const o = c.online, s = c.in_store, out = [];
  if (s && !(skipHeadline && !delivery())) out.push(['In store', money(s.total)]);
  if (!o) { out.push(['', 'No online shop in the price files', true]); return out; }
  out.push(['Groceries online', money(o.total)]);
  if (o.fee == null) out.push(['', o.fee_note || 'Delivery fee not set', true]);
  else {
    out.push(['Delivery', o.fee === 0 ? 'Free' : money(o.fee)]);
    if (!(skipHeadline && delivery())) out.push(['Delivered total', money(o.delivered)]);
    if (o.fee_note) out.push(['', o.fee_note, true]);
  }
  if (o.price_vs_store != null) {
    const d = o.price_vs_store;
    out.push(['Online vs in store', Math.abs(d) < 0.005 ? 'Same prices' : `₪${Math.abs(d).toFixed(2)} ${d > 0 ? 'dearer' : 'cheaper'}`]);
  }
  return out;
}

function fmtQty(q, kg) { return kg ? `${q} kg` : `${q}`; }

function linesTable(ls) {
  return `<table class="lines"><tbody>${ls.map(l => {
    const it = S.cart.items.find(i => i.key === l.key), m = S.meta[l.key] || {};
    const name = esc(it?.name || m.name || l.key);
    if (!l.available) {
      const est = m.median != null ? ` — about ${money(m.median * (it?.qty ?? 1))} elsewhere` : '';
      return `<tr class="missing"><td class="l-name"><span dir="auto">${name}</span><small>Not sold here${est}</small></td><td class="l-cost">Missing</td></tr>`;
    }
    const sub = `${fmtQty(l.qty, m.is_weighted)} × ${money(l.price)}${l.by_name ? ' · matched by name' : ''}`;
    const promo = l.promo ? ` <span class="chip promo" dir="auto">${esc(l.promo.description || 'Promotion')}</span>`
      + (l.promo.club ? ' <span class="chip">Club</span>' : '') : '';
    return `<tr><td class="l-name"><span dir="auto">${name}</span><small>${sub}${promo}</small></td>
      <td class="l-cost${l.promo ? ' promo' : ''}">${l.promo ? `<s>${money(l.full)}</s>` : ''}${money(l.cost)}</td></tr>`;
  }).join('')}</tbody></table>`;
}

function winner(c) {
  const v = view(c), total = head(c), n = S.cart.items.length, next = S.result.chains[1];
  const [whole, ag] = total.toFixed(2).split('.');
  const lead = next && comparable(next) != null && comparable(c) != null ? comparable(next) - comparable(c) : 0;
  const all = n === 1 ? 'Your item' : `All ${n} items`;
  const sub = v.missing.length
    ? `<strong>${v.covered} of ${n}</strong> items here for ${money(paid(c))} · ≈${money(v.estimate_missing)} more for the ${v.missing.length} missing elsewhere`
    : `${all} here` + (lead > 0.005 ? ` · <strong>${money(lead)}</strong> less than ${esc(next.name)}` : '');
  const foot = facts(c, true).map(([k, val, warn]) => warn
    ? `<div>${k ? esc(k) + ': ' : ''}${esc(val)}</div>`
    : `<div class="alt"><span>${esc(k)}</span><strong>${esc(val)}</strong></div>`).join('');
  const others = !delivery() && c.in_store.branches.length > 1
    ? `<p class="note">Other branches: ${c.in_store.branches.slice(1, 4).map(b =>
        `${bdi(storeName(b))} ${money(b.total)}${b.distance_km != null ? ` (${kmText(b.distance_km)}${driveSlot(b)})` : ''}`).join(' · ')}</p>` : '';
  const r = radius();
  const branches = delivery() ? 'Prices on the chain’s website'
    : c.in_store.branch_count > 1 ? `Cheapest of ${c.in_store.branch_count} branches${r ? ` within ${r} km` : ''}`
    : r ? `Only branch within ${r} km` : 'Only branch with prices';
  return `<article class="winner">
    <div class="tag-face">
      <p class="tag-chain">${esc(c.name)}</p>
      <p class="tag-store">${place(v)}</p>
      <p class="tag-total" aria-label="${headText(c)}"><span class="cur">${approx(c) ? '≈' : ''}₪</span>${whole}<span class="agorot">.${ag}</span></p>
      <p class="tag-sub">${sub}</p>
      <div class="tag-foot">${foot}</div>
    </div>
    <div class="winner-detail">
      <div class="detail-head"><h3>${delivery() ? 'Online prices' : 'In-store prices'}</h3><span class="branches">${branches}</span></div>
      ${linesTable(v.lines)}${others}
    </div>
  </article>`;
}

function row(c, hero) {
  const v = view(c);
  const d = comparable(c) != null && comparable(hero) != null ? comparable(c) - comparable(hero) : null;
  const promos = v.lines.filter(l => l.promo).length;
  const flags = [
    v.missing.length && `<span class="chip warn">${v.missing.length} missing · ${money(paid(c))} for the rest</span>`,
    promos && `<span class="chip promo">${promos} on promotion</span>`,
    delivery() && c.online.fee_note && `<span class="chip warn">${esc(c.online.fee_note)}</span>`,
  ].filter(Boolean).join('');
  const alt = facts(c).map(([k, val, warn]) => warn
    ? `<span class="chip warn">${k ? esc(k) + ': ' : ''}${esc(val)}</span>`
    : `<span>${esc(k)} <strong class="num">${esc(val)}</strong></span>`).join('');
  return `<details class="row">
    <summary>
      <span class="row-chain">${esc(c.name)}</span>
      <span class="row-store">${place(v)}</span>
      ${flags ? `<span class="flags">${flags}</span>` : ''}
      <span class="row-total">${headText(c)}</span>
      <span class="row-delta">${d == null ? '' : d < 0.005 ? 'same' : '+' + money(d)}</span>
      ${SVG.chev}
    </summary>
    <div class="row-body"><div class="row-alt">${alt}</div>${linesTable(v.lines)}</div>
  </details>`;
}

function renderResults(loading) {
  const el = $('#results'), st = S.status;
  updateTotals();
  renderTrend();
  renderSbs();
  if (st && !st.products) {
    const ix = st.index || {};
    const bar = ix.running && ix.total ? `<div class="progress"><span style="width:${Math.round(100 * ix.processed / ix.total)}%"></span></div>` : '';
    el.innerHTML = emptyState('Prices are still downloading', '', bar);
    return;
  }
  if (!S.cart.items.length) {
    el.innerHTML = emptyState('Your cart is empty', '');
    return;
  }
  if (loading || !S.result) { el.innerHTML = '<div class="skeleton"></div>'; return; }
  const chains = S.result.chains;
  if (!chains.length) {
    const r = radius();
    el.innerHTML = delivery()
      ? emptyState('No online shop has these products', '')
      : r ? emptyState(`No stores within ${r} km`, '',
          '<div class="actions"><button class="primary" type="button" data-open-loc>Change distance</button></div>')
      : emptyState('No store has these products', '');
    return;
  }
  const [hero, ...rest] = chains;
  const n = S.cart.items.length, full = chains.filter(c => !view(c).missing.length).length;
  const branches = chains.reduce((t, c) => t + (c.in_store?.branch_count || 0), 0);
  const sum = `<p class="co-sum">${delivery() ? plural(chains.length, 'online shop')
    : `${branches} ${branches === 1 ? 'branch' : 'branches'} · ${plural(chains.length, 'chain')}`} · ${full} with all ${n} ${n === 1 ? 'product' : 'products'}</p>`;
  // next ~4 as rows; the rest — always including the most expensive — collapsed
  const cut = rest.length ? Math.min(4, rest.length - 1) : 0;
  const rows = rest.slice(0, cut), pricier = rest.slice(cut);
  const r = radius();
  el.innerHTML = sum + winner(hero)
    + (rows.length ? `<div class="group-label">Next cheapest</div><div class="ranked">${rows.map(c => row(c, hero)).join('')}</div>` : '')
    + (pricier.length ? `<details class="pricier"><summary>More expensive stores (${pricier.length})
        <span class="range">${headText(pricier[0])}${pricier.length > 1 ? ' – ' + headText(pricier.at(-1)) : ''}</span></summary>
        <div class="ranked">${pricier.map(c => row(c, hero)).join('')}</div></details>` : '');
  fillDrive();
}

// ---------------------------------------------------------------- price history: every product, every chain, daily
// The indexer records each product's cheapest and dearest shelf price per chain on every day it changes.
S.trend = null;
let trendSeq = 0;
const fullChains = () => (S.result?.chains || []).filter(c => !view(c).missing.length).slice(0, 4);
async function loadTrend() {
  const ids = [...new Set([...fullChains(), ...sbsCols()].map(c => String(c.chain_id)))];
  if (!S.cart.items.length || !ids.length) { S.trend = null; renderTrend(); return; }
  const my = ++trendSeq;
  const t = await api('/api/history/cart', { method: 'POST', body: { items: S.cart.items, chains: ids } }).catch(() => null);
  if (my !== trendSeq) return;
  S.trend = t;
  renderTrend();
  renderSbs();
}
// a product's price change at a chain over the last 30 days (its cheapest branch), as a small arrow
function moveTag(c, l) {
  const m = S.trend?.changes?.[String(c.chain_id)]?.[l.key];
  if (!m) return '';
  const d = m[1] - m[0];
  return ` <small class="mv ${d > 0 ? 'up' : 'down'}" title="30 days ago ${money(m[0])}">${d > 0 ? '▲' : '▼'}${money(Math.abs(d))}</small>`;
}
const dayDate = d => new Date(Math.floor(d / 10000), Math.floor(d / 100) % 100 - 1, d % 100);
const fmtDay = d => dayDate(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
const todayNum = () => { const t = new Date(); return t.getFullYear() * 10000 + (t.getMonth() + 1) * 100 + t.getDate(); };
// fixed colour per chain (validated categorical palette, light + dark): a chain keeps its colour whatever else is shown
const SLOT = ['Rami Levy', 'Yohananof', 'Victory', 'Bareket', 'Osher Ad', 'Shufersal', 'Carrefour', 'Tiv Taam'];
const hue = name => SLOT.includes(name) ? `var(--s${SLOT.indexOf(name) + 1})` : 'var(--muted)';

const charts = new Map();
let chartId = 0;
// step lines over time (prices change in steps), one per series, carried to today; legend + direct end labels,
// crosshair readout on hover, and a hidden table for screen readers
function lineChart(series, title) {
  const W = 760, H = 230, L = 50, R = 118, T = 12, B = 26, today = todayNum();
  const all = series.flatMap(x => x.points), t0 = dayDate(Math.min(...all.map(p => p[0]))).getTime(), t1 = dayDate(today).getTime();
  let lo = Math.min(...all.map(p => p[1])), hi = Math.max(...all.map(p => p[1]));
  const pad = (hi - lo) * 0.2 || hi * 0.04 || 1;
  lo = Math.max(0, lo - pad); hi += pad;
  const x = d => L + (t1 === t0 ? 1 : (dayDate(d).getTime() - t0) / (t1 - t0)) * (W - L - R);
  const y = v => T + (hi - v) * (H - T - B) / (hi - lo);
  const path = pts => pts.map(([d, v], i) => (i ? `H${x(d).toFixed(1)}V${y(v).toFixed(1)}` : `M${x(d).toFixed(1)} ${y(v).toFixed(1)}`)).join('') + `H${x(today).toFixed(1)}`;
  const ticks = [0, 1, 2].map(k => lo + (k + 0.5) * (hi - lo) / 3);
  const days = [...new Set(all.map(p => p[0]))].sort();
  const xt = [days[0], ...days.filter((_, i) => i && i % Math.ceil(days.length / 4) === 0), today].filter((d, i, a) => a.indexOf(d) === i);
  // end labels, nudged apart to stay legible, each tied to its line with a short leader
  const ends = series.map(sr => ({ sr, y: y(sr.points.at(-1)[1]) })).sort((a, b) => a.y - b.y);
  ends.forEach((e, i) => { e.ly = i ? Math.max(e.y, ends[i - 1].ly + 15) : e.y; });
  const id = ++chartId;
  charts.set(id, { series, x, y, days, t0, t1, L, R, W, today });
  return `<div class="lchart" data-chart="${id}">
    ${series.length > 1 ? `<div class="lc-legend">${series.map(sr => `<span><i style="background:${hue(sr.name)}"></i>${esc(sr.name)}</span>`).join('')}</div>` : ''}
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}">
      ${ticks.map(v => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"/><text class="axis" x="${L - 8}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end">₪${v.toFixed(v < 20 ? 2 : 0)}</text>`).join('')}
      ${xt.map(d => `<text class="axis" x="${x(d).toFixed(1)}" y="${H - 6}" text-anchor="middle">${fmtDay(d)}</text>`).join('')}
      ${series.map(sr => `<path class="ln" d="${path(sr.points)}" style="stroke:${hue(sr.name)}"/>`).join('')}
      ${ends.map(e => `<path class="lead" d="M${x(today) + 3} ${e.y.toFixed(1)}L${x(today) + 9} ${e.ly.toFixed(1)}"/><text class="end" x="${x(today) + 12}" y="${(e.ly + 4).toFixed(1)}">${esc(e.sr.name)}</text>`).join('')}
      <line class="xhair" y1="${T}" y2="${H - B}" hidden/>
    </svg>
    <div class="lc-tip" hidden></div>
    <table class="sr-only"><caption>${esc(title)}</caption><tr><th>Date</th>${series.map(sr => `<th>${esc(sr.name)}</th>`).join('')}</tr>
      ${days.map(d => `<tr><td>${fmtDay(d)}</td>${series.map(sr => `<td>${money(atDay(sr.points, d))}</td>`).join('')}</tr>`).join('')}</table>
  </div>`;
}
const atDay = (pts, d) => { let v = pts[0][1]; for (const [pd, pv] of pts) { if (pd > d) break; v = pv; } return v; };
document.addEventListener('pointermove', e => {
  const box = e.target.closest?.('.lchart'); if (!box) return;
  const c = charts.get(+box.dataset.chart), svg = box.querySelector('svg'); if (!c) return;
  const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * c.W / r.width;
  const hair = svg.querySelector('.xhair'), tip = box.querySelector('.lc-tip');
  if (px < c.L || px > c.W - c.R) { hair.setAttribute('hidden', ''); tip.hidden = true; return; }
  const t = c.t0 + (px - c.L) / (c.W - c.L - c.R) * (c.t1 - c.t0), dt = new Date(t);
  const d = Math.min(c.today, dt.getFullYear() * 10000 + (dt.getMonth() + 1) * 100 + dt.getDate());
  hair.removeAttribute('hidden'); hair.setAttribute('x1', px); hair.setAttribute('x2', px);
  const rows = c.series.map(sr => [sr.name, atDay(sr.points, d)]).sort((a, b) => a[1] - b[1]);
  tip.innerHTML = `<b>${fmtDay(d)}</b>${rows.map(([n, v]) => `<span><i style="background:${hue(n)}"></i>${esc(n)}<em>${money(v)}</em></span>`).join('')}`;
  tip.hidden = false;
  tip.style.left = `${Math.min(e.clientX - box.getBoundingClientRect().left + 14, box.clientWidth - tip.offsetWidth - 4)}px`;
});
document.addEventListener('pointerleave', e => {
  if (e.target.classList?.contains('lchart')) { e.target.querySelector('.xhair')?.setAttribute('hidden', ''); e.target.querySelector('.lc-tip').hidden = true; }
}, true);

// checkout: the whole cart over time at the chains that have all of it. The history is chain-wide (each product at
// the chain's cheapest branch), so each line is scaled to end at today's price at your branch.
function renderTrend() {
  const el = $('#trend'), t = S.trend, ch = fullChains();
  if (!el || !S.cart.items.length || !t || !ch.length || delivery()) { if (el) el.hidden = true; return; }
  const series = ch.map(c => {
    const pts = t.series[String(c.chain_id)] || [], last = pts.at(-1)?.[1];
    return { name: c.name, points: last ? pts.map(([d, v]) => [d, v * paid(c) / last]) : [] };
  }).filter(sr => sr.points.length);
  if (!series.length) { el.hidden = true; return; }
  el.hidden = false;
  const days = new Set(series.flatMap(sr => sr.points.map(p => p[0])));
  el.innerHTML = `<h2 id="trend-title">Price history</h2>` + (days.size < 2 && (t.since || 0) >= todayNum()
    ? `<p class="trend-note">Tracking since ${fmtDay(t.since || todayNum())}.</p>`
    : lineChart(series, `This cart over time at ${series.map(x => x.name).join(', ')}`));
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
    if (cur.length <= 2) return toast('Compare at least two stores');
    sbsPick = cur.filter(x => x !== id);
  } else {
    if (cur.length >= 5) return toast('Up to 5 stores side by side');
    sbsPick = [...cur, id];
  }
  local.set('sbs', JSON.stringify(sbsPick));
  renderSbs();
  loadTrend();
});
function renderSbs() {
  const box = $('#sbs'), ch = S.result?.chains || [], items = S.cart.items;
  box.hidden = !items.length || ch.length < 2;
  if (box.hidden) return;
  const cols = sbsCols(), on = new Set(cols.map(c => String(c.chain_id)));
  $('#sbs-picks').innerHTML = ch.map(c => { const k = String(c.chain_id);
    return `<button type="button" class="pill${on.has(k) ? ' on' : ''}" data-sbs="${esc(k)}" aria-pressed="${on.has(k)}">${esc(c.name)} <span>${headText(c)}</span></button>`; }).join('');
  const wins = cols.map(() => 0);
  let split = 0, everywhere = true;
  const body = items.map(it => {
    const m = S.meta[it.key] || {}, ls = cols.map(c => lineAt(c, it.key));
    const have = ls.filter(l => l?.available).map(l => l.cost), lo = have.length ? Math.min(...have) : null;
    const differ = have.length > 1 && Math.max(...have) - lo > 0.005;
    if (lo == null) everywhere = false; else split += lo;
    return `<tr><th scope="row"><div class="sb-prod"><span class="sb-thumb">${PH}${photo(m)}</span><span class="sb-name" dir="auto">${esc(it.name || m.name || it.key)}</span><small>${fmtQty(it.qty, m.is_weighted)}${m.is_weighted ? '' : ' ×'}</small></div></th>${ls.map((l, j) => {
      if (!l?.available) return '<td class="n miss">—<small>not sold here</small></td>';
      const best = differ && l.cost - lo < 0.005;
      if (best) wins[j]++;
      return `<td class="n${best ? ' best' : ''}">${money(l.cost)}${moveTag(cols[j], l)}${l.promo ? `<small class="promo" dir="auto">${esc(l.promo.description || 'deal')}</small>` : ''}</td>`;
    }).join('')}</tr>`;
  }).join('');
  const low = Math.min(...cols.map(head));
  $('#sbs-table').innerHTML = `<table class="sbs-table"><thead><tr><th scope="col">Product</th>${cols.map(c => { const v = view(c);
      return `<th scope="col" class="n"><span class="sb-chain">${esc(c.name)}</span><small>${delivery() ? 'Online store' : bdi(storeName(v)) + (v.distance_km != null ? ` · ${kmText(v.distance_km)}` : '') + driveSlot(v)}</small></th>`; }).join('')}</tr></thead>
    <tbody>${body}</tbody>
    <tfoot><tr><th scope="row">Cheapest for</th>${wins.map(w => `<td class="n">${plural(w, 'product')}</td>`).join('')}</tr>
      ${delivery() ? `<tr><th scope="row">Delivery</th>${cols.map(c => `<td class="n">${c.online.fee == null ? '—' : c.online.fee === 0 ? 'Free' : money(c.online.fee)}</td>`).join('')}</tr>` : ''}
      <tr class="total"><th scope="row">Whole cart</th>${cols.map(c => { const miss = view(c).missing.length;
        return `<td class="n${head(c) - low < 0.005 ? ' best' : ''}">${headText(c)}${miss ? `<small>${miss} missing, at the usual price</small>` : ''}</td>`; }).join('')}</tr></tfoot></table>`;
  const one = cols.reduce((a, b) => head(a) <= head(b) ? a : b), save = head(one) - split;
  $('#sbs-split').innerHTML = !delivery() && everywhere && save >= 1
    ? `Buying each product where it’s cheapest among these stores comes to <strong>${money(split)}</strong> — ${money(save)} less than all of it at ${esc(one.name)}, if the extra stop is worth it to you.`
    : '';
  fillDrive($('#sbs-table'));
}

// ---------------------------------------------------------------- prefs
function applyPrefsUI() {
  $$('[data-mode]').forEach(b => b.setAttribute('aria-checked', b.dataset.mode === S.prefs.mode));
  $('#club').checked = !!S.prefs.club;
  const h = S.prefs.home, btn = $('#near-btn');
  btn.classList.toggle('set', !!h);
  $('#near-label').innerHTML = !h ? 'Set your location'
    : `${S.prefs.max_km ? `Within ${S.prefs.max_km} km of` : 'Near'} ${bdi(h.label.split(',')[0])}`;
  btn.title = h ? h.label : '';
}
function setPrefs(p) {
  Object.assign(S.prefs, p);
  applyPrefsUI();
  S.result = null;
  compareNow();
  if (document.body.dataset.view === 'product' && S.product) openProduct(S.product.p.key);
  api('/api/settings', { method: 'PUT', body: { prefs: p } }).then(() => { if ('home' in p || 'max_km' in p) updateMap(); })
    .catch(() => toast("Couldn't save your settings"));
}
$$('[data-mode]').forEach(b => { b.onclick = () => setPrefs({ mode: b.dataset.mode }); });
$('#club').onchange = e => setPrefs({ club: e.target.checked });

// location
const RADII = [1, 2, 3, 5, 10, 20, null];
function renderLocation() {
  const h = S.prefs.home;
  $('#loc-set').hidden = !h;
  if (h) {
    $('#loc-label').textContent = h.label;
    $('#radius').innerHTML = RADII.map(r => `<button role="radio" type="button" data-km="${r ?? ''}" aria-checked="${(S.prefs.max_km ?? null) === r}">${r ? r + ' km' : 'Any distance'}</button>`).join('');
  }
  $('#traffic-state').textContent = S.traffic
    ? 'Drive times: live traffic (Google Maps). Save an empty key for typical times.'
    : 'Drive times: typical, without traffic. A Google Maps key (Routes API) adds live traffic.';
  $('#loc-note').textContent = 'Address lookup © OpenStreetMap contributors';
}
function openLocation() {
  $('#loc-results').hidden = true;
  $('#loc-q').value = '';
  renderLocation();
  $('#location').showModal();
  if (!S.prefs.home) $('#loc-q').focus();
}
$('#near-btn').onclick = openLocation;
let locHits = [];
$('#loc-form').addEventListener('submit', async e => {
  e.preventDefault();
  const text = $('#loc-q').value.trim(), out = $('#loc-results');
  if (text.length < 3) return;
  out.hidden = false;
  out.innerHTML = '<li class="sr-empty">Looking up…</li>';
  try { locHits = await api('/api/geocode?q=' + encodeURIComponent(text)); }
  catch { out.innerHTML = '<li class="sr-empty">Address lookup isn’t reachable right now — try again in a minute.</li>'; return; }
  out.innerHTML = locHits.length
    ? locHits.map((h, i) => `<li><button type="button" data-loc="${i}">${bdi(h.label)}</button></li>`).join('')
    : '<li class="sr-empty">No match — try the street, house number and city, e.g. הרצל 10 רמת גן.</li>';
});
$('#loc-results').addEventListener('click', e => {
  const b = e.target.closest('[data-loc]'); if (!b) return;
  const h = locHits[+b.dataset.loc];
  setPrefs({ home: h, max_km: S.prefs.max_km ?? 5 });
  $('#loc-results').hidden = true;
  $('#loc-q').value = '';
  renderLocation();
});
$('#radius').addEventListener('click', e => {
  const b = e.target.closest('[data-km]'); if (!b) return;
  setPrefs({ max_km: b.dataset.km ? +b.dataset.km : null });
  renderLocation();
});
$('#key-form').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    const d = await api('/api/settings', { method: 'PUT', body: { maps_key: $('#maps-key').value } });
    S.traffic = d.traffic;
    $('#maps-key').value = '';
    renderLocation();
    toast(S.traffic ? 'Live traffic is on' : 'Using typical drive times');
    renderResults();
  } catch { toast("Couldn't save the key"); }
});
$('#loc-clear').onclick = () => { setPrefs({ home: null }); renderLocation(); $('#loc-q').focus(); };

// ---------------------------------------------------------------- status / freshness
function renderFreshness() {
  const st = S.status, el = $('#freshness');
  const ix = st.index || {};
  const pct = ix.running && ix.total ? Math.round(100 * ix.processed / ix.total) : 0;
  const updating = ix.running ? ` · updating${pct ? ` ${pct}%` : '…'}` : '';
  const d = tsDate(st.data_ts);
  if (!d) { el.innerHTML = `<span class="dot stale"></span>No prices yet${updating}`; return; }
  const stale = Date.now() - d > 12 * 3600e3;
  el.innerHTML = `<span class="dot${stale ? ' stale' : ''}"></span>Prices from ${fmtDate(d)} · ${st.chains} chains · ${st.stores.toLocaleString()} stores${updating}`;
}

let lastStatusCompare = 0;
async function pollStatus() {
  try {
    const st = await api('/api/status'), prev = S.status;
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
  if (e.target === d || e.target.closest('[data-close]')) d.close();
}));

// delivery fees
const FEES = [['fee', 'Delivery fee'], ['reduced_fee', 'Lower fee'], ['reduced_above', '…for baskets over'],
              ['free_above', 'Free over'], ['min_order', 'Minimum order']];
const RESET = '<button class="ghost small reset" type="button" data-reset>Reset</button>';
const feeVal = v => v == null ? '' : Number.isInteger(v) ? v : Number(v).toFixed(2);
function renderFees(rows) {
  $('#fee-table').innerHTML = `<thead><tr><th>Chain</th>${FEES.map(f => `<th>${f[1]}</th>`).join('')}<th>Delivers</th><th></th></tr></thead><tbody>`
    + rows.map(r => `<tr data-cid="${esc(r.chain_id)}">
        <td>${esc(r.name)}${r.note ? `<small>${esc(r.note)}</small>` : ''}</td>
        ${FEES.map(([k, label]) => `<td><input type="number" min="0" step="0.01" data-f="${k}" value="${feeVal(r[k])}" aria-label="${esc(r.name)} ${label}"></td>`).join('')}
        <td><input type="checkbox" data-f="delivers" ${r.delivers === false ? '' : 'checked'} aria-label="${esc(r.name)} delivers"></td>
        <td>${r.custom ? RESET : ''}</td></tr>`).join('') + '</tbody>';
}
async function saveFees(delivery) {
  try {
    const d = await api('/api/settings', { method: 'PUT', body: { delivery } });
    S.result = null;
    compareNow();
    return d;
  } catch { toast("Couldn't save delivery fees"); }
}
$('#open-settings').onclick = async () => {
  const d = await api('/api/settings').catch(() => null);
  if (!d) return toast("Couldn't load delivery fees");
  renderFees(d.delivery);
  $('#settings').showModal();
};
$('#fee-table').addEventListener('change', e => {
  const tr = e.target.closest('tr[data-cid]'); if (!tr) return;
  const rule = {};
  for (const [k] of FEES) { const v = tr.querySelector(`[data-f="${k}"]`).value; rule[k] = v === '' ? null : +v; }
  rule.delivers = tr.querySelector('[data-f="delivers"]').checked ? null : false;
  tr.lastElementChild.innerHTML = RESET;  // don't re-render: keeps focus while tabbing through fields
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
    body.innerHTML = '<p class="sheet-note">Make another cart to compare.</p>';
    return $('#compare').showModal();
  }
  const pre = new Set([...new Set([S.cart.id, ...carts.map(c => c.id)])].filter(Boolean).slice(0, 2));
  body.innerHTML = `    <div class="cmp-picks">${carts.map(c => `<label><input type="checkbox" value="${c.id}" ${pre.has(c.id) ? 'checked' : ''}><span dir="auto">${esc(c.name)}</span></label>`).join('')}</div>
    <div id="cmp-table" class="table-scroll"></div>`;
  $('#compare').showModal();
  runCompare();
}
$('#compare-body').addEventListener('change', e => {
  if ($$('#compare-body input:checked').length > 4) { e.target.checked = false; return toast('Up to 4 carts'); }
  runCompare();
});
async function runCompare() {
  const ids = $$('#compare-body input:checked').map(i => +i.value), out = $('#cmp-table'), my = ++cmpSeq;
  if (ids.length < 2) { out.innerHTML = '<p class="note">Pick two or more.</p>'; return; }
  out.innerHTML = '<div class="skeleton"></div>';
  let carts, res;
  try {
    carts = await Promise.all(ids.map(id => api(`/api/carts/${id}`)));
    res = await Promise.all(carts.map(c => api('/api/compare', { method: 'POST', body: { items: c.items, prefs: S.prefs } })));
  } catch { out.innerHTML = '<p class="note">Couldn’t price these carts.</p>'; return; }
  if (my !== cmpSeq) return;
  const names = [...new Set(res.flatMap(r => r.chains.map(c => c.name)))];
  const cell = (r, n) => r.chains.find(c => c.name === n);
  // "together": what all the picked baskets cost at one chain; only chains that sell every basket count
  const together = n => res.every(r => cell(r, n)) ? res.reduce((t, r) => t + head(cell(r, n)), 0) : null;
  names.sort((a, b) => (together(a) ?? 1e9) - (together(b) ?? 1e9));
  const bestTogether = names[0] && together(names[0]) != null ? names[0] : null;
  out.innerHTML = `<table class="cmp-table"><thead><tr><th>Chain</th>${carts.map(c => `<th class="n"><bdi>${esc(c.name)}</bdi><br><small>${plural(c.items.length, 'product')}</small></th>`).join('')}
      <th class="n">All together</th></tr></thead>
    <tbody>${names.map(n => `<tr><td>${esc(n)}</td>${res.map(r => {
      const c = cell(r, n);
      if (!c) return '<td class="n">—</td>';
      const miss = view(c).missing.length;
      return `<td class="n${r.chains[0].name === n ? ' best' : ''}">${headText(c)}${miss ? `<br><span class="chip warn">${miss} missing</span>` : ''}</td>`;
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
  b.title = b.ariaLabel = `Theme: ${name} (click to change)`;
}
$('#theme-btn').onclick = () => {
  const order = Object.keys(THEMES);
  theme = order[(order.indexOf(theme) + 1) % order.length];
  local.set('theme', theme);
  applyTheme();
  restyleMap();
};
applyTheme();

// ---------------------------------------------------------------- start
(async function init() {
  const settings = await api('/api/settings').catch(() => null);
  if (settings) { S.prefs = { ...S.prefs, ...settings.prefs }; S.traffic = settings.traffic; }
  applyPrefsUI();
  route();
  api('/api/categories').then(c => { cats = cats || c; renderRail(); }).catch(() => {});
  await loadCarts();
  const carts = S.carts;
  const want = carts.find(c => c.id === +local.get('cart')) || carts[0];
  if (want) await openCart(want.id);
  else { renderBasket(); renderResults(); }
  pollStatus();
})();
