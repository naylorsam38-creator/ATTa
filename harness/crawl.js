// Generic Playwright exercise script: same-origin crawl, safe clicks, form fills.
// Usage: node crawl.js <startUrl> <outDir> [extraStartPaths comma-separated]
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const [, , startUrl, outDir, extra = ''] = process.argv;
const MAX_PAGES = +process.env.MAX_PAGES || 25;
const MAX_CLICKS_PER_PAGE = +process.env.MAX_CLICKS || 12;
const DESTRUCTIVE = /\b(delete|remove|wipe|reset|destroy|drop|purge|erase|clear all|uninstall|deactivate|revoke|log ?out|sign ?out|send|publish|pay|buy|purchase|checkout|place order|subscribe|invite|transfer|disable|shutdown|restart|kill|terminate|import|upload|empty trash|ban)\b/i;

fs.mkdirSync(outDir, { recursive: true });
const origin = new URL(startUrl).origin;
const R = { startUrl, origin, pages: [], consoleErrors: [], pageErrors: [], failedRequests: [], badResponses: [], clicks: [], skipped: [], forms: [], notes: [] };
const seenErr = new Set();
const push = (arr, item) => { const k = JSON.stringify(item); if (!seenErr.has(k)) { seenErr.add(k); arr.push(item); } };

let n = 0;
// One deterministic fictional identity per run so signup -> login works.
const fake = () => ({
  email: process.env.T_EMAIL || 'tester@example.test',
  password: process.env.T_PASS || 'Test-Passw0rd!2026xyz',
  name: 'Testy McTestface',
  user: process.env.T_USER || 'testuser',
});

function norm(u) { try { const x = new URL(u, origin); x.hash = ''; return x.href; } catch { return null; } }

async function fillVisible(page, scope, data) {
  const inputs = await scope.$$('input:visible, textarea:visible, select:visible');
  let filled = 0;
  for (const el of inputs) {
    try {
      const tag = (await el.evaluate(e => e.tagName)).toLowerCase();
      const type = ((await el.getAttribute('type')) || 'text').toLowerCase();
      const nm = ((await el.getAttribute('name')) || '') + ' ' + ((await el.getAttribute('id')) || '') + ' ' + ((await el.getAttribute('placeholder')) || '') + ' ' + ((await el.getAttribute('autocomplete')) || '');
      if (await el.isDisabled()) continue;
      if (['hidden', 'submit', 'button', 'image', 'reset', 'file'].includes(type)) continue;
      if (tag === 'select') { const opts = await el.$$('option'); if (opts.length > 1) { const v = await opts[1].getAttribute('value'); if (v !== null) await el.selectOption(v); filled++; } continue; }
      if (type === 'checkbox' || type === 'radio') { if (!DESTRUCTIVE.test(nm)) { await el.check({ timeout: 1500 }).catch(() => {}); filled++; } continue; }
      let v = 'Test value';
      if (type === 'email' || /mail/i.test(nm)) v = data.email;
      else if (type === 'password' || /pass/i.test(nm)) v = data.password;
      else if (/user|login|handle/i.test(nm)) v = data.user;
      else if (/name/i.test(nm)) v = data.name;
      else if (type === 'url' || /url|site|domain/i.test(nm)) v = 'https://example.test';
      else if (type === 'number' || (await el.getAttribute('pattern')) || /timeout|port|count|limit|size|age|depth/i.test(nm)) v = '60';
      else if (type === 'tel') v = '5550100';
      else if (type === 'date') v = '2026-01-01';
      else if (type === 'search') v = 'test';
      else if (tag === 'textarea') v = 'Fictional test content.';
      await el.fill(v, { timeout: 2000 });
      filled++;
    } catch {}
  }
  return filled;
}

async function collectValidation(page) {
  return page.evaluate(() => {
    const out = [];
    document.querySelectorAll('[role=alert], .error, .errors, .alert, .invalid-feedback, .text-danger, .error-message, .notification, .toast, [class*="error" i]').forEach(e => {
      const t = (e.innerText || '').trim(); if (t && t.length < 300 && e.offsetParent !== null) out.push(t);
    });
    document.querySelectorAll('input:invalid, textarea:invalid, select:invalid').forEach(e => out.push(`invalid field ${e.name || e.id}: ${e.validationMessage}`));
    return [...new Set(out)].slice(0, 15);
  }).catch(() => []);
}

(async () => {
  const browser = await require('./launch')();
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1366, height: 900 }, ...(process.env.PW_HTTP_USER ? { httpCredentials: { username: process.env.PW_HTTP_USER, password: process.env.PW_HTTP_PASS } } : {}) });
  const page = await ctx.newPage();
  page.setDefaultTimeout(8000);
  page.on('console', m => { if (m.type() === 'error') push(R.consoleErrors, { url: page.url(), text: m.text().slice(0, 500) }); });
  page.on('pageerror', e => push(R.pageErrors, { url: page.url(), text: String(e).slice(0, 500) }));
  page.on('requestfailed', r => push(R.failedRequests, { url: r.url().slice(0, 300), method: r.method(), error: r.failure() && r.failure().errorText }));
  page.on('response', r => { if (r.status() >= 400) push(R.badResponses, { url: r.url().slice(0, 300), status: r.status(), method: r.request().method() }); });
  page.on('dialog', d => d.dismiss().catch(() => {}));

  const queue = [norm(startUrl), ...extra.split(',').filter(Boolean).map(p => norm(p))];
  const visited = new Set();
  const formSig = new Set();
  while (queue.length && R.pages.length < MAX_PAGES) {
    const url = queue.shift();
    if (!url || visited.has(url)) continue;
    visited.add(url);
    let status = null, err = null;
    try {
      const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      status = resp ? resp.status() : null;
      await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
    } catch (e) { err = String(e.message).split('\n')[0]; }
    const finalUrl = page.url();
    const title = await page.title().catch(() => '');
    R.pages.push({ url, finalUrl, status, title, error: err });
    if (R.pages.length === 1) await page.screenshot({ path: path.join(outDir, 'first-page.png') }).catch(() => {});
    if (err) continue;
    visited.add(norm(finalUrl));

    // discover links
    const links = await page.$$eval('a[href]', as => as.map(a => ({ href: a.href, text: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 60) }))).catch(() => []);
    for (const l of links) {
      const u = norm(l.href);
      if (!u || !u.startsWith(origin)) continue;
      if (DESTRUCTIVE.test(l.text) || DESTRUCTIVE.test(u.replace(origin, ''))) { push(R.skipped, { reason: 'destructive-looking link', text: l.text, href: u }); continue; }
      if (!visited.has(u) && !queue.includes(u)) queue.push(u);
    }

    // forms (incl. login/signup): fill and submit once per distinct signature
    const forms = await page.$$('form');
    const pw = await page.$$('input[type=password]:visible');
    const scopes = forms.length ? forms : (pw.length ? [await page.$('body')] : []);
    for (const f of scopes) {
      try {
        if (!(await f.isVisible())) continue;
        const sig = await f.evaluate(e => [...e.querySelectorAll('input,textarea,select')].map(i => i.name || i.id || i.type).join('|') + '@' + location.pathname);
        if (formSig.has(sig)) continue; formSig.add(sig);
        const submit = await f.$('button[type=submit]:visible, input[type=submit]:visible, button:not([type]):visible');
        const label = submit ? ((await submit.innerText().catch(() => '')) || (await submit.getAttribute('value')) || '').trim() : '';
        if (DESTRUCTIVE.test(label) && !/sign ?up|register|create/i.test(label)) { push(R.skipped, { reason: 'destructive-looking form submit', label, page: finalUrl }); continue; }
        const before = page.url();
        const filled = await fillVisible(page, f, fake());
        if (!filled) continue;
        if (submit) await submit.click({ timeout: 3000 }).catch(e => R.notes.push('submit click failed: ' + e.message.split('\n')[0]));
        else await page.keyboard.press('Enter');
        await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
        await page.waitForTimeout(800);
        R.forms.push({ page: before, submitLabel: label, filled, afterUrl: page.url(), validation: await collectValidation(page) });
        if (page.url() !== before) { const u = norm(page.url()); if (u && u.startsWith(origin)) { visited.delete(u); queue.unshift(u); } /* re-crawl post-auth landing */ break; }
      } catch (e) { R.notes.push('form error: ' + e.message.split('\n')[0]); }
    }

    // safe button clicks
    const buttons = await page.$$('button:visible, [role=button]:visible');
    let clicks = 0;
    for (const b of buttons) {
      if (clicks >= MAX_CLICKS_PER_PAGE) break;
      try {
        const t = ((await b.innerText().catch(() => '')) || (await b.getAttribute('aria-label')) || (await b.getAttribute('title')) || '').trim().slice(0, 60);
        if (DESTRUCTIVE.test(t)) { push(R.skipped, { reason: 'destructive-looking button', text: t, page: finalUrl }); continue; }
        if ((await b.getAttribute('type')) === 'submit') continue;
        const before = page.url();
        await b.click({ timeout: 2000 });
        clicks++;
        await page.waitForTimeout(400);
        await page.keyboard.press('Escape').catch(() => {});
        R.clicks.push({ page: finalUrl, text: t, afterUrl: page.url() });
        const u = norm(page.url());
        if (u && u.startsWith(origin) && !visited.has(u) && !queue.includes(u)) queue.push(u);
        if (page.url() !== before) { await page.goto(before, { waitUntil: 'domcontentloaded' }).catch(() => {}); break; }
      } catch (e) { /* element detached/obscured: ignore */ }
    }
  }
  R.unvisitedQueue = queue.slice(0, 50);
  await page.screenshot({ path: path.join(outDir, 'last-page.png') }).catch(() => {});
  await browser.close();
  fs.writeFileSync(path.join(outDir, 'playwright.json'), JSON.stringify(R, null, 2));
  console.log(JSON.stringify({ pages: R.pages.length, pageStatuses: R.pages.map(p => `${p.status || p.error} ${p.url}`), consoleErrors: R.consoleErrors.length, pageErrors: R.pageErrors.length, failedRequests: R.failedRequests.length, badResponses: R.badResponses.length, forms: R.forms.length, clicks: R.clicks.length, skipped: R.skipped.length }, null, 1));
})().catch(e => { console.error('CRAWL FATAL', e); process.exit(2); });
