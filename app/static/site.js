'use strict';
// What the public pages share (landing.html, /about, the Price Board): the top bar's language switch and its
// "Sign in" button. One language for the whole site: a choice here is kept in this browser (localStorage + a cookie
// the server reads) and, when you're signed in, in your account, so the app opens in it too. A page open in another
// tab, or one the browser brings back with Back, follows the latest choice. Pages redraw on the 'langchange' event.
(() => {
  const root = document.documentElement;
  const lang = () => (root.lang === 'en' ? 'en' : 'he');
  const stored = () => { try { return localStorage.getItem('lang'); } catch { return null; } };
  const $$ = s => [...document.querySelectorAll(s)];

  let mePromise = null;  // GET /api/me, once per page
  const me = () => (mePromise = mePromise || fetch('/api/me', { credentials: 'same-origin', cache: 'no-store' })
    .then(r => (r.ok ? r.json() : null)).catch(() => null));
  // has this browser used the app? (it remembers the open cart): only then is asking who you are worth a request
  const usedApp = () => { try { return !!localStorage.getItem('cart'); } catch { return false; } };

  function show(l) {
    root.lang = l;
    root.dir = l === 'he' ? 'rtl' : 'ltr';
    document.cookie = `lang=${l};path=/;max-age=31536000;samesite=lax`;
    $$('.langs [data-lang]').forEach(b => b.setAttribute('aria-checked', b.dataset.lang === l));
    document.dispatchEvent(new CustomEvent('langchange', { detail: l }));
  }
  function choose(l) {  // the visitor picked a language: this browser, then the account
    try { localStorage.setItem('lang', l); localStorage.setItem('lang_pick', String(Date.now())); } catch { /* private mode */ }
    show(l);
    if (usedApp()) {
      me().then(m => {
        if (m?.user && m.user.lang !== l) {
          fetch('/api/me', { method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lang: l }) })
            .then(r => { if (r.ok && m.user) m.user.lang = l; }).catch(() => {});
        }
      });
    }
  }
  $$('.langs [data-lang]').forEach(b => b.addEventListener('click', () => {
    if (b.dataset.lang !== lang()) choose(b.dataset.lang);
    try { navigator.vibrate?.(10); } catch { /* not allowed yet */ }
  }));
  // another tab chose (storage), or this page came back from the browser's history after a choice elsewhere
  addEventListener('storage', e => { if (e.key === 'lang' && (e.newValue === 'he' || e.newValue === 'en') && e.newValue !== lang()) show(e.newValue); });
  addEventListener('pageshow', e => { const s = stored(); if (e.persisted && (s === 'he' || s === 'en') && s !== lang()) show(s); });
  show(lang());

  // signed in already: the bar's button opens the app instead of offering to sign in (the same place on every page)
  if (usedApp()) {
    me().then(m => {
      if (!m?.user) return;
      $$('.bar [data-open-app]').forEach(a => { a.innerHTML = '<span lang="he">לסל קל</span><span lang="en">Open Sal-Kal</span>'; });
    });
  }

  // the top bar's height, for whatever sticks under it
  const bar = document.querySelector('.bar');
  const measure = () => { if (bar) root.style.setProperty('--bar-h', `${bar.offsetHeight}px`); };
  measure();
  addEventListener('resize', measure);
  document.fonts?.ready?.then(measure);

  window.Site = { lang, choose, me, usedApp };
})();
