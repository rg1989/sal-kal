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
}
function renderGate(err) {
  const box = $('#gate-buttons');
  if (err !== undefined) box.dataset.err = err || '';
  err = box.dataset.err;
  box.innerHTML = ME.providers.map(p => `<a class="sso ${p}" href="/auth/${p}?lang=${L()}" data-sso="${p}">
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
  el.innerHTML = opts.map(r => `<button role="radio" type="button" data-km="${r ?? ''}" aria-checked="${(value ?? null) === r}">
    ${r ? `<b class="num">${r}</b><small>${t('km')}</small>` : `<b>${t('Any')}</b><small>${t('distance')}</small>`}</button>`).join('');
  el.onclick = e => {
    const b = e.target.closest('[data-km]'); if (!b) return;
    haptic('select');
    el.querySelectorAll('[data-km]').forEach(x => x.setAttribute('aria-checked', x === b));
    onChange(b.dataset.km ? +b.dataset.km : null);
  };
}

// ---------------------------------------------------------------- your location (header pill, "Change distance")
function openLocation() {
  const d = $('#location');
  AddressPicker($('#loc-addr'), {
    home: S.prefs.home,
    onPick: h => {
      const first = !S.prefs.home;
      setPrefs({ home: h, max_km: S.prefs.max_km ?? 5 });
      $('#loc-set').hidden = false;
      if (first) renderRadius();
      toast(t('Address saved'), null, null, 'none');
    },
  });
  const renderRadius = () => radiusPicker($('#radius'), S.prefs.max_km ?? null, km => setPrefs({ max_km: km }));
  $('#loc-set').hidden = !S.prefs.home;
  renderRadius();
  openSheet(d);
}

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
      <li><button type="button" class="acc-row" data-acc="loc"><span class="acc-k">${t('Address')}</span>
        <span class="acc-v">${h ? bdi(h.label) : t('Not set')}</span><span class="acc-go">${SVG.chev}</span></button></li>
      <li><button type="button" class="acc-row" data-acc="loc"><span class="acc-k">${t('Distance')}</span>
        <span class="acc-v">${S.prefs.max_km ? t('{n} km', { n: S.prefs.max_km }) : t('Any distance')}</span><span class="acc-go">${SVG.chev}</span></button></li>
      <li class="acc-row static"><span class="acc-k">${t('Language')}</span>
        <span class="seg small" role="radiogroup" aria-label="${esc(t('Language'))}">
          <button type="button" role="radio" data-lang="he" lang="he" aria-checked="${L() === 'he'}">עברית</button>
          <button type="button" role="radio" data-lang="en" lang="en" aria-checked="${L() === 'en'}">English</button></span></li>
      <li class="acc-row static"><span class="acc-k">${t('Theme')}</span>
        <span class="seg small" role="radiogroup" aria-label="${esc(t('Theme'))}">
          ${Object.keys(THEMES).map(k => `<button type="button" role="radio" data-theme-pick="${k}" aria-checked="${theme === k}">${t(THEMES[k][0])}</button>`).join('')}</span></li>
    </ul>
    ${u?.admin || ME.mode === 'local' ? `<div class="acc-admin">
      <p class="loc-legend">${t('Drive times')}</p>
      <p class="sheet-note">${S.traffic ? t('Drive times: live traffic (Google Maps). Save an empty key for typical times.') : t('Drive times: typical, without traffic. A Google Maps key (Routes API) adds live traffic.')}</p>
      <form class="loc-search" id="key-form"><input id="maps-key" type="password" autocomplete="off" spellcheck="false" placeholder="${esc(t('Google Maps API key (Routes API)'))}" aria-label="${esc(t('Google Maps API key'))}">
        <button class="ghost" type="submit">${t('Save key')}</button></form></div>` : ''}
    ${acc ? `<div class="acc-actions">
      <button type="button" class="ghost wide" data-acc="out">${t('Sign out')}</button>
      <button type="button" class="ghost wide danger" data-acc="delete">${t('Delete my account')}</button></div>` : ''}
    <p class="acc-legal"><a href="/privacy">${t('Privacy')}</a>${acc ? ` · <a href="/data-deletion">${t('Deleting your data')}</a>` : ''}</p>`;
  openSheet($('#account'));
}
$('#account-body').addEventListener('click', async e => {
  const b = e.target.closest('[data-acc], [data-lang], [data-theme-pick]'); if (!b) return;
  if (b.dataset.lang) { setLang(b.dataset.lang); $$('#account-body [data-lang]').forEach(x => x.setAttribute('aria-checked', x === b)); openAccount(); return; }
  if (b.dataset.themePick) { setTheme(b.dataset.themePick); return; }
  if (b.dataset.acc === 'loc') { closeSheet($('#account')); setTimeout(openLocation, 180); return; }
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
  $$('[data-lang-toggle]').forEach(b => {
    b.textContent = L() === 'he' ? 'English' : 'עברית';
    b.lang = L() === 'he' ? 'en' : 'he';
    b.setAttribute('aria-label', t('Language'));
  });
  const lb = $('#lang-btn');
  lb.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M3 10h14M10 3c2 2.2 2.9 4.6 2.9 7S12 14.8 10 17c-2-2.2-2.9-4.6-2.9-7S8 5.2 10 3z" fill="none" stroke="currentColor" stroke-width="1.6"/></svg><span>${L() === 'he' ? 'עב' : 'EN'}</span>`;
  lb.title = lb.ariaLabel = t('Language');
  $$('#lang-menu [data-lang]').forEach(b => b.setAttribute('aria-checked', b.dataset.lang === L()));
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
