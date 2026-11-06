'use strict';
// The landing page (the language switch is site.js, shared with the Price Board): the video that plays while it's on screen, scroll reveals and the
// live counts from the price files.
(() => {
  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const lang = () => Site.lang();  // site.js: the language switch, shared with the Price Board
  let status = null;  // /api/status, once loaded
  const TITLE = { he: 'סל קל · השוואת מחירי סל בסופרמרקטים', en: 'Sal-Kal · Supermarket cart price comparison' };
  const retitle = () => { document.title = TITLE[lang()]; renderLive(); };
  document.addEventListener('langchange', retitle);

  // the walkthrough plays only while it's in view (and never against prefers-reduced-motion)
  const v = $('#walk');
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (v && !still && 'IntersectionObserver' in window) {
    new IntersectionObserver(([e]) => { if (e.isIntersecting) v.play().catch(() => {}); else v.pause(); }, { threshold: .35 }).observe(v);
  } else if (v) v.controls = true;

  // sections fade up as they arrive
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }),
      { rootMargin: '0px 0px -8% 0px', threshold: .12 });
    $$('[data-reveal]').forEach(el => io.observe(el));
  } else $$('[data-reveal]').forEach(el => el.classList.add('in'));

  // live counts from the price files (the same endpoint the app uses; no sign-in needed)
  function renderLive() {
    if (!status) return;
    const loc = lang() === 'he' ? 'he-IL' : 'en-GB', n = x => Number(x).toLocaleString(loc);
    const s = String(status.data_ts || '');
    const when = s.length >= 12 ? new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8), +s.slice(8, 10), +s.slice(10, 12)) : null;
    const time = when ? when.toLocaleString(loc, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
    $('#live-text').textContent = lang() === 'he'
      ? `${n(status.chains)} רשתות · ${n(status.stores)} סניפים${time ? ` · עודכן ${time}` : ''}`
      : `${n(status.chains)} chains · ${n(status.stores)} branches${time ? ` · updated ${time}` : ''}`;
    $('.live').hidden = false;
    $$('[data-stat]').forEach(el => { const v = status[el.dataset.stat]; if (v) el.textContent = n(v); });
  }
  fetch('/api/status', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : null).then(d => {
    if (d && d.products) { status = d; renderLive(); }
  }).catch(() => {});
  retitle();

  // Signed in already: "/" is the app, not this page. The server sends the app to a signed-in visitor, but a landing
  // page from before signing in can come back without a reload (Back, or a phone resuming the installed app).
  // /about stays: it's opened on purpose.
  async function signedIn() {
    if (location.pathname !== '/') return;
    const me = await fetch('/api/me', { credentials: 'same-origin', cache: 'no-store' }).then(r => r.ok ? r.json() : null).catch(() => null);
    if (me && me.user) location.replace('/app' + location.hash);
  }
  signedIn();
  addEventListener('pageshow', e => { if (e.persisted) signedIn(); });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') signedIn(); });
})();
