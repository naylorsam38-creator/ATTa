/* ============================================================================
   CAPABILITY PORT — port.js  (v0.2)
   Drop this one file into any running app that can show a screen.
   It works out what the app is, takes any capability you hand it, works out
   what that capability needs, matches the two, and puts it in the live app.

   Trust tiers (see README):
     trusted   — runs on the page itself, full access. Your own capabilities.
     sandboxed — runs in a sealed frame, talks to the port by messages only.
     raw code  — pasted script text; trusted, but only after a warning + confirm.

   ---------------------------------------------------------------------------
   RULES / CONFIG — edit these, nothing below needs touching
   --------------------------------------------------------------------------- */
const RULES = {
  // Text on the floating button. Change it: the button says something else.
  BUTTON_LABEL: '+',

  // Corner the button sits in: 'bottom-right','bottom-left','top-right','top-left'.
  BUTTON_CORNER: 'bottom-right',

  // Remember attached capabilities and bring them back next time. false: forget on reload.
  PERSIST: true,

  // Identity of THIS app for storage. Two apps on the same domain with different
  // ids keep separate attachments. Leave '' to use the page path.
  APP_ID: '',

  // Prefix for the storage key. Change it: old memory is ignored, fresh start.
  STORAGE_KEY: 'capability-port',

  // Refuse a capability whose needs the host can't meet. false: attach anyway.
  STRICT_NEEDS: true,

  // Seconds to wait for a capability to load AND mount before giving up and cleaning up.
  LOAD_TIMEOUT_SECONDS: 20,

  // Where a capability lands when you don't point at a spot: 'drawer' or 'body'.
  DEFAULT_PLACEMENT: 'drawer',

  // Trust given to a capability loaded from a DIFFERENT origin than this app.
  // 'sandboxed' = sealed frame (safe default). 'trusted' = full page access.
  REMOTE_TRUST: 'sandboxed',

  // Trust given to a capability loaded from THIS app's own origin.
  LOCAL_TRUST: 'trusted',

  // Allow pasted raw script text at all. It always asks first; false = never.
  ALLOW_RAW_CODE: true,

  // Ask before bringing back remote capabilities on reload (they may have changed).
  CONFIRM_REMOTE_RESTORE: true,

  // How the port asks you yes/no questions. Replace with your own function if you like.
  CONFIRM: (text) => window.confirm(text),

  // Accent colour of the port's own UI.
  ACCENT: '#1f6f5f',
};
/* ========================================================================= */

(function CapabilityPort(global) {
  if (global.__capabilityPort) return; // already on this page — never install twice

  const NEED_KEYS = ['screen','dom','network','storage','camera','microphone','geolocation',
    'notifications','clipboard','bluetooth','usb','webgl','audio','websocket','workers','modules','secureContext'];

  /* ---------- 1. PROBE: what this app is and what is AVAILABLE here -------- */
  // "available" means the browser API exists. It is not permission: the browser
  // and the user may still refuse camera, mic, location etc. at use time.
  function probe() {
    const d = global.document;
    const has = (p, o) => { try { return !!(o || global)[p]; } catch { return false; } };
    const framework =
      d.querySelector('[data-reactroot],#root,#__next') && (global.React || Object.keys(d.body).some(k => k.startsWith('__react')) || !!global.__REACT_DEVTOOLS_GLOBAL_HOOK__) ? 'react' :
      global.__VUE__ || d.querySelector('[data-v-app]') ? 'vue' :
      global.ng || d.querySelector('[ng-version]') ? 'angular' :
      global.__svelte || d.querySelector('[class*="svelte-"]') ? 'svelte' :
      global.jQuery ? 'jquery' : 'plain';
    const root = ['#root', '#app', '#__next', 'main', '[role=main]', 'body'].map(s => d.querySelector(s)).find(Boolean) || d.body;
    const cs = getComputedStyle(d.body);
    return {
      url: location.href, origin: location.origin, title: d.title, framework, root,
      routes: [...d.querySelectorAll('a[href]')].map(a => a.getAttribute('href')).filter(h => h && h.startsWith('/')).slice(0, 50),
      theme: { font: cs.fontFamily, background: cs.backgroundColor, color: cs.color },
      available: {
        screen: true, dom: true, network: has('fetch'), storage: storageWorks(),
        camera: !!navigator.mediaDevices?.getUserMedia, microphone: !!navigator.mediaDevices?.getUserMedia,
        geolocation: has('geolocation', navigator), notifications: has('Notification'), clipboard: has('clipboard', navigator),
        bluetooth: has('bluetooth', navigator), usb: has('usb', navigator),
        webgl: (() => { try { return !!d.createElement('canvas').getContext('webgl'); } catch { return false; } })(),
        audio: has('AudioContext') || has('webkitAudioContext'), websocket: has('WebSocket'), workers: has('Worker'),
        modules: 'noModule' in d.createElement('script'), secureContext: !!global.isSecureContext,
      },
    };
  }
  function storageWorks() { try { const k = '__cp_probe__'; localStorage.setItem(k, '1'); localStorage.removeItem(k); return true; } catch { return false; } }

  /* ---------- 2. RECOGNISE: what kind of thing was handed over ------------- */
  function parseUrl(text) {
    let u; try { u = new URL(text, location.href); } catch { return null; }
    if (u.protocol !== 'http:' && u.protocol !== 'https:') throw new Error('Only http and https links are accepted');
    return u;
  }
  function recognise(input) {
    if (input && typeof input === 'object' && typeof input.mount === 'function')
      return { kind: 'module', mod: input, source: '(object)', remote: false };
    if (typeof input !== 'string') throw new Error('The port can only take text, a link, or a capability object');
    const text = input.trim();
    if (!text) throw new Error('Nothing to attach');
    if (/^(https?:\/\/|\/|\.\/)/i.test(text) && !/\s/.test(text)) {
      const u = parseUrl(text); const url = u.href, remote = u.origin !== location.origin;
      if (/\.m?js$/i.test(u.pathname)) return { kind: 'module-url', url, source: url, remote };
      if (/\.json$/i.test(u.pathname)) return { kind: 'manifest-url', url, source: url, remote };
      if (/\.css$/i.test(u.pathname)) return { kind: 'stylesheet', url, source: url, remote };
      if (/\.(png|jpe?g|gif|svg|webp)$/i.test(u.pathname)) return { kind: 'image', url, source: url, remote };
      return { kind: 'page', url, source: url, remote };
    }
    if (/^[a-z][a-z0-9+.-]*:/i.test(text) && !/\s/.test(text) && !/^\s*</.test(text)) parseUrl(text); // throws for javascript:, data:, file:
    if (/^\s*</.test(text)) {
      if (/<script[\s>]|<style[\s>]|<html/i.test(text)) return { kind: 'document', html: text, source: '(html document)', remote: false };
      return { kind: 'fragment', html: text, source: '(html fragment)', remote: false };
    }
    if (/^\s*\{/.test(text)) {
      let m; try { m = JSON.parse(text); } catch { throw new Error('That looks like JSON but does not parse'); }
      return { kind: 'manifest', manifest: m, base: location.href, source: '(manifest)', remote: false };
    }
    if (/\bexport\b|\bimport\b/.test(text)) return { kind: 'module-source', code: text, source: '(module source)', remote: false };
    return { kind: 'script-source', code: text, source: '(script source)', remote: false };
  }

  function validateManifest(m, base) {
    if (!m || typeof m !== 'object' || Array.isArray(m)) throw new Error('Manifest must be an object');
    const allowed = ['name', 'needs', 'entry', 'html', 'url', 'place', 'trust'];
    for (const k of Object.keys(m)) if (!allowed.includes(k)) throw new Error(`Manifest has an unknown field "${k}"`);
    if (m.name !== undefined && (typeof m.name !== 'string' || !m.name.trim() || m.name.length > 80)) throw new Error('Manifest name must be text up to 80 characters');
    if (m.needs !== undefined) {
      if (!Array.isArray(m.needs) || !m.needs.every(n => typeof n === 'string')) throw new Error('Manifest needs must be a list of words');
      const bad = m.needs.filter(n => !NEED_KEYS.includes(n)); if (bad.length) throw new Error(`Manifest asks for unknown needs: ${bad.join(', ')}`);
    }
    const sources = ['entry', 'html', 'url'].filter(k => m[k] !== undefined);
    if (sources.length !== 1) throw new Error('Manifest must have exactly one of entry, html or url');
    if (typeof m[sources[0]] !== 'string' || !m[sources[0]].trim()) throw new Error(`Manifest ${sources[0]} must be text`);
    if (m.place !== undefined && (typeof m.place !== 'string' || m.place.length > 200)) throw new Error('Manifest place must be text');
    if (m.trust !== undefined && !['trusted', 'sandboxed'].includes(m.trust)) throw new Error('Manifest trust must be trusted or sandboxed');
    const out = { ...m };
    if (m.entry) out.entry = parseUrl(new URL(m.entry, base).href).href;
    if (m.url) out.url = parseUrl(new URL(m.url, base).href).href;
    return out;
  }

  /* ---------- 3. TRUST: decide the tier before anything runs --------------- */
  // Returns 'trusted' | 'sandboxed' | 'embedded' (a whole page: shown, never given the host).
  async function decideTrust(rec, requested) {
    const remote = !!rec.remote;
    switch (rec.kind) {
      case 'module': return 'trusted';
      case 'page': return 'embedded';
      case 'image': case 'stylesheet': case 'fragment': case 'document':
        // HTML is not sanitised: event handlers, SVG and embedded resources can all act. So it is trusted-only.
        if (rec.kind === 'fragment' || rec.kind === 'document') return 'trusted';
        return 'trusted';
      case 'script-source': {
        if (!RULES.ALLOW_RAW_CODE) throw new Error('Raw code is switched off in this port (ALLOW_RAW_CODE)');
        const ok = await Promise.resolve(RULES.CONFIRM(
          'This is raw code. It will run on the page itself with full access: it can read and change everything on this page, its storage and its network. Only continue if you trust where it came from.\n\nRun it?'));
        if (!ok) throw new Error('Raw code was not confirmed, nothing was run');
        return 'trusted';
      }
      case 'module-source': return 'trusted';
      case 'module-url': case 'manifest-url': case 'manifest': {
        const def = remote ? RULES.REMOTE_TRUST : RULES.LOCAL_TRUST;
        if (requested === 'trusted' && def === 'sandboxed') {
          const ok = await Promise.resolve(RULES.CONFIRM(`${rec.source}\n\ncomes from another origin and is asking to run with full page access instead of in a sealed frame.\n\nGive it full access?`));
          return ok ? 'trusted' : 'sandboxed';
        }
        return requested === 'sandboxed' ? 'sandboxed' : def;
      }
    }
    throw new Error(`Port cannot recognise this: ${rec.kind}`);
  }

  /* ---------- 4. NORMALISE: one shape { name, needs, place, mount, unmount } */
  async function normalise(rec, res) {
    switch (rec.kind) {
      case 'module': return finish(rec.mod, rec);
      case 'module-url': return finish(await importUrl(rec.url, res), rec);
      case 'module-source': return finish(await importUrl(res.blob(rec.code, 'text/javascript'), res), rec);
      case 'manifest-url': {
        const r = await fetch(rec.url); if (!r.ok) throw new Error(`Manifest fetch failed (${r.status})`);
        let m; try { m = await r.json(); } catch { throw new Error('Manifest is not valid JSON'); }
        return normalise({ ...rec, kind: 'manifest', manifest: m, base: rec.url }, res);
      }
      case 'manifest': {
        const m = validateManifest(rec.manifest, rec.base);
        let mod = {};
        if (m.entry) mod = await importUrl(m.entry, res); else if (m.html) mod = htmlModule(m.html); else mod = pageModule(m.url);
        return finish({ ...mod, name: m.name || mod.name, needs: m.needs || mod.needs, place: m.place || mod.place }, rec);
      }
      case 'fragment': return finish(htmlModule(rec.html), rec);
      case 'document': return finish(pageModule(res.blob(rec.html, 'text/html')), rec);
      case 'page': return finish(pageModule(rec.url), rec);
      case 'stylesheet': return finish({ name: 'stylesheet', needs: ['dom'],
        mount(ctx) { const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = rec.url; document.head.appendChild(l); ctx.own(l); },
        unmount() {} }, rec);
      case 'image': return finish(htmlModule(`<img src="${rec.url.replace(/"/g, '&quot;')}" style="max-width:100%">`), rec);
      case 'script-source': return finish({ name: 'script', needs: ['dom'],
        mount(ctx) { const fn = new Function('port', 'slot', 'host', 'ctx', rec.code); return fn(ctx.port, ctx.slot, ctx.host, ctx); },
        unmount() {} }, rec);
    }
    throw new Error(`Port cannot recognise this: ${rec.kind}`);
  }
  function finish(mod, rec) {
    const cap = mod.default && typeof mod.default.mount === 'function' ? mod.default : mod;
    if (typeof cap.mount !== 'function') throw new Error('Capability has no mount() and is not something the port can wrap');
    const name = String(cap.name || mod.name || rec.source).slice(0, 80);
    const needs = Array.isArray(cap.needs) ? cap.needs : Array.isArray(mod.needs) ? mod.needs : ['dom'];
    return { name, needs: needs.filter(n => typeof n === 'string'), place: cap.place || mod.place, trust: cap.trust || mod.trust,
      mount: cap.mount.bind(cap), unmount: (cap.unmount || (() => {})).bind(cap), source: rec.source, entry: rec.url || (rec.kind === 'manifest' && rec.manifest.entry) || null };
  }
  function htmlModule(html) {
    return { name: 'html', needs: ['dom'],
      mount(ctx) { const r = document.createRange(); r.selectNode(ctx.slot); ctx.slot.appendChild(r.createContextualFragment(html)); }, unmount() {} };
  }
  function pageModule(url) {
    return { name: url.startsWith('blob:') ? 'html page' : url, needs: ['dom'],
      mount(ctx) { const f = document.createElement('iframe'); f.src = url; f.sandbox = 'allow-scripts allow-forms'; f.referrerPolicy = 'no-referrer';
        f.style.cssText = 'width:100%;height:100%;min-height:320px;border:0;'; ctx.slot.appendChild(f); ctx.own(f); }, unmount() {} };
  }
  function importUrl(url, res) {
    return withTimeout(import(/* @vite-ignore */ url), `loading ${url}`);
  }
  function withTimeout(p, what) {
    return Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error(`Timed out ${what} after ${RULES.LOAD_TIMEOUT_SECONDS}s`)), RULES.LOAD_TIMEOUT_SECONDS * 1000))]);
  }

  /* ---------- 5. LIFECYCLE: every attachment owns its resources ----------- */
  // Resources: the AbortController (for listeners), timers, injected nodes, blob URLs.
  function resources() {
    const ctl = new AbortController(); const timers = new Set(); const nodes = new Set(); const blobs = new Set();
    return {
      signal: ctl.signal,
      on(target, type, fn, opts = {}) { target.addEventListener(type, fn, { ...(typeof opts === 'boolean' ? { capture: opts } : opts), signal: ctl.signal }); },
      interval(fn, ms) { const t = setInterval(fn, ms); timers.add(['i', t]); return t; },
      timeout(fn, ms) { const t = setTimeout(fn, ms); timers.add(['t', t]); return t; },
      own(node) { nodes.add(node); return node; },
      style(css) { const s = document.createElement('style'); s.textContent = css; document.head.appendChild(s); nodes.add(s); return s; },
      blob(text, type) { const u = URL.createObjectURL(new Blob([text], { type })); blobs.add(u); return u; },
      dispose() {
        ctl.abort();
        for (const [k, t] of timers) (k === 'i' ? clearInterval : clearTimeout)(t); timers.clear();
        for (const n of nodes) { try { n.remove(); } catch {} } nodes.clear();
        for (const u of blobs) { try { URL.revokeObjectURL(u); } catch {} } blobs.clear();
      },
    };
  }

  /* ---------- 6. NEGOTIATE + PLACE + MOUNT ------------------------------- */
  const attached = new Map();
  const publicHost = () => Object.freeze({ framework: host.framework, title: host.title, root: host.root, theme: Object.freeze({ ...host.theme }),
    routes: Object.freeze([...host.routes]), available: Object.freeze({ ...host.available }) });

  async function attach(input, placement, opts = {}) {
    const rec = recognise(input);
    const requested = opts.trust || (rec.kind === 'manifest' && rec.manifest?.trust) || undefined;
    const trust = await decideTrust(rec, requested);
    const res = resources();
    const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
    let slot, cap;
    try {
      if (trust === 'sandboxed') { cap = sandboxModule(rec, res); }
      else { cap = await normalise(rec, res); if (cap.trust === 'sandboxed' && rec.kind !== 'module') { res.dispose(); return attach(input, placement, { ...opts, trust: 'sandboxed' }); } }
      const deal = negotiate(cap);
      if (!deal.ok) throw new Error(`${cap.name} needs ${deal.missing.join(', ')} and this app can't give that here.`);
      const target = resolvePlacement(placement || cap.place || RULES.DEFAULT_PLACEMENT);
      slot = document.createElement('section'); slot.dataset.capability = cap.name; slot.dataset.trust = trust; slot.style.cssText = 'display:block;position:relative;';
      target.appendChild(slot);
      const ctx = { port: api, host: publicHost(), slot, id, trust, signal: res.signal, on: res.on, interval: res.interval, timeout: res.timeout, own: res.own, style: res.style };
      await withTimeout(Promise.resolve(cap.mount(ctx)), `mounting ${cap.name}`);
      attached.set(id, { cap, slot, res, input, placement: placementRecord(target, placement), trust, remote: !!rec.remote });
      if (RULES.PERSIST && typeof input === 'string') persist();
      ui.say(`${cap.name} is in (${trust}).`); ui.refresh();
      return id;
    } catch (e) {
      res.dispose(); slot?.remove();
      ui.say(e.message, true); ui.refresh();
      throw e;
    }
  }

  async function detach(id) {
    const a = attached.get(id); if (!a) return;
    try { await a.cap.unmount({ port: api, host: publicHost(), slot: a.slot, id }); } catch {}
    finally { a.res.dispose(); a.slot.remove(); attached.delete(id); if (RULES.PERSIST) persist(); ui.refresh(); }
  }

  function negotiate(cap) {
    const missing = cap.needs.filter(n => !host.available[n]);
    return { ok: missing.length === 0 || !RULES.STRICT_NEEDS, missing };
  }

  // Sandboxed tier: the capability runs in a sealed iframe. It gets its own document
  // to draw in and a message channel; it never touches this page.
  function sandboxModule(rec, res) {
    let entry;
    if (rec.kind === 'module-url') entry = rec.url;
    else if (rec.kind === 'manifest') { const m = validateManifest(rec.manifest, rec.base); if (!m.entry) throw new Error('Only entry-based manifests can be sandboxed'); entry = m.entry; }
    else if (rec.kind === 'manifest-url') throw new Error('internal: manifest-url must be resolved first');
    else throw new Error('Only module capabilities can be sandboxed');
    const name = rec.manifest?.name || entry;
    const cap = { name, needs: rec.manifest?.needs || ['dom'], place: rec.manifest?.place, source: rec.source, entry, trust: 'sandboxed',
      mount(ctx) {
        return new Promise((resolve, reject) => {
          const f = document.createElement('iframe'); f.sandbox = 'allow-scripts'; f.referrerPolicy = 'no-referrer';
          f.style.cssText = 'width:100%;min-height:120px;border:0;'; ctx.own(f);
          const hostInfo = { framework: host.framework, title: host.title, available: host.available };
          f.srcdoc = `<!doctype html><meta charset="utf-8"><body style="margin:0;font:14px system-ui,sans-serif"><script type="module">
            const send = (type, data) => parent.postMessage({ cp: true, type, data }, '*');
            const port = { say: t => send('say', String(t)), resize: h => send('resize', Number(h)) };
            const host = ${JSON.stringify(hostInfo)};
            try { const m = await import(${JSON.stringify(entry)}); const cap = m.default?.mount ? m.default : m;
              await cap.mount({ port, host, slot: document.body, id: ${JSON.stringify(ctx.id)}, trust: 'sandboxed' });
              send('mounted', String(cap.name || '')); new ResizeObserver(() => port.resize(document.documentElement.scrollHeight)).observe(document.body); }
            catch (e) { send('error', String(e && e.message || e)); }
          <\/script>`;
          ctx.on(window, 'message', ev => {
            if (ev.source !== f.contentWindow || !ev.data?.cp) return;
            if (ev.data.type === 'mounted') { if (typeof ev.data.data === 'string' && ev.data.data) { cap.name = ev.data.data.slice(0, 80); ctx.slot.dataset.capability = cap.name; } resolve(); }
            else if (ev.data.type === 'error') reject(new Error(`sandboxed ${name}: ${ev.data.data}`));
            else if (ev.data.type === 'say') ui.say(`${cap.name}: ${ev.data.data}`);
            else if (ev.data.type === 'resize') f.style.height = Math.min(Math.max(ev.data.data, 40), 2000) + 'px';
          });
          ctx.slot.appendChild(f);
        });
      },
      unmount() {} };
    return cap;
  }

  function resolvePlacement(p) {
    let el;
    if (p instanceof Element) el = p;
    else if (p === 'drawer') return ui.drawerBody();
    else if (p === 'body') return document.body;
    else if (p === 'root') return host.root;
    else if (typeof p === 'string' && p.startsWith('anchor:')) el = document.querySelector(`[data-capability-anchor="${CSS.escape(p.slice(7))}"]`);
    else if (typeof p === 'string') { try { el = document.querySelector(p); } catch { el = null; } }
    if (!el) throw new Error(`Can't find the spot "${p}" in this app`);
    if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') throw new Error("That's a frame. The port can't reach inside a frame — install port.js in the page that lives in it.");
    if (/^(input|img|br|hr|textarea|select|script|style)$/i.test(el.tagName)) throw new Error(`A ${el.tagName.toLowerCase()} can't hold a capability. Pick its parent.`);
    return el;
  }
  // Placement is stored as a stable anchor, never a CSS path:
  //   the element's own id if it has one (comes from the app, survives reload), else a
  //   data-capability-anchor the app author can put in their markup. A runtime-only anchor
  //   works this session; after a reload with no anchor found, it falls back to the drawer and says so.
  function placementRecord(target, requested) {
    if (typeof requested === 'string' && ['drawer', 'body', 'root'].includes(requested)) return requested;
    if (target === ui.drawerBody(false)) return 'drawer';
    if (target === document.body) return 'body';
    if (target === host.root) return 'root';
    if (target.id) return '#' + CSS.escape(target.id);
    if (!target.dataset.capabilityAnchor) target.dataset.capabilityAnchor = Math.random().toString(36).slice(2, 10);
    return 'anchor:' + target.dataset.capabilityAnchor;
  }

  /* ---------- 7. PERSISTENCE: namespaced, validated, confirmed ------------ */
  let skipped = []; // remote entries the user declined to restore this load: kept saved, not run
  const hookApp = (document.querySelector('script[data-capability-hook][data-capability-app]') || {dataset:{}}).dataset.capabilityApp;
  const appId = () => RULES.APP_ID || document.documentElement.dataset.capabilityApp || hookApp || location.pathname;
  const storageKey = () => `${RULES.STORAGE_KEY}:${location.origin}:${appId()}`;
  function persist() {
    const list = [...attached.values()].filter(a => typeof a.input === 'string')
      .map(a => ({ input: a.input, placement: a.placement, trust: a.trust, remote: a.remote, name: a.cap.name, savedAt: Date.now() }));
    try { localStorage.setItem(storageKey(), JSON.stringify(list.concat(skipped))); } catch { /* storage unavailable: nothing persists, port keeps working */ }
  }
  async function restore() {
    if (!RULES.PERSIST) return;
    let list = [];
    try { const raw = localStorage.getItem(storageKey()); if (raw) list = JSON.parse(raw); } catch { list = []; }
    if (!Array.isArray(list)) list = [];
    list = list.filter(it => it && typeof it === 'object' && typeof it.input === 'string' && (it.placement === undefined || typeof it.placement === 'string'));
    const remote = list.filter(it => it.remote);
    if (remote.length && RULES.CONFIRM_REMOTE_RESTORE) {
      const ok = await Promise.resolve(RULES.CONFIRM(`Bring back ${remote.length} capability(ies) loaded from other origins? They may have changed since you attached them:\n\n${remote.map(r => '- ' + (r.name || r.input)).join('\n')}`));
      if (!ok) { skipped = remote; list = list.filter(it => !it.remote); }
    }
    for (const it of list) {
      try { await attach(it.input, it.placement, { trust: it.trust === 'sandboxed' ? 'sandboxed' : undefined }); }
      catch (e) { if (typeof it.placement === 'string' && /^(anchor:|#)/.test(it.placement)) { try { await attach(it.input, 'drawer', { trust: it.trust === 'sandboxed' ? 'sandboxed' : undefined }); ui.say(`${it.name || it.input}: its old spot is gone, it is in the drawer.`); } catch {} } }
    }
    persist();
  }

  /* ---------- 8. SURFACE: the button and panel, built with DOM not strings - */
  const ui = (() => {
    const shadowHost = document.createElement('div'); shadowHost.id = 'capability-port';
    const sh = shadowHost.attachShadow({ mode: 'open' });
    const corner = RULES.BUTTON_CORNER.split('-');
    const style = document.createElement('style');
    style.textContent = `
      :host{all:initial;position:fixed;z-index:2147483000;${corner[0]}:20px;${corner[1]}:20px;font:14px/1.4 system-ui,sans-serif;color:#1a1a1a}
      button{font:inherit;cursor:pointer;border:0}
      .fab{width:52px;height:52px;border-radius:50%;background:${RULES.ACCENT};color:#fff;font-size:26px;box-shadow:0 6px 18px rgba(0,0,0,.25)}
      .fab:focus-visible,.go:focus-visible,.pick:focus-visible,.close:focus-visible{outline:3px solid #333;outline-offset:2px}
      .panel{position:fixed;top:0;${corner[1]}:0;height:100vh;width:min(420px,100vw);background:#fff;box-shadow:-8px 0 30px rgba(0,0,0,.18);display:none;flex-direction:column}
      .panel.open{display:flex}
      header{padding:14px 16px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #e6e6e6}
      header b{font-weight:600} .close{background:none;font-size:20px}
      .body{padding:14px 16px;overflow:auto;flex:1}
      textarea{width:100%;box-sizing:border-box;min-height:88px;font:13px/1.4 ui-monospace,monospace;padding:8px;border:1px solid #cfcfcf;border-radius:6px}
      .row{display:flex;gap:8px;margin-top:8px;align-items:center;flex-wrap:wrap}
      .go{background:${RULES.ACCENT};color:#fff;padding:9px 14px;border-radius:6px}
      .pick{background:#eee;padding:9px 12px;border-radius:6px} .pick.on{background:#ffe08a}
      .msg{margin-top:10px;padding:8px 10px;border-radius:6px;background:#eef6f3;display:none;white-space:pre-wrap} .msg.bad{background:#fdecec}
      .list{margin-top:18px;border-top:1px solid #e6e6e6;padding-top:12px}
      .item{display:flex;justify-content:space-between;gap:8px;padding:6px 0;font-size:13px;align-items:center}
      .item small{color:#777;margin-left:6px} .item button{background:none;color:#a33}
      .host{font-size:12px;color:#666;margin-top:14px} .drawer{border-top:1px solid #e6e6e6;padding:10px 16px}`;
    const el = (tag, attrs = {}, text) => { const n = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (text != null) n.textContent = text; return n; };
    const fab = el('button', { class: 'fab', 'aria-label': 'Attach a capability' }, RULES.BUTTON_LABEL);
    const panel = el('div', { class: 'panel', role: 'dialog', 'aria-label': 'Capability port' });
    const header = el('header'); header.append(el('b', {}, 'Attach a capability'), el('button', { class: 'close', 'aria-label': 'Close' }, '\u00d7'));
    const body = el('div', { class: 'body' });
    const ta = el('textarea', { placeholder: 'Paste anything: a link to a script, a page, HTML, JSON, or code', 'aria-label': 'Capability to attach' });
    const row = el('div', { class: 'row' }); const go = el('button', { class: 'go' }, 'Attach'); const pick = el('button', { class: 'pick' }, 'Pick a spot'); const where = el('span', { class: 'where' }, 'Goes in: drawer');
    row.append(go, pick, where);
    const msg = el('div', { class: 'msg', role: 'status' }); const list = el('div', { class: 'list' }); const hostLine = el('div', { class: 'host' });
    body.append(ta, row, msg, list, hostLine);
    const drawer = el('div', { class: 'drawer' });
    panel.append(header, body, drawer); sh.append(style, fab, panel);

    let placement = null, picking = false; const messages = [];
    fab.onclick = () => panel.classList.toggle('open');
    header.querySelector('.close').onclick = () => panel.classList.remove('open');
    go.onclick = async () => { const v = ta.value.trim(); if (!v) return; try { await attach(v, placement); ta.value = ''; } catch {} };
    const setPicking = on => { picking = on; pick.classList.toggle('on', on); document.body.style.cursor = on ? 'crosshair' : '';
      where.textContent = on ? 'Click anywhere in the app (Esc cancels)' : 'Goes in: ' + (placement ? describe(placement) : 'drawer'); };
    pick.onclick = () => setPicking(!picking);
    document.addEventListener('click', e => {
      if (!picking || e.composedPath().includes(shadowHost)) return;
      e.preventDefault(); e.stopPropagation();
      const t = e.composedPath()[0]; placement = t instanceof Element ? t : null; setPicking(false);
    }, true);
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && picking) setPicking(false); }, true);
    function describe(e) { return e.tagName.toLowerCase() + (e.id ? '#' + e.id : e.classList.length ? '.' + e.classList[0] : ''); }

    return {
      mount() { document.body.appendChild(shadowHost); this.refresh(); },
      messages: () => [...messages],
      drawerBody(open = true) { if (open) panel.classList.add('open'); return drawer; },
      say(text, bad) { messages.push(String(text)); if (messages.length > 20) messages.shift(); msg.textContent = String(text); msg.className = 'msg' + (bad ? ' bad' : ''); msg.style.display = 'block'; },
      refresh() {
        const avail = Object.entries(host.available).filter(([, v]) => v).map(([k]) => k).join(', ');
        hostLine.textContent = `This app: ${host.framework}, "${host.title || host.url}". Available here: ${avail}. (Available means the browser has it; it may still ask you for permission.)`;
        list.replaceChildren();
        if (!attached.size) { list.append(el('div', { class: 'item' }, 'Nothing attached yet.')); return; }
        for (const [id, a] of attached) {
          const item = el('div', { class: 'item' }); const label = el('span', {}, a.cap.name); label.append(el('small', {}, a.trust));
          const rm = el('button', {}, 'Remove'); rm.onclick = () => detach(id); item.append(label, rm); list.append(item);
        }
      },
    };
  })();

  /* ---------- 9. PUBLIC API ---------------------------------------------- */
  const host = probe();
  const api = { host: publicHost(), attach, detach, attached: () => [...attached.keys()], messages: () => ui.messages(), rules: RULES, version: '0.2.0' };
  global.__capabilityPort = api;
  const start = async () => { ui.mount(); await restore(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})(window);
