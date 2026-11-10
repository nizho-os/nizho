/* The Nizho film: 58 seconds, made from screen recordings of the Nizho image in a virtual machine,
   with camera moves and a few words added. It plays once, muted, on a visitor's first visit, unless
   they asked their system to reduce motion. It always has Pause and Skip, and any element with
   data-film plays it again. It loads nothing from other sites. */
(function () {
  'use strict';
  var KEY = 'nizho-film-seen';
  var SRC = 'media/nizho-intro.mp4';

  function seen() { try { return localStorage.getItem(KEY) === '1'; } catch (e) { return false; } }
  function markSeen() { try { localStorage.setItem(KEY, '1'); } catch (e) {} }
  function reduceMotion() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  var open = null;

  function play() {
    if (open) return;
    var lastFocus = document.activeElement;
    var box = document.createElement('div');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', 'Nizho film');
    box.style.cssText = 'position:fixed;inset:0;z-index:1000;background:#f5f6f8;display:flex;align-items:center;justify-content:center';

    var v = document.createElement('video');
    v.src = SRC; v.muted = true; v.playsInline = true; v.autoplay = true; v.preload = 'auto';
    v.setAttribute('aria-describedby', 'nizho-film-text');
    v.style.cssText = 'width:100%;height:100%;object-fit:contain;background:#f5f6f8';

    var text = document.createElement('p');
    text.id = 'nizho-film-text';
    text.style.cssText = 'position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)';
    text.textContent = 'A silent film. Privacy settings usually start off; Nizho turns them on for you. ' +
      'It shows the real setup screen listing six privacy settings as configured, the choice of a light or dark look, ' +
      'the top bar saying 6 of 6 settings are in place, the Privacy Dashboard, and the update screen explaining that ' +
      'going back to an older version leaves your files alone. Developer preview, recorded in a virtual machine, no installer yet.';

    var bar = document.createElement('div');
    bar.style.cssText = 'position:absolute;right:24px;bottom:24px;display:flex;gap:12px';
    function button(label) {
      var b = document.createElement('button');
      b.type = 'button'; b.textContent = label;
      b.style.cssText = 'font:500 15px/1 Inter,system-ui,sans-serif;color:#111317;background:#fff;border:1px solid #d6dae0;' +
        'border-radius:999px;padding:11px 18px;cursor:pointer';
      return b;
    }
    var pause = button('Pause (Space)');
    var skip = button('Skip (Esc)');
    bar.appendChild(pause); bar.appendChild(skip);
    box.appendChild(v); box.appendChild(text); box.appendChild(bar);
    document.body.appendChild(box);
    var overflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = 'hidden';
    open = box;

    function setPaused(p) {
      if (p) v.pause(); else { var r = v.play(); if (r && r.catch) r.catch(function () {}); }
      pause.textContent = p ? 'Play (Space)' : 'Pause (Space)';
    }
    function close() {
      if (!open) return;
      markSeen();
      v.pause(); v.removeAttribute('src'); v.load();
      document.removeEventListener('keydown', onKey, true);
      box.remove(); open = null;
      document.documentElement.style.overflow = overflow;
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    }
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); close(); }
      else if (e.key === ' ' || e.key === 'Spacebar') { e.preventDefault(); setPaused(!v.paused); }
      else if (e.key === 'Tab') {
        e.preventDefault();
        (document.activeElement === pause ? skip : pause).focus();
      }
    }
    pause.addEventListener('click', function () { setPaused(!v.paused); });
    skip.addEventListener('click', close);
    v.addEventListener('ended', close);
    v.addEventListener('error', close);
    document.addEventListener('keydown', onKey, true);
    pause.focus();
    var r = v.play(); if (r && r.catch) r.catch(function () { setPaused(true); });
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest && e.target.closest('[data-film]');
    if (t) { e.preventDefault(); play(); }
  });

  function firstVisit() {
    if (!seen() && !reduceMotion()) play();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', firstVisit);
  else firstVisit();
})();
