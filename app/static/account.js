'use strict';
// Signing in, the first-run setup (address, distance, what carts are), the address picker, the account sheet and the
// language menu. Loads last: it starts the app (startApp in app.js) once it knows who you are.

const ME = { mode: 'local', providers: [], user: null, turnstile: null };
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
  if (ME.user?.lang && ME.user.lang !== L()) {
    // a language picked in this browser (here, on the landing page or the Price Board: site.js) wins and the account
    // follows it; a browser where none was picked takes the account's, so a new device opens in your language
    let picked = false;
    try { picked = !!localStorage.getItem('lang_pick'); } catch { /* private mode */ }
    if (picked) { ME.user.lang = L(); api('/api/me', { method: 'PUT', body: { lang: L() }, quiet: true }).catch(() => {}); }
    else setLang(ME.user.lang, false);
  }
  renderAccountButton();
  if (ME.mode === 'accounts' && !ME.user) return showGate(err);
  if (ME.user && backToPrices()) return;
  if (ME.user && !ME.user.onboarded) return startOnboarding();
  goShop();
}
// signed in from Price Watch (/prices, prices.js left 'pw_return' within the last half hour): back there, with what was
// typed in its search; once, and the setup waits for the next visit to the app
function backToPrices() {
  let r = null;
  try { r = JSON.parse(localStorage.getItem('pw_return') || 'null'); localStorage.removeItem('pw_return'); } catch { return false; }
  if (!r || !(Date.now() - r.t < 30 * 60e3)) return false;
  try { localStorage.setItem('pw_member', '1'); } catch { /* private mode */ }
  const h = typeof r.h === 'string' && /^#q=[^#]{0,1300}$/.test(r.h) ? r.h : '';
  location.replace(`/prices${h}`);
  return true;
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
                bot: t('We couldn’t confirm you’re a person. Try again.'), busy: t('Too many sign-ins right now. Try again later.'),
                expired: t('You were signed out. Sign in again.'), offline: t('Sal-Kal isn’t reachable right now. Try again in a minute.') }[err]
    || (!ME.providers.length ? t('Sign-in opens soon.') : '');
  $('#gate-error').hidden = !msg;
  $('#gate-error').textContent = msg || '';
  $('#gate-error').classList.toggle('soon', !ME.providers.length && !err);
  if (err === 'offline') box.innerHTML = `<button class="primary big" type="button" onclick="location.reload()">${t('Try again')}</button>`;
  else if (ME.turnstile && ME.providers.length) BotCheck.start();
}

// ---------------------------------------------------------------- the bot check (Cloudflare Turnstile)
// Runs on the sign-in page while you read it: invisible for most people, a checkbox when Cloudflare isn't sure. Its
// one-time token goes with the sign-in (POST /auth/<provider>); the server checks it with Cloudflare (app.botcheck).
const BotCheck = (() => {
  let widget = null, token = null, waiting = [], loading = null;
  const load = () => loading ||= new Promise((ok, fail) => {
    const s = document.createElement('script');
    s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    s.onload = () => ok(window.turnstile);
    s.onerror = () => { loading = null; fail(new Error('turnstile')); };
    document.head.append(s);
  });
  const give = v => { token = v; waiting.splice(0).forEach(w => w(v)); };
  async function start() {
    const box = $('#gate-check');
    if (!box) return;
    let ts;
    try { ts = await load(); } catch { return; }
    if (widget !== null) return;
    token = null;
    const theme = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    widget = ts.render(box, {
      sitekey: ME.turnstile, action: 'signin', appearance: 'interaction-only', theme, language: L(),
      'refresh-expired': 'auto', 'response-field': false,
      callback: give, 'expired-callback': () => { token = null; }, 'error-callback': () => { token = null; },
      'before-interactive-callback': () => box.classList.add('asking'),  // Cloudflare shows its checkbox
      'after-interactive-callback': () => box.classList.remove('asking'),
    });
  }
  // the token, as soon as Cloudflare gives it (null after a minute: the check couldn't finish)
  const get = () => token ? Promise.resolve(token) : new Promise(ok => {
    waiting.push(ok);
    setTimeout(() => { waiting = waiting.filter(w => w !== ok); ok(null); }, 60000);
  });
  // a token is good once: a new check for the next try
  const again = () => {
    token = null;
    if (widget !== null) try { window.turnstile.reset(widget); } catch { widget = null; }
  };
  return { start, get, again };
})();
const ssoReady = () => $$('#gate-buttons .sso').forEach(x => { x.classList.remove('busy'); x.removeAttribute('aria-disabled'); });
$('#gate-buttons').addEventListener('click', async e => {
  const a = e.target.closest('[data-sso]'); if (!a) return;
  if (ME.turnstile || a.getAttribute('aria-disabled') === 'true') e.preventDefault();
  if (a.getAttribute('aria-disabled') === 'true') return;
  haptic('select');
  Usage.ev('signin_click', { a: a.dataset.sso });
  Usage.send(true);
  a.classList.add('busy');
  $$('#gate-buttons .sso').forEach(x => x.setAttribute('aria-disabled', 'true'));
  if (!ME.turnstile) return;  // a plain link: the browser follows it
  const token = await BotCheck.get();
  let r = null;
  if (token) {
    r = await fetch(`/auth/${a.dataset.sso}`, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Sal-V': Usage.ids() },
      body: JSON.stringify({ token, lang: L(), v: Usage.ids() }),
    }).catch(() => null);
  }
  const out = r?.ok ? await r.json().catch(() => null) : null;
  if (out?.url) return location.assign(out.url);
  BotCheck.again();
  ssoReady();
  gateMessage(token && !r ? t('Couldn’t sign you in. Try again.') : r?.status === 429
    ? t('Too many sign-ins right now. Try again later.') : t('We couldn’t confirm you’re a person. Try again.'));
});
function gateMessage(msg) {
  const el = $('#gate-error');
  el.hidden = false;
  el.classList.remove('soon');
  el.textContent = msg;
}
window.addEventListener('pageshow', e => { if (e.persisted) { ssoReady(); BotCheck.again(); } });

// ---------------------------------------------------------------- the address picker
// Town and street come from Israel's official lists (the server's places module), so only real ones can be chosen;
// "Use my location" asks the phone and names the address it finds. Nothing typed is ever thrown away: a name that
// is the only fit (or exactly a name on the list) is taken as chosen, otherwise the field says to pick from the list.
// The town alone is already an address (its centre); street and number only make it exact.
function AddressPicker(root, { home, onPick }) {
  const st = { town: null, street: null, number: null, home: home || null, seq: 0, noStreets: false, pending: null };
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
        <div class="combo-box"><input id="${root.id}-town" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="${root.id}-town-list" aria-describedby="${root.id}-town-need" autocomplete="off" dir="auto" placeholder="${esc(t('Start typing — e.g. Ramat Gan'))}" enterkeyhint="next">
          <button type="button" class="combo-clear" tabindex="-1" aria-label="${esc(t('Clear'))}">${SVG.x}</button></div>
        <p class="combo-need" id="${root.id}-town-need" hidden></p>
        <ul class="combo-list" id="${root.id}-town-list" role="listbox" hidden></ul>
      </div>
      <div class="combo" data-field="street">
        <label for="${root.id}-street">${t('Street')} <small>${t('optional')}</small></label>
        <div class="combo-box"><input id="${root.id}-street" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="${root.id}-street-list" aria-describedby="${root.id}-street-need" autocomplete="off" dir="auto" placeholder="${esc(t('Choose a street'))}" enterkeyhint="next" disabled>
          <button type="button" class="combo-clear" tabindex="-1" aria-label="${esc(t('Clear'))}">${SVG.x}</button></div>
        <p class="combo-need" id="${root.id}-street-need" hidden></p>
        <ul class="combo-list" id="${root.id}-street-list" role="listbox" hidden></ul>
      </div>
      <div class="num-field">
        <label for="${root.id}-num">${t('Number')}</label>
        <input id="${root.id}-num" class="num-in" inputmode="numeric" pattern="[0-9]*" maxlength="4" autocomplete="off" enterkeyhint="done" disabled>
      </div>
    </div>
    <div class="addr-result" aria-live="polite"></div>
    <div class="addr-map" hidden><div class="addr-map-canvas"></div><span class="addr-map-pin" aria-hidden="true"></span>
      <p class="addr-map-hint">${t('Drag the map so the pin is on your building')}</p></div>`;
  const $r = s => root.querySelector(s);
  const townIn = $r('[data-field="town"] input'), streetIn = $r('[data-field="street"] input'), numIn = $r('.num-in');
  const combos = {};

  // chosen values are written into the fields; text typed and not (yet) chosen stays as typed
  function paint() {
    if (st.town) townIn.value = st.town.name;
    streetIn.disabled = numIn.disabled = st.noStreets;
    streetIn.placeholder = st.noStreets ? t('No street names here') : t('Choose a street');
    if (st.street) streetIn.value = st.street.name;
    else if (st.noStreets) streetIn.value = '';
    numIn.value = st.noStreets ? '' : st.number || '';
    root.querySelectorAll('.combo').forEach(c => c.classList.toggle('chosen', !!(c.dataset.field === 'town' ? st.town : st.street)));
  }
  function need(field, text) {
    const c = $r(`[data-field="${field}"]`), p = c.querySelector('.combo-need');
    c.classList.toggle('need', !!text);
    p.hidden = !text;
    p.textContent = text || '';
  }
  function result(state, text) {
    const el = $r('.addr-result');
    el.className = `addr-result ${state || ''}`;
    el.innerHTML = state === 'busy' ? `<span class="spin" aria-hidden="true"></span>${t('Finding it on the map…')}`
      : state === 'ok' ? `<span class="loc-pin" aria-hidden="true"></span><bdi>${esc(st.home.label)}</bdi>${text ? `<small>${esc(text)}</small>` : pinNote(st.home)}`
      : state === 'err' ? esc(text) : '';
    pinMap(state === 'ok' ? st.home : null);
  }
  // No exact house found (neither the national address points nor OpenStreetMap have it): a small map with the pin in
  // the middle; moving the map puts the pin on the building. Optional: the address works as it is.
  const PM = { map: null, for: null, touched: false };
  async function pinMap(h) {
    const box = $r('.addr-map');
    if (!h || ['house', 'gps'].includes(h.precision)) { box.hidden = true; PM.for = null; return; }
    const key = `${h.town}:${h.street || ''}:${h.number || ''}`;
    box.hidden = false;
    if (PM.for === key && PM.map) return;  // the same address (a pin just moved): the map stays as the person left it
    PM.for = key;
    const zoom = h.precision === 'town' ? 14.5 : 17.5;
    try {
      await loadMapLib();
      if (PM.for !== key) return;
      if (maplibregl.getRTLTextPluginStatus() === 'unavailable') maplibregl.setRTLTextPlugin('/static/vendor/mapbox-gl-rtl-text.js', false);
      if (!PM.map) {
        mapBase = mapBase || await fetch('https://tiles.openfreemap.org/styles/positron').then(r => r.json());
        // the plain map, house numbers and street names in the page's language: for finding your own building
        const style = structuredClone(mapBase), name = L() === 'he' ? ['coalesce', ['get', 'name:he'], ['get', 'name']]
          : ['coalesce', ['get', 'name:en'], ['get', 'name:latin'], ['get', 'name']];
        for (const l of style.layers) if (l.type === 'symbol' && /name/.test(JSON.stringify(l.layout?.['text-field'] || ''))) l.layout['text-field'] = name;
        PM.map = new maplibregl.Map({ container: $r('.addr-map-canvas'), style, center: [h.lon, h.lat], zoom,
          attributionControl: { compact: true }, dragRotate: false, pitchWithRotate: false, touchPitch: false });
        PM.map.once('load', () => $r('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show'));  // the credit folds into its (i)
        PM.map.touchZoomRotate.disableRotation();
        PM.map.on('dragstart', () => { PM.touched = true; });
        PM.map.on('zoomstart', e => { if (e.originalEvent) PM.touched = true; });
        PM.map.on('moveend', () => {
          if (!PM.touched || !st.home) return;
          PM.touched = false;
          const c = PM.map.getCenter();
          st.home = { ...st.home, lat: c.lat, lon: c.lng, precision: 'pin', source: 'pin' };
          delete st.home.near;
          const note = $r('.addr-result small');
          if (note) note.textContent = t('Pin placed by you'); else $r('.addr-result')?.insertAdjacentHTML('beforeend', `<small>${t('Pin placed by you')}</small>`);
          haptic('select');
          Usage.ev('addr', { a: 'pin' });
          onPick?.(st.home);
        });
      } else {
        PM.map.resize();
        PM.map.jumpTo({ center: [h.lon, h.lat], zoom });
      }
    } catch { box.hidden = true; }
  }
  // list boxes: type to filter, arrows + Enter, tap to choose
  function combo(field, input, load, choose) {
    const list = $r(`#${root.id}-${field}-list`);
    let items = [], active = -1, seq = 0, shown = '';
    const fetch = async () => {
      const my = ++seq, q = input.value.trim();
      const got = await load(q);
      return my === seq ? { ...got, q } : null;
    };
    const show = async () => {
      const got = await fetch();
      if (!got || document.activeElement !== input) return;
      items = got.items;
      shown = got.q;
      active = items.length ? 0 : -1;
      list.innerHTML = items.length ? items.map((x, i) => `<li role="option" id="${root.id}-${field}-o${i}" data-i="${i}" aria-selected="${i === active}"><bdi>${esc(x.name)}</bdi>${x.sub ? `<small>${esc(x.sub)}</small>` : ''}</li>`).join('')
        + (got.more ? `<li class="combo-more" aria-disabled="true">${esc(t('Type to see more'))}</li>` : '')
        : `<li class="combo-none" aria-disabled="true">${esc(got.none || (got.q ? t('No match — check the spelling') : t('Start typing')))}</li>`;
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
    const pick = x => { need(field, null); hide(); choose(x); };
    // left without choosing: the only fit, or the name typed in full, counts as chosen; otherwise say so
    const settle = async () => {
      const q = input.value.trim(), chosen = field === 'town' ? st.town : st.street;
      if (chosen || !q) { if (!q && field === 'street') need(field, null); return chosen ? 'picked' : 'empty'; }
      const got = q === shown ? { items, q } : await fetch();
      if (!got || input.value.trim() !== q || (field === 'town' ? st.town : st.street)) return 'picked';
      const same = got.items.filter(x => normName(x.name) === normName(q));
      const x = same.length === 1 ? same[0] : got.items.length === 1 ? got.items[0] : null;
      if (x) { Usage.ev('addr', { a: 'auto', b: field }); pick(x); return 'picked'; }
      const how = got.items.length ? 'unpicked' : 'nomatch';
      Usage.ev('addr', { a: how, b: field });
      need(field, got.items.length ? (field === 'town' ? t('Tap your town in the list') : t('Tap the street in the list'))
        : t('No match — check the spelling'));
      return how;
    };
    const debounced = debounce(show, 120);
    input.addEventListener('focus', async () => {
      input.select();
      if (field === 'street' && !st.town && townIn.value.trim()) await combos.town.settle();  // the town typed in full counts
      show();
    });
    input.addEventListener('input', () => {
      need(field, null);
      if (field === 'town') { if (st.town) { st.town = null; st.street = null; st.number = null; streetIn.value = ''; paint(); } }
      else if (st.street) { st.street = null; paint(); }
      unpick();
      debounced();
    });
    input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (list.hidden) return show();
        active = Math.max(0, Math.min(items.length - 1, active + (e.key === 'ArrowDown' ? 1 : -1)));
        mark();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (items[active] && !list.hidden && shown === input.value.trim()) pick(items[active]); else settle();
      } else if (e.key === 'Escape') { hide(); }
    });
    list.addEventListener('pointerdown', e => e.preventDefault());  // keep focus in the input while tapping a row
    list.addEventListener('click', e => {
      const li = e.target.closest('[data-i]'); if (!li) return;
      pick(items[+li.dataset.i]);
    });
    input.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== input) { hide(); settle(); } }, 120));
    input.closest('.combo').querySelector('.combo-clear').addEventListener('click', () => {
      if (field === 'town') { st.town = st.street = null; st.number = null; streetIn.value = ''; need('street', null); }
      else st.street = null;
      input.value = '';
      need(field, null);
      unpick(); paint(); input.focus();
      if (field === 'street' && st.town) locate();  // back to the town alone
    });
    combos[field] = { show, settle };
  }
  combo('town', townIn, async q => {
    const r = await api('/api/places/towns?q=' + encodeURIComponent(q), { quiet: true }).catch(() => []);
    return { items: r.map(x => ({ ...x, sub: L() === 'en' && x.en ? x.en : '' })) };
  }, x => {
    haptic('select');
    const changed = !st.town || st.town.code !== x.code;
    st.town = { code: x.code, name: x.name };
    if (changed) { st.street = null; st.number = null; st.noStreets = false; streetIn.value = ''; need('street', null); }
    paint();
    if (!changed) return;
    if (!st.quiet && document.activeElement === townIn) streetIn.focus();  // in the tap: phones keep the keyboard
    locate();  // the town alone is enough to go on; a street makes it exact
    api(`/api/places/streets?town=${x.code}`, { quiet: true }).then(s => {
      if (st.town?.code !== x.code || !s?.no_streets) return;
      st.noStreets = true;
      if (document.activeElement === streetIn) streetIn.blur();
      paint();
    }).catch(() => {});
  });
  combo('street', streetIn, async q => {
    if (!st.town) return { items: [], none: t('Choose the town first') };
    const r = await api(`/api/places/streets?town=${st.town.code}&q=${encodeURIComponent(q)}`, { quiet: true }).catch(() => ({ streets: [] }));
    return { items: r.streets, more: r.total > r.streets.length };
  }, x => {
    haptic('select');
    st.street = { code: x.code, name: x.name };
    paint();
    if (!st.quiet && document.activeElement === streetIn) numIn.focus();  // in the tap: the number pad opens at once
    locate();
  });
  const relocate = debounce(() => locate(), 500);
  root.addEventListener('click', e => { if (e.target.closest('[data-gps]')) fromGps(e.target.closest('[data-gps]')); });
  numIn.addEventListener('input', () => {
    numIn.value = numIn.value.replace(/\D+/g, '').replace(/^0+/, '').slice(0, 4);
    st.number = numIn.value ? +numIn.value : null;
    if (!st.street) return;  // kept for the street, once it's chosen
    unpick();
    relocate();
  });
  numIn.addEventListener('focus', () => { if (!st.street && streetIn.value.trim()) combos.street.settle(); });
  numIn.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); numIn.blur(); locate(); } });

  // the address shown changed: what was picked before no longer counts (Save / Continue wait for the new one)
  function unpick() {
    if (!st.home) return;
    st.home = null;
    result('');
    onPick?.(null);
  }
  function locate() {
    if (!st.town) return Promise.resolve(null);
    const my = ++st.seq;
    unpick();
    result('busy');
    const ask = (street, number) => {
      const qs = new URLSearchParams({ town: st.town.code });
      if (street) qs.set('street', street.code);
      if (street && number) qs.set('number', number);
      return api('/api/places/locate?' + qs, { quiet: true });
    };
    const run = (async () => {
      let h = null, note = '';
      try { h = await ask(st.street, st.number); } catch { h = null; }
      if (my !== st.seq) return null;
      // a street that isn't on the map: the town centre, said so (never a town-centre point that looks like the street)
      if (st.street && (!h || h.precision === 'town')) {
        try { h = await ask(null); note = t('Street not found on the map: placed in the town centre. You can change it later.'); } catch { h = null; }
        if (my !== st.seq) return null;
        if (h) Usage.ev('addr', { a: 'fallback' });
      }
      if (!h) { result('err', t('Couldn’t find this address on the map just now. Try again in a minute.')); haptic('error'); return null; }
      const exact = !note;
      st.home = { ...h, town_name: st.town.name, street_name: exact ? st.street?.name || null : null };
      result('ok', note);
      if (exact && st.street) haptic('success');
      onPick?.(st.home);
      return st.home;
    })();
    st.pending = run.finally(() => { if (st.pending === run) st.pending = null; });
    return run;
  }
  async function fromGps(btn) {
    if (btn.classList.contains('busy')) return;
    btn.classList.add('busy');
    btn.setAttribute('aria-busy', 'true');
    const my = ++st.seq;
    const run = (async () => {
      let why = null;
      const pos = await new Promise(ok => navigator.geolocation.getCurrentPosition(p => ok(p),
        e => { why = e?.code === 1 ? 'denied' : e?.code === 3 ? 'timeout' : 'unavailable'; ok(null); },
        { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 }));
      if (my !== st.seq) return null;
      if (!pos) {
        Usage.ev('gps', { a: why });
        result('err', why === 'denied' ? t('Location is off for this site. Choose your town below.') : t('Couldn’t get your location. Choose your town below.'));
        haptic('error');
        townIn.focus();
        return null;
      }
      try {
        const h = await api(`/api/places/reverse?lat=${pos.coords.latitude}&lon=${pos.coords.longitude}`, { quiet: true });
        if (my !== st.seq) return null;
        st.town = h.town ? { code: h.town, name: h.town_name } : null;
        st.street = h.street ? { code: h.street, name: h.street_name } : null;
        st.number = h.number || null;
        st.home = h;
        need('town', null); need('street', null);
        paint();
        result('ok');
        haptic('success');
        Usage.ev('gps', { a: 'ok', b: h.precision });
        onPick?.(st.home);
        return h;
      } catch {
        if (my !== st.seq) return null;
        Usage.ev('gps', { a: 'outside' });
        result('err', t('That location isn’t an address in Israel we can use. Choose your town below.'));
        haptic('error');
        townIn.focus();
        return null;
      }
    })();
    st.pending = run.finally(() => { btn.classList.remove('busy'); btn.removeAttribute('aria-busy'); if (st.pending === run) st.pending = null; });
    return run;
  }
  paint();
  if (st.home) result('ok');
  return {
    get home() { return st.home; },
    // the address, once what's under way has finished (a lookup, or a name typed and not chosen); null with the
    // field that's missing shown and focused
    async ready() {
      st.quiet = true;  // finishing up: no jumping to the next field
      try {
        const q = townIn.value.trim();
        if (!st.town && q) await combos.town.settle();
        // a street typed and not chosen while the list has it: one tap on it (a street not on the list is left out)
        if (st.town && !st.street && !st.noStreets && streetIn.value.trim() && await combos.street.settle() === 'unpicked') {
          Usage.ev('addr', { a: 'blocked', b: 'street' });
          streetIn.focus();
          return null;
        }
        if (st.pending) await st.pending;
        if (st.home) return st.home;
        if (st.town) return await locate();  // a lookup that failed: once more
        if (!q) need('town', gps ? t('Choose your town, or use your location') : t('Choose your town'));
        Usage.ev('addr', { a: 'blocked', b: q ? 'town' : 'empty' });
        haptic('error');
        townIn.focus();
        return null;
      } finally { st.quiet = false; }
    },
  };
}
const normName = s => String(s || '').toLowerCase().replace(/["'׳״`()\[\].,/\\-]+/g, ' ').replace(/[ךםןףץ]/g, c => ({ ך: 'כ', ם: 'מ', ן: 'נ', ף: 'פ', ץ: 'צ' })[c]).replace(/\s+/g, ' ').trim();

// How exact a located address is, said plainly when it isn't the house itself.
const roughPin = h => h && (h.precision === 'street' || (h.precision === 'town' && h.street));
function pinNote(h) {
  if (h.precision === 'pin') return `<small>${t('Pin placed by you')}</small>`;
  if (h.precision === 'near') return `<small>${h.near ? t('No. {n} isn’t on the map: placed at no. {m}', { n: h.number, m: h.near }) : t('Placed a few doors away')}</small>`;
  if (h.precision === 'street') return `<small>${h.number ? t('This number isn’t on the map: the pin is on the street') : t('Add the house number to be exact')}</small>`;
  if (h.precision === 'town') return `<small>${h.street ? t('The street isn’t on the map: the pin is in the town centre') : t('Town centre: add a street to be exact')}</small>`;
  return '';
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
const ADDR = ['label', 'lat', 'lon', 'town', 'street', 'number', 'town_name', 'street_name', 'precision', 'near', 'source'];
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
        detourOpts().map(km => `<button role="radio" type="button" data-detour="${km}" aria-checked="${Math.abs(detour() - km) < 1e-9}" aria-label="${detourText(km)}">
          <b class="num">${km < 1 ? Math.round(km * 1000) : km}</b><small>${km < 1 ? t('m') : t('km')}</small></button>`).join('')}</div>`;
  else if (pl.length) pick = `<button type="button" class="pill new-btn" data-add>${PLUS}${t('Add a second place')}</button>`;
  const rows = pl.map(x => LOC.edit === x.id ? `<li>${placeEditor(x)}</li>` : `<li class="pl-row">
      <span class="pl-dot" aria-hidden="true"></span><span class="pl-text"><b dir="auto">${esc(placeLabel(x))}</b><small><bdi>${esc(x.label)}</bdi></small>${roughPin(x) ? `<small class="pl-note">${x.precision === 'street' ? t('Pin on the street') : t('Pin in the town centre')}</small>` : ''}</span>
      <button type="button" class="pl-btn" data-edit="${esc(x.id)}">${t('Edit')}</button>
      ${pl.length > 1 ? `<button type="button" class="pl-btn pl-del" data-del="${esc(x.id)}" aria-label="${esc(t('Remove {x}', { x: placeLabel(x) }))}">${SVG.trash}</button>` : ''}</li>`).join('');
  el.innerHTML = (pl.length ? `<div class="seg area-seg" role="radiogroup" aria-label="${esc(t('Where to look'))}">
      <button role="radio" type="button" data-area="home" aria-checked="${!way}">${t('Around a place')}</button>
      <button role="radio" type="button" data-area="route" aria-checked="${way}">${t('On the way')}</button></div>
      <div class="loc-pick">${pick}</div>` : '')
    + `<h3 class="loc-h">${t('Your places')}</h3>${rows ? `<ul class="place-list">${rows}</ul>` : ''}`
    + (LOC.edit === 'new' ? placeEditor(null) : pl.length < MAX_PLACES ? `<button type="button" class="pill new-btn" data-add>${PLUS}${t('Add a place')}</button>` : '');
  if (pl.length && !way) radiusPicker($('#radius'), S.prefs.max_km ?? null, km => setPrefs({ max_km: km }));
  const ed = el.querySelector('.place-edit');
  if (ed) {
    const save = ed.querySelector('[data-save]');
    AddressPicker(ed.querySelector('.addr'), { home: LOC.edit === 'new' ? null : placeById(LOC.edit),
                                               onPick: h => { LOC.draft = h; LOC.touched = true; save.disabled = !h; } });
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
    if (i < 0 || (LOC.touched && !LOC.draft)) return;  // the address was changed and isn't on the map yet
    pl[i] = { ...pl[i], name, ...(LOC.draft ? addrOnly(LOC.draft) : {}) };
    setPrefs({ places: pl });
  }
  LOC.edit = LOC.draft = null;
  LOC.touched = false;
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
  if ('add' in d) { haptic('tap'); LOC.edit = 'new'; LOC.draft = null; LOC.touched = false; renderLoc(); setTimeout(() => $('#loc-body .place-edit')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 30); return; }
  if (d.edit) { haptic('tap'); LOC.edit = d.edit; LOC.draft = null; LOC.touched = false; renderLoc(); return; }
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

// ---------------------------------------------------------------- first sign-in: one screen, the address
// Only the address is asked (the town alone will do). The distance starts at 5 km and the first cart is made for you:
// both are changed later in the app (the address pill, the cart).
const OB = { home: null, km: 5, picker: null, t0: 0 };
function startOnboarding() {
  document.body.classList.remove('booting', 'gated');
  document.body.classList.add('onboarding');
  $('#gate').hidden = true;
  $('#onboard').hidden = false;
  OB.home = ME.prefs?.home || null;
  OB.km = ME.prefs?.max_km || 5;
  OB.t0 = Date.now();
  obPicker();
  Usage.view('onboard', '1');
  Usage.ev('onboard', { a: '1' });
}
function obPicker() {
  OB.picker = AddressPicker($('#ob-addr'), { home: OB.home, onPick: h => { OB.home = h; obReady(); } });
  obReady();
}
function obReady() { $('#ob-next').classList.toggle('ready', !!OB.home); }
$('#ob-next').addEventListener('click', async () => {
  const b = $('#ob-next');
  if (b.classList.contains('busy')) return;
  b.classList.add('busy');
  b.setAttribute('aria-busy', 'true');
  try {
    const home = await OB.picker.ready();  // what's being looked up, or typed and not chosen, is finished first
    if (!home) return;
    OB.home = home;
    await api('/api/settings', { method: 'PUT', body: { prefs: { home, max_km: OB.km } } });
    const carts = await api('/api/carts');
    if (!carts.length) {
      const c = await api('/api/carts', { method: 'POST', body: { name: t('Weekly shop') } });
      local.set('cart', c.id);
    }
    const r = await api('/api/me', { method: 'PUT', body: { onboarded: true, lang: L() } });
    ME.user = r.user;
    haptic('success');
    Usage.ev('onboard', { a: 'done', n: Math.round((Date.now() - OB.t0) / 1000), b: home.precision });
    goShop();
  } catch { toast(t('Couldn’t save — try again'), null, null, 'error'); }
  finally { b.classList.remove('busy'); b.removeAttribute('aria-busy'); }
});

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
      <button type="button" class="ghost wide" data-acc="out">${t('Sign out')}</button></div>
    <details class="acc-danger"><summary>${t('Danger zone')}${SVG.chev}</summary>
      <button type="button" class="ghost wide danger" data-acc="delete">${t('Delete my account')}</button></details>` : ''}
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
  if (save) {
    haptic('select');
    try { localStorage.setItem('lang_pick', String(Date.now())); } catch { /* private mode */ }
  }
  renderLangButtons();
  renderAccountButton();
  if (!$('#gate').hidden) renderGate();
  if (!$('#onboard').hidden) obPicker();
  if (appStarted) relabel();
  if (save && ME.user) api('/api/me', { method: 'PUT', body: { lang: l }, quiet: true }).catch(() => {});
}
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
