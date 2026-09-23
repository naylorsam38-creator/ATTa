'use strict';
// Playwright checks run against the app *through the proxy*.
// Each check is numbered so results line up across every app.
const { chromium } = require('playwright');

const CHROMIUM = process.env.ATTA_CHROMIUM || undefined;

async function runChecks({ proxyUrl, appId, pagePath = '/', screenshot }) {
  const checks = [];
  const record = (id, name, status, detail) => checks.push({ id, name, status, detail });
  const consoleErrors = [];
  const pageErrors = [];

  const browser = await chromium.launch(CHROMIUM ? { executablePath: CHROMIUM } : {});
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
    page.on('pageerror', (e) => pageErrors.push(String(e && e.message)));

    // C1 — page loads through the proxy
    const resp = await page.goto(proxyUrl + pagePath, { waitUntil: 'load', timeout: 30000 });
    const status = resp ? resp.status() : 0;
    record('C1', 'Page loads through proxy', status && status < 400 ? 'pass' : 'fail', `HTTP ${status}`);

    // C2 — proxy.js hook: tag sits immediately before </head> in the served HTML
    const raw = resp ? await resp.text() : '';
    const tag = `<script src="/_cp/port.js" data-capability-app="${appId}" defer></script>`;
    const hooked = new RegExp(tag.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '<\\/head\\s*>', 'i').test(raw);
    record('C2', 'proxy.js injects port.js before </head>', hooked ? 'pass' : 'fail',
      hooked ? 'tag found directly before </head>' :
        (/<\/head\s*>/i.test(raw) ? 'tag missing before </head>' : 'served HTML has no </head>'));

    // C3 — port.js running in the live page
    let port = null;
    try {
      await page.waitForFunction(() => window.CapabilityPort && window.CapabilityPort.ready, null, { timeout: 10000 });
      port = await page.evaluate(() => ({ appId: window.CapabilityPort.appId }));
    } catch (_) {}
    record('C3', 'port.js runs in the live page', port && port.appId === appId ? 'pass' : 'fail',
      port ? `appId=${port.appId}` : 'window.CapabilityPort not ready');

    // C4 — Port-created <section data-capability="button-mover"> inside drawer body
    let slot = null;
    if (port) {
      try {
        await page.waitForSelector('section[data-capability="button-mover"]', { state: 'attached', timeout: 10000 });
        slot = await page.evaluate(() => {
          const s = document.querySelector('section[data-capability="button-mover"]');
          return {
            trust: s.getAttribute('data-trust'),
            inDrawer: s.parentElement && s.parentElement.id === '_cp-drawer-body',
            status: window.CapabilityPort.status['button-mover'],
          };
        });
      } catch (_) {}
    }
    record('C4', 'Mover slot is a Port <section> in the drawer body',
      slot && slot.inDrawer && slot.trust && slot.status === 'mounted' ? 'pass' : 'fail',
      slot ? `data-trust=${slot.trust}, inDrawer=${slot.inDrawer}, status=${slot.status}` : 'slot not created');

    // C5 — Mover finds and numbers the app's own buttons/links
    let found = [];
    if (slot) found = await page.evaluate(() => window.CapabilityPort.capabilities['button-mover'].api.scan());
    record('C5', 'Mover numbers app buttons/links', !slot ? 'fail' : found.length ? 'pass' : 'warn',
      `${found.length} found` + (found.length ? ': ' + found.slice(0, 8).map((f) => `#${f.n} ${f.label}`).join(', ') : ''));

    // C6 — a move changes the live DOM, and reset restores it
    if (!found.length) {
      record('C6', 'Move + reset on live DOM', slot ? 'skip' : 'fail', 'no movable targets');
    } else {
      const r = await page.evaluate(() => {
        const api = window.CapabilityPort.capabilities['button-mover'].api;
        const els = Array.from(document.querySelectorAll('[data-cp-n]'));
        const el = els.find((e) => e.previousElementSibling && !window.CapabilityPort.isPortNode(e));
        if (!el) return { skipped: true };
        const n = Number(el.getAttribute('data-cp-n'));
        const parent = el.parentNode;
        const before = Array.from(parent.children).indexOf(el);
        const moved = api.move(n, 'up');
        const after = Array.from(parent.children).indexOf(el);
        api.reset();
        const restored = Array.from(parent.children).indexOf(el);
        return { n, before, after, moved, restored };
      });
      if (r.skipped) record('C6', 'Move + reset on live DOM', 'skip', 'no target has a preceding sibling');
      else {
        const ok = r.moved && r.after === r.before - 1 && r.restored === r.before;
        record('C6', 'Move + reset on live DOM', ok ? 'pass' : 'fail',
          `#${r.n}: index ${r.before} → ${r.after} → reset ${r.restored}`);
      }
    }

    // C7 — drawer UI opens from the toggle
    let opened = false;
    if (port) {
      await page.click('#_cp-toggle').catch(() => {});
      await page.waitForTimeout(250);
      opened = await page.evaluate(() => {
        const d = document.getElementById('_cp-drawer');
        return !!d && d.getAttribute('data-open') === '1';
      });
    }
    record('C7', 'Drawer opens from the CP toggle', opened ? 'pass' : 'fail', opened ? 'open' : 'did not open');
    if (screenshot) await page.screenshot({ path: screenshot });

    // C8 — no uncaught page errors
    record('C8', 'No uncaught page errors', pageErrors.length ? 'fail' : 'pass',
      pageErrors.length ? pageErrors.slice(0, 3).join(' | ') : 'none');

    return { checks, consoleErrors, pageErrors };
  } finally {
    await browser.close();
  }
}

module.exports = { runChecks };
