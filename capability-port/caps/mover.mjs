// Button mover — a capability. Turn "Move" on, then:
//   • drag any button or link to a new spot, and/or
//   • double-click it to rename its label.
// It still works where it lands (same element, same handlers — only its
// position and wording change). Moves and names are remembered per app screen
// and come back on reload. "Reset" puts everything on this screen back.
export const name = 'button-mover';
export const needs = ['dom', 'storage'];
export const place = 'drawer';

const KEY = 'button-mover:' + location.origin;          // positions (same format as before)
const NKEY = 'button-mover-names:' + location.origin;   // renamed labels
const PICKABLE = 'a,button,input[type=submit],input[type=button],[role=button],.ui.button,summary';
let slot, btn, info, on = false, drag = null, saved = {}, names = {}, observer;

const read = k => { try { return JSON.parse(localStorage.getItem(k) || '{}') || {}; } catch { return {}; } };
const load = () => { saved = read(KEY); names = read(NKEY); };
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(saved)); localStorage.setItem(NKEY, JSON.stringify(names)); } catch {} };
const screen = () => location.pathname;

// A stable-enough address for an element: id if it has one, else a tag:nth-of-type chain.
function address(el) {
  if (el.id) return '#' + CSS.escape(el.id);
  const parts = [];
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    if (n.id) { parts.unshift('#' + CSS.escape(n.id)); break; }
    let i = 1; for (let s = n.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === n.tagName) i++;
    parts.unshift(`${n.tagName.toLowerCase()}:nth-of-type(${i})`);
  }
  return parts.join(' > ');
}

function place_(el, dx, dy) {
  el.style.translate = `${dx}px ${dy}px`;
  el.style.position = el.style.position || (getComputedStyle(el).position === 'static' ? 'relative' : '');
  el.style.zIndex = '2147481000';
  el.dataset.moved = '1';
}

// ---- Labels -------------------------------------------------------------
// Change only the visible words, keeping icons/badges inside the button intact.
function labelNode(el) {
  const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, { acceptNode: n => n.nodeValue.trim() ? 1 : 3 });
  return w.nextNode();
}
function getLabel(el) {
  if (el.tagName === 'INPUT') return el.value;
  const n = labelNode(el); return n ? n.nodeValue.trim() : '';
}
function setLabel(el, text) {
  if (el.dataset.bmOrig === undefined) el.dataset.bmOrig = getLabel(el);
  const old = getLabel(el);
  if (old === text) return;
  if (el.tagName === 'INPUT') el.value = text;
  else {
    const n = labelNode(el);
    if (n) n.nodeValue = n.nodeValue.replace(n.nodeValue.trim(), text);
    else el.appendChild(document.createTextNode(text));
  }
  if (el.getAttribute('aria-label') === old) el.setAttribute('aria-label', text);
  el.dataset.renamed = '1';
}
function restoreLabel(el) {
  if (el.dataset.bmOrig !== undefined) setLabel(el, el.dataset.bmOrig);
  delete el.dataset.bmOrig; delete el.dataset.renamed;
}

function applySaved() {
  for (const [sel, [dx, dy]] of Object.entries(saved[screen()] || {})) {
    let el; try { el = document.querySelector(sel); } catch { continue; }
    if (el && el.dataset.moved !== '1') place_(el, dx, dy);
  }
  for (const [sel, text] of Object.entries(names[screen()] || {})) {
    let el; try { el = document.querySelector(sel); } catch { continue; }
    if (el && getLabel(el) !== text) setLabel(el, text);   // also undoes app re-renders
  }
  const t = status(); if (info.textContent !== t) info.textContent = t;
}
const cnt = o => Object.keys(o[screen()] || {}).length;
const status = () => `${cnt(saved)} moved, ${cnt(names)} renamed on this screen`;
const outside = e => !e.composedPath().some(x => x.id === 'capability-port');

export function mount(ctx) {
  slot = ctx.slot; load();
  slot.innerHTML = `<div style="font:13px system-ui,sans-serif;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <button data-t style="padding:7px 12px;border:0;border-radius:6px;background:#eee;cursor:pointer">Move: off</button>
    <button data-r style="padding:7px 12px;border:0;border-radius:6px;background:#fbe3e3;cursor:pointer">Reset</button>
    <span data-i style="color:#555"></span>
    <div style="width:100%;color:#777;font-size:12px">When Move is on: drag to move, double-click to rename.</div></div>`;
  btn = slot.querySelector('[data-t]'); info = slot.querySelector('[data-i]');
  const on_ = ctx.on || ((t, ty, fn, o) => t.addEventListener(ty, fn, o));
  on_(btn, 'click', () => { on = !on; btn.textContent = 'Move: ' + (on ? 'on' : 'off'); btn.style.background = on ? '#ffd98a' : '#eee'; document.documentElement.classList.toggle('bm-on', on); });
  on_(slot.querySelector('[data-r]'), 'click', () => {
    delete saved[screen()]; delete names[screen()]; save();
    document.querySelectorAll('[data-moved="1"]').forEach(el => { el.style.translate = ''; el.style.zIndex = ''; delete el.dataset.moved; });
    document.querySelectorAll('[data-renamed="1"]').forEach(restoreLabel);
    info.textContent = 'reset';
  });
  const style = document.createElement('style');
  style.textContent = `.bm-on :is(${PICKABLE}){outline:2px dashed #e0a020!important;outline-offset:2px;cursor:grab!important}
    [data-moved="1"],[data-renamed="1"]{box-shadow:0 0 0 2px #1f6f5f55}`;
  document.head.appendChild(style); ctx.own ? ctx.own(style) : null;

  on_(document, 'pointerdown', e => {
    if (!on || !outside(e)) return;
    const el = e.target.closest(PICKABLE); if (!el) return;
    e.preventDefault(); e.stopPropagation();
    const [dx, dy] = (el.style.translate || '0px 0px').split(' ').map(v => parseFloat(v) || 0);
    drag = { el, x: e.clientX, y: e.clientY, dx, dy, moved: false };
    el.style.cursor = 'grabbing';
  }, true);
  on_(document, 'pointermove', e => {
    if (!drag) return;
    if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 3) drag.moved = true;
    if (!drag.moved) return;   // a still click (e.g. half of a double-click) doesn't move anything
    place_(drag.el, Math.round(drag.dx + e.clientX - drag.x), Math.round(drag.dy + e.clientY - drag.y));
  }, true);
  on_(document, 'pointerup', () => {
    if (!drag) return;
    const { el, moved } = drag; el.style.cursor = ''; drag = null;
    if (!moved) return;
    const [dx, dy] = el.style.translate.split(' ').map(v => parseFloat(v) || 0);
    (saved[screen()] ||= {})[address(el)] = [dx, dy]; save();
    info.textContent = status();
  }, true);
  // While Move is on, a click must not trigger the button/link.
  on_(document, 'click', e => { if (on && outside(e) && e.target.closest(PICKABLE)) { e.preventDefault(); e.stopPropagation(); } }, true);
  // Double-click to rename. Uses the browser's own prompt box, so the app's
  // keyboard shortcuts can't eat the typing. Blank = back to the original name.
  on_(document, 'dblclick', e => {
    if (!on || !outside(e)) return;
    const el = e.target.closest(PICKABLE); if (!el) return;
    e.preventDefault(); e.stopPropagation();
    const orig = el.dataset.bmOrig ?? getLabel(el);
    const text = window.prompt(`Rename "${getLabel(el)}" (leave blank to restore "${orig}")`, getLabel(el));
    if (text === null) return;
    const sel = address(el), here = (names[screen()] ||= {});
    if (text.trim() === '' || text.trim() === orig) { restoreLabel(el); delete here[sel]; }
    else { setLabel(el, text.trim()); here[sel] = text.trim(); }
    save(); info.textContent = status();
  }, true);

  applySaved();
  // Apps that render late or re-render text (React/Ember/Vue) — re-apply when the DOM settles.
  observer = new MutationObserver(() => { clearTimeout(observer.t); observer.t = setTimeout(applySaved, 150); });
  observer.observe(document.body, { childList: true, subtree: true, characterData: true });
}

export function unmount() {
  observer?.disconnect(); on = false; drag = null;
  document.documentElement.classList.remove('bm-on');
}
