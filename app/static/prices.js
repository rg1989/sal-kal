'use strict';
// Price Watch (/prices, app/board.py). The page as built is the visitor's: the default period's rises and cuts
// across chains, the chains by price level, a few rises, the method. Signed in (one /api/me, asked only when this
// browser has used the app or signed in from here), this fills in the rest: search any product (GET /api/prices),
// every period, and each chain's card with its chart and biggest changes (GET /api/prices/data/chg-<period>.json);
// both need sign-in. A visitor reaching for one of those gets the sign-in sheet saying what it holds; signing in from
// here comes back here (account.js reads 'pw_return'), with the search typed. The search and the period live in the
// address (#q=…&p=…), so a view can be linked to. The language switch is site.js (shared with the landing page).
(() => {
  const $ = (s, el = document) => el.querySelector(s), $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const blob = (() => { try { return JSON.parse($('#board-data')?.textContent || 'null'); } catch { return null; } })();
  if (!blob) return;

  // ---------------------------------------------------------------- words and numbers
  const lang = () => (document.documentElement.lang === 'en' ? 'en' : 'he');
  const L = (he, en) => (lang() === 'he' ? he : en);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const both = (he, en) => `<span lang="he">${esc(he)}</span><span lang="en">${esc(en)}</span>`;
  const int = n => Math.round(n).toLocaleString('en-US');
  const money = v => Number(v).toFixed(2);
  const rng = (lo, hi) => (hi - lo < 0.005 ? money(lo) : `${money(lo)}–${money(hi)}`);
  const day = d => `${d % 100}.${Math.floor(d / 100) % 100}`;
  const dayY = d => `${day(d)}.${Math.floor(d / 10000)}`;
  const span = (a, b) => (Math.floor(a / 10000) === Math.floor(b / 10000) ? `${day(a)} – ${dayY(b)}` : `${dayY(a)} – ${dayY(b)}`);
  const chainHe = b => blob.he[b] || b;
  const chainBoth = b => both(chainHe(b), b);
  const pctTxt = x => { const a = Math.abs(x) * 100; return a >= 9.5 ? `${a.toFixed(0)}%` : `${a.toFixed(1)}%`.replace('.0%', '%'); };
  const levelWords = x => (Math.abs(x) < 0.005 ? ['כמו הרשת האמצעית', 'Same as the middle chain']
    : x < 0 ? [`${pctTxt(x)} זולה מהרשת האמצעית`, `${pctTxt(x)} cheaper than the middle chain`]
      : [`${pctTxt(x)} יקרה מהרשת האמצעית`, `${pctTxt(x)} more expensive than the middle chain`]);
  const store = { get: k => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
    del: k => { try { localStorage.removeItem(k); } catch { /* private mode */ } } };
  const chg = (lo, hi) => {  // +12%, or +5–12% when the branches' changes differ
    const f = x => (x >= 0.0095 ? (x * 100).toFixed(0) : (x * 100).toFixed(1));
    const [a, b] = [Math.abs(lo), Math.abs(hi)].sort((x, y) => x - y), s = hi > 0 ? '+' : '−';
    return f(a) === f(b) ? `${s}${f(b)}%` : `${s}${f(a)}–${f(b)}%`;
  };
  // "at X of Y branches": "at least X" when only a lower bound is known (changes from before branches were told apart)
  const branchesAt = (lo, hi, y) => {
    const of = y && y >= lo;
    return lo < hi
      ? both(`לפחות ב־${int(lo)}${of ? ` מתוך ${int(y)}` : ''} סניפים`, `at least ${int(lo)}${of ? ` of ${int(y)}` : ''} branches`)
      : both(`ב־${int(lo)}${of ? ` מתוך ${int(y)}` : ''} סניפים`, `at ${int(lo)}${of ? ` of ${int(y)}` : ''} branches`);
  };

  // ---------------------------------------------------------------- tooltip (charts)
  let tip;
  function showTip(html, x, y) {
    tip = tip || document.body.appendChild(Object.assign(document.createElement('div'), { className: 'tip', role: 'tooltip' }));
    tip.innerHTML = html;
    tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, x + 14))}px`;
    tip.style.top = `${Math.max(8, y - h - 12 < 60 ? y + 16 : y - h - 12)}px`;
  }
  const hideTip = () => { if (tip) tip.hidden = true; };
  addEventListener('scroll', hideTip, { passive: true, capture: true });

  // ---------------------------------------------------------------- charts: rises above the line, cuts below it
  function ticks(hi) {
    if (hi <= 0) return [0];
    const raw = hi / 2, mag = 10 ** Math.floor(Math.log10(raw)), step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
    const out = [];
    for (let v = step; v < hi + step; v += step) out.push(+v.toFixed(6));
    return out;
  }
  const when = (B, i) => (B.step === 1 ? dayY(B.ends[i]) : span(B.starts[i], B.ends[i]));
  // one column per day (or week): s = {up: [], dn: []}; mini = a chain card's small chart (no axes, its own scale)
  function columns(el, B, s, mini) {
    const W = Math.max(240, el.clientWidth || 600), H = mini ? 64 : Math.max(180, Math.min(240, Math.round(W * 0.34)));
    const P = mini ? { l: 0, r: 34, t: 6, b: 6 } : { l: 44, r: 10, t: 12, b: 26 };
    const n = B.ends.length, upMax = Math.max(0, ...s.up), dnMax = Math.max(0, ...s.dn);
    const tu = mini ? [upMax] : ticks(upMax), td = mini ? [dnMax] : ticks(dnMax);
    const top = Math.max(upMax, tu.at(-1) || 0), bot = Math.max(dnMax, td.at(-1) || 0), all = top + bot || 1;
    const ph = H - P.t - P.b, z = P.t + ph * top / all;
    const y = v => z - ph * v / all;
    const slot = (W - P.l - P.r) / n, bw = Math.max(2, Math.min(24, slot - 2)), x = i => P.l + slot * (i + 0.5);
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true">`;
    if (!mini) {
      for (const v of tu) if (v) svg += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${P.l - 6}" y="${y(v) + 4}" text-anchor="end">${int(v)}</text>`;
      for (const v of td) if (v) svg += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${y(-v)}" y2="${y(-v)}"/><text x="${P.l - 6}" y="${y(-v) + 4}" text-anchor="end">${int(v)}</text>`;
      const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((W - P.l - P.r) / 64))));
      for (let i = n - 1; i >= 0; i -= every) svg += `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${day(B.ends[i])}</text>`;
    } else {
      // its own scale: the busiest day's rises and cuts, as ticks
      if (upMax) svg += `<line class="grid" x1="0" x2="${W - P.r}" y1="${y(upMax)}" y2="${y(upMax)}"/><text x="${W - P.r + 6}" y="${y(upMax) + 4}">${int(upMax)}</text>`;
      if (dnMax) svg += `<line class="grid" x1="0" x2="${W - P.r}" y1="${y(-dnMax)}" y2="${y(-dnMax)}"/><text x="${W - P.r + 6}" y="${y(-dnMax) + 4}">${int(dnMax)}</text>`;
    }
    const r = Math.min(4, bw / 2);
    // 4px round at the data end, square at the baseline; 1px off the line each side, so a rise and a cut never touch
    const col = (i, v, up) => {
      if (!v) return '';
      const k = up ? 1 : -1, z0 = z - k, h = Math.max(2, Math.abs(y(up ? v : -v) - z) - 1), x0 = x(i) - bw / 2, rr = Math.min(r, h), e = z0 - k * h;
      return `<path class="${up ? 'ru' : 'fd'}" d="M${x0} ${z0}V${e + k * rr}Q${x0} ${e} ${x0 + rr} ${e}H${x0 + bw - rr}Q${x0 + bw} ${e} ${x0 + bw} ${e + k * rr}V${z0}Z"/>`;
    };
    for (let i = 0; i < n; i++) {
      if (B.holes[i]) { svg += `<rect class="hole" x="${x(i) - bw / 2}" y="${z - 2}" width="${bw}" height="4" rx="2"/>`; continue; }
      svg += col(i, s.up[i], true) + col(i, s.dn[i], false);
    }
    svg += `<line class="zero" x1="${P.l}" x2="${W - P.r}" y1="${z}" y2="${z}"/>`;
    for (let i = 0; i < n; i++) svg += `<rect class="hit" data-i="${i}" x="${x(i) - slot / 2}" y="0" width="${slot}" height="${H}"/>`;
    el.innerHTML = svg + '</svg>';
    const move = e => {
      const t = e.target.closest?.('.hit');
      if (!t) return;
      const i = +t.dataset.i;
      $$('.hit', el).forEach(h => h.classList.toggle('on', h === t));
      showTip(B.holes[i] ? `<div class="td">${esc(when(B, i))}</div><div>${esc(L('אין נתונים לימים האלה', 'No data for these days'))}</div>`
        : `<div class="td">${esc(when(B, i))}</div>
          <div class="tr"><i class="ru"></i><b>${int(s.up[i])}</b><span>${esc(mini ? L('מוצרים שהמחיר שלהם עלה', 'products raised') : L('העלאות מחיר', 'price rises'))}</span></div>
          <div class="tr"><i class="fd"></i><b>${int(s.dn[i])}</b><span>${esc(mini ? L('מוצרים שהמחיר שלהם ירד', 'products cut') : L('הורדות מחיר', 'price cuts'))}</span></div>`, e.clientX, e.clientY);
    };
    el.onpointermove = move;
    el.onpointerdown = move;
    el.onpointerleave = () => { $$('.hit', el).forEach(h => h.classList.remove('on')); hideTip(); };
  }
  function table(B, s, caption, hidden) {
    const rows = B.ends.map((_, i) => i).reverse().map(i => `<tr><th scope="row"><bdi dir="ltr">${esc(when(B, i))}</bdi></th>${B.holes[i]
      ? `<td colspan="2">${esc(L('אין נתונים', 'No data'))}</td>` : `<td>${int(s.up[i])}</td><td>${int(s.dn[i])}</td>`}</tr>`).join('');
    return `<div class="dt-wrap${hidden ? ' sr-only' : ''}"><table class="dt"><caption class="sr-only">${esc(caption)}</caption><thead><tr>
      <th scope="col">${esc(B.step === 1 ? L('יום', 'Day') : L('שבוע', 'Week'))}</th><th scope="col">${esc(L('העלאות', 'Rises'))}</th><th scope="col">${esc(L('הורדות', 'Cuts'))}</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  }

  // ---------------------------------------------------------------- who is looking: a visitor, or signed in
  // member: null while asking, then true / false. Asked only when this browser has a sign-in to find (it used the
  // app, or signed in from here): a visitor who never did costs nothing beyond the page (it may come from a cache).
  let member = null;
  const root = document.documentElement;
  // 'pw_return': the sign-in this page sent you to, for half an hour (account.js brings you back and clears it)
  const returning = () => {
    try { const r = JSON.parse(store.get('pw_return') || 'null'); if (r && Date.now() - r.t < 30 * 60e3) return true; } catch { /* bad value */ }
    store.del('pw_return');
    return false;
  };
  const hinted = () => !!(window.Site?.usedApp() || store.get('pw_member') || returning());
  async function who() {
    let m = null;
    if (hinted() && window.Site) m = await Site.me();
    member = !!m?.user;
    if (member) store.set('pw_member', '1'); else store.del('pw_member');
    root.classList.remove('pw-check');
    root.classList.toggle('member', member);
    if (member) {  // the bar's button opens the app (site.js does the same when this browser used the app)
      $$('.bar [data-open-app]').forEach(a => { a.innerHTML = both('לסל קל', 'Open Sal-Kal'); });
    }
    if (member) await becomeMember();
    else guestSearch();
  }
  const usage = (k, f) => { try { if (typeof Usage !== 'undefined') Usage.ev(k, f); } catch { /* stats are optional */ } };

  // ---------------------------------------------------------------- the period
  const periods = (blob.periods || []).map(x => x.p);
  const cache = {};
  let B = blob.body, cur = blob.default, tableOpen = false;
  const load = p => (cache[p] = cache[p] || fetch(`/api/prices/data/chg-${p}.json?v=${blob.v}`, { credentials: 'same-origin' })
    .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); }).catch(e => { delete cache[p]; throw e; }));
  const hasLists = body => Object.values(body.chains).some(x => x.tu);

  function drawAll() {
    if (!B) return;
    const range = $('#range');
    if (range) range.textContent = span(B.from, B.to);
    $$('.seg [data-p]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.p === cur)));
    const a = B.all, plot = $('#all-plot');
    $('#all-cnt').innerHTML = both(`${int(a.nup)} העלאות מחיר · ${int(a.ndn)} הורדות`, `${int(a.nup)} price rises · ${int(a.ndn)} cuts`);
    if (plot) columns(plot, B, a, false);
    const caption = L('העלאות והורדות מחיר בכל הרשתות', 'Price rises and cuts, all chains');
    $('#all-table').innerHTML = table(B, a, caption, false);
    $('#all-table').hidden = !tableOpen;
    const tg = $('#tbl-toggle');
    tg.hidden = false;
    tg.setAttribute('aria-expanded', String(tableOpen));
    tg.innerHTML = tableOpen ? both('הסתרת הטבלה', 'Hide the table') : both('כטבלה', 'As a table');
    for (const card of $$('.chain')) {
      const b = card.dataset.chain, x = B.chains[b] || { up: B.ends.map(() => 0), dn: B.ends.map(() => 0), nup: 0, ndn: 0 };
      const mini = $('.mini', card);
      columns(mini, B, x, true);
      mini.insertAdjacentHTML('beforeend', table(B, x, `${L(chainHe(b), b)}: ${L('מוצרים שהמחיר שלהם עלה וירד', 'products raised and cut')}`, true));
      $('.cnt', card).innerHTML = both(`${x.nup === 1 ? 'העלתה מוצר אחד' : `העלתה ${int(x.nup)} מוצרים`} · הורידה ${int(x.ndn)}`,
        `${x.nup === 1 ? 'Raised 1 product' : `Raised ${int(x.nup)} products`} · cut ${int(x.ndn)}`);
      if (x.tu) drawLists(card, x);
    }
  }
  const SHOW = 8;
  function changeLi(it) {
    const [, name, d, wlo, whi, nlo, nhi, lo, hi, y, plo, phi] = it;
    return `<li><span class="nm" dir="auto">${esc(name)}</span><span class="pr"><bdi dir="ltr">${rng(wlo, whi)} → <b>${rng(nlo, nhi)}</b></bdi> <b class="${plo > 0 ? 'up' : 'dn'}"><bdi dir="ltr">${esc(chg(plo, phi))}</bdi></b></span>
      <span class="sub">${branchesAt(lo, hi, y)} · <bdi dir="ltr">${day(d)}</bdi></span></li>`;
  }
  function drawLists(card, x) {
    const box = $('.cols', card);
    if (!x.tu.length && !x.td.length) { box.dataset.p = cur; box.innerHTML = `<p class="none">${both('אין שינויים בתקופה', 'No changes in this period')}</p>`; return; }
    const all = box.dataset.all === '1';
    const col = (he, en, items) => `<div class="col"><h4>${both(he, en)}</h4>${items.length
      ? `<ol class="mvs">${items.slice(0, all ? items.length : SHOW).map(changeLi).join('')}</ol>` : `<p class="none">${both('אין', 'None')}</p>`}</div>`;
    box.dataset.p = cur;
    const more = !all && (x.tu.length > SHOW || x.td.length > SHOW);
    box.innerHTML = col('העלאות גדולות', 'Biggest rises', x.tu) + col('הורדות גדולות', 'Biggest cuts', x.td)
      + (more ? `<button type="button" class="more" data-more>${both('עוד', 'More')}</button>` : '');
  }
  async function choose(p) {
    if (member === false && p !== cur) gate('period');
    if (!member) return;
    if (!periods.includes(p) || (p === cur && B && hasLists(B))) return;
    cur = p;
    document.body.classList.add('busy');
    try {
      B = await load(p);
    } catch {
      document.body.classList.remove('busy');
      return;
    }
    document.body.classList.remove('busy');
    $$('.cols').forEach(c => { delete c.dataset.all; });
    drawAll();
    writeHash();
  }

  // ---------------------------------------------------------------- search
  const input = $('#q'), results = $('#results');
  let seq = 0, timer, lastQ = '';
  const SHOW_CHAINS = 8;
  function resultHtml(r, open) {
    const ps = Object.entries(r.p).sort((a, b) => a[1][0] - b[1][0] || a[1][1] - b[1][1]);
    const lo = ps[0][1][0], hi = Math.max(...ps.map(p => p[1][1]));
    const evs = {};
    for (const e of r.e || []) (evs[e[0]] = evs[e[0]] || []).push(e);
    const row = ([b, [plo, phi, n]], i) => `<li class="rr${plo - lo < 0.005 ? ' lo' : ''}"${i >= SHOW_CHAINS ? ' hidden' : ''}>
      <span class="cn">${chainBoth(b)}</span><span class="pz"><bdi dir="ltr"><b>${money(plo)}</b>${phi - plo >= 0.005 ? `–${money(phi)}` : ''}</bdi></span>
      <span class="nb">${n === 1 ? both('בסניף אחד', 'at 1 branch') : both(`ב־${int(n)} סניפים`, `at ${int(n)} branches`)}</span>
      ${evs[b] ? `<ul class="ev">${evs[b].map(e => `<li class="${e[2] > 0 ? 'up' : 'dn'}"><i aria-hidden="true">${e[2] > 0 ? '▲' : '▼'}</i><span class="sr-only">${esc(e[2] > 0 ? L('עלייה', 'Rise') : L('ירידה', 'Cut'))}</span><bdi dir="ltr">${day(e[1])}</bdi> · <bdi dir="ltr">${rng(e[3], e[4])} → ${rng(e[5], e[6])}</bdi> · ${branchesAt(e[7], e[8], e[9])}</li>`).join('')}</ul>` : ''}</li>`;
    return `<li><details class="res"${open ? ' open' : ''}><summary><span class="nm" dir="auto">${esc(r.n)}</span>
      <span class="sm">${both(`${ps.length} רשתות`, `${ps.length} chains`)}<b><bdi dir="ltr">${rng(lo, hi)}</bdi></b></span>
      <span class="bc">${esc(r.k)}</span></summary>
      <ul class="rp">${ps.map(row).join('')}</ul>${ps.length > SHOW_CHAINS ? `<button type="button" class="more" data-chains>${both(`כל ${ps.length} הרשתות`, `All ${ps.length} chains`)}</button>` : ''}</details></li>`;
  }
  // a visitor's search: the sign-in, saying what the results would hold, over a veiled list (nothing is fetched)
  function guestSearch() {
    if (member || !results) return;
    const q = (input?.value || '').trim();
    if (!q) { results.innerHTML = ''; return; }
    const veil = '<li><span class="sk-a"></span><span class="sk-b"></span></li>'.repeat(3);
    results.innerHTML = `<div class="sgate"><div class="veil" aria-hidden="true"><ol class="sk">${veil}</ol></div>
      <div class="sgate-in"><p class="g-k">${LOCK}${both('חינם, עם כניסה', 'Free with sign-in')}</p>
      <p class="sg-t"><bdi dir="auto" class="sg-q">${esc(q.slice(0, 60))}</bdi> ${both('בכל הרשתות', 'at every chain')}</p>
      <p class="sg-f">${both('המחיר בכל רשת · בכמה סניפים · מתי עלה וירד', 'Price at each chain · at how many branches · when it rose and fell')}</p>
      ${SIGNUP}</div></div>`;
  }
  async function search(q, push) {
    const my = ++seq;
    lastQ = q;
    if (push) writeHash();
    if (member === false) { guestSearch(); return; }
    if (member === null) return;  // still asking who you are: who() runs the search once it knows
    if (!q.trim()) { results.innerHTML = ''; return; }
    let out;
    try {
      const r = await fetch(`/api/prices?q=${encodeURIComponent(q.slice(0, 200))}`, { credentials: 'same-origin' });
      if (r.status === 429) { if (my === seq) results.innerHTML = `<p class="r-none">${both('יותר מדי חיפושים, נסו שוב בעוד רגע.', 'Too many searches, try again in a moment.')}</p>`; return; }
      out = await r.json();
    } catch {
      if (my === seq) results.innerHTML = `<p class="r-none">${both('החיפוש נכשל. נסו שוב.', 'The search failed. Try again.')}</p>`;
      return;
    }
    if (my !== seq) return;
    if (!out.rows.length) {
      results.innerHTML = `<p class="r-none">${both('לא נמצאו מוצרים שנמכרים בשתי רשתות או יותר.', 'No products sold at two chains or more were found.')}</p>`;
      return;
    }
    const head = out.total > out.rows.length
      ? both(`${int(out.rows.length)} מתוך ${int(out.total)} מוצרים · אפשר לחדד את החיפוש`, `${int(out.rows.length)} of ${int(out.total)} products · narrow the search`)
      : both(out.total === 1 ? 'מוצר אחד' : `${int(out.total)} מוצרים`, out.total === 1 ? '1 product' : `${int(out.total)} products`);
    results.innerHTML = `<p class="r-head">${head}</p><ol class="r-list">${out.rows.map((r, i) => resultHtml(r, out.rows.length <= 2 && i === 0)).join('')}</ol>`;
  }
  if (input) {
    input.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => search(input.value, true), 220);
    });
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { clearTimeout(timer); search(input.value, true); input.blur(); }
      if (e.key === 'Escape' && input.value) { input.value = ''; search('', true); }
    });
  }
  const placeholder = () => { if (input) input.placeholder = input.dataset[lang() === 'he' ? 'phHe' : 'phEn'] || ''; };

  // ---------------------------------------------------------------- the address: #q=…&p=…
  function writeHash() {
    const h = new URLSearchParams();
    if (lastQ.trim()) h.set('q', lastQ.trim().slice(0, 200));
    if (cur && cur !== blob.default) h.set('p', cur);
    const s = h.toString();
    history.replaceState(null, '', s ? `#${s}` : location.pathname);
  }
  function readHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    const q = (h.get('q') || '').slice(0, 200), p = h.get('p');
    if (input && q && q !== input.value) { input.value = q; search(q, false); }
    if (p && periods.includes(p) && p !== cur && member) choose(p);
  }

  // ---------------------------------------------------------------- signed in: each chain's card, every period
  function cardHtml([b, level, n, branches], span) {
    const [lh, le] = levelWords(level), cls = level < -0.005 ? 'dn' : level > 0.005 ? 'up' : '';
    const f = span ? Math.min(1, Math.abs(level) / span) : 0;
    return `<li class="chain card" data-chain="${esc(b)}"><div class="ch-head"><h3>${chainBoth(b)}</h3>
      <span class="ch-br">${both(`${int(branches)} סניפים`, `${int(branches)} branches`)}</span></div>
      <p class="lvl"><b class="${cls}">${both(lh, le)}</b> <span>${both(`לפי ${int(n)} מוצרים`, `over ${int(n)} products`)}</span></p>
      <div class="lbar" aria-hidden="true"><span class="${level < 0 ? 'neg' : 'pos'}" style="--f:${f.toFixed(3)}"></span></div>
      <div class="mini" data-chain="${esc(b)}"></div><p class="cnt"></p>
      <details class="big"><summary>${both('השינויים הגדולים', 'Biggest changes')}</summary><div class="cols"></div></details></li>`;
  }
  async function becomeMember() {
    results.innerHTML = '';
    const rank = blob.rank || [], span = Math.max(0, ...rank.map(r => Math.abs(r[1]))), box = $('#member-chains');
    if (box) box.innerHTML = rank.map(r => cardHtml(r, span)).join('');
    const h = new URLSearchParams(location.hash.slice(1)), p = h.get('p');
    const want = p && periods.includes(p) ? p : cur;
    try {
      B = await load(want);
      cur = want;
    } catch {
      // the period's lists didn't come (signed out meanwhile, or offline): the page stays as a visitor's
      member = false;
      root.classList.remove('member');
      store.del('pw_member');
      if (box) box.innerHTML = '';
      guestSearch();
      return;
    }
    drawAll();
    writeHash();
    if (input?.value.trim()) search(input.value, false);
  }

  // ---------------------------------------------------------------- a visitor: the sign-in sheet
  const LOCK = $('.find-lk')?.innerHTML || '';
  const SIGNUP = `<a class="btn primary signup" href="/app" data-signin>${both('הרשמה חינם', 'Sign up free')}</a>`;
  const sheet = $('#gate');
  const TITLES = {
    search: () => both('חיפוש כל מוצר', 'Search any product'),
    period: () => both('כל התקופות', 'Every period'),
    items: () => both('השינויים הגדולים של כל רשת', "Every chain's biggest changes"),
  };
  function gate(kind, chain) {
    if (!sheet?.showModal) { location.href = '/app'; return; }
    $('#gate-t').innerHTML = chain ? `${chainBoth(chain)}${both(': יום אחר יום', ', day by day')}` : (TITLES[kind] || TITLES.items)();
    const mark = kind === 'chain' ? ['chain', 'items'] : kind === 'period' ? ['period'] : kind === 'search' ? ['search'] : ['items'];
    $$('.g-list li', sheet).forEach(li => li.classList.toggle('on', mark.includes(li.dataset.f)));
    sheet.showModal();
  }
  sheet?.addEventListener('click', e => {  // the ×, or the backdrop (a click on the sheet itself, outside its box)
    const r = sheet.getBoundingClientRect();
    const out = e.target === sheet && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom);
    if (out || e.target.closest('[data-close]')) sheet.close();
  });

  // ---------------------------------------------------------------- events
  document.addEventListener('click', e => {
    const signin = e.target.closest('[data-signin]');
    if (signin) {  // come back here after signing in, with what was typed (account.js)
      const h = new URLSearchParams();
      if (input?.value.trim()) h.set('q', input.value.trim().slice(0, 200));
      store.set('pw_return', JSON.stringify({ t: Date.now(), h: h.toString() ? `#${h}` : '' }));
      usage('signin_click', { a: 'prices' });
      return;
    }
    if (member === false) {
      const g = e.target.closest('[data-gate]');
      if (g) { gate(g.dataset.gate, g.dataset.chain); return; }
      if (e.target.closest('.find-lk')) { gate('search'); return; }
    }
    const rkMore = e.target.closest('.rk-more');
    if (rkMore) {  // the ranking's folded rows (on the page already: no sign-in)
      const list = $('.rks', rkMore.closest('.rank'));
      rkMore.dataset.label = rkMore.dataset.label || rkMore.innerHTML;
      const open = !list.classList.toggle('cut');
      rkMore.setAttribute('aria-expanded', String(open));
      rkMore.innerHTML = open ? both('פחות', 'Fewer') : rkMore.dataset.label;
      return;
    }
    const per = e.target.closest('.seg [data-p]');
    if (per) { choose(per.dataset.p); try { navigator.vibrate?.(8); } catch { /* not allowed yet */ } return; }
    if (e.target.closest('#tbl-toggle')) { tableOpen = !tableOpen; drawAll(); return; }
    const more = e.target.closest('[data-more]');
    if (more) {
      const card = more.closest('.chain'), box = $('.cols', card);
      box.dataset.all = '1';
      const x = B.chains[card.dataset.chain];
      if (x?.tu) drawLists(card, x);
      return;
    }
    const allChains = e.target.closest('[data-chains]');
    if (allChains) { $$('.rr[hidden]', allChains.closest('.res')).forEach(li => { li.hidden = false; }); allChains.remove(); }
  });
  // a chain's biggest changes: the default period's are in the page; another period's come with its data file
  document.addEventListener('toggle', e => {
    const d = e.target;
    if (!d.classList?.contains('big') || !d.open || !B) return;
    const card = d.closest('.chain'), p = cur;
    load(p).then(body => {
      if (p !== cur) return;
      B = body;
      const x = B.chains[card.dataset.chain];
      if (x?.tu && $('.cols', card).dataset.p !== p) drawLists(card, x);
    }).catch(() => {});
  }, true);
  document.addEventListener('langchange', () => { placeholder(); hideTip(); drawAll(); if (member === false) guestSearch(); });
  let w = innerWidth;
  addEventListener('resize', () => { if (Math.abs(innerWidth - w) > 20) { w = innerWidth; drawAll(); } });
  addEventListener('hashchange', readHash);
  placeholder();
  drawAll();
  readHash();
  who();
})();
