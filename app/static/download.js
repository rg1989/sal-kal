'use strict';
// sal-kal.com/download: the installer for the computer you're on (Windows, Mac or Linux), and the language.
(() => {
  const $$ = s => [...document.querySelectorAll(s)];
  const lang = () => (document.documentElement.lang === 'en' ? 'en' : 'he');
  const site = location.origin;
  const CMD = { sh: `curl -fsSL ${site}/install.sh | bash`, ps: `irm ${site}/install.ps1 | iex` };

  function setLang(l) {
    document.documentElement.lang = l;
    document.documentElement.dir = l === 'he' ? 'rtl' : 'ltr';
    try { localStorage.setItem('lang', l); } catch { /* private mode */ }
    document.cookie = `lang=${l};path=/;max-age=31536000;samesite=lax`;
    document.title = l === 'en' ? 'Sal-Kal on your own computer' : 'סל קל על המחשב שלכם · Sal-Kal';
    $$('.langs [data-lang]').forEach(b => b.setAttribute('aria-checked', b.dataset.lang === l));
  }
  $$('.langs [data-lang]').forEach(b => b.addEventListener('click', () => setLang(b.dataset.lang)));
  setLang(lang());

  function setOs(os) {
    $$('.oses [data-os]').forEach(b => b.setAttribute('aria-checked', b.dataset.os === os));
    document.body.dataset.os = os;  // setup.css shows this computer's version in every slot
  }
  $$('.oses [data-os]').forEach(b => b.addEventListener('click', () => setOs(b.dataset.os)));
  const ua = navigator.userAgent, plat = navigator.userAgentData?.platform || navigator.platform || '';
  setOs(/win/i.test(plat) || /Windows/.test(ua) ? 'windows' : /mac|iphone|ipad/i.test(plat) || /Mac OS X/.test(ua) ? 'mac'
    : /linux|x11/i.test(plat + ua) && !/Android/.test(ua) ? 'linux' : 'windows');

  $$('[data-cmd]').forEach(c => { c.textContent = CMD[c.dataset.cmd]; });
  $$('.cmd .copy').forEach(b => b.addEventListener('click', async () => {
    const text = b.parentElement.querySelector('code').textContent;
    try { await navigator.clipboard.writeText(text); } catch {
      const r = document.createRange(); r.selectNodeContents(b.parentElement.querySelector('code'));
      getSelection().removeAllRanges(); getSelection().addRange(r);
      return;
    }
    b.classList.add('done');
    setTimeout(() => b.classList.remove('done'), 2000);
  }));
})();
