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

  // signed in: the app's own header (search, where you shop, your picture, your cart), each part opening the app there
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const both = (he, en) => `<span lang="he">${he}</span><span lang="en">${en}</span>`;
  const ICON = {
    search: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="9" cy="9" r="5.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M13.2 13.2L17 17" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    pin: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 17.6s5.6-5.1 5.6-9.4a5.6 5.6 0 0 0-11.2 0c0 4.3 5.6 9.4 5.6 9.4z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><circle cx="10" cy="8.2" r="2.2" fill="currentColor"/></svg>',
    cart: '<svg viewBox="0 0 20 20" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M7.1 8 8.8 4.8M12.9 8 11.2 4.8"/><path d="M3.8 8h12.4l-1.3 6.1a1.8 1.8 0 0 1-1.76 1.42H6.86a1.8 1.8 0 0 1-1.76-1.42z"/><path d="M8.2 10.6v2.4M11.8 10.6v2.4"/></g></svg>',
  };
  const initials = n => (n || '?').trim().split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase();
  function where(p) {  // the app's place pill: "Within 3 km of Home" / "Home → Work"
    const pl = p?.places || [], by = id => pl.find(x => x.id === id);
    const name = x => !x ? '' : x.name ? esc(x.name) : x.id === 'home' ? both('בית', 'Home') : x.id === 'work' ? both('עבודה', 'Work')
      : esc((x.label || '').split(',')[0]);
    if (!pl.length) return both('הגדרת מיקום', 'Set your location');
    if (p.area === 'route' && by(p.from) && by(p.to)) {
      return `<bdi>${name(by(p.from))}</bdi> <span lang="he">←</span><span lang="en">→</span> <bdi>${name(by(p.to))}</bdi>`;
    }
    const at = `<bdi>${name(by(p.at) || pl[0])}</bdi>`;
    return p.max_km ? `<span lang="he">עד ${esc(p.max_km)} ק״מ מ${at}</span><span lang="en">Within ${esc(p.max_km)} km of ${at}</span>`
      : `<span lang="he">ליד ${at}</span><span lang="en">Near ${at}</span>`;
  }
  let barDone = false;
  function memberBar(m) {
    const u = m?.user, bar = document.querySelector('.bar');
    if (!u || !bar || barDone) return;
    barDone = true;
    const logo = bar.querySelector('.logo'), end = bar.querySelector('.bar-end'), tag = bar.querySelector('.pw-tag');
    const start = document.createElement('div');
    start.className = 'bar-start';
    logo.replaceWith(start);
    start.append(logo);
    if (tag) start.append(tag);
    const form = document.createElement('form');
    form.className = 'mb-search';
    form.setAttribute('role', 'search');
    form.innerHTML = `${ICON.search}<input type="search" name="q" autocomplete="off" dir="auto" enterkeyhint="search">`;
    const q = form.querySelector('input');
    const ph = () => { q.placeholder = lang() === 'he' ? 'חיפוש מוצרים — חלב, ביצים, ברקוד' : 'Search milk, eggs, a barcode…'; q.setAttribute('aria-label', q.placeholder); };
    ph();
    document.addEventListener('langchange', ph);
    form.addEventListener('submit', e => {
      e.preventDefault();
      const v = q.value.trim().split(/\s+/).filter(Boolean).join('|');
      location.href = v ? `/app#/s/${encodeURIComponent(v)}` : '/app';
    });
    start.after(form);
    const near = document.createElement('a');
    near.className = 'mb-near';
    near.href = '/app';
    near.innerHTML = `${ICON.pin}<span>${where(m.prefs)}</span>`;
    const av = document.createElement('a');
    av.className = 'mb-avatar';
    av.href = '/app';
    av.setAttribute('aria-label', u.name || 'Account');
    av.innerHTML = `<span class="avatar" aria-hidden="true">${u.avatar ? `<img src="${esc(u.avatar)}" alt="" referrerpolicy="no-referrer">` : ''}<span>${esc(initials(u.name || u.email))}</span></span>`;
    av.querySelector('img')?.addEventListener('error', e => e.target.remove());
    const cart = document.createElement('a');
    cart.className = 'mb-cart';
    cart.href = '/app';
    cart.innerHTML = `${ICON.cart}<span class="label">${both('סל', 'Cart')}</span><span class="n"></span>`;
    $$('.bar [data-open-app]').forEach(a => a.remove());
    const langs = end.querySelector('.langs');
    end.prepend(near);
    if (langs) end.append(langs);
    end.append(av, cart);
    bar.classList.add('member-bar');
    // the open cart's count, as the app shows it (the app keeps the open cart's id in this browser)
    let open = null;
    try { open = localStorage.getItem('cart'); } catch { /* private mode */ }
    fetch('/api/carts', { credentials: 'same-origin' }).then(r => (r.ok ? r.json() : [])).then(cs => {
      const c = cs.find(x => String(x.id) === open) || cs[0];
      if (c?.n) cart.querySelector('.n').textContent = c.n;
    }).catch(() => {});
    measure();
  }
  if (usedApp()) me().then(memberBar);

  // the top bar's height, for whatever sticks under it
  const bar = document.querySelector('.bar');
  const measure = () => { if (bar) root.style.setProperty('--bar-h', `${bar.offsetHeight}px`); };
  measure();
  addEventListener('resize', measure);
  document.fonts?.ready?.then(measure);

  window.Site = { lang, choose, me, usedApp, memberBar };
})();
