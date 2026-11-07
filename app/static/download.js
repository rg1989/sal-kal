'use strict';
// sal-kal.com/download, "Run it yourself": shows the steps for the computer you're on (Windows, Mac or Linux) and
// copies the install lines. The language switch is site.js's, as on every public page.
(() => {
  const $$ = s => [...document.querySelectorAll(s)];
  const site = location.origin;
  const CMD = { sh: `curl -fsSL ${site}/install.sh | bash`, ps: `irm ${site}/install.ps1 | iex` };

  const title = () => {
    document.title = document.documentElement.lang === 'en' ? 'Run Sal-Kal yourself · Sal-Kal' : 'הריצו את סל קל בעצמכם · Sal-Kal';
  };
  title();
  document.addEventListener('langchange', title);

  function setOs(os) {
    $$('.oses [data-os]').forEach(b => b.setAttribute('aria-checked', b.dataset.os === os));
    document.body.dataset.os = os;  // download.css shows this computer's version in every slot
  }
  $$('.oses [data-os]').forEach(b => b.addEventListener('click', () => setOs(b.dataset.os)));
  const ua = navigator.userAgent, plat = navigator.userAgentData?.platform || navigator.platform || '';
  setOs(/win/i.test(plat) || /Windows/.test(ua) ? 'windows' : /mac|iphone|ipad/i.test(plat) || /Mac OS X/.test(ua) ? 'mac'
    : /linux|x11/i.test(plat + ua) && !/Android/.test(ua) ? 'linux' : 'windows');

  $$('[data-cmd]').forEach(c => { c.textContent = CMD[c.dataset.cmd]; });
  $$('.cmd .copy').forEach(b => b.addEventListener('click', async () => {
    const code = b.parentElement.querySelector('code');
    try { await navigator.clipboard.writeText(code.textContent); } catch {  // no clipboard (http, old browser): select it
      const r = document.createRange(); r.selectNodeContents(code);
      getSelection().removeAllRanges(); getSelection().addRange(r);
      return;
    }
    b.classList.add('done');
    setTimeout(() => b.classList.remove('done'), 2000);
  }));
})();
