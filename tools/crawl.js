// Generic Playwright crawler used for steps F-I of every app.
// Usage: node crawl.js <config.json>
// Config keys:
//   base            base URL (http://localhost:PORT)
//   out             output directory for report.json + screenshots
//   startPaths      extra paths to visit first (default ["/"])
//   maxPages        max pages to crawl (default 40)
//   maxClicks       max clickables to try per page (default 25)
//   clickPages      max pages on which to click everything (default 15)
//   signupPaths     candidate signup URLs (relative)
//   loginPaths      candidate login URLs (relative)
//   user            {email, username, password, name}
//   pre             optional path to a JS module: async (page, ctx, log) => {}
//                   run before crawling (app-specific signup/login flow)
//   ignoreHTTPSErrors  true for apps that serve self-signed certs
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fs = require('fs');
const path = require('path');

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = cfg.out;
fs.mkdirSync(path.join(out, 'shots'), { recursive: true });

const user = Object.assign({
  email: 'tester.alpha@example.test',
  username: 'testeralpha',
  password: 'Zx9!test-Passw0rd-2026',
  name: 'Tester Alpha',
}, cfg.user || {});

const DANGER = /delete|remove|destroy|drop|purge|erase|wipe|reset all|uninstall|log ?out|sign ?out|disconnect|revoke|deactivate|archive all|truncate/i;

const report = {
  base: cfg.base, started: new Date().toISOString(),
  pages: [], consoleErrors: [], pageErrors: [], failedRequests: [], httpErrors: [],
  clicks: [], forms: [], auth: [], notes: [],
};
const seenConsole = new Set();
const budget = (k, def) => (cfg[k] || def) * 60000;
function save() { try { fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 1)); } catch {} }
setInterval(save, 15000).unref();
function log(...a) { console.log(...a); }
function rel(u) { try { const x = new URL(u); return x.pathname + x.search + x.hash; } catch { return u; } }

function fakeValue(type, name) {
  const n = (name || '').toLowerCase();
  if (type === 'email' || /mail/.test(n)) return user.email;
  if (type === 'password' || /pass/.test(n)) return user.password;
  if (type === 'number' || type === 'range') return '1';
  if (type === 'url' || /url|website|link/.test(n)) return 'http://test.invalid/page';
  if (type === 'tel' || /phone|tel/.test(n)) return '5550100';
  if (type === 'date') return '2026-01-15';
  if (type === 'datetime-local') return '2026-01-15T10:00';
  if (type === 'time') return '10:00';
  if (type === 'color') return '#336699';
  if (type === 'search') return 'test';
  if (/user|login|handle/.test(n)) return user.username;
  if (/first/.test(n)) return 'Tester';
  if (/last/.test(n)) return 'Alpha';
  if (/name/.test(n)) return user.name;
  return 'Test entry ' + Math.floor(Math.random() * 1000);
}

async function fillForm(page, form) {
  const filled = [];
  const inputs = await form.locator('input, textarea, select').all();
  for (const el of inputs) {
    try {
      if (!(await el.isVisible()) || !(await el.isEditable())) continue;
      const tag = await el.evaluate(e => e.tagName.toLowerCase());
      const type = ((await el.getAttribute('type')) || 'text').toLowerCase();
      const name = (await el.getAttribute('name')) || (await el.getAttribute('id')) || (await el.getAttribute('placeholder')) || (await el.getAttribute('aria-label')) || '';
      if (tag === 'select') {
        const opts = await el.locator('option').all();
        if (opts.length > 1) await el.selectOption({ index: 1 });
        filled.push(name + '=<select>');
      } else if (['checkbox', 'radio'].includes(type)) {
        await el.check({ timeout: 2000 }).catch(() => {});
        filled.push(name + '=<checked>');
      } else if (['hidden', 'submit', 'button', 'file', 'image', 'reset'].includes(type)) {
        continue;
      } else {
        const v = fakeValue(type, name);
        await el.fill(v, { timeout: 3000 });
        filled.push(name + '=' + (type === 'password' ? '***' : v));
      }
    } catch (e) { /* skip unfillable field */ }
  }
  return filled;
}

async function submitForm(page, form) {
  const btn = form.locator('button[type=submit], input[type=submit], button:not([type])').first();
  if (await btn.count() && await btn.isVisible().catch(() => false)) {
    const t = ((await btn.innerText().catch(() => '')) || (await btn.getAttribute('value').catch(() => '')) || '');
    if (DANGER.test(t)) return 'skipped (danger: ' + t + ')';
    await btn.click({ timeout: 5000 });
    return 'clicked ' + JSON.stringify(t.trim().slice(0, 40));
  }
  await form.evaluate(f => f.requestSubmit ? f.requestSubmit() : f.submit());
  return 'requestSubmit()';
}

async function visibleText(page) {
  return (await page.locator('body').innerText({ timeout: 3000 }).catch(() => '')).replace(/\s+/g, ' ').slice(0, 300);
}

async function tryAuth(page, kind, paths) {
  for (const p of paths || []) {
    const url = new URL(p, cfg.base).href;
    const rec = { kind, url: rel(url) };
    try {
      const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
      rec.status = resp && resp.status();
      const pw = page.locator('input[type=password]');
      if (!(await pw.count())) { rec.result = 'no password field on page'; rec.text = await visibleText(page); report.auth.push(rec); continue; }
      const form = (await page.locator('form:has(input[type=password])').count()) ? page.locator('form:has(input[type=password])').first() : page.locator('body');
      rec.filled = await fillForm(page, form);
      rec.submit = await submitForm(page, form).catch(e => 'submit error: ' + e.message.split('\n')[0]);
      await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
      await page.waitForTimeout(1500);
      rec.afterUrl = rel(page.url());
      rec.text = await visibleText(page);
      await page.screenshot({ path: path.join(out, 'shots', `auth-${kind}-${report.auth.length}.png`) }).catch(() => {});
    } catch (e) { rec.error = e.message.split('\n')[0]; }
    report.auth.push(rec);
    log('auth', kind, JSON.stringify(rec).slice(0, 300));
  }
}

(async () => {
  const browser = await chromium.launch({ executablePath: undefined, args: ['--no-proxy-server'] });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: !!cfg.ignoreHTTPSErrors, viewport: { width: 1366, height: 900 } });
  const page = await ctx.newPage();
  const origin = new URL(cfg.base).origin;
  const hook = (p) => {
    p.on('console', m => {
      if (m.type() !== 'error') return;
      const k = m.text().slice(0, 400);
      const key = rel(p.url()) + '|' + k;
      if (seenConsole.has(key)) return; seenConsole.add(key);
      report.consoleErrors.push({ page: rel(p.url()), text: k, loc: m.location() && m.location().url ? rel(m.location().url) + ':' + m.location().lineNumber : '' });
    });
    p.on('pageerror', e => report.pageErrors.push({ page: rel(p.url()), text: (e.stack || e.message).split('\n').slice(0, 3).join(' | ').slice(0, 500) }));
    p.on('requestfailed', r => { if (r.url().startsWith('data:')) return; report.failedRequests.push({ page: rel(p.url()), url: r.url().slice(0, 200), method: r.method(), err: r.failure() && r.failure().errorText }); });
    p.on('response', r => { if (r.status() >= 400) report.httpErrors.push({ page: rel(p.url()), url: r.url().slice(0, 200), method: r.request().method(), status: r.status() }); });
    p.on('dialog', d => d.dismiss().catch(() => {}));
  };
  hook(page);
  ctx.on('page', np => { if (np !== page) { hook(np); np.close().catch(() => {}); } });

  if (cfg.pre) {
    try { await require(path.resolve(cfg.pre))(page, ctx, report, cfg, { fillForm, submitForm, tryAuth, visibleText }); }
    catch (e) { report.notes.push('pre-step error: ' + e.message.split('\n')[0]); }
  }
  if (!cfg.skipGenericAuth) {
    await tryAuth(page, 'signup', cfg.signupPaths);
    await tryAuth(page, 'login', cfg.loginPaths);
  }
  if (cfg.preAfter) {
    try { await require(path.resolve(cfg.preAfter))(page, ctx, report, cfg, { fillForm, submitForm, tryAuth, visibleText }); }
    catch (e) { report.notes.push('preAfter error: ' + e.message.split('\n')[0]); }
  }

  // BFS crawl
  const queue = (cfg.startPaths || ['/']).map(p => new URL(p, cfg.base).href);
  const seen = new Set();
  const maxPages = cfg.maxPages || 40;
  const tCrawl = Date.now();
  while (queue.length && report.pages.length < maxPages && Date.now() - tCrawl < budget('crawlMinutes', 8)) {
    const url = queue.shift().split('#')[0];
    if (seen.has(url)) continue; seen.add(url);
    if (DANGER.test(url)) continue;
    const rec = { url: rel(url) };
    try {
      const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
      rec.status = resp && resp.status();
      rec.finalUrl = rel(page.url());
      rec.title = await page.title().catch(() => '');
      const hrefs = await page.$$eval('a[href]', as => as.map(a => a.href)).catch(() => []);
      for (const h of hrefs) {
        try { const u = new URL(h); if (u.origin === origin && !seen.has(u.href.split('#')[0])) queue.push(u.href); } catch {}
      }
      rec.links = hrefs.length;
    } catch (e) { rec.error = e.message.split('\n')[0]; }
    report.pages.push(rec);
    log('page', JSON.stringify(rec));
  }

  // Click phase: every visible link/button on the first N pages
  const clickPages = report.pages.filter(p => !p.error).slice(0, cfg.clickPages || 15);
  const tClick = Date.now();
  for (const p of clickPages) {
    if (Date.now() - tClick > budget('clickMinutes', 10)) { report.notes.push('click phase time budget reached'); break; }
    const url = new URL(p.url, cfg.base).href;
    let n = 0;
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {});
      n = await page.locator('a:visible, button:visible, [role=button]:visible, input[type=submit]:visible').count();
    } catch (e) { report.clicks.push({ page: p.url, error: 'load: ' + e.message.split('\n')[0] }); continue; }
    const lim = Math.min(n, cfg.maxClicks || 25);
    for (let i = 0; i < lim; i++) {
      if (Date.now() - tClick > budget('clickMinutes', 10)) break;
      const c = { page: p.url, idx: i };
      try {
        if (rel(page.url()) !== rel(url)) { await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }); await page.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {}); }
        const el = page.locator('a:visible, button:visible, [role=button]:visible, input[type=submit]:visible').nth(i);
        const txt = ((await el.innerText({ timeout: 2000 }).catch(() => '')) || (await el.getAttribute('aria-label').catch(() => '')) || (await el.getAttribute('title').catch(() => '')) || '').trim().replace(/\s+/g, ' ').slice(0, 50);
        c.text = txt;
        const href = await el.getAttribute('href').catch(() => null);
        if (DANGER.test(txt) || (href && DANGER.test(href))) { c.result = 'skipped (destructive/logout)'; report.clicks.push(c); continue; }
        if (href && /^https?:/.test(href) && !href.startsWith(origin)) { c.result = 'external link not followed: ' + href.slice(0, 80); report.clicks.push(c); continue; }
        const errsBefore = report.pageErrors.length + report.consoleErrors.length;
        await el.click({ timeout: 4000 });
        await page.waitForTimeout(800);
        await page.waitForLoadState('networkidle', { timeout: 2500 }).catch(() => {});
        c.after = rel(page.url());
        c.newErrors = report.pageErrors.length + report.consoleErrors.length - errsBefore;
        await page.keyboard.press('Escape').catch(() => {});
      } catch (e) { c.error = e.message.split('\n')[0].slice(0, 200); }
      report.clicks.push(c);
    }
  }

  // Form phase: fill + submit every form found on crawled pages
  const tForm = Date.now();
  for (const p of report.pages.filter(p => !p.error).slice(0, cfg.formPages || 20)) {
    if (Date.now() - tForm > budget('formMinutes', 6)) { report.notes.push('form phase time budget reached'); break; }
    const url = new URL(p.url, cfg.base).href;
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {});
      const forms = await page.locator('form:visible').count();
      for (let i = 0; i < forms; i++) {
        const f = { page: p.url, idx: i };
        try {
          if (rel(page.url()) !== rel(url)) { await page.goto(url, { waitUntil: 'domcontentloaded' }); await page.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {}); }
          const form = page.locator('form:visible').nth(i);
          if (await form.locator('input[type=password]').count() && cfg.skipPasswordForms) { f.result = 'skipped password form'; report.forms.push(f); continue; }
          f.filled = await fillForm(page, form);
          f.submit = await submitForm(page, form);
          await page.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {});
          await page.waitForTimeout(800);
          f.after = rel(page.url());
          f.text = (await visibleText(page)).slice(0, 160);
        } catch (e) { f.error = e.message.split('\n')[0].slice(0, 200); }
        report.forms.push(f);
      }
    } catch (e) { report.forms.push({ page: p.url, error: e.message.split('\n')[0] }); }
  }

  await page.goto(cfg.base, { timeout: 20000 }).catch(() => {});
  await page.screenshot({ path: path.join(out, 'shots', 'final.png') }).catch(() => {});
  report.finished = new Date().toISOString();
  fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 1));
  const s = {
    pages: report.pages.length, pageLoadErrors: report.pages.filter(p => p.error || (p.status && p.status >= 400)).length,
    consoleErrors: report.consoleErrors.length, pageErrors: report.pageErrors.length,
    httpErrors: report.httpErrors.length, failedRequests: report.failedRequests.length,
    clicks: report.clicks.length, clickErrors: report.clicks.filter(c => c.error).length,
    forms: report.forms.length, auth: report.auth.map(a => `${a.kind}:${a.url}:${a.result || a.afterUrl || a.error}`),
  };
  fs.writeFileSync(path.join(out, 'summary.json'), JSON.stringify(s, null, 1));
  log('SUMMARY', JSON.stringify(s));
  await browser.close();
})().catch(e => { console.error('CRAWLER FATAL', e); process.exit(2); });
