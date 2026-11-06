'use strict';
// Usage statistics for the owner's admin page: pages opened and for how long, searches and what came of them, clicks
// (a control's label, never what was typed), rage and dead clicks, errors and load times. Sent only to this server
// (/api/ux, kept in stats.db for 180 days; see app/stats.py), never to anyone else. No dependencies.

const Usage = (() => {
  const IDLE = 30 * 60 * 1000;  // a new visit after 30 minutes without anything happening
  const rid = () => {
    try { return crypto.randomUUID().replace(/-/g, '').slice(0, 24); }
    catch { return (Math.random().toString(36).slice(2) + Date.now().toString(36) + Math.random().toString(36).slice(2)).slice(0, 24); }
  };
  const get = (s, k) => { try { return window[s].getItem(k); } catch { return null; } };
  const set = (s, k, v) => { try { window[s].setItem(k, v); } catch { /* private mode: ids live for this page only */ } };
  const okId = v => /^[\w-]{10,40}$/.test(v || '');

  let vid = get('localStorage', 'sal_vid');
  if (!okId(vid)) { vid = rid(); set('localStorage', 'sal_vid', vid); }
  let sid, last, ctxSent;
  function visit() {  // this tab's visit; a new one after IDLE
    const now = Date.now();
    if (!sid) {
      const [s, l] = (get('sessionStorage', 'sal_sid') || '').split('|');
      if (okId(s) && now - +l < IDLE) { sid = s; last = +l; ctxSent = get('sessionStorage', 'sal_ctx') === s; }
    }
    if (!sid || now - last > IDLE) { sid = rid(); ctxSent = false; }
    last = now;
    set('sessionStorage', 'sal_sid', `${sid}|${now}`);
    return sid;
  }
  visit();

  const page = () => {
    const b = document.body;
    if (!b) return 'boot';
    if (!document.getElementById('view-home')) return 'landing';  // the landing page (/ before signing in, /about)
    if (b.classList.contains('gated')) return 'signin';
    if (b.classList.contains('onboarding')) return 'onboard';
    return b.dataset.view || 'home';
  };

  function ctx() {
    const qs = new URLSearchParams(location.search);
    let ref = '';
    try { const r = document.referrer && new URL(document.referrer); if (r && r.host !== location.host) ref = r.host; } catch { /* none */ }
    const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === 'dark'
      : matchMedia('(prefers-color-scheme: dark)').matches;
    return {
      lang: document.documentElement.lang || '', theme: dark ? 'dark' : 'light', screen: `${screen.width}x${screen.height}`,
      vw: innerWidth, touch: navigator.maxTouchPoints > 0, ref,
      pwa: matchMedia('(display-mode: standalone)').matches || navigator.standalone === true,
      tz: (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { return ''; } })(),
      utm_source: qs.get('utm_source'), utm_medium: qs.get('utm_medium'), utm_campaign: qs.get('utm_campaign'),
      landing: (location.hash || '#/').split('?')[0].slice(0, 80),
    };
  }

  // ---------------------------------------------------------------- sending
  let q = [], timer = null;
  function ev(k, f = {}) {
    visit();
    q.push({ k, t: Date.now(), p: f.p ?? page(), a: f.a ?? undefined, b: f.b ?? undefined, n: f.n ?? undefined, d: f.d ?? undefined });
    if (q.length >= 25) send();
    else if (!timer) timer = setTimeout(send, 4000);
  }
  function send(beacon) {
    clearTimeout(timer); timer = null;
    while (q.length) {
      const batch = q.splice(0, 50);
      const body = { vid, sid, now: Date.now(), ev: batch };
      if (!ctxSent) { body.ctx = ctx(); ctxSent = true; set('sessionStorage', 'sal_ctx', sid); }
      const json = JSON.stringify(body);
      try {
        if (beacon && navigator.sendBeacon && navigator.sendBeacon('/api/ux', new Blob([json], { type: 'application/json' }))) continue;
        fetch('/api/ux', { method: 'POST', body: json, headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', keepalive: true }).catch(() => {});
      } catch { /* statistics never get in the way */ }
    }
  }

  // ---------------------------------------------------------------- pages and how long they're looked at
  let cur = null;  // {p, a, t0, shown, depth}
  const scrollDepth = () => {
    const h = document.documentElement.scrollHeight - innerHeight;
    return h > 40 ? Math.min(100, Math.round(scrollY / h * 100)) : 100;
  };
  function endView() {
    if (!cur) return;
    const ms = cur.shown + (document.visibilityState === 'visible' ? Date.now() - cur.t0 : 0);
    if (ms > 300) ev('dwell', { p: cur.p, a: cur.a, n: Math.round(ms), d: { depth: cur.depth } });
    cur = null;
  }
  function view(p, a) {
    if (cur && cur.p === p && cur.a === (a ?? null)) return;
    if (p !== 'browse' && p !== 'product') endSearch();
    if (p === 'product' && srch) srch.outcome = srch.outcome || 'open';
    endView();
    cur = { p, a: a ?? null, t0: Date.now(), shown: 0, depth: 0 };
    ev('view', { p, a });
  }
  addEventListener('scroll', () => { if (cur) cur.depth = Math.max(cur.depth, scrollDepth()); }, { passive: true });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      if (cur) { cur.shown += Date.now() - cur.t0; }
      endSearch();
      send(true);
    } else if (cur) cur.t0 = Date.now();
  });
  addEventListener('pagehide', () => { endSearch(); endView(); send(true); });

  // ---------------------------------------------------------------- searches: one event per settled query, with what came of it
  let srch = null, settle = null, prevQ = null;
  function results(terms, total, cat) {
    const text = (terms || []).join(' ').trim();
    clearTimeout(settle);
    if (!text) return;
    settle = setTimeout(() => {
      if (srch && srch.q === text) { srch.n = total; return; }  // back on the same results
      endSearch();
      srch = { q: text, n: total, cat: cat || '', outcome: null, prev: prevQ, t: Date.now() };
    }, 1200);
  }
  function endSearch() {
    clearTimeout(settle);
    if (!srch) return;
    const s = srch; srch = null;
    prevQ = s.q;
    setTimeout(() => { if (prevQ === s.q) prevQ = null; }, 120000);
    ev('search', { p: 'browse', a: s.q, b: s.outcome || 'none', n: s.n, d: { cat: s.cat || null, prev: s.prev, secs: Math.round((Date.now() - s.t) / 1000) } });
  }
  function add(key, qty) {
    if (srch) srch.outcome = 'add';
    ev('add', { a: key, n: qty });
  }

  // ---------------------------------------------------------------- clicks: what's tapped, rage clicks, dead clicks
  const CONTROL = 'a,button,input,select,textarea,label,summary,[role=button],[role=tab],[role=switch],[role=menuitem],[role=option],[data-add],[data-ckey],[data-term],[data-size]';
  const SKIP_DATA = new Set(['data-i18n', 'data-i18n-title', 'data-i18n-aria', 'data-i18n-ph', 'data-tip']);
  const digits = s => s.replace(/[\d₪.,:%  ]*\d[\d₪.,:%  ]*/g, ' # ').replace(/\s+/g, ' ').trim();
  function label(el) {
    if (!el || el === document.body || el === document.documentElement) return '';
    const tag = el.tagName.toLowerCase();
    const data = [...el.attributes].map(a => a.name).find(n => n.startsWith('data-') && !SKIP_DATA.has(n));
    let s = tag + (el.id ? '#' + el.id : '') + (data ? `[${data.slice(5)}]` : '');
    if (!el.id && !data && el.classList.length) s += '.' + el.classList[0];
    if (/^(input|select|textarea)$/.test(tag)) return s + (el.type && tag === 'input' ? `:${el.type}` : '');  // never the value
    if (!el.id && !data && !el.closest('.card')) {
      const text = digits((el.getAttribute('aria-label') || el.title || el.innerText || el.textContent || '').trim()).slice(0, 40);  // what's shown, in the language shown
      if (text) s += ` “${text}”`;
    }
    return s.slice(0, 120);
  }
  function zone(el) {
    for (let x = el?.parentElement; x && x !== document.body; x = x.parentElement) {
      if (x.id && !/^(app|main)$/.test(x.id)) return x.id;
      if (x.tagName === 'DIALOG') return 'dialog';
    }
    return '';
  }
  let taps = [];
  document.addEventListener('click', e => {
    if (!e.isTrusted) return;  // haptics click a hidden switch: not a person
    const t = e.target instanceof Element ? e.target : e.target?.parentElement;
    if (!t) return;
    const ctl = t.closest(CONTROL);
    const p = page();
    if (ctl) ev('click', { p, a: label(ctl), b: zone(ctl) });
    // rage: 3+ taps within a second on about the same spot
    const now = Date.now();
    taps = taps.filter(x => now - x.t < 1000 && Math.hypot(x.x - e.clientX, x.y - e.clientY) < 40);
    taps.push({ t: now, x: e.clientX, y: e.clientY });
    if (taps.length === 3) ev('rage', { p, a: label(ctl || t), b: zone(ctl || t), n: 3 });
    if (!ctl) deadCheck(t, p);
  }, true);

  // dead click: a tap on something that looks tappable (a photo, an icon, a pointer cursor, a short label) and nothing happens
  function deadCheck(t, p) {
    const media = t.closest('img, svg, picture, canvas');
    if (t.closest('.maplibregl-canvas-container, .maplibregl-map')) return;  // the map pans and zooms without DOM changes
    const looks = media || getComputedStyle(t).cursor === 'pointer' || (t.children.length === 0 && (t.textContent || '').trim().length <= 40 && (t.textContent || '').trim().length > 0);
    if (!looks || getSelection()?.toString()) return;
    let changed = false;
    const hash = location.hash, y = scrollY;
    const mo = new MutationObserver(() => { changed = true; });
    mo.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
    setTimeout(() => {
      mo.disconnect();
      if (!changed && location.hash === hash && Math.abs(scrollY - y) < 4 && !document.querySelector('dialog[open]'))
        ev('dead', { p, a: label(media || t), b: zone(media || t) });
    }, 800);
  }

  // dialogs and sheets: which get opened
  for (const m of ['showModal', 'show']) {
    const orig = HTMLDialogElement.prototype[m];
    HTMLDialogElement.prototype[m] = function (...a) { try { if (!this.open) ev('dialog', { a: this.id || 'dialog' }); } catch { /* ignore */ } return orig.apply(this, a); };
  }

  // ---------------------------------------------------------------- errors
  const seen = new Map();
  function error(msg, src) {
    msg = String(msg || 'Error').slice(0, 200);
    if (/signed out|ResizeObserver loop/.test(msg)) return;
    const k = msg + src, n = seen.get(k) || 0;
    if (n >= 3 || seen.size > 30) return;
    seen.set(k, n + 1);
    ev('error', { a: msg, b: src });
  }
  const where = (f, l, c) => f ? `${String(f).replace(location.origin, '').split('?')[0]}:${l || 0}:${c || 0}` : '';
  // "Script error." with no file is another origin's script, mostly the browser's own (Firefox for iPhone, each time a
  // dialog opens): nothing in it is about this page
  addEventListener('error', e => { if (e instanceof ErrorEvent && (e.filename || e.message !== 'Script error.')) error(e.message, where(e.filename, e.lineno, e.colno)); });
  addEventListener('unhandledrejection', e => {
    const r = e.reason;
    const st = (r && r.stack || '').split('\n').find(l => /\/static\//.test(l)) || '';
    error(r && r.message || String(r), (st.match(/\/static\/[^\s)]+/) || [''])[0].replace(/\?v=\d+/, ''));
  });
  function apiFail(path, status) {
    path = String(path).split('?')[0].replace(/\/\d+(?=\/|$)/g, '/{id}').slice(0, 80);
    if (status === 401) return;
    ev('api_fail', { a: path, n: status });
  }

  // ---------------------------------------------------------------- load speed (once per page load)
  let lcp = null;
  try { new PerformanceObserver(l => { const e = l.getEntries(); if (e.length) lcp = e[e.length - 1].startTime; }).observe({ type: 'largest-contentful-paint', buffered: true }); } catch { /* Safari */ }
  addEventListener('load', () => setTimeout(() => {
    try {
      const nav = performance.getEntriesByType('navigation')[0];
      if (!nav) return;
      const fcp = performance.getEntriesByName('first-contentful-paint')[0];
      ev('perf', { n: Math.round(nav.loadEventEnd || nav.duration), d: { ttfb: Math.round(nav.responseStart), dcl: Math.round(nav.domContentLoadedEventEnd),
                    fcp: fcp ? Math.round(fcp.startTime) : null, lcp: lcp ? Math.round(lcp) : null, type: nav.type } });
    } catch { /* old browser */ }
  }, 3000));

  // the landing page has no router of its own to say it was opened
  const landing = () => { if (page() === 'landing') view('landing', location.pathname === '/about' ? 'about' : undefined); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', landing); else landing();

  return {
    ev, view, results, add, apiFail, send,
    ids: () => `${vid}.${visit()}`,                 // sent with API calls, so the server's own events join this visit
    reset() {  // account deleted: a new browser id and visit
      clearTimeout(settle); srch = null; cur = null; q = [];
      vid = rid(); set('localStorage', 'sal_vid', vid);
      sid = null; set('sessionStorage', 'sal_sid', ''); visit();
    },
  };
})();
