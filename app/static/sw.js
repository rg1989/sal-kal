'use strict';
// Sal-Kal's service worker (served at /sw.js so it covers the whole site): it makes the site installable as an app,
// keeps Sal's own /static files for a fast start, and shows "No connection" when the app is opened offline (without
// live prices there is nothing to show). Pages, prices (/api), sign-in (/auth), the admin pages and product photos
// always come from the network and are never stored here.
// To switch it off everywhere, replace this file with one that calls self.registration.unregister().

const CACHE = 'sal-shell-1';
const PAGES = new Set(['/', '/app']);
const KEEP = /\.(?:js|css|png|svg|webp|woff2|webmanifest)$/;

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', e => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
  await self.clients.claim();
})()));

self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET') return;
  const u = new URL(r.url);
  if (u.origin !== location.origin) return;  // fonts and map tiles: the browser's own cache
  if (r.mode === 'navigate') {
    if (PAGES.has(u.pathname)) e.respondWith(fetch(r).catch(offline));
    return;
  }
  if (u.pathname.startsWith('/static/') && !u.pathname.startsWith('/static/landing/')
      && KEEP.test(u.pathname) && !r.headers.has('range')) e.respondWith(file(e, r, u));
});

// /static files. A URL with ?v= (scripts and styles: index.html stamps them with the file's change time) never
// changes, so a stored copy is used as is. Other files are served from storage and refreshed in the background.
async function file(e, r, u) {
  const c = await caches.open(CACHE);
  const hit = await c.match(r);
  const versioned = u.searchParams.has('v');
  const net = fetch(r).then(async res => {
    if (res.ok && res.type === 'basic') {
      await c.put(r, res.clone());
      if (versioned) {  // drop the copies an earlier deploy left
        for (const k of await c.keys()) {
          const ku = new URL(k.url);
          if (ku.pathname === u.pathname && ku.search !== u.search) await c.delete(k);
        }
      }
    }
    return res;
  });
  if (hit) {
    if (!versioned) e.waitUntil(net.catch(() => {}));
    return hit;
  }
  return net;
}

function offline() {
  const html = `<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>סל קל</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#EEF0EC;color:#1C2321;
font:600 17px/1.5 system-ui,sans-serif;text-align:center}@media(prefers-color-scheme:dark){body{background:#121715;color:#E9EDE9}}
a{display:inline-block;margin-top:14px;padding:10px 20px;border-radius:12px;background:#FFD23F;color:#1C2321;text-decoration:none}</style>
</head><body><main><p>אין חיבור לאינטרנט<br><span lang="en" dir="ltr">No connection</span></p>
<a href="/">נסו שוב · Try again</a></main></body></html>`;
  return new Response(html, { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
}
