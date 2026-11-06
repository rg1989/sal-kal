'use strict';
// The public Price Board (/prices): the pages come ready from the server (app/board.py). This adds the language
// switch, a product's details (every chain, its last change, a price chart), tooltips and, for signed-in people,
// search, categories and sorting (/api/prices). Without it the page still reads in full.
(() => {
  const $ = (s, el = document) => el.querySelector(s), $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const lang = () => (document.documentElement.lang === 'en' ? 'en' : 'he');
  const L = (he, en) => (lang() === 'he' ? he : en);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const money = v => Number(v).toFixed(2);
  const day = d => `${d % 100}.${Math.floor(d / 100) % 100}`;
  const iso2day = s => +String(s).replace(/-/g, '');
  const data = (() => { try { return JSON.parse($('#board-data')?.textContent || 'null'); } catch { return null; } })();
  const he = Object.assign({}, data?.he || {});
  const chainName = b => (lang() === 'he' ? he[b] || b : b);
  const both = (h, e) => `<span lang="he">${esc(h)}</span><span lang="en">${esc(e)}</span>`;

  // ---------------------------------------------------------------- language
  function setLang(l) {
    document.documentElement.lang = l;
    document.documentElement.dir = l === 'he' ? 'rtl' : 'ltr';
    try { localStorage.setItem('lang', l); } catch { /* private mode */ }
    document.cookie = `lang=${l};path=/;max-age=31536000;samesite=lax`;
    $$('.langs [data-lang]').forEach(b => b.setAttribute('aria-checked', b.dataset.lang === l));
    $$('tr.det').forEach(r => { const k = r.previousElementSibling?.dataset.k; if (k) r.firstElementChild.innerHTML = details(k); });
    tools?.relabel();
  }
  $$('.langs [data-lang]').forEach(b => b.addEventListener('click', () => { if (b.dataset.lang !== lang()) setLang(b.dataset.lang); }));
  $$('.langs [data-lang]').forEach(b => b.setAttribute('aria-checked', b.dataset.lang === lang()));
  const tab = location.pathname.startsWith('/prices/changes') ? 'changes' : 'prices';
  $$('.tabs a').forEach(a => { if (a.dataset.tab === tab) a.setAttribute('aria-current', 'page'); });

  // ---------------------------------------------------------------- tooltips (a change's details, a price range)
  let tipEl;
  function tipText(el) {
    if (el.dataset.mv) {
      const [d, was, now, st] = el.dataset.mv.split('|');
      return L(`היה ${was} עד ${day(iso2day(d))}, עכשיו ${now} · ב־${st} סניפים`, `Was ${was} until ${day(iso2day(d))}, now ${now} · at ${st} branches`);
    }
    if (el.dataset.range) return L(`${el.dataset.range} בסניפי הרשת`, `${el.dataset.range} across the chain's branches`);
    return '';
  }
  function showTip(el) {
    const s = tipText(el);
    if (!s) return;
    tipEl = tipEl || document.body.appendChild(Object.assign(document.createElement('div'), { className: 'tip', role: 'tooltip' }));
    tipEl.textContent = s;
    tipEl.hidden = false;
    const r = el.getBoundingClientRect(), w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    tipEl.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2))}px`;
    tipEl.style.top = `${r.top - h - 8 < 60 ? r.bottom + 8 : r.top - h - 8}px`;
  }
  const hideTip = () => { if (tipEl) tipEl.hidden = true; };
  // the server writes the range as a title; move it to data-range so the tooltip is ours, in the page language
  const tipify = root => $$('td b[title]', root).forEach(b => { b.dataset.range = b.title; b.removeAttribute('title'); });
  tipify(document);
  document.addEventListener('pointerover', e => { const el = e.target.closest?.('[data-mv], [data-range]'); if (el) showTip(el); });
  document.addEventListener('pointerout', e => { if (e.target.closest?.('[data-mv], [data-range]')) hideTip(); });
  document.addEventListener('scroll', hideTip, true);

  // ---------------------------------------------------------------- product details
  const rows = Object.assign({}, data?.rows || {});   // key -> {p: {chain: [mn, mx]}, mv: {chain: [day, was, now, branches]}}
  const hist = Object.assign({}, data?.hist || {});   // key -> {chain: [[day, price|null], ...]}
  for (const k of Object.keys(rows)) hist[k] = hist[k] || {};  // the page's own rows: no entry = no price changed
  const cols = () => ($('.grid')?.dataset.cols || '').split('|').filter(Boolean);

  function chart(series) {
    const names = Object.keys(series);
    if (!names.length) return '';
    const W = 560, H = 190, P = { l: 40, r: 70, t: 10, b: 22 };
    const pts = names.flatMap(n => series[n].filter(p => p[1] != null));
    const toT = d => Date.UTC(Math.floor(d / 10000), Math.floor(d / 100) % 100 - 1, d % 100);
    const now = new Date(), today = now.getFullYear() * 10000 + (now.getMonth() + 1) * 100 + now.getDate();
    const t0 = Math.min(...pts.map(p => toT(p[0]))), t1 = Math.max(toT(today), t0 + 86400000);
    let lo = Math.min(...pts.map(p => p[1])), hi = Math.max(...pts.map(p => p[1]));
    if (hi - lo < 0.01) { lo -= 1; hi += 1; }
    // round ticks (0.1, 0.2, 0.5, 1, 2, 5 ...), about three of them
    const raw = (hi - lo) / 3, mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 5, 10].map(m => m * mag).find(s => s >= raw);
    lo = Math.max(0, Math.floor(lo / step) * step); hi = Math.ceil(hi / step) * step;
    const ticks = []; for (let v = lo; v <= hi + step / 2; v += step) ticks.push(v);
    const x = t => P.l + (W - P.l - P.r) * (t - t0) / (t1 - t0), y = v => P.t + (H - P.t - P.b) * (1 - (v - lo) / (hi - lo));
    const rtl = lang() === 'he';
    const X = t => (rtl ? W - x(t) : x(t));
    let out = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(L('מחיר לאורך זמן', 'Price over time'))}">`;
    for (const v of ticks) {
      const yy = y(v);
      out += `<line class="axis" x1="${rtl ? W - P.l : P.l}" x2="${rtl ? P.r : W - P.r}" y1="${yy}" y2="${yy}"/>`;
      out += `<text x="${rtl ? W - P.l + 6 : P.l - 6}" y="${yy + 4}" text-anchor="${rtl ? 'start' : 'end'}">${money(v)}</text>`;
    }
    out += `<text x="${X(t0)}" y="${H - 4}" text-anchor="middle">${day(pts.reduce((a, p) => Math.min(a, p[0]), 99999999))}</text>`;
    out += `<text x="${X(t1)}" y="${H - 4}" text-anchor="middle">${day(today)}</text>`;
    const ends = [];
    names.forEach((n, i) => {
      const color = `var(--s${(i % 8) + 1})`;
      let d = '', prev = null, pen = false;
      for (const [dd, v] of series[n]) {
        const tx = X(toT(dd));
        if (v == null) { if (prev != null && pen) d += `H${tx}`; pen = false; prev = null; continue; }
        d += pen ? `H${tx}V${y(v)}` : `M${tx} ${y(v)}`;
        pen = true; prev = v;
      }
      if (pen) d += `H${X(t1)}`;
      out += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round"/>`;
      if (pen && prev != null) ends.push({ y: y(prev), n, color, v: prev });
    });
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 13) ends[i].y = ends[i - 1].y + 13;
    for (const e of ends) out += `<text class="lbl" x="${rtl ? P.r - 6 : W - P.r + 6}" y="${e.y + 4}" text-anchor="${rtl ? 'end' : 'start'}" style="fill:${e.color}">${money(e.v)}</text>`;
    out += '</svg><ul class="legend">' + names.map((n, i) => `<li><i style="background:var(--s${(i % 8) + 1})"></i>${esc(chainName(n))}</li>`).join('') + '</ul>';
    return out;
  }

  function details(key) {
    const r = rows[key];
    if (!r) return '';
    const list = Object.entries(r.p).sort((a, b) => a[1][0] - b[1][0]);
    const low = list[0]?.[1][0];
    const items = list.map(([b, [mn, mx]]) => {
      const m = r.mv[b];
      const mv = m ? `<small class="${m[2] > m[1] ? 'up' : 'dn'}">${m[2] > m[1] ? '▲' : '▼'} <bdi dir="ltr">${money(m[1])} → ${money(m[2])}</bdi> · ${day(m[0])} · ${L(`${m[3]} סניפים`, `${m[3]} branches`)}</small>` : '';
      const rng = mx - mn >= 0.005 ? `<small dir="ltr">${money(mn)}–${money(mx)}</small>` : '';
      return `<li class="${mn - low < 0.005 ? 'lo' : ''}"><span>${esc(chainName(b))}${mv}</span><span><b>${money(mn)}</b>${rng}</span></li>`;
    }).join('');
    const h = hist[key];
    const graph = h === undefined ? `<p class="flat" data-hist="${esc(key)}">…</p>`
      : Object.keys(h).length ? chart(h) : `<p class="flat">${esc(L('המחירים לא השתנו מאז שהתחלנו לעקוב.', 'No price changes since tracking began.'))}</p>`;
    return `<div class="det-in"><div class="det-grid"><div class="chart">${graph}</div><ul class="all">${items}</ul></div>
      <p class="det-links"><a href="/app#/p/${encodeURIComponent(key)}">${both('לעמוד המוצר בסל קל', 'Open in Sal-Kal')}</a></p></div>`;
  }

  async function loadHist(key) {  // signed-in search results: the chart comes from the app's own history endpoint
    try {
      const res = await fetch(`/api/history?key=${encodeURIComponent(key)}`, { credentials: 'same-origin' });
      if (!res.ok) throw 0;
      const out = await res.json(), h = {};
      for (const s of out.chains || []) {
        const pts = s.points.map(p => [p[0], p[1]]);
        for (const e of s.ends || []) pts.push([e, null]);
        pts.sort((a, b) => a[0] - b[0]);
        if (pts.length > 1) h[s.chain] = pts;
      }
      hist[key] = h;
    } catch { hist[key] = {}; }
    const tr = $(`tr[data-k="${CSS.escape(key)}"]`);
    if (tr?.nextElementSibling?.classList.contains('det')) tr.nextElementSibling.firstElementChild.innerHTML = details(key);
  }

  document.addEventListener('click', e => {
    const btn = e.target.closest('.pn');
    if (!btn) return;
    const tr = btn.closest('tr'), key = tr.dataset.k, next = tr.nextElementSibling;
    if (next?.classList.contains('det')) { next.remove(); tr.classList.remove('open'); btn.setAttribute('aria-expanded', 'false'); return; }
    const det = document.createElement('tr');
    det.className = 'det';
    det.innerHTML = `<td colspan="${tr.children.length}">${details(key)}</td>`;
    tr.after(det);
    tr.classList.add('open');
    btn.setAttribute('aria-expanded', 'true');
    if (hist[key] === undefined) loadHist(key);
  });
  $$('.pn').forEach(b => b.setAttribute('aria-expanded', 'false'));

  // ---------------------------------------------------------------- signed in: search, categories, sorting
  let tools = null;
  const SEARCH_ICON = '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="m13 13 4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';

  function rowHtml(r, cs) {
    const lows = Object.values(r.p).map(v => v[0]), low = Math.min(...lows), n = lows.length;
    const cell = b => {
      const p = r.p[b];
      if (!p) return '<td class="na">—</td>';
      const rng = p[1] - p[0] >= 0.005 ? ` data-range="${money(p[0])}–${money(p[1])}"` : '';
      let out = `<td${p[0] - low < 0.005 && n > 1 ? ' class="lo"' : ''}><b${rng}>${money(p[0])}</b>`;
      const m = r.mv[b];
      if (m) {
        const up = m[2] > m[1], d = String(m[0]);
        out += `<i class="${up ? 'up' : 'dn'}" data-mv="${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}|${money(m[1])}|${money(m[2])}|${m[3]}">${up ? '▲' : '▼'}${money(m[1])}<s>${day(m[0])}</s></i>`;
      }
      return out + '</td>';
    };
    const others = Object.keys(r.p).filter(b => !cs.includes(b)).length;
    return `<tr data-k="${esc(r.key)}"><th scope="row"><button type="button" class="pn" dir="auto" aria-expanded="false">${esc(r.name)}</button><span class="bc">${esc(r.key)}</span></th>`
      + cs.map(cell).join('') + (others ? `<td class="more">${others}</td>` : '<td class="more na"></td>') + '</tr>';
  }

  function initTools(signedIn) {
    const box = $('#tools');
    if (!box) return;
    if (!signedIn) {
      box.innerHTML = `<a class="lock" href="/app">${SEARCH_ICON}${both('חיפוש, קטגוריות ומיון', 'Search, categories and sorting')}<b>${both('כניסה', 'Sign in')}</b></a>`;
      return;
    }
    const tbody = $('#rows'), pager = $('#pager');
    const orig = { rows: tbody.innerHTML, pager: pager.innerHTML };
    const st = { q: '', cat: '', sort: 'popular', moved: false, page: 1 };
    const SORTS = [['popular', 'הנמכרים ביותר', 'Most stores'], ['rises', 'עליות מחיר', 'Price rises'], ['drops', 'ירידות מחיר', 'Price drops'],
      ['recent', 'השתנו לאחרונה', 'Changed lately'], ['gap', 'פער בין רשתות', 'Gap between chains']];
    let cats = data?.cats || [], seq = 0, timer;
    box.innerHTML = `<input type="search" id="q" enterkeyhint="search" autocomplete="off" maxlength="200">
      <select id="cat"></select><select id="sort"></select>
      <label><input type="checkbox" id="moved"><span></span></label><span class="count" id="count"></span>`;
    const q = $('#q'), cat = $('#cat'), sort = $('#sort'), moved = $('#moved');
    function relabel() {
      q.placeholder = L('חיפוש מוצר או ברקוד', 'Search a product or barcode');
      q.setAttribute('aria-label', q.placeholder);
      cat.innerHTML = `<option value="">${esc(L('כל הקטגוריות', 'All categories'))}</option>` + cats.map(c => `<option value="${esc(c.slug)}">${esc(L(c.he, c.name))}</option>`).join('');
      cat.value = st.cat;
      sort.innerHTML = SORTS.map(([v, h, e]) => `<option value="${v}">${esc(L(h, e))}</option>`).join('');
      sort.value = st.sort;
      cat.setAttribute('aria-label', L('קטגוריה', 'Category'));
      sort.setAttribute('aria-label', L('מיון', 'Sort'));
      moved.nextElementSibling.textContent = L('רק מה שהשתנה', 'Changed only');
      if (last) count(last);
    }
    let last = null;
    const count = out => { $('#count').textContent = L(`${out.total.toLocaleString('he-IL')} מוצרים`, `${out.total.toLocaleString('en-GB')} products`); };
    const plain = () => !st.q.trim() && !st.cat && st.sort === 'popular' && !st.moved;
    async function run() {
      if (plain() && st.page === 1) {
        tbody.innerHTML = orig.rows; pager.innerHTML = orig.pager; tipify(tbody); $('#count').textContent = ''; last = null;
        return;
      }
      const my = ++seq;
      const qs = new URLSearchParams({ q: st.q, cat: st.cat, sort: st.sort, page: st.page });
      if (st.moved) qs.set('moved', 'true');
      let out;
      try {
        const res = await fetch(`/api/prices?${qs}`, { credentials: 'same-origin', headers: { 'X-Sal-V': window.Usage?.ids?.() || '' } });
        if (!res.ok) throw new Error(res.status);
        out = await res.json();
      } catch { return; }
      if (my !== seq) return;
      last = out;
      if (!cats.length && out.cats) { cats = out.cats; relabel(); }
      const cs = cols();
      for (const r of out.rows) { rows[r.key] = { p: r.p, mv: r.mv }; Object.keys(r.p).forEach(b => { he[b] = he[b] || b; }); }
      tbody.innerHTML = out.rows.length ? out.rows.map(r => rowHtml(r, cs)).join('')
        : `<tr><td colspan="${cs.length + 2}" class="na" style="font-family:var(--ui);font-size:15px;color:var(--muted)">${esc(L('לא נמצאו מוצרים', 'No products found'))}</td></tr>`;
      count(out);
      pager.innerHTML = out.pages > 1 ? `<nav class="pager"><button type="button" class="step" data-go="${out.page - 1}" ${out.page <= 1 ? 'disabled' : ''} aria-label="Previous">‹</button>
        <span class="cur gap">${out.page} / ${out.pages}</span>
        <button type="button" class="step" data-go="${out.page + 1}" ${out.page >= out.pages ? 'disabled' : ''} aria-label="Next">›</button></nav>` : '';
      if (st.q.trim()) window.Usage?.results?.(st.q.trim().split(/\s+/), out.total);
    }
    pager.addEventListener('click', e => {
      const b = e.target.closest('[data-go]');
      if (!b || plain()) return;
      st.page = +b.dataset.go; run();
      $('#products').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { st.q = q.value; st.page = 1; run(); }, 250); });
    cat.addEventListener('change', () => { st.cat = cat.value; st.page = 1; run(); });
    sort.addEventListener('change', () => { st.sort = sort.value; st.page = 1; run(); });
    moved.addEventListener('change', () => { st.moved = moved.checked; st.page = 1; run(); });
    tools = { relabel };
    relabel();
  }

  // Asking the server who you are only when this browser has used the app (it remembers the open cart): a visitor
  // who never signed in costs the server nothing beyond the page itself, which may come from Cloudflare's cache.
  if ($('#tools')) {
    let hint = null;
    try { hint = localStorage.getItem('cart'); } catch { /* private mode */ }
    if (hint) fetch('/api/me', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : null).then(me => initTools(!!me?.user)).catch(() => initTools(false));
    else initTools(false);
  }
})();
