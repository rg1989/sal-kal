'use strict';
// Sal-Kal as an installed app: registers the service worker (/sw.js), keeps the manifest in the page's language, and
// runs the Install buttons: any element with data-install (the sign-in page, the landing page) and the account sheet's
// row. They show only on a phone, in a browser that can put the app on the home screen (per owner, 2026-11-07: never
// a button that wouldn't work): Android browsers, Safari on iPhone, and Chrome or Edge on iPhone from iOS 16.4 (their
// Share sheet has Add to Home Screen). Never on computers or tablets, and never in an app's built-in browser
// (Facebook, Instagram, WhatsApp's or Telegram's in-app views, an Android web view): those can't install.
// Chrome, Edge and Samsung Internet open their own install dialog once they offer it (beforeinstallprompt: Chrome
// waits for a tap and ~30 s on the page). Until then, and on iPhones (a site can't start the install there), the
// button shows the steps instead (#install). A link to /app?install opens the steps too (for pages without #install).
// Loads after account.js.
window.PWA = (() => {
  const ua = navigator.userAgent;
  // an app's own browser: Facebook, Messenger, Instagram, LinkedIn, X, TikTok, Snapchat, Pinterest, Line, WeChat,
  // KakaoTalk, Telegram, the Google app; on Android also any web view ("; wv)")
  const inApp = /FBAN|FBAV|FB_IAB|FBIOS|Messenger|Instagram|LinkedInApp|Twitter|musical_ly|TikTok|Bytedance|Snapchat|Pinterest|Line\/|MicroMessenger|KAKAOTALK|Telegram|GSA\/|; wv\)/.test(ua);
  const iphone = /iPhone|iPod/.test(ua);  // iPads are left out (they also say Macintosh), as are computers
  const [, maj = 0, min = 0] = (ua.match(/OS (\d+)_(\d+)/) || []).map(Number);
  const safari = /Version\/[\d.]+ .*Safari\//.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS|OPT\/|DuckDuckGo|Brave/.test(ua);
  const ios = iphone && (safari || (/CriOS|EdgiOS/.test(ua) && (maj > 16 || (maj === 16 && min >= 4))));
  const android = /Android/.test(ua) && (/Mobile/.test(ua) || navigator.userAgentData?.mobile === true);  // a phone, not a tablet
  const phone = !inApp && (ios || android);
  const installed = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const tr = s => (typeof t === 'function' ? t(s) : s);
  let deferred = null;  // Chrome's install prompt, kept for the button

  if ('serviceWorker' in navigator && isSecureContext) {
    addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
  }

  // the installed app's name follows the page's language
  function follow() {
    const l = document.documentElement.lang === 'en' ? 'en' : 'he';
    const m = document.getElementById('manifest');
    if (m && !m.href.endsWith(`-${l}.webmanifest`)) m.href = `/static/manifest-${l}.webmanifest`;
    document.querySelector('meta[name="apple-mobile-web-app-title"]')?.setAttribute('content', l === 'en' ? 'Sal-Kal' : 'סל קל');
  }
  follow();
  new MutationObserver(follow).observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] });

  let have = false;  // already installed on this device (Chrome tells a browser tab through getInstalledRelatedApps)
  const offer = () => phone && !installed() && !have;
  const sync = () => document.querySelectorAll('[data-install]').forEach(b => { b.hidden = !offer(); });
  sync();
  navigator.getInstalledRelatedApps?.().then(apps => { if (apps.length) { have = true; sync(); } }).catch(() => {});
  addEventListener('beforeinstallprompt', e => { if (!phone) return; e.preventDefault(); deferred = e; sync(); });  // computers keep the browser's own
  addEventListener('appinstalled', () => {
    deferred = null;
    have = true;
    sync();
    if (typeof toast === 'function') toast(tr('Installed'));
  });

  const ICON = {
    share: '<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 14.5V3.5M8.2 7.2 12 3.4l3.8 3.8"/><path d="M8 10H6.5A1.5 1.5 0 0 0 5 11.5v8A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5v-8a1.5 1.5 0 0 0-1.5-1.5H16"/></g></svg>',
    add: '<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="4" y="4" width="16" height="16" rx="4.5"/><path d="M12 8.5v7M8.5 12h7"/></g></svg>',
    menu: '<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="currentColor"><circle cx="12" cy="5.5" r="1.9"/><circle cx="12" cy="12" r="1.9"/><circle cx="12" cy="18.5" r="1.9"/></g></svg>',
  };

  function steps() {
    const d = document.getElementById('install');
    if (!d) {  // a page without the steps sheet (the landing page): the app page has it
      if (location.pathname !== '/app') location.href = '/app?install';
      return;
    }
    const list = ios ? [[ICON.share, `${tr('Share')}<small>${tr('or under ⋯')}</small>`], [ICON.add, tr('Add to Home Screen')]]
      : [[ICON.menu, tr('Browser menu')], [ICON.add, `${tr('Install app')}<small>${tr('or Add to Home screen')}</small>`]];
    document.getElementById('install-steps').innerHTML = list.map(([icon, label]) => `<li>${icon}<span>${label}</span></li>`).join('');
    if (typeof openSheet === 'function') openSheet(d); else d.showModal();
  }

  function install(delay = 0) {
    if (deferred) {  // Chrome's own install dialog; it can be shown once per page load
      const e = deferred;
      deferred = null;
      e.prompt();
      e.userChoice.finally(sync);
      return;
    }
    setTimeout(steps, delay);
  }

  document.addEventListener('click', e => {
    const b = e.target.closest('[data-install]');
    if (!b) return;
    e.preventDefault();
    install();
  });

  // /app?install: open the steps once the page has finished starting (the sign-in page closes open sheets)
  if (new URLSearchParams(location.search).has('install')) {
    history.replaceState(null, '', location.pathname + location.hash);
    let n = 0;
    const wait = setInterval(() => {
      if (document.body.classList.contains('booting') && ++n < 50) return;
      clearInterval(wait);
      if (offer()) install(300);
    }, 200);
  }

  return {
    offer,  // show "Install app"? Only on a phone browser that can install, and not once installed
    install: () => install(180),  // from the account sheet: after it has slid away
  };
})();
