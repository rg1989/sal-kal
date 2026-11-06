'use strict';
// Sal-Kal on this computer: the setup wizard (app/local.py runs the steps; this page shows them and its settings).
(() => {
  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const lang = () => (document.documentElement.lang === 'en' ? 'en' : 'he');
  const t = (he, en) => (lang() === 'en' ? en : he);
  let S = null, checked = false, polling = null;

  function setLang(l) {
    document.documentElement.lang = l;
    document.documentElement.dir = l === 'he' ? 'rtl' : 'ltr';
    try { localStorage.setItem('lang', l); } catch { /* private mode */ }
    document.cookie = `lang=${l};path=/;max-age=31536000;samesite=lax`;
    document.title = t('סל קל · התקנה', 'Sal-Kal · Setup');
    $$('.langs [data-lang]').forEach(b => b.setAttribute('aria-checked', b.dataset.lang === l));
    if (S) render();
  }
  $$('.langs [data-lang]').forEach(b => b.addEventListener('click', () => setLang(b.dataset.lang)));
  setLang(lang());

  async function api(path, opts = {}) {
    const r = await fetch(path, { method: opts.method || 'GET', headers: opts.body ? { 'Content-Type': 'application/json' } : {},
      body: opts.body ? JSON.stringify(opts.body) : undefined });
    if (!r.ok) throw new Error(r.status);
    return r.json();
  }

  const gb = n => (n == null ? '' : n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`);
  const num = n => Number(n || 0).toLocaleString(lang() === 'en' ? 'en-US' : 'he-IL');
  function ago(ts) {
    if (!ts) return t('עוד לא', 'not yet');
    const m = Math.round((Date.now() / 1000 - ts) / 60);
    if (m < 2) return t('עכשיו', 'just now');
    if (m < 60) return t(`לפני ${m} דקות`, `${m} min ago`);
    const h = Math.round(m / 60);
    return h < 24 ? t(`לפני ${h} שעות`, `${h} h ago`) : new Date(ts * 1000).toLocaleString(lang() === 'en' ? 'en-GB' : 'he-IL');
  }
  const ICON = {
    mac: () => t('Launchpad או ⌘ רווח › ״Sal-Kal״', 'Launchpad, or ⌘ Space › “Sal-Kal”'),
    windows: () => t('הסמל Sal-Kal בשולחן העבודה או בתפריט התחל', 'The Sal-Kal icon on the desktop or in the Start menu'),
    linux: () => t('Sal-Kal בתפריט היישומים', 'Sal-Kal in the apps menu'),
  };
  const ERRORS = {
    'no-stores': () => t('לא הצלחנו להוריד את רשימות הסניפים. כדאי לבדוק את החיבור לאינטרנט.', 'Couldn’t download the store lists. Check the internet connection.'),
  };

  function render() {
    const ph = S.phase;
    $$('[data-phase]').forEach(c => { c.hidden = c.dataset.phase !== ph; });
    const order = ['welcome', 'installing', 'area', 'prices', 'ready'];
    $$('.dots li').forEach(d => {
      const i = order.indexOf(d.dataset.for), at = order.indexOf(ph);
      d.dataset.state = i < at ? 'done' : i === at ? 'now' : '';
    });
    const card = $(`[data-phase="${ph}"]`);
    if (!card) return;
    const p = S.progress || {};
    // steps: those before the running one are done; all of them once the phase moved on
    const steps = [...card.querySelectorAll('[data-step]')];
    const cur = steps.findIndex(li => li.dataset.step === p.step);
    steps.forEach((li, i) => {
      const st = cur < 0 ? (S.running ? 'wait' : 'wait') : i < cur ? 'done' : i === cur ? 'now' : 'wait';
      li.dataset.state = st;
      const c = li.querySelector('.count');
      c.textContent = st === 'now' && p.total ? `${num(Math.min(p.done || 0, p.total))} / ${num(p.total)}` : '';
      if (li.dataset.step === 'browser' && p.warning === 'browser' && i < cur) li.dataset.state = 'warn';
    });
    const err = card.querySelector('.err');
    if (err) {
      err.hidden = !p.error;
      if (p.error) err.querySelector('p').textContent = (ERRORS[p.error] || (() => t('משהו השתבש:', 'Something went wrong:') + ' ' + p.error))();
    }
    if (ph === 'ready') {
      $('#addr').textContent = S.url.replace(/^http:\/\//, '');
      $('#where-icon').textContent = (ICON[S.os] || ICON.linux)();
      $('#last-run').textContent = S.running ? t('מתעדכן עכשיו…', 'updating now…') : ago(S.last_run);
      $('#cover').textContent = t(`${num(S.covered)} סניפים`, `${num(S.covered)} stores`);
      $('#update-now').disabled = S.running;
      $('#autostart').checked = S.autostart;
      $('#lan').checked = S.lan;
      $('.lan-url').hidden = !S.lan_url;
      $('#lan-url').textContent = S.lan_url || '';
      $('#folder').textContent = S.folder;
      $('#used').textContent = gb(S.disk?.used);
    }
    if (ph === 'welcome' && S.disk) {
      $('#disk-free').textContent = gb(S.disk.free);
      $('#ck-disk').dataset.state = S.disk.free == null || S.disk.free > 5e9 ? 'ok' : 'bad';
    }
  }

  async function refresh() {
    try { S = await api('/api/local/state'); } catch { return; }
    render();
    if (S.phase === 'welcome' && !checked) {
      checked = true;
      api('/api/local/check').then(r => { $('#ck-net').dataset.state = r.ok >= Math.ceil(r.of / 2) ? 'ok' : 'bad'; })
        .catch(() => { $('#ck-net').dataset.state = 'bad'; });
    }
    if (S.phase === 'area' && S.onboarded) api('/api/local/start', { method: 'POST' }).catch(() => {});
  }
  function poll() {
    clearInterval(polling);
    polling = setInterval(() => { if (!document.hidden) refresh(); }, 2500);
  }

  $('#go').addEventListener('click', async e => {
    e.currentTarget.disabled = true;
    try { S = await api('/api/local/start', { method: 'POST' }); render(); } finally { e.currentTarget.disabled = false; }
  });
  $$('.retry').forEach(b => b.addEventListener('click', async () => {
    b.disabled = true;
    try { await api('/api/local/start', { method: 'POST' }); await refresh(); } finally { b.disabled = false; }
  }));
  $('#update-now').addEventListener('click', async () => { S = await api('/api/local/start', { method: 'POST' }); render(); });
  $('#copy-addr').addEventListener('click', async e => {
    try { await navigator.clipboard.writeText(S.url); e.currentTarget.classList.add('done'); } catch { /* not allowed */ }
  });
  $('#autostart').addEventListener('change', async e => {
    S = await api('/api/local/settings', { method: 'PUT', body: { autostart: e.target.checked } }); render();
  });
  $('#lan').addEventListener('change', async e => {
    await api('/api/local/settings', { method: 'PUT', body: { lan: e.target.checked } }).catch(() => {});
    setTimeout(refresh, 3000);  // it restarts to listen on the network (or stop)
  });
  $('#uninstall').addEventListener('click', async () => {
    if (!confirm(t('להסיר את סל קל מהמחשב?', 'Remove Sal-Kal from this computer?'))) return;
    await api('/api/local/uninstall', { method: 'POST', body: { confirm: 'uninstall', keep_data: $('#keep-data').checked } }).catch(() => {});
    clearInterval(polling);
    S = { ...S, phase: 'removed' };
    render();
  });

  refresh();
  poll();
})();
