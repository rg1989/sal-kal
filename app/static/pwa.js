'use strict';
// Sal-Kal as an installed app: registers the service worker (/sw.js), keeps the manifest in the page's language, and
// runs "Install app" in the account sheet. Chrome, Edge and Samsung Internet install with one tap. A site can't start
// the install on iPhones or in Mac Safari, so those get the steps instead (#install). Loads after account.js.
window.PWA = (() => {
  const ua = navigator.userAgent;
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);  // iPads say Macintosh
  const inApp = /FBAN|FBAV|Instagram|Line\/|GSA\//.test(ua);  // Facebook, Instagram, Google app: no Add to Home Screen
  const macSafari = !ios && /Macintosh/.test(ua) && /Version\/[\d.]+.*Safari/.test(ua) && !/Chrome|Chromium|Edg|Firefox|OPR/.test(ua);
  const installed = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
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

  addEventListener('beforeinstallprompt', e => { e.preventDefault(); deferred = e; });
  addEventListener('appinstalled', () => { deferred = null; toast(t('Installed')); });

  const ICON = {
    share: '<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 14.5V3.5M8.2 7.2 12 3.4l3.8 3.8"/><path d="M8 10H6.5A1.5 1.5 0 0 0 5 11.5v8A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5v-8a1.5 1.5 0 0 0-1.5-1.5H16"/></g></svg>',
    add: '<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="4" y="4" width="16" height="16" rx="4.5"/><path d="M12 8.5v7M8.5 12h7"/></g></svg>',
    dock: '<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="M7.5 15.5h9"/></g></svg>',
  };

  function steps() {
    $('#install-steps').innerHTML = (macSafari
      ? [[ICON.dock, t('File › Add to Dock')]]
      : [[ICON.share, `${t('Share')}<small>${t('or under ⋯')}</small>`], [ICON.add, t('Add to Home Screen')]])
      .map(([icon, label]) => `<li>${icon}<span>${label}</span></li>`).join('');
    openSheet($('#install'));
  }

  return {
    // show "Install app"? Not once installed, nor where installing isn't possible
    offer: () => !installed() && (!!deferred || ((ios || macSafari) && !inApp)),
    install() {
      if (deferred) {  // Chrome's own install dialog; it can be shown once per page load
        const e = deferred;
        deferred = null;
        e.prompt();
        return;
      }
      setTimeout(steps, 180);  // after the account sheet has slid away
    },
  };
})();
