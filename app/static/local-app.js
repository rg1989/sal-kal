'use strict';
// Sal-Kal on your own computer, during setup only (app/local.py adds this script to the app page while it waits for
// your address): once the app's first-run setup has saved your places, back to the setup page, where the prices for
// the stores around them are downloaded.
(() => {
  let woke = false;
  const tick = async () => {
    try {
      const r = await fetch('/api/local/state');
      if (!r.ok) return;
      const s = await r.json();
      if (s.phase === 'area' && s.onboarded && s.places && !woke) {
        woke = true;
        fetch('/api/local/start', { method: 'POST' }).catch(() => {});
      }
      if (s.phase === 'prices') location.href = '/setup';
    } catch { /* restarting */ }
  };
  setInterval(tick, 3000);
})();
