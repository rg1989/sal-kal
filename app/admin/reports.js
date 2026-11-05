'use strict';
// Reports tab of the owner's admin page (#reports): reports to sell, built by app/reports.py from the chains' public
// price files only. Uses admin.js's helpers (esc, num, card, table, kpi, bars, VIEWS, render). Product and store
// names come from the chains' files: they only reach the page through esc().
(() => {
  const CATS = { list: [] };
  const opt = (v, l) => ({ v: String(v), l });
  const REPORTS = [
    { id: 'price-level', name: 'Chain price index', params: [
      { k: 'min_chains', label: 'Products sold at', def: '3', options: [opt(2, '2+ chains'), opt(3, '3+ chains'), opt(5, '5+ chains'), opt(10, '10+ chains')] }] },
    { id: 'basket', name: 'Basket over time', params: [
      { k: 'size', label: 'Products', def: '30', options: [opt(20, '20'), opt(30, '30'), opt(50, '50'), opt(100, '100')] },
      { k: 'period', label: 'Period', def: '90', options: [opt(30, '30 days'), opt(90, '90 days'), opt(365, '1 year')] },
      { k: 'codes', label: 'Own basket: barcodes', type: 'area' }] },
    { id: 'changes', name: 'Price changes', params: [
      { k: 'period', label: 'Compared with', def: '30', options: [opt(7, '7 days ago'), opt(30, '30 days ago'), opt(90, '90 days ago'), opt(365, '1 year ago')] },
      { k: 'cat', label: 'Category', type: 'cat' }] },
    { id: 'deals', name: 'Deals by chain', params: [] },
    { id: 'gaps', name: 'Price gaps', params: [
      { k: 'mode', label: 'Compare', def: 'chains', options: [opt('chains', 'Across chains'), opt('branches', 'Across a chain’s branches')] },
      { k: 'cat', label: 'Category', type: 'cat' }] },
    { id: 'products', name: 'Product tracker', params: [
      { k: 'q', label: 'Name words', type: 'text' }, { k: 'prefix', label: 'Barcode prefix', type: 'text' },
      { k: 'codes', label: 'Barcodes', type: 'area' }] },
  ];
  const byId = id => REPORTS.find(r => r.id === id);

  async function rget(path) {
    const r = await fetch(`/api/admin/${path}`, { credentials: 'same-origin' });
    if (r.status === 401 || r.status === 404) throw Object.assign(new Error('gone'), { status: r.status });
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  }

  // ---------------------------------------------------------------- cells
  const money = v => `₪${Number(v).toFixed(2)}`;
  const SIGNED = new Set(['change', 'avg']);
  function cell(v, kind, key) {
    if (v == null || v === '') return '<span class="rp-none">—</span>';
    switch (kind) {
      case 'money': return money(v);
      case 'pct': { const s = (v * 100).toFixed(1); return `${SIGNED.has(key) && v > 0 ? '+' : ''}${s}%`; }
      case 'index': return Number(v).toFixed(1);
      case 'int': return num(v);
      case 'day': return esc(dayLabel(v));
      case 'he': return he(v);
      case 'code': return `<span class="mono">${esc(v)}</span>`;
      default: return esc(v);
    }
  }
  const NUMERIC = new Set(['money', 'pct', 'index', 'int']);
  const PREVIEW = 200;
  function grid(cols, rows) {
    const t = table(cols.map(c => ({ h: c.label, n: NUMERIC.has(c.kind), cls: c.kind === 'he' ? 'wrap' : '', f: r => cell(r[c.key], c.kind, c.key) })), rows, { limit: PREVIEW });
    return t + (rows.length > PREVIEW ? `<p class="note">${num(PREVIEW)} of ${num(rows.length)} rows shown</p>` : '');
  }
  const summaryFacts = s => s.length ? `<dl class="facts">${s.map(([l, v, k]) => `<div><dt>${esc(l)}</dt><dd>${cell(v, k)}</dd></div>`).join('')}</dl>` : '';

  // ---------------------------------------------------------------- charts
  /** chain price index: one bar per chain from 100, blue = cheaper than typical, orange = dearer */
  function indexBars(rows) {
    const span = Math.max(5, ...rows.map(r => Math.abs(r.index - 100)));
    return `<div class="rp-div" role="img" aria-label="Price index by chain, 100 = typical">${rows.map(r => {
      const d = r.index - 100, w = Math.abs(d) / span * 50;
      return `<span class="k">${esc(r.chain)}</span><span class="track"><span class="mid"></span><span class="fill ${d < 0 ? 'lo' : 'hi'}" style="${d < 0 ? `right:50%` : `left:50%`};width:${w}%" title="${esc(r.chain)}: ${r.index.toFixed(1)}"></span></span><span class="v">${r.index.toFixed(1)}</span>`;
    }).join('')}</div><p class="note">100 = typical price · lower = cheaper</p>`;
  }
  /** a basket's total per chain on the latest day */
  function basketBars(rep) {
    const last = rep.rows[rep.rows.length - 1] || {}, chains = rep.columns.slice(1).filter(c => last[c.key] != null);
    if (!chains.length) return empty();
    const lo = Math.min(...chains.map(c => last[c.key]));
    return bars(chains.map(c => ({ k: esc(c.label), v: last[c.key], sub: last[c.key] - lo < 0.005 ? 'cheapest' : `+${money(last[c.key] - lo)}` })),
      { fmt: money });
  }

  // ---------------------------------------------------------------- the parameter form
  function field(p, val) {
    const v = val ?? p.def ?? '';
    if (p.type === 'area') return `<label class="rp-f rp-wide"><span>${esc(p.label)}</span><textarea name="${p.k}" rows="2" spellcheck="false">${esc(v)}</textarea></label>`;
    if (p.type === 'text') return `<label class="rp-f"><span>${esc(p.label)}</span><input name="${p.k}" value="${esc(v)}" autocomplete="off"></label>`;
    const options = p.type === 'cat' ? [opt('', 'All'), ...CATS.list.map(c => opt(c.slug, c.name))] : p.options;
    return `<label class="rp-f"><span>${esc(p.label)}</span><select name="${p.k}">${options.map(o => `<option value="${esc(o.v)}"${o.v === String(v) ? ' selected' : ''}>${esc(o.l)}</option>`).join('')}</select></label>`;
  }
  const query = params => { const q = new URLSearchParams(); for (const [k, v] of Object.entries(params)) if (v) q.set(k, v); return q.toString(); };

  // ---------------------------------------------------------------- views
  async function home() {
    const d = await rget('reports');
    CATS.list = d.categories;
    const h = d.holdings;
    return `<div class="kpis">${kpi('Chains', h.chains)}${kpi('Current stores', h.stores)}${kpi('Products priced', h.products)}
        ${kpi('Product × chain prices', h.prices)}${kpi('Products at 3+ chains', h.multi)}${kpi('Products on a deal', h.deals)}
        ${kpi('Days of price history', h.history_days)}${kpi('History rows', h.history_rows)}</div>
      <p class="note rp-src">From the chains' public price files (Food Act §30) · history since ${esc(dayLabel(h.history_since))} · no user data</p>
      <div class="rp-list">${REPORTS.map(r => `<a class="rp-item" href="#reports/${r.id}">${esc(r.name)}<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 5l5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></a>`).join('')}</div>`;
  }

  async function report(arg) {
    const [id, qs] = arg.split('?'), def = byId(id);
    if (!def) return home();
    if (!CATS.list.length) CATS.list = (await rget('reports')).categories;
    const given = Object.fromEntries(new URLSearchParams(qs || ''));
    const params = Object.fromEntries(def.params.map(p => [p.k, given[p.k] ?? p.def ?? '']));
    const q = query(params);
    const rep = await rget(`reports/${id}${q ? '?' + q : ''}`);
    const dl = (part, label) => `<a class="rp-btn" href="/api/admin/reports/${id}?${q ? q + '&' : ''}format=csv${part ? '&part=' + part : ''}" download>${esc(label)}</a>`;
    const parts = Object.entries(rep.parts || {});
    const chart = id === 'price-level' && rep.rows.length ? card('Price index', '', indexBars(rep.rows), 'full')
      : id === 'basket' && rep.rows.length ? card('Basket total today', '', basketBars(rep), 'full') : '';
    const stamp = new Date(rep.generated * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    return `<a class="back" href="#reports">← Reports</a>
      <section class="card full rp-head">
        <h2>${esc(rep.title)}</h2>
        ${def.params.length ? `<form class="rp-form" data-rep="${id}">${def.params.map(p => field(p, params[p.k])).join('')}<button type="submit" class="rp-btn rp-run">Run</button></form>` : ''}
        <div class="rp-actions">${dl('', 'CSV')}${parts.map(([k, t]) => dl(k, `CSV: ${t.title}`)).join('')}<button type="button" class="rp-btn" data-print>Print / PDF</button></div>
        ${summaryFacts(rep.summary)}
      </section>
      <div class="grid wide rp-body">
        ${chart}
        ${rep.columns.length ? card(parts.length ? 'Summary' : 'Results', '', grid(rep.columns, rep.rows), 'full') : ''}
        ${parts.map(([, t]) => card(t.title, '', grid(t.columns, t.rows), 'full')).join('')}
      </div>
      <div class="rp-method">
        <p><b>Method.</b> ${esc(rep.method)}</p>
        <p>${esc(rep.source)}</p>
        <p>Generated ${esc(stamp)} by Sal-Kal${rep.seconds != null ? ` · ${esc(rep.seconds)} s` : ''}</p>
      </div>`;
  }

  VIEWS.reports = arg => arg ? report(arg) : home();

  document.addEventListener('submit', e => {
    const f = e.target.closest('form.rp-form');
    if (!f) return;
    e.preventDefault();
    const q = query(Object.fromEntries(new FormData(f)));
    const hash = `#reports/${f.dataset.rep}${q ? '?' + q : ''}`;
    if (location.hash === hash) render(); else location.hash = hash;
  });
  document.addEventListener('click', e => { if (e.target.closest('[data-print]')) print(); });

  // admin.js rendered before this file loaded: show the tab if the address asks for it
  if (location.hash.startsWith('#reports')) render();
})();
