'use strict';
// The owner's admin page: usage statistics from app/stats.py. Everything visitors sent (searches, labels, error
// messages, names) is untrusted: it only ever reaches the page through esc() or textContent.

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = n => n == null ? '—' : Number(n).toLocaleString('en-US', { maximumFractionDigits: 1 });
const compact = n => n == null ? '—' : Math.abs(n) >= 10000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1).replace(/\.0$/, '') + 'K' : num(n);
const pct = (a, b) => b ? `${Math.round(a / b * 100)}%` : '—';
const bytes = n => n == null ? '—' : n >= 1e9 ? (n / 1e9).toFixed(1) + ' GB' : n >= 1e6 ? (n / 1e6).toFixed(1) + ' MB' : Math.round(n / 1e3) + ' KB';
const dur = s => s == null ? '—' : s < 1 ? '<1 s' : s < 60 ? `${Math.round(s)} s` : s < 3600 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${Math.floor(s / 3600)} h ${Math.round(s % 3600 / 60)} min`;
const ms = v => v == null ? '—' : v >= 1000 ? (v / 1000).toFixed(1) + ' s' : Math.round(v) + ' ms';
const dayDate = d => new Date(+String(d).slice(0, 4), +String(d).slice(4, 6) - 1, +String(d).slice(6, 8));
const dayLabel = d => dayDate(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
const tsDate = t => new Date(t * 1000);
function when(t) {
  if (!t) return '—';
  const d = tsDate(t), s = Date.now() / 1000 - t;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  const hm = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  const today = new Date(); today.setHours(0, 0, 0, 0);
  if (d >= today) return `today ${hm}`;
  if (d >= today - 86400000) return `yesterday ${hm}`;
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', ...(d.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}) }) + ` ${hm}`;
}
const flag = c => c && /^[A-Z]{2}$/.test(c) && c !== 'XX' && c !== 'T1' ? String.fromCodePoint(...[...c].map(x => 0x1F1E6 + x.charCodeAt(0) - 65)) + ' ' + c : (c || '—');
const PAGE = { landing: 'Landing page', home: 'Home', browse: 'Browse & search', product: 'Product page', basket: 'Checkout', signin: 'Sign-in page',
               onboard: 'Setup', boot: 'Loading' };
const pageName = p => PAGE[p] || p || '—';
const he = v => `<bdi>${esc(v)}</bdi>`;

// ---------------------------------------------------------------- state, fetching
const ST = { days: 7, mine: false };
try { ST.days = +localStorage.getItem('admin_days') || 7; ST.mine = localStorage.getItem('admin_mine') === '1'; } catch { /* defaults */ }
async function get(name) {
  const sep = name.includes('?') ? '&' : '?';
  const r = await fetch(`/api/admin/${name}${sep}days=${ST.days}&mine=${ST.mine ? 1 : 0}`, { credentials: 'same-origin' });
  if (r.status === 401 || r.status === 404) throw Object.assign(new Error('gone'), { status: r.status });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}
const periodName = () => ST.days === 1 ? 'today' : `the last ${ST.days} days`;
const prevName = () => ST.days === 1 ? 'yesterday' : `the ${ST.days} days before`;

// ---------------------------------------------------------------- tooltips (one, for every chart mark)
let TIPS = [];
const tipId = html => { TIPS.push(html); return TIPS.length - 1; };
const tip = $('#tip');
function showTip(el, x, y) {
  tip.innerHTML = TIPS[+el.dataset.ti] || '';
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.max(8, Math.min(innerWidth - r.width - 8, x + 14)) + 'px';
  tip.style.top = Math.max(8, y - r.height - 12) + 'px';
}
document.addEventListener('pointermove', e => {
  const el = e.target.closest?.('[data-ti]');
  $$('.col.on').forEach(c => c.classList.remove('on'));
  if (!el) { tip.hidden = true; return; }
  el.closest('.colg')?.querySelectorAll('.col').forEach(c => c.classList.add('on'));
  showTip(el, e.clientX, e.clientY);
});
document.addEventListener('focusin', e => {
  const el = e.target.closest?.('[data-ti]');
  if (!el) return;
  const r = el.getBoundingClientRect();
  showTip(el, r.left + r.width / 2, r.top);
});
document.addEventListener('focusout', () => { tip.hidden = true; });
const tipRows = (title, rows) => `<div class="tl">${esc(title)}</div>` + rows.map(([color, label, v]) =>
  `<div class="row">${color ? `<span class="key" style="background:${color}"></span>` : ''}<span class="tv">${esc(v)}</span> <span class="muted">${esc(label)}</span></div>`).join('');

// ---------------------------------------------------------------- building blocks
const card = (title, sub, body, cls = '') => `<section class="card ${cls}"><h2>${esc(title)}</h2>${sub ? `<p class="sub">${esc(sub)}</p>` : ''}${body}</section>`;
const empty = (text = 'Nothing yet') => `<p class="empty">${esc(text)}</p>`;

function kpi(label, v, prev, { f = compact, invert = false, alert = false } = {}) {
  let d = '';
  if (prev != null && v != null) {
    if (!prev && v) d = `<div class="d ${invert ? 'down' : 'up'}">▲ new</div>`;
    else if (prev) {
      const ch = Math.round((v - prev) / prev * 100);
      d = ch === 0 ? '<div class="d">no change</div>'
        : `<div class="d ${(ch > 0) !== invert ? 'up' : 'down'}">${ch > 0 ? '▲' : '▼'} ${Math.abs(ch)}%</div>`;
    } else d = '<div class="d">—</div>';
  }
  return `<div class="kpi${alert ? ' alert' : ''}"><div class="l">${esc(label)}</div><div class="v">${esc(f(v))}</div>${d}</div>`;
}

/** rows: [{k (HTML, already escaped), v, sub?}] → label · bar · value */
function bars(rows, { color = 'var(--s1)', fmt = num, total } = {}) {
  if (!rows.length) return empty();
  const max = Math.max(...rows.map(r => r.v || 0), 1), sum = total ?? rows.reduce((s, r) => s + (r.v || 0), 0);
  return `<div class="bars">${rows.map(r => `<span class="k" title="${esc(r.title || '')}">${r.k}</span>
    <span class="track"><span class="fill" style="width:${(r.v || 0) / max * 100}%;background:${color}"></span></span>
    <span class="v">${esc(fmt(r.v))}<small>${r.sub != null ? esc(r.sub) : pct(r.v || 0, sum)}</small></span>`).join('')}</div>`;
}

/** cols: [{h, f(row) → HTML, n?: numeric column}] */
function table(cols, rows, { click, limit = 0 } = {}) {
  if (!rows.length) return empty();
  const shown = limit ? rows.slice(0, limit) : rows;
  return `<div class="tw"><table><thead><tr>${cols.map(c => `<th class="${c.n ? 'n' : ''}">${esc(c.h)}</th>`).join('')}</tr></thead><tbody>
    ${shown.map(r => `<tr${click ? ` class="click" data-go="${esc(click(r))}" tabindex="0"` : ''}>${cols.map(c => `<td class="${c.n ? 'n' : c.cls || ''}">${c.f(r)}</td>`).join('')}</tr>`).join('')}
  </tbody></table></div>`;
}
document.addEventListener('click', e => { const tr = e.target.closest('tr[data-go]'); if (tr) location.hash = tr.dataset.go; });
document.addEventListener('change', e => { if (e.target.matches('[data-minor]')) e.target.closest('.card').querySelector('.timeline').classList.toggle('hide-minor', !e.target.checked); });
document.addEventListener('keydown', e => { const tr = e.target.closest?.('tr[data-go]'); if (tr && e.key === 'Enter') location.hash = tr.dataset.go; });

const niceMax = v => { const p = 10 ** Math.floor(Math.log10(v)), m = v / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p; };
const topRound = (x, y, w, h, r) => h <= 0 ? '' : h < r
  ? `M${x},${y + h}V${y}H${x + w}V${y + h}Z`
  : `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;

/** Columns per day, stacked by series. points: [{label, v: {key: n}}]; series: [{key, label, color}] */
function cardWidth(minCol = 560) {  // the drawing is made at the size it's shown, so its text stays 11 px
  const avail = Math.min(innerWidth, 1320) - 32, cols = Math.max(1, Math.floor((avail + 16) / (minCol + 16)));
  return Math.max(260, Math.round((avail - 16 * (cols - 1)) / cols - 34));
}
function columns(points, series, { height = 190, title = '' } = {}) {
  const W = cardWidth(), H = height, L = 40, B = 24, T = 10;
  const tot = points.map(p => series.reduce((s, x) => s + (p.v[x.key] || 0), 0));
  const max = Math.max(4, niceMax(Math.max(1, ...tot))), band = (W - L) / Math.max(points.length, 1), bw = Math.min(24, band * 0.62);
  const y = v => T + (H - T - B) * (1 - v / max);
  let svg = '';
  for (let i = 0; i <= 4; i++) {
    const v = max / 4 * i;
    svg += `<line class="gridline" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text class="axis" x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${esc(compact(v))}</text>`;
  }
  const every = Math.ceil(points.length / Math.max(2, Math.floor(W / 64)));
  points.forEach((p, i) => {
    const x = L + i * band + (band - bw) / 2;
    let base = 0, segs = '';
    const parts = series.filter(s => p.v[s.key] > 0);
    parts.forEach((s, j) => {
      const h0 = y(base) - y(base + p.v[s.key]);
      const top = y(base + p.v[s.key]);
      const gap = j ? 2 : 0;
      segs += j === parts.length - 1
        ? `<path class="col" d="${topRound(x, top, bw, Math.max(0, h0 - gap), 4)}" fill="${s.color}"/>`
        : `<rect class="col" x="${x}" y="${top}" width="${bw}" height="${Math.max(0, h0 - gap)}" fill="${s.color}"/>`;
      base += p.v[s.key];
    });
    const ti = tipId(tipRows(p.label, [...series].reverse().map(s => [s.color, s.label, num(p.v[s.key] || 0)])));
    svg += `<g class="colg">${segs}<rect class="hit" x="${L + i * band}" y="${T}" width="${band}" height="${H - T - B}" data-ti="${ti}" tabindex="0" aria-label="${esc(p.label)}"/></g>`;
    if (i % every === 0) svg += `<text class="axis" x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${esc(p.label)}</text>`;
  });
  const legend = series.length > 1 ? `<div class="legend">${series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join('')}</div>` : '';
  const tableView = `<details class="table-view"><summary>Table</summary>${table([{ h: 'Day', f: p => esc(p.label) }, ...series.map(s => ({ h: s.label, n: 1, f: p => num(p.v[s.key] || 0) }))], points)}</details>`;
  return `${legend}<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}">${svg}</svg></div>${tableView}`;
}

function heatmap(heat) {
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const max = Math.max(1, ...heat.flat());
  const shade = n => n ? `color-mix(in srgb, var(--s1) ${Math.round(18 + 82 * n / max)}%, var(--grid))` : 'var(--grid)';
  let h = '<div class="heat"><span></span>' + Array.from({ length: 24 }, (_, i) => `<span class="hl">${i % 3 === 0 ? i : ''}</span>`).join('');
  heat.forEach((row, d) => {
    h += `<span class="dl">${days[d]}</span>` + row.map((n, hr) => `<span class="c" style="background:${shade(n)}" data-ti="${tipId(tipRows(`${days[d]} ${String(hr).padStart(2, '0')}:00–${String(hr + 1).padStart(2, '0')}:00`, [[null, 'visits', num(n)]]))}"></span>`).join('');
  });
  return h + `</div><div class="scale">Fewer <i style="background:${shade(0)}"></i><i style="background:${shade(max * .25)}"></i><i style="background:${shade(max * .5)}"></i><i style="background:${shade(max)}"></i> More · Israel time</div>`;
}

function funnelHTML(steps) {
  if (!steps.length || !steps[0].n) return empty('No new browsers in this period');
  const top = steps[0].n;
  return `<div class="funnel">${steps.map(s => `<div class="fstep"><span>${esc(s.step)}</span>
    <span class="track"><span class="fill" style="width:${s.n / top * 100}%"></span></span>
    <span class="v">${num(s.n)}<small>${pct(s.n, top)}</small></span></div>`).join('')}</div>`;
}

const device = v => [v.device, v.os, v.browser].filter(Boolean).map(esc).join(' · ') || '—';
const avatar = u => u.avatar ? `<img class="av" src="${esc(u.avatar)}" alt="" referrerpolicy="no-referrer" loading="lazy">`
  : `<span class="av">${esc((u.name || u.email || '?').trim().split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase())}</span>`;
const product = r => r.name ? `${he(r.name)} <a class="muted mono" href="/#/p/${encodeURIComponent(r.key)}" target="_blank" rel="noopener">${esc(r.key)}</a>` : `<span class="mono">${esc(r.key)}</span>`;
const outcome = b => b === 'add' ? '<span class="chip good">added</span>' : b === 'open' ? '<span class="chip">opened a product</span>' : '<span class="chip warn">nothing</span>';

// ---------------------------------------------------------------- tabs
const VIEWS = {
  async overview() {
    const d = await get('overview');
    const k = d.kpi, p = d.prev;
    const tiles = [
      kpi('Visitors', k.visitors, p.visitors), kpi('Visits', k.visits, p.visits), kpi('Signed-in people', k.users, p.users),
      kpi('New accounts', k.new_accounts, p.new_accounts), kpi('Page views', k.views, p.views), kpi('Searches', k.searches, p.searches),
      kpi('Products added', k.adds, p.adds), kpi('Checkouts opened', k.checkouts, p.checkouts),
      kpi('Median visit', k.visit_median, p.visit_median, { f: dur }),
      kpi('Bounced visits', k.bounce, p.bounce, { f: v => v == null ? '—' : Math.round(v * 100) + '%', invert: true }),
      kpi('Searches with no results', k.zero_rate, p.zero_rate, { f: v => v == null ? '—' : Math.round(v * 100) + '%', invert: true, alert: k.zero_rate > .2 }),
      kpi('Errors', (k.js_errors || 0) + (k.server_errors || 0), (p.js_errors || 0) + (p.server_errors || 0), { invert: true, alert: (k.js_errors || 0) + (k.server_errors || 0) > 0 }),
    ].join('');
    const pts = d.daily.map(x => ({ label: dayLabel(x.day), v: { signed: x.signed, anon: Math.max(0, x.visitors - x.signed), acc: x.new_accounts } }));
    const live = d.live.length ? table([
      { h: 'Who', f: v => v.uid ? `${he(v.name || `#${v.uid}`)}` : '<span class="muted">Not signed in</span>' },
      { h: 'Now on', f: v => esc(v.here ? pageName(v.here.split(':')[0]) : '—') },
      { h: 'Device', f: device }, { h: 'Where', f: v => esc([flag(v.country), v.city].filter(Boolean).join(' · ')) },
      { h: 'Pages', n: 1, f: v => num(v.views) }, { h: 'For', n: 1, f: v => dur(v.last - v.start) },
    ], d.live) : empty('Nobody in the last 5 minutes');
    return `<p class="note" style="margin:0 0 8px">${esc(ST.days === 1 ? 'Today so far' : `The last ${ST.days} days`)} · change vs ${esc(prevName())}</p><div class="kpis">${tiles}</div><div class="grid wide">
      ${card('Visitors per day', `Unique browsers, ${periodName()}`, columns(pts, [{ key: 'signed', label: 'Signed in', color: 'var(--s1)' }, { key: 'anon', label: 'Not signed in', color: 'var(--s2)' }], { title: 'Visitors per day' }))}
      ${card('New accounts per day', '', columns(pts, [{ key: 'acc', label: 'New accounts', color: 'var(--s1)' }], { height: 150, title: 'New accounts per day' }))}
      ${card('How far new browsers got', `Seen for the first time ${periodName()}; share of them that did each`, funnelHTML(d.funnel))}
      ${card('When people visit', 'Visits by weekday and hour', heatmap(d.heat))}
      ${card('Here now', 'Active in the last 5 minutes', live, 'full')}
    </div>`;
  },

  async stuck() {
    const d = await get('stuck');
    const lab = r => `<span class="mono">${esc(r.label)}</span>${r.zone ? ` <span class="muted">in #${esc(r.zone)}</span>` : ''}`;
    const clicks = rows => table([{ h: 'Page', f: r => esc(pageName(r.page)) }, { h: 'On', cls: 'wrap', f: lab },
      { h: 'Times', n: 1, f: r => num(r.n) }, { h: 'People', n: 1, f: r => num(r.people) }, { h: 'Last', n: 1, f: r => esc(when(r.last)) }], rows);
    const ob = d.onboard.filter(x => x.step !== 'done').sort((a, b) => a.step - b.step), done = d.onboard.find(x => x.step === 'done');
    const obRows = [...ob.map(x => ({ k: `Step ${esc(x.step)} · ${esc(['Address', 'Distance', 'Name the cart'][+x.step - 1] || '')}`, v: x.people })),
                    ...(done ? [{ k: 'Finished', v: done.people }] : [])];
    const first = obRows[0]?.v || 0;
    return `<div class="grid">
      ${card('Rage clicks', 'Tapped 3+ times in a second on the same spot: something looked like it should respond', clicks(d.rage))}
      ${card('Dead clicks', 'Tapped something that looks tappable (a photo, an icon, a label) and nothing happened', clicks(d.dead))}
      ${card('Searches with no results', 'What people want that the catalogue doesn’t find', table([
        { h: 'Search', cls: 'wrap', f: r => he(r.q) }, { h: 'Times', n: 1, f: r => num(r.n) }, { h: 'People', n: 1, f: r => num(r.people) },
        { h: 'Last', n: 1, f: r => esc(when(r.last)) }], d.zero))}
      ${card('Searched again after no results', 'The words they tried next', table([
        { h: 'Tried', cls: 'wrap', f: r => he(r.tried) }, { h: 'Then', cls: 'wrap', f: r => he(r.then_q) },
        { h: 'Results', n: 1, f: r => num(r.then_n) }, { h: 'When', n: 1, f: r => esc(when(r.ts)) }], d.rephrased))}
      ${card('Searches that led nowhere', 'Results shown, but no product opened or added', table([
        { h: 'Search', cls: 'wrap', f: r => he(r.q) }, { h: 'Times', n: 1, f: r => num(r.n) }, { h: 'Results', n: 1, f: r => num(r.results) },
        { h: 'Last', n: 1, f: r => esc(when(r.last)) }], d.nothing))}
      ${card('Setup steps reached', 'First sign-in: how far people get', obRows.length ? bars(obRows, { total: first }) : empty())}
      ${card('Sign-in failures', '', table([{ h: 'With', f: r => esc(r.provider) }, { h: 'Why', cls: 'wrap', f: r => esc(r.reason) },
        { h: 'Times', n: 1, f: r => num(r.n) }, { h: 'Last', n: 1, f: r => esc(when(r.last)) }], d.signin))}
      ${card('Sign-in by browser', 'Started vs finished. Google refuses sign-in inside Facebook’s and Instagram’s own browsers', table([
        { h: 'Browser', f: r => esc(r.browser || '—') }, { h: 'Started', n: 1, f: r => num(r.started) }, { h: 'Signed in', n: 1, f: r => num(r.finished) },
        { h: 'Rate', n: 1, f: r => pct(r.finished, r.started) }], d.inapp))}
      ${card('JavaScript errors', 'From visitors’ browsers', table([{ h: 'Error', cls: 'wrap', f: r => `<span class="mono">${esc(r.message)}</span><br><span class="muted mono">${esc(r.source || '')}</span>` },
        { h: 'Page', f: r => esc(pageName(r.page)) }, { h: 'Times', n: 1, f: r => num(r.n) }, { h: 'People', n: 1, f: r => num(r.people) },
        { h: 'Last', n: 1, f: r => esc(when(r.last)) }], d.errors))}
      ${card('Failed requests', 'As browsers saw them (0 = no connection)', table([{ h: 'Request', cls: 'wrap', f: r => `<span class="mono">${esc(r.path)}</span>` },
        { h: 'Status', n: 1, f: r => `<span class="chip ${r.status >= 500 || !r.status ? 'bad' : 'warn'}">${esc(r.status)}</span>` },
        { h: 'Times', n: 1, f: r => num(r.n) }, { h: 'People', n: 1, f: r => num(r.people) }, { h: 'Last', n: 1, f: r => esc(when(r.last)) }], d.api))}
      ${card('Error messages people saw', '', table([{ h: 'Message', cls: 'wrap', f: r => he(r.message) }, { h: 'Page', f: r => esc(pageName(r.page)) },
        { h: 'Times', n: 1, f: r => num(r.n) }, { h: 'Last', n: 1, f: r => esc(when(r.last)) }], d.toasts))}
      ${card('Server errors', '', table([{ h: 'Route', f: r => `<span class="mono">${esc(r.route)}</span>` }, { h: 'Error', cls: 'wrap', f: r => `<span class="mono">${esc(r.error)}</span>` },
        { h: 'Times', n: 1, f: r => num(r.n) }, { h: 'Last', n: 1, f: r => esc(when(r.last)) }], d.server_errors))}
      ${card('Where visits end', 'The last page of each visit', bars(d.exits.map(x => ({ k: esc(pageName(x.page)), v: x.n }))))}
      ${card('Bounced', 'Left within 10 seconds having seen one page, by where they landed', bars(d.bounces.map(x => ({ k: `<span class="mono">${esc(x.landing || '—')}</span>`, v: x.n }))))}
      ${card('Checkout with no store that has everything', 'Times checkout showed no store with the whole cart', `<div class="kpi" style="border:0;padding:0"><div class="v">${num(d.no_store)}</div></div>`)}
      ${card('Load times', '75th percentile, per device', table([{ h: 'Device', f: r => esc(r.device) }, { h: 'Page loads', n: 1, f: r => num(r.n) },
        { h: 'Loaded', n: 1, f: r => ms(r.load_p75) }, { h: 'Main content', n: 1, f: r => ms(r.lcp_p75) }, { h: 'First byte', n: 1, f: r => ms(r.ttfb_p75) }], d.perf))}
    </div>`;
  },

  async usage() {
    const d = await get('usage');
    const pr = d.prefs || {};
    const kv = rows => bars((rows || []).map(x => ({ k: esc(x.k), v: x.n })));
    const cmp = d.compare || {};
    return `<div class="grid">
      ${card('Pages', '', table([{ h: 'Page', f: r => esc(pageName(r.page)) }, { h: 'Views', n: 1, f: r => num(r.views) },
        { h: 'People', n: 1, f: r => num(r.visitors) }, { h: 'Median time', n: 1, f: r => dur(r.dwell_median / 1000) },
        { h: 'Scrolled', n: 1, f: r => r.depth_avg == null ? '—' : r.depth_avg + '%' }, { h: 'Visits ended here', n: 1, f: r => num(r.exits) }], d.pages), 'full')}
      ${card('Searches', 'Each settled search, with what came of it', table([{ h: 'Search', cls: 'wrap', f: r => he(r.q) },
        { h: 'Times', n: 1, f: r => num(r.n) }, { h: 'People', n: 1, f: r => num(r.people) }, { h: 'Results', n: 1, f: r => num(r.results) },
        { h: 'Added', n: 1, f: r => num(r.adds) }, { h: 'Opened', n: 1, f: r => num(r.opens) }, { h: 'Nothing', n: 1, f: r => num(r.nothing) }], d.searches), 'full')}
      ${card('Products added', '', table([{ h: 'Product', cls: 'wrap', f: product }, { h: 'Times', n: 1, f: r => num(r.n) }, { h: 'People', n: 1, f: r => num(r.people) }], d.added))}
      ${card('Products opened', '', table([{ h: 'Product', cls: 'wrap', f: product }, { h: 'Times', n: 1, f: r => num(r.n) }, { h: 'People', n: 1, f: r => num(r.people) }], d.viewed))}
      ${card('Added from', 'The page people were on when they added', bars(d.added_from.map(x => ({ k: esc(pageName(x.page)), v: x.n }))))}
      ${card('Products removed', '', table([{ h: 'Product', cls: 'wrap', f: product }, { h: 'Times', n: 1, f: r => num(r.n) }], d.removed))}
      ${card('Categories', '', bars(d.categories.map(x => ({ k: esc(x.slug), v: x.views, sub: `${x.people} people` }))))}
      ${card('Filters', 'Narrow-down words, sizes, deals, sort', table([{ h: 'Filter', f: r => esc(r.type) }, { h: 'Value', cls: 'wrap', f: r => he(r.value || '') }, { h: 'Times', n: 1, f: r => num(r.n) }], d.filters))}
      ${card('Checkout', '', `<dl class="facts"><div><dt>Times shown</dt><dd>${num(cmp.n)}</dd></div><div><dt>Items in cart (average)</dt><dd>${num(cmp.items == null ? null : Math.round(cmp.items * 10) / 10)}</dd></div>
        <div><dt>A store had everything</dt><dd>${pct(cmp.any_complete || 0, cmp.n)}</dd></div><div><dt>No store had everything</dt><dd>${num(cmp.none_complete)}</dd></div></dl>
        <p class="section-title" style="margin-top:16px">Cheapest chain</p>${bars(d.best.map(x => ({ k: esc(x.chain), v: x.n })))}`)}
      ${card('Most tapped', 'Buttons and links, by page', table([{ h: 'Page', f: r => esc(pageName(r.page)) },
        { h: 'Control', cls: 'wrap', f: r => `<span class="mono">${esc(r.label)}</span>${r.zone ? ` <span class="muted">in #${esc(r.zone)}</span>` : ''}` },
        { h: 'Times', n: 1, f: r => num(r.n) }, { h: 'People', n: 1, f: r => num(r.people) }], d.clicks, { limit: 40 }), 'full')}
      ${card('Dialogs opened', '', bars(d.dialogs.map(x => ({ k: `<span class="mono">${esc(x.dialog)}</span>`, v: x.n, sub: `${x.people} people` }))))}
      ${card('Carts', '', bars(d.carts.map(x => ({ k: esc({ 'cart_new:': 'Made a cart', 'cart_delete:': 'Deleted a cart' }[x.action] || x.action), v: x.n }))))}
      ${card('Distance setting', `${num(pr.accounts)} accounts · ${num(pr.onboarded)} finished setup`, kv((pr.km || []).map(x => ({ ...x, k: x.k === 'any' ? 'Any' : `${x.k} km` }))))}
      ${card('Shopping mode', '', kv((pr.mode || []).map(x => ({ ...x, k: x.k === 'delivery' ? 'Delivered home' : 'In store' }))))}
      ${card('Club prices', '', kv(pr.club))}
      ${card('Language', '', kv((pr.lang || []).map(x => ({ ...x, k: x.k === 'he' ? 'Hebrew' : x.k === 'en' ? 'English' : x.k }))))}
      ${card('Towns', 'From accounts’ addresses', bars((pr.towns || []).map(x => ({ k: he(x.k), v: x.n }))))}
    </div>`;
  },

  async audience() {
    const d = await get('audience');
    const b = (rows, label = x => esc(x || '—')) => bars(rows.map(x => ({ k: label(x.k), v: x.visits, sub: `${x.people} people` })));
    return `<div class="grid">
      ${card('New and returning', `Browsers ${periodName()}`, bars([{ k: 'New', v: d.new }, { k: 'Came before', v: d.returning }]))}
      ${card('Device', '', b(d.device))}
      ${card('Operating system', '', b(d.os))}
      ${card('Browser', '', b(d.browser))}
      ${card('In-app browser', 'Opened from inside another app', b(d.inapp, k => esc(k || 'None (a real browser)')))}
      ${card('Country', 'From Cloudflare', b(d.country, k => esc(flag(k))))}
      ${card('City', 'Needs Cloudflare’s visitor location headers', d.city.some(x => x.k) ? b(d.city, k => esc(k || 'Unknown')) : empty('No city data: turn on “Add visitor location headers” in Cloudflare → Rules → Managed Transforms'))}
      ${card('Came from', 'The site that linked here', b(d.ref, k => esc(k || 'Direct, or no referrer')))}
      ${card('Campaign tags', 'utm_source / medium / campaign', b(d.utm, k => esc(k || 'None')))}
      ${card('Screen width', '', b(d.width, k => esc(k || 'Unknown')))}
      ${card('Language', '', b(d.lang, k => esc(k === 'he' ? 'Hebrew' : k === 'en' ? 'English' : k || '—')))}
      ${card('Theme', '', b(d.theme))}
      ${card('Installed as an app', 'Home-screen app vs browser tab', b(d.pwa, k => esc(k === '1' || k === 1 ? 'Installed app' : k === '0' || k === 0 ? 'Browser tab' : 'Unknown')))}
      ${card('Time zone', '', b(d.tz))}
      ${card('Reached through', '', b(d.via, k => esc(k === 'internet' ? 'sal-kal.com' : k === 'home' ? 'Home network / Tailscale' : k || '—')))}
    </div>`;
  },

  async users() {
    const rows = await get('users');
    return `<div class="kpis">${kpi('Accounts', rows.length)}${kpi('Finished setup', rows.filter(u => u.onboarded).length)}
        ${kpi(`Active ${periodName()}`, rows.filter(u => u.visits_period).length)}${kpi('With a cart', rows.filter(u => u.items).length)}</div>
      ${card('Accounts', 'Most recently seen first', table([
        { h: 'Who', f: u => `<div class="who">${avatar(u)}<div><b>${he(u.name || '—')}</b><small>${esc(u.email || '')}</small></div></div>` },
        { h: 'With', f: u => u.providers.map(p => `<span class="chip">${esc(p)}</span>`).join(' ') || '—' },
        { h: 'Joined', cls: 't', f: u => esc(when(u.created)) }, { h: 'Last seen', cls: 't', f: u => esc(when(u.seen)) },
        { h: 'Visits', n: 1, f: u => num(u.visits) }, { h: 'Days', n: 1, f: u => num(u.days) }, { h: 'Searches', n: 1, f: u => num(u.searches) },
        { h: 'Added', n: 1, f: u => num(u.adds) }, { h: 'Carts · items', n: 1, f: u => `${num(u.carts)} · ${num(u.items)}` },
        { h: 'Trouble', n: 1, f: u => u.trouble ? `<span class="chip warn">${num(u.trouble)}</span>` : '0' },
        { h: 'Town', f: u => u.town ? he(u.town) : u.onboarded ? '—' : '<span class="chip warn">no setup</span>' },
        { h: 'Device', f: device }, { h: 'Country', f: u => esc(flag(u.country)) },
      ], rows, { click: u => `user/${u.id}` }), 'full')}`;
  },

  async user(id) {
    const d = await get(`users/${encodeURIComponent(id)}`);
    const u = d.user, pr = d.prefs;
    const evText = e => {
      const x = e.data ? JSON.parse(e.data) : {}, nm = e.name ? he(e.name) : `<span class="mono">${esc(e.a)}</span>`;
      switch (e.kind) {
        case 'view': return `Opened <b>${esc(pageName(e.page))}</b>${e.a ? (e.page === 'product' ? ` · ${nm}` : ` · ${esc(e.a)}`) : ''}`;
        case 'dwell': return `<span class="muted">${esc(pageName(e.page))} for ${esc(dur(e.n / 1000))}${x.depth != null ? `, scrolled ${esc(x.depth)}%` : ''}</span>`;
        case 'search': return `Searched “${he(e.a)}” → ${num(e.n)} results ${outcome(e.b)}`;
        case 'add': return `Added ${nm}${e.n > 1 ? ` ×${num(e.n)}` : ''}`;
        case 'remove': return `Removed ${nm}`;
        case 'click': return `<span class="muted">Tapped <span class="mono">${esc(e.a)}</span></span>`;
        case 'rage': return `<span class="bad">Rage-tapped <span class="mono">${esc(e.a)}</span></span>`;
        case 'dead': return `<span class="bad">Dead tap on <span class="mono">${esc(e.a)}</span></span>`;
        case 'error': return `<span class="bad">JS error: <span class="mono">${esc(e.a)}</span></span>`;
        case 'api_fail': return `<span class="bad">Request failed: <span class="mono">${esc(e.a)}</span> (${esc(e.n)})</span>`;
        case 'toast': return `<span class="bad">Saw “${he(e.a)}”</span>`;
        case 'perf': return `<span class="muted">Page loaded in ${esc(ms(e.n))}</span>`;
        case 'filter': return `Filter: ${esc(e.a)}${e.b ? ` “${he(e.b)}”` : ''}`;
        case 'dialog': return `Opened <span class="mono">${esc(e.a)}</span>`;
        case 'onboard': return e.a === 'done' ? 'Finished setup' : `Setup step ${esc(e.a)}`;
        case 'compare': return `Checkout: ${num(x.complete)} of ${num(x.chains)} chains have the whole cart (${num(e.n)} items)${x.best ? `, cheapest ${he(x.best)} ₪${num(x.total)}` : ''}`;
        case 'signin_click': case 'signin_start': return `Started sign-in with ${esc(e.a)}`;
        case 'signin': return `Signed in with ${esc(e.a)}${e.b === 'new' ? ' <span class="chip good">new account</span>' : ''}`;
        case 'signin_fail': return `<span class="bad">Sign-in failed (${esc(e.a)}): ${esc(e.b)}</span>`;
        case 'onboarded': return 'Finished setup';
        case 'address': return `Set address in ${he(e.a || '—')}`;
        case 'distance': return `Distance ${num(e.n)} km`;
        case 'mode': return `Mode: ${esc(e.a)}`;
        case 'club': return `Club prices: ${esc(e.a)}`;
        case 'cart_new': return 'Made a cart';
        case 'cart_delete': return 'Deleted a cart';
        case 'delivery_edit': return 'Edited delivery settings';
        case 'logout': return 'Signed out';
        default: return `${esc(e.kind)} ${esc(e.a || '')}`;
      }
    };
    let lastDay = '', tl = '';
    for (const e of d.events) {
      const dt = tsDate(e.ts), day = dt.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
      if (day !== lastDay) { tl += `<li class="day">${esc(day)}</li>`; lastDay = day; }
      tl += `<li${['click', 'dwell', 'perf'].includes(e.kind) ? ' class="minor"' : ''}><span class="t">${esc(dt.toLocaleTimeString('en-GB'))}</span><span>${evText(e)}</span></li>`;
    }
    const carts = d.carts.length ? d.carts.map(c => `<p style="margin:10px 0 0"><b>${he(c.name)}</b> <span class="muted">· ${num(c.items.length)} items · updated ${esc(when(c.updated))}</span></p>
      <ol class="cartlist">${c.items.map(i => `<li>${i.name ? he(i.name) : `<span class="mono">${esc(i.key)}</span>`} <span class="muted">×${num(i.qty)}</span></li>`).join('')}</ol>`).join('') : empty('No carts');
    return `<a class="back" href="#users">← All accounts</a>
      <div class="grid wide">
      ${card('', '', `<div class="profile">${avatar(u)}<div><h1>${he(u.name || '—')}</h1><div class="muted">${esc(u.email || '')}</div></div></div>
        <dl class="facts" style="margin-top:16px">
          <div><dt>Joined</dt><dd>${esc(when(u.created))}</dd></div><div><dt>Last seen</dt><dd>${esc(when(u.seen))}</dd></div>
          <div><dt>Signs in with</dt><dd>${d.identities.map(i => `${esc(i.provider)}${i.verified ? '' : ' <span class="chip">email not verified</span>'}`).join(', ') || '—'}</dd></div>
          <div><dt>Signed-in devices</dt><dd>${num(d.sessions)}</dd></div>
          <div><dt>Address</dt><dd>${pr.address ? he(pr.address) : '—'}${pr.precision ? ` <span class="muted">(${esc(pr.precision)})</span>` : ''}</dd></div>
          <div><dt>Distance</dt><dd>${pr.km ? `${num(pr.km)} km` : 'Any'}</dd></div>
          <div><dt>Mode</dt><dd>${pr.mode === 'delivery' ? 'Delivered home' : 'In store'}${pr.club ? ' · club prices' : ''}</dd></div>
          <div><dt>Delivery</dt><dd>${num(pr.delivers_here)} chains deliver to them${pr.fee_edits ? ` · ${num(pr.fee_edits)} fee edits` : ''}</dd></div>
          <div><dt>Language</dt><dd>${esc(u.lang === 'en' ? 'English' : 'Hebrew')}</dd></div>
          <div><dt>Setup</dt><dd>${u.onboarded ? 'Finished' : '<span class="chip warn">Not finished</span>'}</dd></div>
          <div><dt>Account</dt><dd>#${esc(u.id)}${u.admin ? ' · admin' : ''}</dd></div>
        </dl>`)}
      ${card('Carts', '', carts)}
      ${card('Visits', 'Newest first', table([{ h: 'When', cls: 't', f: v => esc(when(v.start)) }, { h: 'For', n: 1, f: v => dur(v.last - v.start) },
        { h: 'Pages', n: 1, f: v => num(v.views) }, { h: 'Device', f: v => device(v) + (v.inapp ? ` <span class="chip warn">${esc(v.inapp)}</span>` : '') },
        { h: 'Where', f: v => esc([flag(v.country), v.city].filter(Boolean).join(' · ')) }, { h: 'From', f: v => esc(v.ref || v.utm || '') }], d.visits), 'full')}
      ${card('What they did', 'Newest first, last 400 events', d.events.length ? `<label class="switch" style="margin-bottom:6px"><input type="checkbox" data-minor> Every tap, page time and load time</label><ul class="timeline hide-minor">${tl}</ul>` : empty(), 'full')}
      </div>`;
  },

  async server() {
    const d = await get('server');
    const pts = d.daily.map(x => ({ label: dayLabel(x.day), v: { people: Math.max(0, x.n - x.bots), bots: x.bots } }));
    const total = d.routes.reduce((s, r) => s + r.n, 0), e5 = d.routes.reduce((s, r) => s + r.e5, 0);
    return `<div class="kpis">${kpi('Requests', total)}${kpi('Server errors (5xx)', e5, null, { alert: e5 > 0 })}
        ${kpi('Bot requests', d.routes.reduce((s, r) => s + r.bots, 0))}${kpi('Stats kept', d.rows.events, null)}
        ${kpi('Stats database', d.stats_size['stats.db'], null, { f: bytes })}</div>
      <div class="grid wide">
      ${card('Requests per day', '', columns(pts, [{ key: 'people', label: 'Browsers', color: 'var(--s1)' }, { key: 'bots', label: 'Bots and scripts', color: 'var(--s2)' }], { title: 'Requests per day' }))}
      ${card('Status codes', '', bars(d.status.map(x => ({ k: `<span class="mono">${esc(x.status)}</span>`, v: x.n }))))}
      ${card('Routes', 'Response time from the histogram: “<250 ms” = within that bucket', table([
        { h: 'Route', f: r => `<span class="mono">${esc(r.method)} ${esc(r.route)}</span>` }, { h: 'Requests', n: 1, f: r => num(r.n) },
        { h: 'Average', n: 1, f: r => ms(r.avg) }, { h: 'Median', n: 1, f: r => r.p50 ? `<${ms(r.p50)}` : '>2.5 s' },
        { h: '95%', n: 1, f: r => r.p95 ? `<${ms(r.p95)}` : '<span class="chip warn">>2.5 s</span>' }, { h: 'Slowest', n: 1, f: r => ms(r.max) },
        { h: '5xx', n: 1, f: r => r.e5 ? `<span class="chip bad">${num(r.e5)}</span>` : '0' }, { h: '4xx', n: 1, f: r => num(r.e4) },
        { h: 'Sign-in needed', n: 1, f: r => num(r.e401) }, { h: 'Bots', n: 1, f: r => num(r.bots) }], d.routes), 'full')}
      ${card('Recent server errors', '', table([{ h: 'When', cls: 't', f: r => esc(when(r.ts)) }, { h: 'Route', f: r => `<span class="mono">${esc(r.route)}</span>` },
        { h: 'Error', cls: 'wrap', f: r => `<span class="mono">${esc(r.error)}</span>` }], d.errors), 'full')}
      </div>
      <p class="note">Visits and events are kept ${esc(d.keep_days)} days, request counts 400 days. No IP addresses or User-Agent strings are stored.</p>`;
  },

  async data() {
    const d = await get('data');
    const ix = d.index || {};
    const tsText = v => { if (!v) return '—'; const s = String(v); return when(new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8), +s.slice(8, 10), +s.slice(10, 12)) / 1000); };
    const stale = d.chains.filter(c => c.fresh < c.stores).length;
    return `<div class="kpis">${kpi('Products on sale', d.products)}${kpi('With a barcode', d.barcodes)}${kpi('Stores', d.stores)}
        ${kpi('Chains', d.chains.length)}${kpi('Live deals', d.deals)}${kpi('Product photos', d.photos)}
        ${kpi('Disk free', d.disk?.free, null, { f: bytes, alert: d.disk && d.disk.free < 5e9 })}</div>
      <div class="grid wide">
      ${card('Chains', `Fresh = prices from the last ${d.fresh_hours} hours${stale ? ` · ${stale} chains have out-of-date stores` : ''}`, table([
        { h: 'Chain', f: c => he(c.chain) }, { h: 'Stores', n: 1, f: c => num(c.stores) },
        { h: 'Fresh', n: 1, f: c => c.fresh === c.stores ? num(c.fresh) : `<span class="chip ${c.fresh ? 'warn' : 'bad'}">${num(c.fresh)}</span>` },
        { h: 'Online shops', n: 1, f: c => num(c.online) }, { h: 'Newest prices', n: 1, f: c => esc(tsText(c.newest)) }], d.chains), 'full')}
      ${card('Last index run', '', `<dl class="facts">
        <div><dt>State</dt><dd>${ix.running ? '<span class="chip warn">running</span>' : ix.error ? `<span class="chip bad">${esc(ix.error)}</span>` : 'idle'}</dd></div>
        <div><dt>Started</dt><dd>${esc(when(ix.started))}</dd></div><div><dt>Finished</dt><dd>${esc(when(ix.finished || ix.done_at))}</dd></div>
        <div><dt>Files</dt><dd>${num(ix.processed)}${ix.total ? ` of ${num(ix.total)}` : ''}</dd></div>
        <div><dt>Checked through</dt><dd>${esc(tsText(d.confirmed_through))}</dd></div>
        <div><dt>Price history since</dt><dd>${esc(d.history_since || '—')}</dd></div></dl>`)}
      ${card('Storage', '', `<dl class="facts">${Object.entries(d.sizes).map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${bytes(v)}</dd></div>`).join('')}
        ${d.disk ? `<div><dt>Disk</dt><dd>${bytes(d.disk.free)} free of ${bytes(d.disk.total)}</dd></div>` : ''}</dl>`)}
      ${card('Sign-in', '', `<dl class="facts"><div><dt>Accounts</dt><dd>${d.accounts ? 'on' : 'off (local mode)'}</dd></div>
        <div><dt>Providers</dt><dd>${esc(d.providers.join(', ') || 'none')}</dd></div></dl>`)}
      </div>`;
  },
};

// ---------------------------------------------------------------- routing and controls
let seq = 0;
async function render() {
  const my = ++seq, [tab, arg] = (location.hash.slice(1) || 'overview').split('/');
  const view = VIEWS[tab] ? tab : 'overview';
  $$('#tabs a').forEach(a => a.setAttribute('aria-current', a.hash === `#${view === 'user' ? 'users' : view}` ? 'page' : 'false'));
  const main = $('#main');
  main.classList.add('busy');
  $('#refresh').classList.add('spin');
  try {
    TIPS = [];
    const html = await VIEWS[view](arg);
    if (my !== seq) return;
    main.innerHTML = html;
  } catch (e) {
    if (my !== seq) return;
    main.innerHTML = e.status ? `<div class="msg"><h1>Signed out</h1><p>Sign in on the shop with the owner’s Google account, then come back.</p><p><a href="/">Go to Sal-Kal</a></p></div>`
      : `<div class="msg"><h1>Couldn’t load this</h1><p class="mono">${esc(e.message)}</p></div>`;
  } finally {
    if (my === seq) { main.classList.remove('busy'); $('#refresh').classList.remove('spin'); }
  }
}
async function livePill() {
  try {
    const r = await get('live');
    const el = $('#live-pill');
    el.hidden = false;
    el.querySelector('span').textContent = r.length === 1 ? '1 here now' : `${r.length} here now`;
  } catch { /* the page shows the error */ }
}
function setRange(days) {
  ST.days = days;
  try { localStorage.setItem('admin_days', days); } catch { /* ignore */ }
  $$('#range button').forEach(b => b.setAttribute('aria-checked', +b.dataset.days === days));
}
$('#range').addEventListener('click', e => { const b = e.target.closest('button'); if (b) { setRange(+b.dataset.days); render(); } });
$('#mine').checked = ST.mine;
$('#mine').addEventListener('change', e => { ST.mine = e.target.checked; try { localStorage.setItem('admin_mine', ST.mine ? '1' : '0'); } catch { /* ignore */ } render(); livePill(); });
$('#refresh').addEventListener('click', () => { render(); livePill(); });
addEventListener('hashchange', render);
setRange([1, 7, 30, 90].includes(ST.days) ? ST.days : 7);
render();
livePill();
setInterval(() => { if (document.visibilityState === 'visible') livePill(); }, 30000);
