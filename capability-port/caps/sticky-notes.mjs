// Sticky notes — a capability. Type a note in the drawer and press "Pin note":
// a coloured note appears on the live app page. Drag it anywhere; × removes it.
// Notes are remembered per app screen and come back on reload. "Clear" removes
// every note on this screen.
//
// Loaded through the proxy like every capability: attach('/_cp/caps/sticky-notes.mjs').
// All styling goes through element.style (CSSOM), never <style> or style="…" markup,
// so it still renders on apps whose Content-Security-Policy refuses inline styles.
export const name = 'sticky-notes';
export const needs = ['dom', 'storage'];
export const place = 'drawer';

const KEY = 'sticky-notes:' + location.origin;
const COLOURS = ['#ffe066', '#9be7a0', '#8fd3ff', '#ffb3c7'];
const screen = () => location.pathname;
let slot, info, layer, notes = {}, drag = null;

const read = () => { try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { return {}; } };
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(notes)); } catch {} };
const here = () => (notes[screen()] ||= []);
const css = (el, props) => { for (const [k, v] of Object.entries(props)) el.style.setProperty(k, v); return el; };
const make = (tag, props, text) => { const el = css(document.createElement(tag), props || {}); if (text != null) el.textContent = text; return el; };

function status() {
  info.textContent = `${here().length} note(s) on this screen`;
}

function draw() {
  layer.replaceChildren();
  here().forEach((n, i) => {
    const card = make('div', {
      position: 'fixed', left: n.x + 'px', top: n.y + 'px', width: '190px', 'min-height': '70px',
      padding: '10px 12px 12px', background: n.colour, color: '#222', 'border-radius': '4px',
      'box-shadow': '0 8px 20px rgba(0,0,0,.28)', font: '14px/1.35 system-ui, sans-serif',
      transform: `rotate(${i % 2 ? 1.5 : -1.5}deg)`, cursor: 'grab', 'z-index': '2147481500',
      'white-space': 'pre-wrap', 'overflow-wrap': 'anywhere', 'pointer-events': 'auto',
    });
    card.dataset.stickyNote = String(i);
    const close = make('button', {
      position: 'absolute', top: '2px', right: '4px', border: '0', background: 'transparent',
      font: '16px/1 system-ui', cursor: 'pointer', color: '#333', padding: '2px',
    }, '×');
    close.setAttribute('aria-label', 'Remove note');
    close.addEventListener('click', (e) => { e.stopPropagation(); here().splice(i, 1); save(); draw(); status(); });
    card.append(make('div', { 'padding-right': '14px' }, n.text), close);
    card.addEventListener('pointerdown', (e) => {
      if (e.target === close) return;
      e.preventDefault();
      drag = { i, card, x: e.clientX, y: e.clientY, ox: n.x, oy: n.y };
      card.style.cursor = 'grabbing';
    });
    layer.append(card);
  });
}

export function mount(ctx) {
  slot = ctx.slot;
  notes = read();
  const on = ctx.on || ((t, ty, fn, o) => t.addEventListener(ty, fn, o));

  const box = make('div', { font: '13px system-ui, sans-serif', display: 'flex', 'flex-direction': 'column', gap: '8px' });
  const title = make('b', { 'font-weight': '600' }, 'Sticky notes');
  const text = make('textarea', { width: '100%', 'min-height': '54px', 'box-sizing': 'border-box', padding: '6px 8px',
    border: '1px solid #cfcfcf', 'border-radius': '6px', font: '13px/1.4 system-ui, sans-serif' });
  text.placeholder = 'Write a note, then pin it to the page';
  text.setAttribute('aria-label', 'Note text');
  const row = make('div', { display: 'flex', gap: '8px', 'align-items': 'center', 'flex-wrap': 'wrap' });
  const pin = make('button', { padding: '7px 12px', border: '0', 'border-radius': '6px', background: '#ffe066', cursor: 'pointer' }, 'Pin note');
  pin.dataset.pin = '';
  const clear = make('button', { padding: '7px 12px', border: '0', 'border-radius': '6px', background: '#fbe3e3', cursor: 'pointer' }, 'Clear');
  clear.dataset.clear = '';
  info = make('span', { color: '#555' });
  row.append(pin, clear, info);
  box.append(title, text, row);
  slot.append(box);

  // Notes live in their own fixed layer on the page, outside the Port's shadow root.
  layer = make('div', { position: 'fixed', inset: '0', 'pointer-events': 'none', 'z-index': '2147481500' });
  layer.dataset.stickyNotes = '';
  document.body.append(layer);
  ctx.own ? ctx.own(layer) : null;

  on(pin, 'click', () => {
    const list = here();
    const i = list.length;
    list.push({ text: text.value.trim() || 'Note', x: 60 + (i % 4) * 40, y: 90 + (i % 4) * 34, colour: COLOURS[i % COLOURS.length] });
    text.value = '';
    save(); draw(); status();
  });
  on(clear, 'click', () => { delete notes[screen()]; save(); draw(); status(); });
  on(document, 'pointermove', (e) => {
    if (!drag) return;
    const n = here()[drag.i];
    n.x = Math.round(drag.ox + e.clientX - drag.x);
    n.y = Math.round(drag.oy + e.clientY - drag.y);
    drag.card.style.left = n.x + 'px';
    drag.card.style.top = n.y + 'px';
  }, true);
  on(document, 'pointerup', () => { if (!drag) return; drag.card.style.cursor = 'grab'; drag = null; save(); }, true);

  draw(); status();
}

export function unmount() {
  drag = null;
  layer?.remove();
}
