'use strict';
// Signing in, the first-run setup (address, distance, what carts are), the address picker, the account sheet and the
// language menu. Loads last: it starts the app (startApp in app.js) once it knows who you are.

const ME = { mode: 'local', providers: [], user: null };
let appStarted = false;
const RADII = [1, 2, 3, 5, 10, 20, null];  // km; null = any distance (the nearest stores, up to 15 km)

const ICON = {
  google: '<svg viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.1H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.6-.4-3.9z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.1H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.6-.4-3.9z"/></svg>',
  facebook: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#fff" d="M15.1 5.3H17V2.1c-.3 0-1.5-.1-2.8-.1-2.8 0-4.7 1.7-4.7 4.9V9.7H6.4v3.6h3.1V22h3.8v-8.7h3l.5-3.6h-3.5V7.2c0-1 .3-1.9 1.8-1.9z"/></svg>',
  dev: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 6 3 10l4 4M13 6l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  gps: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="3" fill="currentColor"/><circle cx="10" cy="10" r="6.3" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10 1.8v2.4M10 15.8v2.4M1.8 10h2.4M15.8 10h2.4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  user: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="7.2" r="3.3" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M3.8 16.6c.9-3 3.3-4.6 6.2-4.6s5.3 1.6 6.2 4.6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  check: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4.5 10.5 3.6 3.6 7.4-8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};
const PROVIDER_NAME = { google: 'Google', facebook: 'Facebook', dev: 'Dev' };

// ---------------------------------------------------------------- boot
async function boot() {
  applyI18n();
  renderLangButtons();
  const err = new URLSearchParams(location.hash.split('?')[1] || '').get('signin');
  if (/^#\/signin/.test(location.hash)) history.replaceState(null, '', location.pathname + '#/');
  let me = null;
  for (let i = 0; i < 3 && !me; i++) {
    me = await api('/api/me', { noAuth: true, quiet: true }).catch(() => null);
    if (!me) await new Promise(r => setTimeout(r, 800 * (i + 1)));
  }
  if (!me) { document.body.classList.remove('booting'); return showGate('offline'); }
  Object.assign(ME, me);
  if (ME.user?.lang && ME.user.lang !== L()) setLang(ME.user.lang, false);  // your account's language, on every device
  renderAccountButton();
  if (ME.mode === 'accounts' && !ME.user) return showGate(err);
  if (ME.user && !ME.user.onboarded) return startOnboarding();
  goShop();
}
function goShop() {
  $('#gate').hidden = $('#onboard').hidden = true;
  document.body.classList.remove('booting', 'gated', 'onboarding');
  if (!appStarted) { appStarted = true; startApp(); }
  else relabel();
}
function signedOut() {
  if (ME.mode !== 'accounts' || $('#gate').hidden === false) return;
  ME.user = null;
  showGate('expired');
}

// ---------------------------------------------------------------- sign in
function showGate(err) {
  document.body.classList.remove('booting', 'onboarding');
  document.body.classList.add('gated');
  closeCart?.();
  $$('dialog[open]').forEach(d => d.close());
  $('#onboard').hidden = true;
  $('#gate').hidden = false;
  enter($('#gate .gate-body'));
  renderGate(err);
  Usage.view('signin', err || undefined);
}
function renderGate(err) {
  const box = $('#gate-buttons');
  if (err !== undefined) box.dataset.err = err || '';
  err = box.dataset.err;
  box.innerHTML = ME.providers.map(p => `<a class="sso ${p}" href="/auth/${p}?lang=${L()}&v=${encodeURIComponent(Usage.ids())}" data-sso="${p}">
      <span class="sso-ico">${ICON[p] || ''}</span><span>${esc(t('Continue with {name}', { name: PROVIDER_NAME[p] || p }))}</span><span class="spin" aria-hidden="true"></span></a>`).join('');
  const msg = { cancelled: t('Sign-in was cancelled.'), failed: t('Couldn’t sign you in. Try again.'),
                expired: t('You were signed out. Sign in again.'), offline: t('Sal-Kal isn’t reachable right now. Try again in a minute.') }[err]
    || (!ME.providers.length ? t('Sign-in opens soon.') : '');
  $('#gate-error').hidden = !msg;
  $('#gate-error').textContent = msg || '';
  $('#gate-error').classList.toggle('soon', !ME.providers.length && !err);
  if (err === 'offline') box.innerHTML = `<button class="primary big" type="button" onclick="location.reload()">${t('Try again')}</button>`;
}
$('#gate-buttons').addEventListener('click', e => {
  const a = e.target.closest('[data-sso]'); if (!a) return;
  haptic('select');
  Usage.ev('signin_click', { a: a.dataset.sso });
  Usage.send(true);
  a.classList.add('busy');
  $$('#gate-buttons .sso').forEach(x => x.setAttribute('aria-disabled', 'true'));
});
window.addEventListener('pageshow', e => { if (e.persisted) $$('#gate-buttons .sso').forEach(x => { x.classList.remove('busy'); x.removeAttribute('aria-disabled'); }); });

// ---------------------------------------------------------------- the address picker
// Town and street come from Israel's official lists (the server's places module), so only real ones can be chosen;
// the house number is a number stepper. "Use my location" asks the phone and names the address it finds.
function AddressPicker(root, { home, onPick, compact }) {
  const st = { town: null, street: null, number: null, home: home || null, seq: 0, noStreets: false };
  if (home?.town) {
    st.town = { code: home.town, name: home.town_name || (home.label.split(', ').at(-1)) };
    if (home.street) st.street = { code: home.street, name: home.street_name || '' };
    st.number = home.number || null;
  }
  const gps = window.isSecureContext && 'geolocation' in navigator;
  root.innerHTML = `
    ${gps ? `<button class="addr-gps" type="button" data-gps>${ICON.gps}<span>${t('Use my current location')}</span><span class="spin" aria-hidden="true"></span></button>
      <p class="addr-or"><span>${t('or choose it')}</span></p>` : ''}
    <div class="addr-fields">
      <div class="combo" data-field="town">
        <label for="${root.id}-town">${t('City or town')}</label>
        <div class="combo-box"><input id="${root.id}-town" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="${root.id}-town-list" autocomplete="off" dir="auto" placeholder="${esc(t('Start typing — e.g. Ramat Gan'))}" enterkeyhint="next">
          <button type="button" class="combo-clear" tabindex="-1" aria-label="${esc(t('Clear'))}">${SVG.x}</button></div>
        <ul class="combo-list" id="${root.id}-town-list" role="listbox" hidden></ul>
      </div>
      <div class="combo" data-field="street">
        <label for="${root.id}-street">${t('Street')}</label>
        <div class="combo-box"><input id="${root.id}-street" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="${root.id}-street-list" autocomplete="off" dir="auto" placeholder="${esc(t('Choose a street'))}" enterkeyhint="next" disabled>
          <button type="button" class="combo-clear" tabindex="-1" aria-label="${esc(t('Clear'))}">${SVG.x}</button></div>
        <ul class="combo-list" id="${root.id}-street-list" role="listbox" hidden></ul>
      </div>
      <div class="num-field">
        <label for="${root.id}-num">${t('Number')}</label>
        <div class="num-step"><button type="button" data-num="-1" aria-label="${esc(t('One less'))}" disabled>−</button>
          <input id="${root.id}-num" inputmode="numeric" pattern="[0-9]*" maxlength="4" autocomplete="off" disabled>
          <button type="button" data-num="1" aria-label="${esc(t('One more'))}" disabled>+</button></div>
      </div>
    </div>
    <div class="addr-result" aria-live="polite"></div>`;
  const $r = s => root.querySelector(s);
  const townIn = $r('[data-field="town"] input'), streetIn = $r('[data-field="street"] input'), numIn = $r('.num-field input');

  function paint() {
    townIn.value = st.town ? st.town.name : townIn === document.activeElement ? townIn.value : '';
    streetIn.disabled = !st.town || st.noStreets;
    streetIn.placeholder = st.noStreets ? t('No street names here') : t('Choose a street');
    streetIn.value = st.street ? st.street.name : streetIn === document.activeElement ? streetIn.value : '';
    const numOn = !!st.street;
    numIn.disabled = !numOn;
    root.querySelectorAll('[data-num]').forEach(b => { b.disabled = !numOn; });
    numIn.value = st.number || '';
    root.querySelectorAll('.combo').forEach(c => c.classList.toggle('chosen', !!(c.dataset.field === 'town' ? st.town : st.street)));
  }
  function result(state, text) {
    const el = $r('.addr-result');
    el.className = `addr-result ${state || ''}`;
    el.innerHTML = state === 'busy' ? `<span class="spin" aria-hidden="true"></span>${t('Finding it on the map…')}`
      : state === 'ok' ? `<span class="loc-pin" aria-hidden="true"></span><bdi>${esc(st.home.label)}</bdi>${st.home.precision === 'street' ? `<small>${t('Placed on the street')}</small>` : st.home.precision === 'town' ? `<small>${t('Placed in the town centre')}</small>` : ''}`
      : state === 'err' ? esc(text) : '';
  }
  // list boxes: type to filter, arrows + Enter, tap to choose; what you type is only a filter, never the value
  function combo(field, input, load, choose) {
    const list = $r(`#${root.id}-${field}-list`);
    let items = [], active = -1, seq = 0;
    const show = async () => {
      const my = ++seq;
      const got = await load(input.value.trim());
      if (my !== seq) return;
      items = got.items;
      active = items.length ? 0 : -1;
      list.innerHTML = items.length ? items.map((x, i) => `<li role="option" id="${root.id}-${field}-o${i}" data-i="${i}" aria-selected="${i === active}"><bdi>${esc(x.name)}</bdi>${x.sub ? `<small>${esc(x.sub)}</small>` : ''}</li>`).join('')
        + (got.more ? `<li class="combo-more" aria-disabled="true">${esc(t('Type to see more'))}</li>` : '')
        : `<li class="combo-none" aria-disabled="true">${esc(input.value.trim() ? t('No match — check the spelling') : t('Start typing'))}</li>`;
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      input.setAttribute('aria-activedescendant', active >= 0 ? `${root.id}-${field}-o${active}` : '');
    };
    const hide = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); };
    const mark = () => {
      list.querySelectorAll('[role="option"]').forEach((li, i) => li.setAttribute('aria-selected', i === active));
      const li = list.querySelector(`[data-i="${active}"]`);
      li?.scrollIntoView({ block: 'nearest' });
      input.setAttribute('aria-activedescendant', li ? li.id : '');
    };
    const debounced = debounce(show, 120);
    input.addEventListener('focus', () => { input.select(); show(); });
    input.addEventListener('input', () => { if (field === 'town') st.town = null; else st.street = null; debounced(); });
    input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (list.hidden) return show();
        active = Math.max(0, Math.min(items.length - 1, active + (e.key === 'ArrowDown' ? 1 : -1)));
        mark();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (items[active]) { choose(items[active]); hide(); }
      } else if (e.key === 'Escape') { hide(); }
    });
    list.addEventListener('pointerdown', e => e.preventDefault());  // keep focus in the input while tapping a row
    list.addEventListener('click', e => {
      const li = e.target.closest('[data-i]'); if (!li) return;
      choose(items[+li.dataset.i]);
      hide();
    });
    input.addEventListener('blur', () => { setTimeout(hide, 120); setTimeout(paint, 130); });  // typed text that wasn't chosen goes away
    input.closest('.combo').querySelector('.combo-clear').addEventListener('click', () => {
      if (field === 'town') { st.town = st.street = null; st.number = null; } else { st.street = null; st.number = null; }
      paint(); input.focus();
    });
    return { show, hide };
  }
  combo('town', townIn, async q => {
    const r = await api('/api/places/towns?q=' + encodeURIComponent(q), { quiet: true }).catch(() => []);
    return { items: r.map(x => ({ ...x, sub: L() === 'en' && x.en ? x.en : '' })) };
  }, async x => {
    haptic('select');
    const changed = !st.town || st.town.code !== x.code;
    st.town = { code: x.code, name: x.name };
    if (changed) { st.street = null; st.number = null; st.home = null; }
    const s = await api(`/api/places/streets?town=${x.code}`, { quiet: true }).catch(() => null);
    st.noStreets = !!s?.no_streets;
    paint();
    if (st.noStreets) locate();
    else if (changed) setTimeout(() => streetIn.focus(), 30);
  });
  combo('street', streetIn, async q => {
    if (!st.town) return { items: [] };
    const r = await api(`/api/places/streets?town=${st.town.code}&q=${encodeURIComponent(q)}`, { quiet: true }).catch(() => ({ streets: [] }));
    return { items: r.streets, more: r.total > r.streets.length };
  }, x => {
    haptic('select');
    st.street = { code: x.code, name: x.name };
    paint();
    locate();
    if (matchMedia('(pointer: fine)').matches) setTimeout(() => numIn.focus(), 30);
  });
  const relocate = debounce(() => locate(), 650);
  root.addEventListener('click', e => {
    const b = e.target.closest('[data-num]');
    if (b) {
      haptic('tap');
      st.number = Math.max(1, Math.min(9999, (st.number || 0) + +b.dataset.num));
      paint();
      relocate();
    }
    if (e.target.closest('[data-gps]')) fromGps(e.target.closest('[data-gps]'));
  });
  numIn.addEventListener('input', () => {
    numIn.value = numIn.value.replace(/\D+/g, '').replace(/^0+/, '').slice(0, 4);
    st.number = numIn.value ? +numIn.value : null;
    relocate();
  });

  async function locate() {
    if (!st.town) return;
    const my = ++st.seq;
    result('busy');
    const qs = new URLSearchParams({ town: st.town.code });
    if (st.street) qs.set('street', st.street.code);
    if (st.street && st.number) qs.set('number', st.number);
    try {
      const h = await api('/api/places/locate?' + qs);
      if (my !== st.seq) return;
      st.home = { ...h, town_name: st.town.name, street_name: st.street?.name || null };
      result('ok');
      haptic('success');
      onPick?.(st.home);
    } catch {
      if (my === st.seq) { result('err', t('Couldn’t find this address on the map just now. Try again in a minute.')); haptic('error'); }
    }
  }
  async function fromGps(btn) {
    btn.classList.add('busy');
    const pos = await new Promise(ok => navigator.geolocation.getCurrentPosition(p => ok(p), () => ok(null),
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 }));
    if (!pos) { btn.classList.remove('busy'); result('err', t('Location isn’t available. Allow it in your browser, or choose the address below.')); haptic('error'); return; }
    try {
      const h = await api(`/api/places/reverse?lat=${pos.coords.latitude}&lon=${pos.coords.longitude}`);
      st.town = h.town ? { code: h.town, name: h.town_name } : null;
      st.street = h.street ? { code: h.street, name: h.street_name } : null;
      st.number = h.number || null;
      st.home = h;
      paint();
      result('ok');
      haptic('success');
      onPick?.(st.home);
    } catch { result('err', t('That location isn’t an address in Israel we can use. Choose it below instead.')); haptic('error'); }
    btn.classList.remove('busy');
  }
  paint();
  if (st.home) result('ok');
  return { get home() { return st.home; } };
}

function radiusPicker(el, value, onChange, withAny = true) {
  const opts = withAny ? RADII : RADII.filter(Boolean);
  el.innerHTML = opts.map(r => `<button role="radio" type="button" data-km="${r ?? ''}" aria-checked="${(value ?? null) === r}" aria-label="${r ? t('{n} km', { n: r }) : t('Any distance')}">
    ${r ? `<b class="num">${r}</b><small>${t('km')}</small>` : `<b class="num">∞</b><small>${t('km')}</small>`}</button>`).join('');
  el.onclick = e => {
    const b = e.target.closest('[data-km]'); if (!b) return;
    haptic('select');
    el.querySelectorAll('[data-km]').forEach(x => x.setAttribute('aria-checked', x === b));
    onChange(b.dataset.km ? +b.dataset.km : null);
  };
}

// ---------------------------------------------------------------- your places (header pill, the where bar, "Add a place")
// Named places (home, work, a parent's, the gym): the stores around one of them, or along the way between two.
const MAX_PLACES = 12;
const LOC = { edit: null, draft: null };  // edit: a place's id, 'new' or null; draft: the address picked in the editor
const ADDR = ['label', 'lat', 'lon', 'town', 'street', 'number', 'town_name', 'street_name', 'precision'];
const addrOnly = h => Object.fromEntries(ADDR.filter(k => h[k] != null).map(k => [k, h[k]]));
const newPlaceId = () => 'p' + Date.now().toString(36).slice(-7) + Math.floor(Math.random() * 1296).toString(36);
const PLUS = '<span class="plus" aria-hidden="true"><svg viewBox="0 0 20 20"><path d="M10 5v10M5 10h10" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg></span>';
function openLocation(focus) {
  LOC.edit = focus === 'add' || !placesOf().length ? 'new' : null;
  LOC.draft = null;
  renderLoc();
  openSheet($('#location'));
  if (LOC.edit) setTimeout(() => $('#loc-body .place-edit')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 250);
}
// the name a new place starts with: the first is home, the next work
const defaultName = () => { const pl = placesOf(); return !pl.length ? t('Home') : pl.length === 1 ? t('Work') : ''; };
function placeEditor(x) {
  return `<div class="place-edit">
    <label class="pl-name"><span>${t('Name')}</span><input id="pl-name" maxlength="40" dir="auto" autocomplete="off" enterkeyhint="done" value="${esc(x ? placeLabel(x) : defaultName())}"></label>
    <div class="addr" id="pl-addr"></div>
    <div class="pl-actions"><button type="button" class="primary" data-save${x ? '' : ' disabled'}>${t('Save place')}</button>
      ${placesOf().length ? `<button type="button" class="ghost" data-cancel>${t('Cancel')}</button>` : ''}</div></div>`;
}
function renderLoc() {
  const el = $('#loc-body'), pl = placesOf(), way = S.prefs.area === 'route';
  const chips = (k, cur) => `<div class="place-chips" role="radiogroup">${pl.map(x =>
    `<button type="button" role="radio" data-pick="${k}" data-id="${esc(x.id)}" aria-checked="${x.id === cur}"><bdi>${esc(placeLabel(x))}</bdi></button>`).join('')}</div>`;
  let pick = '';
  if (pl.length && !way) pick = `<p class="loc-legend">${t('Around')}</p>${chips('at', S.prefs.at)}
      <p class="loc-legend" id="radius-label">${t('Show stores within')}</p><div class="radius-picker" role="radiogroup" aria-labelledby="radius-label" id="radius"></div>`;
  else if (pl.length >= 2) pick = `<p class="loc-legend">${t('From')}</p>${chips('from', S.prefs.from)}
      <p class="loc-legend">${t('To')}</p>${chips('to', S.prefs.to)}
      <p class="loc-legend" id="travel-label">${t('How you go')}</p><div class="seg travel-seg" role="radiogroup" aria-labelledby="travel-label">${
        Object.entries(TRAVEL).map(([k, [label, ico]]) => `<button type="button" role="radio" data-travel="${k}" aria-checked="${(S.prefs.travel || 'car') === k}">${ico}<span>${t(label)}</span></button>`).join('')}</div>
      <p class="loc-legend" id="detour-label">${t('How far off the way')}</p><div class="radius-picker" role="radiogroup" aria-labelledby="detour-label">${
        DETOURS.map(km => `<button role="radio" type="button" data-detour="${km}" aria-checked="${Math.abs(detour() - km) < 1e-9}" aria-label="${km < 1 ? t('{n} m', { n: km * 1000 }) : t('{n} km', { n: km })}">
          <b class="num">${km < 1 ? km * 1000 : km}</b><small>${km < 1 ? t('m') : t('km')}</small></button>`).join('')}</div>`;
  else if (pl.length) pick = `<button type="button" class="pill add wide-add" data-add>${PLUS}${t('Add a second place')}</button>`;
  const rows = pl.map(x => LOC.edit === x.id ? `<li>${placeEditor(x)}</li>` : `<li class="pl-row">
      <span class="pl-dot" aria-hidden="true"></span><span class="pl-text"><b dir="auto">${esc(placeLabel(x))}</b><small><bdi>${esc(x.label)}</bdi></small></span>
      <button type="button" class="pl-btn" data-edit="${esc(x.id)}">${t('Edit')}</button>
      ${pl.length > 1 ? `<button type="button" class="pl-btn pl-del" data-del="${esc(x.id)}" aria-label="${esc(t('Remove {x}', { x: placeLabel(x) }))}">${SVG.trash}</button>` : ''}</li>`).join('');
  el.innerHTML = (pl.length ? `<div class="seg area-seg" role="radiogroup" aria-label="${esc(t('Where to look'))}">
      <button role="radio" type="button" data-area="home" aria-checked="${!way}">${t('Around a place')}</button>
      <button role="radio" type="button" data-area="route" aria-checked="${way}">${t('On the way')}</button></div>
      <div class="loc-pick">${pick}</div>` : '')
    + `<h3 class="loc-h">${t('Your places')}</h3>${rows ? `<ul class="place-list">${rows}</ul>` : ''}`
    + (LOC.edit === 'new' ? placeEditor(null) : pl.length < MAX_PLACES ? `<button type="button" class="pill add" data-add>${PLUS}${t('Add a place')}</button>` : '');
  if (pl.length && !way) radiusPicker($('#radius'), S.prefs.max_km ?? null, km => setPrefs({ max_km: km }));
  const ed = el.querySelector('.place-edit');
  if (ed) {
    const save = ed.querySelector('[data-save]');
    AddressPicker(ed.querySelector('.addr'), { home: LOC.edit === 'new' ? null : placeById(LOC.edit),
                                               onPick: h => { LOC.draft = h; save.disabled = false; } });
    ed.querySelector('#pl-name').addEventListener('keydown', e => { if (e.key === 'Enter' && !save.disabled) { e.preventDefault(); savePlace(); } });
  }
}
function savePlace() {
  const name = ($('#pl-name')?.value || '').trim().slice(0, 40) || null, pl = placesOf().map(x => ({ ...x }));
  if (LOC.edit === 'new') {
    if (!LOC.draft) return;
    const id = !pl.length ? 'home' : newPlaceId(), patch = { places: [...pl, { id, name, ...addrOnly(LOC.draft) }] };
    if (!pl.length) Object.assign(patch, { at: id, max_km: S.prefs.max_km ?? 5 });
    else if (pl.length === 1) patch.to = id;  // a second place: the other end of the way
    setPrefs(patch);
  } else {
    const i = pl.findIndex(x => x.id === LOC.edit);
    if (i < 0) return;
    pl[i] = { ...pl[i], name, ...(LOC.draft ? addrOnly(LOC.draft) : {}) };
    setPrefs({ places: pl });
  }
  LOC.edit = LOC.draft = null;
  renderLoc();
  toast(t('Place saved'), null, null, 'none');
}
$('#loc-body').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b || b.disabled) return;
  const d = b.dataset;
  if (d.area) { if ((d.area === 'route') !== (S.prefs.area === 'route')) { haptic('select'); setPrefs({ area: d.area }); renderLoc(); } return; }
  if (d.pick) { haptic('select'); pickPlace(d.pick, d.id); renderLoc(); return; }
  if (d.travel) { if (d.travel !== S.prefs.travel) { haptic('select'); setPrefs({ travel: d.travel, detour_km: null }); renderLoc(); } return; }
  if (d.detour) { haptic('select'); setPrefs({ detour_km: +d.detour }); renderLoc(); return; }
  if ('add' in d) { haptic('tap'); LOC.edit = 'new'; LOC.draft = null; renderLoc(); setTimeout(() => $('#loc-body .place-edit')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 30); return; }
  if (d.edit) { haptic('tap'); LOC.edit = d.edit; LOC.draft = null; renderLoc(); return; }
  if ('cancel' in d) { LOC.edit = LOC.draft = null; renderLoc(); return; }
  if ('save' in d) { haptic('success'); savePlace(); return; }
  if (d.del) {
    const before = placesOf().map(x => ({ ...x })), gone = before.find(x => x.id === d.del);
    haptic('warn');
    setPrefs({ places: before.filter(x => x.id !== d.del) });
    renderLoc();
    toast(t('Removed {name}', { name: iso(placeLabel(gone)) }), t('Undo'), () => { setPrefs({ places: before }); renderLoc(); });
  }
});

// ---------------------------------------------------------------- first sign-in: three short steps
const OB = { step: 0, home: null, km: 5, map: null, picker: null };
function startOnboarding() {
  document.body.classList.remove('booting', 'gated');
  document.body.classList.add('onboarding');
  $('#gate').hidden = true;
  $('#onboard').hidden = false;
  OB.home = ME.prefs?.home || null;
  OB.km = ME.prefs?.max_km || 5;
  OB.picker = AddressPicker($('#ob-addr'), { home: OB.home, onPick: h => { OB.home = h; obButtons(); } });
  $('#ob-cart').value = t('Weekly shop');
  obShow(0, 0);
}
function obShow(step, dir = 1) {
  const prev = OB.step;
  OB.step = step;
  $$('#ob-steps .ob-step').forEach(s => {
    const n = +s.dataset.step;
    s.hidden = n !== step;
    s.classList.remove('from-next', 'from-prev');
    if (n === step && dir && prev !== step) { void s.offsetWidth; s.classList.add(dir > 0 ? 'from-next' : 'from-prev'); }
  });
  $$('#ob-dots li').forEach((li, i) => { li.classList.toggle('on', i === step); li.classList.toggle('done', i < step); });
  $('#ob-back').style.visibility = step ? 'visible' : 'hidden';
  if (prev !== step || !OB.seen) { OB.seen = true; Usage.view('onboard', String(step + 1)); Usage.ev('onboard', { a: String(step + 1) }); }
  if (step === 1) obRadius();
  if (step === 2) $('#ob-compare-text').textContent = t('See where each cart costs least within {n} km of {place}.', { n: OB.km, place: OB.home.label.split(',')[0] });
  obButtons();
  $('#onboard').scrollTop = 0;
}
function obButtons() {
  const b = $('#ob-next');
  b.textContent = OB.step === 2 ? t('Start shopping') : t('Continue');
  b.disabled = OB.step === 0 && !OB.home;
}
async function obRadius() {
  radiusPicker($('#ob-radius'), OB.km, km => { OB.km = km; saveObPrefs().then(obMapData); }, false);
  await saveObPrefs();
  const canvas = $('#ob-map-canvas');
  try {
    await loadMapLib();
    if (!OB.map) {
      if (maplibregl.getRTLTextPluginStatus() === 'unavailable') maplibregl.setRTLTextPlugin('/static/vendor/mapbox-gl-rtl-text.js', false);
      OB.map = new maplibregl.Map({ container: canvas, style: await mapStyle(), center: [OB.home.lon, OB.home.lat], zoom: 12,
        attributionControl: { compact: true }, interactive: false });
      OB.map.once('load', () => canvas.querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show'));  // the credit folds into its (i)
      OB.map.on('style.load', () => {
        const C = mapColors(), empty = { type: 'FeatureCollection', features: [] };
        OB.map.addSource('r', { type: 'geojson', data: empty });
        OB.map.addLayer({ id: 'rf', type: 'fill', source: 'r', paint: { 'fill-color': C.dot, 'fill-opacity': .08 } });
        OB.map.addLayer({ id: 'rl', type: 'line', source: 'r', paint: { 'line-color': C.dot, 'line-width': 1.6, 'line-dasharray': [2, 2] } });
        OB.map.addSource('s', { type: 'geojson', data: empty });
        OB.map.addLayer({ id: 's', type: 'circle', source: 's', paint: { 'circle-radius': 4, 'circle-color': C.dot, 'circle-stroke-color': C.bg, 'circle-stroke-width': 1.5 } });
        obMapData();
      });
      const pin = document.createElement('div');
      pin.className = 'home-pin';
      pin.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3.5 9.2 10 4l6.5 5.2M5.5 8v7.5h9V8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      OB.pin = new maplibregl.Marker({ element: pin }).setLngLat([OB.home.lon, OB.home.lat]).addTo(OB.map);
    } else { OB.map.resize(); obMapData(); }
  } catch { canvas.closest('.ob-map').classList.add('nomap'); obMapData(); }
}
function saveObPrefs() {
  return api('/api/settings', { method: 'PUT', body: { prefs: { home: OB.home, max_km: OB.km } }, quiet: true }).catch(() => null);
}
let obSeq = 0;
async function obMapData() {
  const my = ++obSeq;
  $('#ob-count').innerHTML = `<span class="spin" aria-hidden="true"></span>`;
  const d = await api('/api/stores/near', { quiet: true }).catch(() => null);
  if (my !== obSeq || !d) return;
  const chains = new Set(d.stores.map(x => x.chain)).size;
  $('#ob-count').innerHTML = d.total ? t('{branches} of {chains} {where}', { branches: `<strong>${plural(d.total, 'branch')}</strong>`, chains: `<strong>${plural(chains, 'chain')}</strong>`, where: t('within {n} km', { n: OB.km }) })
    : t('No stores within {n} km', { n: OB.km });
  if (!OB.map?.getSource('s')) return;
  OB.pin.setLngLat([OB.home.lon, OB.home.lat]);
  OB.map.getSource('r').setData(circle(OB.home.lat, OB.home.lon, OB.km));
  OB.map.getSource('s').setData({ type: 'FeatureCollection', features: d.stores.map(x => ({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [x.lon, x.lat] } })) });
  const dx = OB.km / (111.32 * Math.cos(OB.home.lat * Math.PI / 180)), dy = OB.km / 110.574;
  OB.map.fitBounds([[OB.home.lon - dx, OB.home.lat - dy], [OB.home.lon + dx, OB.home.lat + dy]], { padding: 18, duration: reduced() ? 0 : 700 });
}
$('#ob-next').addEventListener('click', async () => {
  if (OB.step < 2) { haptic('select'); return obShow(OB.step + 1, 1); }
  const b = $('#ob-next');
  b.disabled = true;
  b.classList.add('busy');
  const name = $('#ob-cart').value.trim() || t('Weekly shop');
  try {
    await saveObPrefs();
    const carts = await api('/api/carts');
    if (!carts.length) {
      const c = await api('/api/carts', { method: 'POST', body: { name } });
      local.set('cart', c.id);
    }
    const r = await api('/api/me', { method: 'PUT', body: { onboarded: true, lang: L() } });
    ME.user = r.user;
    haptic('success');
    Usage.ev('onboard', { a: 'done' });
    goShop();
  } catch { toast(t('Couldn’t save — try again'), null, null, 'error'); }
  b.disabled = false;
  b.classList.remove('busy');
});
$('#ob-back').addEventListener('click', () => { haptic('tap'); if (OB.step) obShow(OB.step - 1, -1); });
$('#ob-cart').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('#ob-next').click(); } });

// ---------------------------------------------------------------- the account sheet
function initials(name) {
  return (name || '?').trim().split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase();
}
function avatarHTML(u, cls = '') {
  return `<span class="avatar ${cls}" aria-hidden="true">${u?.avatar ? `<img src="${esc(u.avatar)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : ''}<span>${esc(u ? initials(u.name || u.email) : '')}</span></span>`;
}
function renderAccountButton() {
  const b = $('#account-btn');
  b.innerHTML = ME.user && ME.mode === 'accounts' ? avatarHTML(ME.user) : `<span class="avatar plain">${ICON.user}</span>`;
  b.title = ME.user?.name || t('Settings');
}
$('#account-btn').addEventListener('click', openAccount);
function openAccount() {
  const u = ME.user, acc = ME.mode === 'accounts' && u, h = S.prefs.home;
  $('#account h2').textContent = acc ? t('Account') : t('Settings');
  $('#account-body').innerHTML = `
    ${acc ? `<div class="acc-who">${avatarHTML(u, 'big')}<div><p class="acc-name" dir="auto">${esc(u.name || '')}</p>
      <p class="acc-mail">${esc(u.email || '')}${u.providers?.length ? ` · ${esc(u.providers.map(p => PROVIDER_NAME[p] || p).join(', '))}` : ''}</p></div></div>` : ''}
    <ul class="acc-list">
      <li><button type="button" class="acc-row" data-acc="loc"><span class="acc-k">${t('Your places')}</span>
        <span class="acc-v">${h ? placesOf().map(x => bdi(placeLabel(x))).join(', ') : t('Not set')}</span><span class="acc-go">${SVG.chev}</span></button></li>
      <li><button type="button" class="acc-row" data-acc="loc"><span class="acc-k">${onWay() ? t('On the way') : t('Around a place')}</span>
        <span class="acc-v">${onWay() ? `${bdi(placeLabel(placeById(S.prefs.from)))} ${wayArrow()} ${bdi(placeLabel(placeById(S.prefs.to)))} · ${t(TRAVEL[S.prefs.travel || 'car'][0])}`
          : `${h ? bdi(placeLabel(placeById(S.prefs.at))) + ' · ' : ''}${S.prefs.max_km ? t('{n} km', { n: S.prefs.max_km }) : t('Any distance')}`}</span><span class="acc-go">${SVG.chev}</span></button></li>
      <li class="acc-row static"><span class="acc-k">${t('Language')}</span>
        <span class="seg small" role="radiogroup" aria-label="${esc(t('Language'))}">
          <button type="button" role="radio" data-lang="he" lang="he" aria-checked="${L() === 'he'}">${FLAG.he}עברית</button>
          <button type="button" role="radio" data-lang="en" lang="en" aria-checked="${L() === 'en'}">${FLAG.en}English</button></span></li>
      <li class="acc-row static"><span class="acc-k">${t('Theme')}</span>
        <span class="seg small" role="radiogroup" aria-label="${esc(t('Theme'))}">
          ${Object.keys(THEMES).map(k => `<button type="button" role="radio" data-theme-pick="${k}" aria-checked="${theme === k}">${t(THEMES[k][0])}</button>`).join('')}</span></li>
      ${window.PWA?.offer() ? `<li><button type="button" class="acc-row" data-acc="install"><span class="acc-k">${t('Install app')}</span>
        <span class="acc-v"></span><span class="acc-go">${SVG.chev}</span></button></li>` : ''}
    </ul>
    ${u?.admin || ME.mode === 'local' ? `<div class="acc-admin">
      <p class="loc-legend">${t('Drive times')}</p>
      <p class="sheet-note">${S.traffic ? t('Drive times: live traffic (Google Maps). Save an empty key for typical times.') : t('Drive times: typical, without traffic. A Google Maps key (Routes API) adds live traffic.')}</p>
      <form class="loc-search" id="key-form"><input id="maps-key" type="password" autocomplete="off" spellcheck="false" placeholder="${esc(t('Google Maps API key (Routes API)'))}" aria-label="${esc(t('Google Maps API key'))}">
        <button class="ghost" type="submit">${t('Save key')}</button></form></div>` : ''}
    ${acc ? `<div class="acc-actions">
      <button type="button" class="ghost wide" data-acc="out">${t('Sign out')}</button>
      <button type="button" class="ghost wide danger" data-acc="delete">${t('Delete my account')}</button></div>` : ''}
    <p class="acc-legal"><a href="/about">${t('About')}</a> · <a href="/privacy">${t('Privacy')}</a>${acc ? ` · <a href="/data-deletion">${t('Deleting your data')}</a>` : ''}</p>
    <p class="acc-credit">${t('Built by')} <a href="https://www.linkedin.com/in/roman-grinevich/" target="_blank" rel="noopener">Roman Grinevich</a> · <a href="https://github.com/rg1989/sal-kal" target="_blank" rel="noopener">GitHub</a></p>`;
  openSheet($('#account'));
}
$('#account-body').addEventListener('click', async e => {
  const b = e.target.closest('[data-acc], [data-lang], [data-theme-pick]'); if (!b) return;
  if (b.dataset.lang) { setLang(b.dataset.lang); $$('#account-body [data-lang]').forEach(x => x.setAttribute('aria-checked', x === b)); openAccount(); return; }
  if (b.dataset.themePick) { setTheme(b.dataset.themePick); return; }
  if (b.dataset.acc === 'loc') { closeSheet($('#account')); setTimeout(openLocation, 180); return; }
  if (b.dataset.acc === 'install') { closeSheet($('#account')); PWA.install(); return; }
  if (b.dataset.acc === 'out') {
    haptic('select');
    await api('/api/logout', { method: 'POST', noAuth: true }).catch(() => null);
    location.replace('/');
    return;
  }
  if (b.dataset.acc === 'delete') {
    closeSheet($('#account'));
    const yes = await confirmSheet(t('Delete your account? Your carts, address and settings are erased for good.'), t('Delete my account'));
    if (!yes) return;
    try {
      await api('/api/me', { method: 'DELETE' });
      Usage.reset();  // a fresh browser id: nothing sent from here joins the erased account's statistics
      haptic('success');
      local.set('cart', '');
      location.replace('/');
    } catch { toast(t('Couldn’t delete the account — try again'), null, null, 'error'); }
  }
});
$('#account-body').addEventListener('submit', async e => {
  if (e.target.id !== 'key-form') return;
  e.preventDefault();
  try {
    const d = await api('/api/settings', { method: 'PUT', body: { maps_key: $('#maps-key').value } });
    S.traffic = d.traffic;
    toast(S.traffic ? t('Live traffic is on') : t('Using typical drive times'), null, null, 'success');
    openAccount();
    renderResults();
  } catch { toast(t("Couldn't save the key"), null, null, 'error'); }
});

// ---------------------------------------------------------------- language
function renderLangButtons() {
  const other = L() === 'he' ? 'en' : 'he';
  $$('[data-lang-toggle]').forEach(b => {
    b.innerHTML = `${FLAG[other]}<span>${LANG_NAME[other]}</span>`;
    b.lang = other;
    b.setAttribute('aria-label', t('Language'));
  });
  const lb = $('#lang-btn');
  lb.innerHTML = `${FLAG[L()]}<span>${L() === 'he' ? 'עב' : 'EN'}</span>`;
  lb.title = lb.ariaLabel = t('Language');
  $$('#lang-menu [data-lang]').forEach(b => {
    b.innerHTML = `${FLAG[b.dataset.lang]}<span>${LANG_NAME[b.dataset.lang]}</span>`;
    b.setAttribute('aria-checked', b.dataset.lang === L());
  });
}
function setLang(l, save = true) {
  if (l === L() || !['he', 'en'].includes(l)) return;
  switchLang(l);  // i18n.js: dictionary, <html lang dir>, data-i18n labels
  if (save) haptic('select');
  renderLangButtons();
  renderAccountButton();
  if (!$('#gate').hidden) renderGate();
  if (!$('#onboard').hidden) { const keep = OB.home; OB.picker = AddressPicker($('#ob-addr'), { home: keep, onPick: h => { OB.home = h; obButtons(); } }); obShow(OB.step, 0); if (OB.map) restyleOb(); }
  if (appStarted) relabel();
  if (save && ME.user) api('/api/me', { method: 'PUT', body: { lang: l }, quiet: true }).catch(() => {});
}
async function restyleOb() { OB.map.setStyle(await mapStyle(), { diff: false }); }
$$('[data-lang-toggle]').forEach(b => b.addEventListener('click', () => setLang(L() === 'he' ? 'en' : 'he')));
$('#lang-btn').addEventListener('click', e => {
  e.stopPropagation();
  const m = $('#lang-menu'), b = $('#lang-btn');
  if (!m.hidden) { m.hidden = true; b.setAttribute('aria-expanded', 'false'); return; }
  const r = b.getBoundingClientRect();
  m.style.top = `${r.bottom + 6}px`;
  if (rtl()) { m.style.left = `${Math.max(8, r.left)}px`; m.style.right = ''; }
  else { m.style.right = `${Math.max(8, innerWidth - r.right)}px`; m.style.left = ''; }
  m.hidden = false;
  b.setAttribute('aria-expanded', 'true');
  haptic('tap');
  m.querySelector(`[data-lang="${L()}"]`).focus();
});
$('#lang-menu').addEventListener('click', e => {
  const b = e.target.closest('[data-lang]'); if (!b) return;
  $('#lang-menu').hidden = true;
  $('#lang-btn').setAttribute('aria-expanded', 'false');
  setLang(b.dataset.lang);
});
document.addEventListener('click', e => { if (!e.target.closest('#lang-menu')) { $('#lang-menu').hidden = true; $('#lang-btn').setAttribute('aria-expanded', 'false'); } });
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#lang-menu').hidden = true; });

// the phone cart sheet closes when pulled down by its top
dragToClose($('#cart'), closeCart);

boot();
