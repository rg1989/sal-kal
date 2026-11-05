'use strict';
// The landing page: language (shared with the app), the video that plays while it's on screen, scroll reveals and the
// live counts from the price files.
(() => {
  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const lang = () => (document.documentElement.lang === 'en' ? 'en' : 'he');
  let status = null;  // /api/status, once loaded
  const TITLE = { he: 'סל קל · השוואת מחירי סל בסופרמרקטים', en: 'Sal-Kal · Supermarket cart price comparison' };

  function setLang(l) {
    document.documentElement.lang = l;
    document.documentElement.dir = l === 'he' ? 'rtl' : 'ltr';
    try { localStorage.setItem('lang', l); } catch { /* private mode */ }
    document.cookie = `lang=${l};path=/;max-age=31536000;samesite=lax`;
    document.title = TITLE[l];
    $$('.langs [data-lang]').forEach(b => b.setAttribute('aria-checked', b.dataset.lang === l));
    renderLive();
  }
  $$('.langs [data-lang]').forEach(b => b.addEventListener('click', () => {
    if (b.dataset.lang !== lang()) setLang(b.dataset.lang);
    try { navigator.vibrate?.(10); } catch { /* not allowed yet */ }
  }));
  setLang(lang());

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
      ? `${n(status.chains)} רשתות · ${n(status.stores)} סניפים${time ? ` · מחירים נכון ל־${time}` : ''}`
      : `${n(status.chains)} chains · ${n(status.stores)} stores${time ? ` · prices as of ${time}` : ''}`;
    $('.live').hidden = false;
    $$('[data-stat]').forEach(el => { const v = status[el.dataset.stat]; if (v) el.textContent = n(v); });
  }
  fetch('/api/status', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : null).then(d => {
    if (d && d.products) { status = d; renderLive(); }
  }).catch(() => {});
})();
