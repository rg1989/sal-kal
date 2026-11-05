'use strict';
// How Sal feels on a phone: haptics, the loading bar, page and sheet motion, skeletons. No dependencies.

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

// ---------------------------------------------------------------- haptics
// Android: the Vibration API. iPhone (Safari 18+): no vibration API, but toggling a native switch control gives the
// system's selection tick, so a hidden one is clicked. Both are no-ops elsewhere (desktop, older phones).
const Haptic = (() => {
  const patterns = { tap: 8, select: 10, success: [10, 60, 18], warn: [18, 50, 18], error: [30, 60, 30, 60, 30] };
  const canVibrate = typeof navigator !== 'undefined' && 'vibrate' in navigator;
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  let label = null, last = 0;
  function tick() {
    if (!label) {
      label = document.createElement('label');
      label.setAttribute('aria-hidden', 'true');
      label.style.cssText = 'position:fixed;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none;inset-block-start:0;inset-inline-start:0';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.setAttribute('switch', '');
      input.tabIndex = -1;
      label.append(input);
      document.body.append(label);
    }
    label.click();
  }
  return kind => {
    if (kind === 'none' || !kind) return;
    if (navigator.userActivation && !navigator.userActivation.hasBeenActive) return;  // browsers allow it only after a first tap
    const now = performance.now();
    if (now - last < 35) return;  // one burst per gesture
    last = now;
    try {
      if (canVibrate && !ios) navigator.vibrate(patterns[kind] ?? 8);
      else if (ios) {
        tick();
        if (kind === 'success' || kind === 'warn') setTimeout(tick, 90);
        if (kind === 'error') { setTimeout(tick, 90); setTimeout(tick, 180); }
      }
    } catch { /* not allowed before the first touch: fine */ }
  };
})();
const haptic = kind => Haptic(kind);

// ---------------------------------------------------------------- loading bar (under the header)
// Shows only when something takes longer than a blink, creeps towards the end while waiting, then completes.
const Loadbar = (() => {
  let n = 0, timer = null, shown = false, w = 0, creep = null;
  const bar = () => document.getElementById('loadbar');
  function show() {
    shown = true; w = 8;
    const b = bar(); if (!b) return;
    b.classList.remove('done'); b.classList.add('on');
    b.firstElementChild.style.transform = `scaleX(${w / 100})`;
    creep = setInterval(() => { w += (90 - w) * 0.08; b.firstElementChild.style.transform = `scaleX(${w / 100})`; }, 160);
  }
  function hide() {
    clearInterval(creep);
    const b = bar(); if (!b) return;
    b.firstElementChild.style.transform = 'scaleX(1)';
    b.classList.add('done');
    setTimeout(() => { if (!n) { b.classList.remove('on', 'done'); b.firstElementChild.style.transform = 'scaleX(0)'; } }, 380);
    shown = false;
  }
  return () => {
    n++;
    if (n === 1 && !shown) timer = setTimeout(show, 160);
    let ended = false;
    return () => {
      if (ended) return;
      ended = true;
      n = Math.max(0, n - 1);
      if (!n) { clearTimeout(timer); if (shown) hide(); }
    };
  };
})();
const loading = () => Loadbar();

// ---------------------------------------------------------------- motion
function enter(el) {
  if (!el || reduced()) return;
  el.classList.remove('enter');
  void el.offsetWidth;
  el.classList.add('enter');
  el.addEventListener('animationend', () => el.classList.remove('enter'), { once: true });
}

// the product's photo flies into the cart (the header button on phones, the cart column on wide screens)
function flyToCart(from) {
  if (!from || reduced()) return;
  const target = matchMedia('(min-width: 1100px)').matches ? document.querySelector('#cart .lc-top')
    : !document.getElementById('cart-bar').hidden ? document.getElementById('cart-bar') : document.getElementById('basket-btn');
  if (!target) return;
  const a = from.getBoundingClientRect(), b = target.getBoundingClientRect();
  if (!a.width || !b.width) return;
  const ghost = from.cloneNode(true);
  ghost.classList.add('fly');
  Object.assign(ghost.style, { position: 'fixed', left: `${a.left}px`, top: `${a.top}px`, width: `${a.width}px`, height: `${a.height}px`, margin: 0, zIndex: 120 });
  document.body.append(ghost);
  const dx = b.left + b.width / 2 - (a.left + a.width / 2), dy = b.top + b.height / 2 - (a.top + a.height / 2);
  const anim = ghost.animate([
    { transform: 'translate(0,0) scale(1)', opacity: 1, borderRadius: '12px' },
    { transform: `translate(${dx * 0.55}px, ${dy * 0.55 - 60}px) scale(.55)`, opacity: .95, offset: .55 },
    { transform: `translate(${dx}px, ${dy}px) scale(.16)`, opacity: .2, borderRadius: '50%' },
  ], { duration: 620, easing: 'cubic-bezier(.45,.05,.3,1)' });
  anim.onfinish = () => {
    ghost.remove();
    target.classList.remove('bump'); void target.offsetWidth; target.classList.add('bump');
  };
}

// ---------------------------------------------------------------- sheets: dialogs that slide up on phones, drag to close
function openSheet(d) {
  if (d.open) return;
  d.classList.remove('closing');
  d.showModal();
  const inner = d.querySelector('.sheet-inner');
  if (inner) { inner.tabIndex = -1; inner.focus({ preventScroll: true }); }
  haptic('tap');
}
function closeSheet(d, value) {
  if (!d.open || d.classList.contains('closing')) return;
  if (reduced()) { d.close(value); return; }
  d.classList.add('closing');
  const done = () => { d.classList.remove('closing'); d.style.transform = ''; if (d.open) d.close(value); };
  d.addEventListener('animationend', done, { once: true });
  setTimeout(done, 320);  // in case the animation never runs (hidden tab)
}
// pull a sheet (or the phone cart) down by its top edge to close it
function dragToClose(el, close, handleSel = '.sheet-grab, .sheet-head, .lc-top') {
  let y0 = null, dy = 0, id = null;
  el.addEventListener('pointerdown', e => {
    if (!matchMedia('(max-width: 640px)').matches || e.button > 0) return;
    if (!e.target.closest(handleSel) || e.target.closest('button, input, a, select')) return;
    y0 = e.clientY; dy = 0; id = e.pointerId;
    el.setPointerCapture(id);
    el.style.transition = 'none';
  });
  el.addEventListener('pointermove', e => {
    if (y0 == null || e.pointerId !== id) return;
    dy = Math.max(0, e.clientY - y0);
    el.style.transform = `translateY(${dy}px)`;
  });
  const end = e => {
    if (y0 == null || e.pointerId !== id) return;
    y0 = null;
    el.style.transition = '';
    if (dy > 90) { haptic('tap'); close(); setTimeout(() => { el.style.transform = ''; }, 300); }
    else el.style.transform = '';
  };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
}
document.querySelectorAll('dialog.sheet').forEach(d => {
  dragToClose(d, () => closeSheet(d));
  d.addEventListener('cancel', e => { e.preventDefault(); closeSheet(d); });  // Esc: animate out too
});

// a yes/no question in Sal's own sheet instead of the browser's confirm(); red for deleting, `safe` for anything else
function confirmSheet(question, yes, safe = false) {
  const d = document.getElementById('confirm'), y = document.getElementById('confirm-yes');
  document.getElementById('confirm-q').textContent = question;
  y.textContent = yes;
  y.classList.toggle('danger', !safe);
  openSheet(d);
  haptic(safe ? 'select' : 'warn');
  return new Promise(ok => {
    const pick = e => {
      const b = e.target.closest('[data-answer]');
      if (!b && e.target !== d) return;
      d.removeEventListener('click', pick);
      d.removeEventListener('close', none);
      closeSheet(d);
      ok(b?.dataset.answer === 'yes');
    };
    const none = () => { d.removeEventListener('click', pick); ok(false); };
    d.addEventListener('click', pick);
    d.addEventListener('close', none, { once: true });
  });
}

// ---------------------------------------------------------------- tooltips: [data-tip] shows its text on hover or focus,
// and on a tap (phones have no hover); a tap anywhere else, scrolling or Esc closes it
const tipEl = Object.assign(document.createElement('div'), { className: 'tip', id: 'tip' });
tipEl.setAttribute('role', 'tooltip');
tipEl.hidden = true;
document.body.append(tipEl);
let tipFor = null;
function showTip(el, text = el.dataset.tip) {
  if (!el.isConnected || !text) return;
  tipFor = el;
  tipEl.textContent = text;
  tipEl.hidden = false;
  el.setAttribute('aria-describedby', 'tip');
  const r = el.getBoundingClientRect(), w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  const x = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2));
  const y = r.top - h - 8 >= 8 ? r.top - h - 8 : r.bottom + 8;
  tipEl.style.left = `${Math.round(x)}px`;
  tipEl.style.top = `${Math.round(y)}px`;
}
function hideTip() {
  tipFor?.removeAttribute('aria-describedby');
  tipFor = null;
  tipEl.hidden = true;
}
const canHover = matchMedia('(hover: hover)');
// text cut short by an ellipsis or a line clamp anywhere on the page: its full words, in the same tooltip, on hover
function clipped(target) {
  for (let el = target, i = 0; el && el !== document.body && i < 4; el = el.parentElement, i++) {
    const cs = getComputedStyle(el);
    const cuts = cs.textOverflow === 'ellipsis' || (cs.webkitLineClamp && cs.webkitLineClamp !== 'none');
    if (cuts && (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)) return el;
  }
  return null;
}
document.addEventListener('mouseover', e => {
  if (!canHover.matches) return;
  const el = e.target.closest?.('[data-tip]');
  if (el) { if (el !== tipFor) showTip(el); return; }
  const cut = clipped(e.target);
  if (cut && cut !== tipFor) {
    const copy = cut.cloneNode(true);
    copy.querySelectorAll('[data-tip], [aria-hidden="true"], .sr-only').forEach(x => x.remove());  // its words only
    showTip(cut, copy.textContent.replace(/\s+/g, ' ').trim());
  }
});
document.addEventListener('mouseout', e => { if (tipFor && canHover.matches && !tipFor.contains(e.relatedTarget)) hideTip(); });
document.addEventListener('focusin', e => { const el = e.target.closest?.('[data-tip]'); if (el) showTip(el); });
document.addEventListener('focusout', e => { if (tipFor && e.target === tipFor) hideTip(); });
document.addEventListener('click', e => {  // capture: a tap on the icon only opens its tip, never what's behind it
  const el = e.target.closest?.('[data-tip]');
  if (el) { e.preventDefault(); e.stopPropagation(); haptic('select'); showTip(el); } else if (tipFor) hideTip();
}, true);
addEventListener('scroll', () => { if (tipFor) hideTip(); }, true);
addEventListener('keydown', e => { if (e.key === 'Escape' && tipFor) hideTip(); });

// ---------------------------------------------------------------- skeletons: the page's shape while it loads
function productSkeleton() {
  return `<div class="sk-product" aria-hidden="true"><span class="sk-back"></span>
    <div class="sk-head"><span class="sk-thumb"></span><div><span class="sk-line w70"></span><span class="sk-line w40"></span><span class="sk-line w30 tall"></span><span class="sk-btn"></span></div></div>
    <span class="sk-line w30"></span>${'<span class="sk-row"></span>'.repeat(4)}</div>`;
}
// a cart's rows while the carts load (never "this cart is empty" before we know)
function cartSkeleton(n) {
  return '<li class="item skel" aria-hidden="true"><span class="lc-thumb"></span><span class="item-name"><span class="sk-line"></span></span><span class="item-meta"><span class="sk-line"></span></span><span class="stepper sk-step"></span><span class="lc-price"><span class="sk-price"></span></span></li>'.repeat(n);
}
// the cheapest stores while the comparison runs
function storeSkeleton(n) {
  return '<div class="lc-store skel" aria-hidden="true"><span class="ls-name"><span class="sk-line"></span></span><span class="ls-sub"><span class="sk-line"></span></span><span class="ls-total"><span class="sk-price big"></span></span></div>'.repeat(n)
    + `<p class="sr-only">${t('Finding the cheapest store…')}</p>`;
}
function catSkeleton(n) {
  return '<span class="cat skel" aria-hidden="true"><span class="cat-icon"></span><span class="sk-line"></span></span>'.repeat(n);
}
function resultsSkeleton() {
  return `<div class="sk-results" aria-hidden="true"><span class="sk-line w40"></span>
    <div class="sk-winner"><span class="sk-tag"></span><div>${'<span class="sk-line"></span>'.repeat(5)}</div></div>
    ${'<span class="sk-row"></span>'.repeat(3)}</div>`;
}
function backLink() {
  return `<a class="back" href="#/" data-back><svg class="flip" viewBox="0 0 20 20" aria-hidden="true"><path d="M12.5 4.5 7 10l5.5 5.5" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>${t('Back')}</a>`;
}
