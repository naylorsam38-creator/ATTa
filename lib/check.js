'use strict';
// Playwright checks against an app *through ui-bridge/proxy.js*, exercising the
// real capability-port/port.js and capability-port/caps/mover.mjs.
//
// Hook chain under test:
//   app HTML <head> ← proxy injects <script src="/_cp/port.js" data-capability-hook="1" data-capability-app=ID defer>
//   port.js → window.__capabilityPort, UI in #capability-port (open shadow root)
//   attach('/_cp/caps/mover.mjs') → <section data-capability="button-mover" data-trust> in the drawer
//   mover.mjs mount(ctx) → works on the app's live DOM (drag = translate, dblclick = rename)
const path = require('path');
const http = require('http');
const { chromium } = require('playwright');

// One request through the proxy without following redirects.
function probe(url) {
  return new Promise((resolve) => {
    http.get(url, (res) => { res.resume(); resolve({ status: res.statusCode, location: res.headers.location || null }); })
      .on('error', (e) => resolve({ status: 0, error: e.message }));
  });
}

const MOVER_URL = '/_cp/caps/mover.mjs';
const PICKABLE = 'a,button,input[type=submit],input[type=button],[role=button],.ui.button,summary';
const RENAMED = 'ATTa renamed';

function hookTag(appId) {
  return `<script src="/_cp/port.js" data-capability-hook="1" data-capability-app="${appId}" defer></script>`;
}

// Runs in the page: pick the first app control of a kind that a real pointer can hit.
// kind: 'link' (<a>), 'button' (buttons, inputs, role=button, summary) or 'any'.
function pickTarget([PICKABLE, kind]) {
  document.querySelectorAll('[data-atta-target]').forEach((e) => e.removeAttribute('data-atta-target'));
  const port = document.getElementById('capability-port');
  const els = [...document.querySelectorAll(PICKABLE)].filter((el) => !(port && port.contains(el)));
  const ofKind = els.filter((el) => kind === 'any' || (kind === 'link') === (el.tagName === 'A'));
  const fits = (r) => r.width >= 8 && r.height >= 8 && r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth - 80;
  for (const el of ofKind) {
    let r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    if (!fits(r)) { el.scrollIntoView({ block: 'center', inline: 'center' }); r = el.getBoundingClientRect(); }
    if (!fits(r)) continue;
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const hit = document.elementFromPoint(x, y);
    if (!hit || hit.closest(PICKABLE) !== el) continue;
    el.setAttribute('data-atta-target', '1');
    const label = (el.value || el.innerText || el.getAttribute('aria-label') || el.title || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    // Drag 120px/60px towards whichever side has room.
    const dx = r.right + 130 < innerWidth - 80 ? 120 : -120, dy = r.bottom + 70 < innerHeight ? 60 : -60;
    return { x, y, dx, dy, left: r.left, top: r.top, label, tag: el.tagName.toLowerCase(), display: getComputedStyle(el).display, total: els.length, kindTotal: ofKind.length };
  }
  return { total: els.length, kindTotal: ofKind.length };
}

// Normalise an error message so the same error matches across runs (nonces, hashes, ports, ids differ).
const norm = (m) => String(m).split('\n')[0]
  .replace(/'(nonce|sha\d+)-[^']+'/g, "'$1-*'").replace(/127\.0\.0\.1:\d+/g, 'HOST').replace(/\b[0-9a-f]{8,}\b/gi, '#');

// Load the app directly (no proxy, no Port) and collect the errors it raises on its own.
async function baseline(browser, appUrl, pagePath) {
  const errs = { console: [], page: [] };
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, ignoreHTTPSErrors: true });
  const page = await context.newPage();
  page.on('console', (m) => { if (m.type() === 'error') errs.console.push(norm(m.text())); });
  page.on('pageerror', (e) => errs.page.push(norm((e && e.message) || e)));
  try {
    await page.goto(appUrl + pagePath, { waitUntil: 'load', timeout: 45000 });
    await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
    await page.reload({ waitUntil: 'load' }).catch(() => {});
    await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
  } catch (_) {}
  await context.close();
  return { console: new Set(errs.console), page: new Set(errs.page) };
}

async function runChecks({ proxyUrl, appUrl, appId, pagePath = '/', outDir, skins }) {
  const checks = [];
  const record = (id, name, status, detail) => checks.push({ id, name, status, detail: String(detail) });
  const consoleErrors = [];
  const pageErrors = [];

  const browser = await chromium.launch();
  try {
    const base = appUrl ? await baseline(browser, appUrl, pagePath) : { console: new Set(), page: new Set() };
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, ignoreHTTPSErrors: true });
    const page = await context.newPage();
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
    const pageMsgs = [];
    page.on('pageerror', (e) => { pageErrors.push(String((e && e.stack) || e)); pageMsgs.push(norm((e && e.message) || e)); });
    let dialogAnswer = null;
    page.on('dialog', (d) => (d.type() === 'prompt' && dialogAnswer !== null ? d.accept(dialogAnswer) : d.dismiss()).catch(() => {}));
    const settle = () => page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});

    // C1 — page loads through the proxy (redirects followed, e.g. to a login screen)
    let resp = null, loadErr = null;
    try { resp = await page.goto(proxyUrl + pagePath, { waitUntil: 'load', timeout: 45000 }); } catch (e) { loadErr = e.message.split('\n')[0]; }
    await settle();
    const status = resp ? resp.status() : 0;
    const onProxy = page.url().startsWith(proxyUrl);
    const finalPath = onProxy ? page.url().replace(proxyUrl, '') || '/' : page.url();
    record('C1', 'Page loads through proxy', status && status < 400 && onProxy ? 'pass' : 'fail',
      loadErr || `HTTP ${status}, landed on ${finalPath}` + (onProxy ? '' : ' — OFF the proxy (browser left it)') +
        `, title "${(await page.title()).slice(0, 60)}"`);

    // C2 — proxy hook: exact tag immediately before </head> in the served document
    let raw = '';
    try { raw = resp ? await resp.text() : ''; } catch (_) {}
    const tag = hookTag(appId);
    const count = raw.split('data-capability-hook').length - 1;
    const hooked = raw.includes(tag + '</head>') || new RegExp(tag.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '<\\/head\\s*>', 'i').test(raw);
    record('C2', 'Proxy injects port.js immediately before </head>', hooked && count === 1 ? 'pass' : 'fail',
      hooked ? `tag found once, directly before </head>` :
        !raw ? 'no document body captured' : /<\/head\s*>/i.test(raw) ? `tag not before </head> (hook markers: ${count})` : 'served HTML has no </head>');

    // C3 — port.js live in the page, reading the app id from the hook tag
    let port = null;
    try {
      await page.waitForFunction(() => window.__capabilityPort && document.getElementById('capability-port'), null, { timeout: 15000 });
      port = await page.evaluate(() => ({
        version: window.__capabilityPort.version,
        framework: window.__capabilityPort.host.framework,
        app: (document.querySelector('script[data-capability-hook][data-capability-app]') || { dataset: {} }).dataset.capabilityApp,
      }));
    } catch (_) {}
    record('C3', 'port.js runs in the live page', port && port.app === appId ? 'pass' : 'fail',
      port ? `v${port.version}, framework=${port.framework}, app id=${port.app}` : 'window.__capabilityPort never appeared');

    // C4 — attach the mover: Port-created <section> in the drawer, mount(ctx.slot)
    let slot = null, attachErr = null;
    if (port) {
      try {
        await page.evaluate((u) => window.__capabilityPort.attach(u), MOVER_URL);
      } catch (e) { attachErr = e.message.split('\n')[0]; }
      slot = await page.evaluate(() => {
        const sh = document.getElementById('capability-port').shadowRoot;
        const s = sh && sh.querySelector('section[data-capability="button-mover"]');
        return s ? { trust: s.dataset.trust, inDrawer: !!(s.parentElement && s.parentElement.classList.contains('drawer')),
          ui: !!s.querySelector('[data-t]'), msg: window.__capabilityPort.messages().slice(-1)[0] } : null;
      });
    }
    const slotOk = slot && slot.inDrawer && slot.ui && slot.trust === 'trusted';
    record('C4', 'Mover attached into Port drawer slot', slotOk ? 'pass' : 'fail',
      slot ? `section[data-capability=button-mover] data-trust=${slot.trust}, in drawer=${slot.inDrawer}, mover UI=${slot.ui}` :
        attachErr || (port ? 'no section created' : 'no port'));

    // C5 — the mover's targets: the app's own buttons/links
    const counts = slotOk ? await page.evaluate(pickTarget, [PICKABLE, 'any']) : { total: 0 };
    record('C5', 'App controls found for the mover', !slotOk ? 'fail' : counts.x !== undefined ? 'pass' : 'warn',
      `${counts.total} buttons/links on the page` + (counts.total && counts.x === undefined ? '; none hittable in the viewport' : ''));

    const toggleMove = () => page.evaluate(() => document.getElementById('capability-port').shadowRoot
      .querySelector('section[data-capability="button-mover"] [data-t]').click());
    const moveIsOn = () => page.evaluate(() => document.getElementById('capability-port').shadowRoot
      .querySelector('section[data-capability="button-mover"] [data-t]').textContent.includes('on'));
    const targetState = () => page.evaluate(() => {
      const el = document.querySelector('[data-atta-target]');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const label = el.tagName === 'INPUT' ? el.value : (el.innerText || '').trim();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return { moved: el.dataset.moved === '1', translate: el.style.translate, renamed: el.dataset.renamed === '1', label,
        left: r.left, top: r.top, reachable: !!hit && (hit === el || el.contains(hit)) };
    });
    const saved = () => page.evaluate(() => {
      try { const s = JSON.parse(localStorage.getItem('button-mover:' + location.origin) || '{}'); return Object.keys(s[location.pathname] || {}).length; }
      catch (_) { return -1; }
    });

    // C6/C7 — drag a link and a button 120px sideways and 60px vertically. Pass only if it really moved on screen.
    // The Port drawer is closed while dragging so it doesn't cover half the app.
    const setPanel = (open) => page.evaluate((o) => document.getElementById('capability-port').shadowRoot.querySelector('.panel').classList.toggle('open', o), open);
    if (slotOk) await setPanel(false);
    let movedOk = false;
    const expected = [];
    for (const [id, kind, name] of [['C6', 'link', 'Drag-move a link'], ['C7', 'button', 'Drag-move a button']]) {
      const t = slotOk ? await page.evaluate(pickTarget, [PICKABLE, kind]) : { kindTotal: 0 };
      if (t.x === undefined) {
        record(id, name, 'skip', t.kindTotal ? `${t.kindTotal} on page, none hittable in the viewport` : `no ${kind}s on this page`);
        continue;
      }
      if (!(await moveIsOn())) await toggleMove();
      await page.mouse.move(t.x, t.y);
      await page.mouse.down();
      await page.mouse.move(t.x + t.dx / 3, t.y + t.dy / 2, { steps: 6 });
      await page.mouse.move(t.x + t.dx, t.y + t.dy, { steps: 6 });
      await page.mouse.up();
      // Let the app's own CSS transitions (e.g. `transition: all .15s`) finish before measuring.
      await page.waitForTimeout(600);
      const st = await targetState();
      const n = await saved();
      const what = `<${t.tag}> "${t.label}" (display:${t.display})`;
      if (!st) { record(id, name, 'fail', `${what} disappeared from the page`); continue; }
      const dx = Math.round(st.left - t.left), dy = Math.round(st.top - t.top);
      const want = `${t.dx}px ${t.dy}px`;
      const recorded = st.moved && st.translate === want && n > 0;
      // Apps' own hover/transition effects nudge the measured box a few px; a real non-move is ~0,0.
      const onScreen = Math.abs(dx - t.dx) <= 15 && Math.abs(dy - t.dy) <= 15;
      if (recorded) { movedOk = true; expected.push(want); }
      record(id, name, recorded && onScreen && st.reachable ? 'pass' : recorded && onScreen ? 'warn' : 'fail',
        `${what}: mover recorded translate="${st.translate}" (saved=${n > 0}); on screen it moved ${dx},${dy}px` +
          (!recorded ? ' — mover did not register the drag' :
            !onScreen ? ` — NOT moved visually${/^inline$/.test(t.display) ? ': CSS translate has no effect on display:inline elements' : ''}` :
              st.reachable ? ', visible and clickable at the new spot' : ', but covered/clipped at the new spot'));
    }
    if (outDir && movedOk) await page.screenshot({ path: path.join(outDir, 'screenshot-moved.png') });

    // C8 — reload: port restores the mover from storage, mover re-applies the move
    if (!movedOk) {
      record('C8', 'Move survives reload', 'skip', 'nothing moved');
    } else {
      await page.reload({ waitUntil: 'load' }).catch(() => {});
      await settle();
      let restored = null;
      try {
        await page.waitForFunction(() => {
          const sh = document.getElementById('capability-port') && document.getElementById('capability-port').shadowRoot;
          return sh && sh.querySelector('section[data-capability="button-mover"]');
        }, null, { timeout: 15000 });
        await page.waitForTimeout(600);
        restored = await page.evaluate(() => [...document.querySelectorAll('[data-moved="1"]')].map((e) => e.style.translate));
      } catch (_) {}
      record('C8', 'Move survives reload', restored && expected.every((w) => restored.includes(w)) ? 'pass' : 'fail',
        restored ? `mover re-attached from storage; moved controls after reload: ${restored.length} (${restored.join(', ') || 'none'})` : 'mover not re-attached after reload');
    }

    // C9 — rename by double-click (Move on), then C10 — Reset restores position and label
    if (slotOk) await setPanel(false);
    const renameTarget = slotOk ? await page.evaluate(pickTarget, [PICKABLE, 'any']) : { total: 0 };
    let before = null;
    if (renameTarget.x === undefined) {
      record('C9', 'Rename an app control (double-click)', 'skip', 'no hittable control');
    } else {
      before = await targetState();
      const on = await page.evaluate(() => document.getElementById('capability-port').shadowRoot
        .querySelector('section[data-capability="button-mover"] [data-t]').textContent.includes('on'));
      if (!on) await toggleMove();
      dialogAnswer = RENAMED;
      await page.mouse.dblclick(renameTarget.x, renameTarget.y);
      await page.waitForTimeout(300);
      dialogAnswer = null;
      const st = await targetState();
      record('C9', 'Rename an app control (double-click)', st && st.renamed && st.label.includes(RENAMED) ? 'pass' : 'fail',
        st ? `"${before.label.slice(0, 30)}" → "${st.label.slice(0, 40)}"` : 'control disappeared');
    }

    if (!slotOk) {
      record('C10', 'Reset restores positions and labels', 'skip', 'mover not attached');
    } else {
      await page.evaluate(() => document.getElementById('capability-port').shadowRoot
        .querySelector('section[data-capability="button-mover"] [data-r]').click());
      await page.waitForTimeout(300);
      const left = await page.evaluate(() => ({
        moved: document.querySelectorAll('[data-moved="1"]').length,
        renamed: document.querySelectorAll('[data-renamed="1"]').length,
      }));
      const st = await targetState();
      const n = await saved();
      const labelBack = !before || !st || st.label === before.label;
      record('C10', 'Reset restores positions and labels', !left.moved && !left.renamed && n === 0 && labelBack ? 'pass' : 'fail',
        `moved left=${left.moved}, renamed left=${left.renamed}, saved positions=${n}, label restored=${labelBack}`);
    }

    // C11 — the Port's own button opens its panel
    let opened = false;
    if (port) {
      opened = await page.evaluate(() => {
        const sh = document.getElementById('capability-port').shadowRoot;
        sh.querySelector('.panel').classList.remove('open');
        sh.querySelector('.fab').click();
        return sh.querySelector('.panel').classList.contains('open');
      }).catch(() => false);
      if (outDir) await page.screenshot({ path: path.join(outDir, 'screenshot-drawer.png') });
    }
    record('C11', 'Port button opens the drawer', opened ? 'pass' : 'fail', opened ? 'panel open' : 'did not open');

    // C12 — no uncaught errors that the app doesn't also raise without the Port
    const added = pageErrors.filter((e, i) => !base.page.has(pageMsgs[i]));
    record('C12', 'No new uncaught errors with the Port', added.length ? 'fail' : 'pass',
      added.length ? added[0].split('\n')[0].slice(0, 200) : `none (${pageErrors.length} uncaught error(s) seen, all also raised by the app without the Port)`);

    // C13 — the app's Content-Security-Policy vs the Port: violations that only appear with the Port
    const cspHeader = resp ? (resp.headers()['content-security-policy'] || '') : '';
    const csp = consoleErrors.filter((e) => /Content Security Policy/i.test(e) && !base.console.has(norm(e)));
    const blockedScripts = csp.filter((e) => /script/i.test(e) && /_cp\//.test(e));
    const styleRefusals = csp.filter((e) => /inline style/i.test(e));
    const otherCsp = csp.filter((e) => !/inline style/i.test(e));
    // Refused inline styles strip the Port's shadow-DOM stylesheet: no fixed button, no overlaid drawer. A user can't work it.
    record('C13', 'App CSP lets the Port run', blockedScripts.length || styleRefusals.length ? 'fail' : csp.length ? 'warn' : 'pass',
      blockedScripts.length ? blockedScripts[0].slice(0, 200) :
        csp.length ? [
          styleRefusals.length ? `${styleRefusals.length} inline-style refusal(s) (style-src has no 'unsafe-inline'): the Port's stylesheet is blocked, so its button renders as a bare '+' at the end of the page and the drawer does not overlay — works programmatically, not usable by a person` : '',
          otherCsp.length ? `${otherCsp.length} other violation(s) only seen through the proxy: ${otherCsp[0].slice(0, 150)}` : '',
        ].filter(Boolean).join('; ') :
          cspHeader ? 'CSP present, no violations added by the Port' : 'no CSP header');
    const extraConsole = consoleErrors.filter((e) => !base.console.has(norm(e)));

    // C14 — redirects keep the browser on the proxy (proxy sends Host=upstream and no X-Forwarded-Host,
    // so apps that build absolute URLs from Host point the browser straight at the upstream)
    const hops = [];
    for (const p of [...new Set(['/', pagePath])]) {
      const r = await probe(proxyUrl + p);
      if (r.location) hops.push({ p, ...r });
    }
    const leaks = hops.filter((h) => /^https?:\/\//i.test(h.location) && !h.location.startsWith(proxyUrl));
    // Links and form targets in the page that name the upstream origin (built from the rewritten Host header)
    const upstreamRefs = appUrl ? await page.evaluate((up) => [...document.querySelectorAll('a[href],form[action],link[href],script[src],img[src]')]
      .map((e) => e.getAttribute('href') || e.getAttribute('action') || e.getAttribute('src') || '')
      .filter((u) => u.startsWith(up)).length, appUrl).catch(() => 0) : 0;
    const bits = [];
    if (leaks.length) bits.push(leaks.map((h) => `GET ${h.p} → ${h.status} Location: ${h.location.replace(appUrl || '#', '<upstream>')}`).join('; ') + ' (bypasses the proxy)');
    if (upstreamRefs) bits.push(`${upstreamRefs} link/form/asset URL(s) in the page point at <upstream>`);
    record('C14', 'Redirects and links stay on the proxy', leaks.length || upstreamRefs ? 'fail' : 'pass',
      bits.length ? bits.join('; ') : hops.length ? hops.map((h) => `GET ${h.p} → ${h.status} ${h.location}`).join('; ') : 'no redirects; no upstream URLs in page');

    // C15 — skins: the proxy picks each customer's layout (x-authenticated-customer-id) and injects /_cs/<app>/<skin>.<v>.css
    const skinResults = [];
    for (const [customer, skin] of Object.entries(skins || {})) {
      await page.setExtraHTTPHeaders({ 'x-authenticated-customer-id': customer });
      await page.goto(proxyUrl + pagePath, { waitUntil: 'load' }).catch(() => {});
      const got = await page.evaluate(([appId, skin]) => {
        const link = document.querySelector(`link[rel=stylesheet][href^="/_cs/${appId}/${skin}."]`);
        const sheet = link && [...document.styleSheets].find((x) => x.ownerNode === link);
        let rules = 0; try { rules = sheet ? sheet.cssRules.length : 0; } catch (_) {}
        return { link: !!link, rules, badge: getComputedStyle(document.body, '::after').content };
      }, [appId, skin]).catch(() => ({}));
      skinResults.push({ customer, skin, ok: got.link && got.rules > 0 && String(got.badge).includes(skin), ...got });
    }
    await page.setExtraHTTPHeaders({});
    await page.goto(proxyUrl + pagePath, { waitUntil: 'load' }).catch(() => {});
    const unskinned = await page.evaluate(() => !document.querySelector('link[href^="/_cs/"]')).catch(() => false);
    if (!skinResults.length) record('C15', 'Customer skins through the proxy', 'skip', 'no skins configured');
    else record('C15', 'Customer skins through the proxy', skinResults.every((r) => r.ok) && unskinned ? 'pass' : 'fail',
      skinResults.map((r) => `${r.customer}→${r.skin}: ${r.ok ? 'applied' : `NOT applied (link=${r.link}, rules=${r.rules})`}`).join('; ') +
        `; no header → ${unskinned ? 'original look' : 'still skinned'}`);

    // C16 — sticky-notes capability through the proxy: pin a note on the live page, survives reload, clears
    let notes = null;
    if (port) {
      try {
        await page.waitForFunction(() => window.__capabilityPort, null, { timeout: 15000 });
        await page.evaluate(() => window.__capabilityPort.attach('/_cp/caps/sticky-notes.mjs'));
        await page.evaluate(() => {
          const s = document.getElementById('capability-port').shadowRoot.querySelector('section[data-capability="sticky-notes"]');
          s.querySelector('textarea').value = 'Pinned by ATTa';
          s.querySelector('[data-pin]').click();
        });
        const pinned = await page.evaluate(() => {
          const n = document.querySelector('[data-sticky-note]');
          if (!n) return null;
          const r = n.getBoundingClientRect();
          return { text: n.textContent, visible: r.width > 50 && r.height > 30 };
        });
        await page.reload({ waitUntil: 'load' });
        await page.waitForFunction(() => document.querySelector('[data-sticky-note]'), null, { timeout: 15000 }).catch(() => {});
        const kept = await page.evaluate(() => !!document.querySelector('[data-sticky-note]'));
        await page.evaluate(() => document.getElementById('capability-port').shadowRoot
          .querySelector('section[data-capability="sticky-notes"] [data-clear]').click()).catch(() => {});
        const cleared = await page.evaluate(() => !document.querySelector('[data-sticky-note]'));
        notes = { pinned, kept, cleared };
      } catch (e) { notes = { error: e.message.split('\n')[0] }; }
    }
    const notesOk = notes && notes.pinned && notes.pinned.visible && notes.kept && notes.cleared;
    record('C16', 'Sticky-notes capability on the live page', notesOk ? 'pass' : 'fail',
      !notes ? 'no port' : notes.error ? notes.error :
        `pinned=${!!notes.pinned}${notes.pinned ? ` ("${notes.pinned.text.slice(0, 20)}", visible=${notes.pinned.visible})` : ''}, after reload=${notes.kept}, clear=${notes.cleared}`);

    return { checks, consoleErrors: extraConsole, pageErrors, baseline: { console: [...base.console], page: [...base.page] } };
  } finally {
    await browser.close();
  }
}

module.exports = { runChecks, hookTag, pickTarget, PICKABLE };
