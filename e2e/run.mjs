// End-to-end check of 5 apps behind proxy__100_adapter.js. Report-only: nothing here changes the adapter files.
// Usage: node e2e/run.mjs [appId ...]     Evidence goes to e2e/evidence/<app>/ (video .webm, screenshots, result.json).
import { chromium } from '/home/user/ATTa/node_modules/playwright/index.mjs';
import http from 'node:http';
import zlib from 'node:zlib';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';

const E2E = '/home/user/ATTa/e2e';
const BED = path.join(E2E, 'bed');
const USER_PORT_JS = fs.readFileSync('/home/user/ATTa/adapter/port.js');
const MOVER_URL = '/_cp/caps/button-mover.mjs';
const PASS = 'e2e-pass-123';

const APPS = [
  { id: 'archivebox', direct: 'http://127.0.0.1:9101', proxy: 'http://127.0.0.1:9201',
    login: { path: '/admin/login/', user: 'admin', pass: PASS }, live: null },
  { id: 'authelia', direct: 'http://auth.e2e.localhost:9102', proxy: 'http://auth.e2e.localhost:9202',
    login: { path: '/', user: 'atta', pass: PASS }, live: null },
  { id: 'mixpost', direct: 'http://127.0.0.1:9103', proxy: 'http://127.0.0.1:9203',
    login: { path: '/mixpost/login', user: 'admin@example.com', pass: 'changeme' }, live: null },
  { id: 'alexandrie', direct: 'http://127.0.0.1:9104', proxy: 'http://127.0.0.1:9204',
    signup: { path: '/signup', user: 'e2euser', email: 'e2e@example.com', pass: 'E2e-Pass-123!' },
    login: { path: '/login', user: 'e2euser', pass: 'E2e-Pass-123!' }, live: null },
  { id: 'memos', direct: 'http://127.0.0.1:9105', proxy: 'http://127.0.0.1:9205',
    signup: { path: '/auth/signup', user: 'e2euser', pass: PASS },
    login: { path: '/auth', user: 'e2euser', pass: PASS }, live: { kind: 'sse', path: '/api/sse' } },
  // 6-24: apps without a login form use login.path '/' (step e then only checks no password form blocks the app)
  { id: 'opencut', direct: 'http://127.0.0.1:9106', proxy: 'http://127.0.0.1:9206', login: { path: '/' }, live: null },
  { id: 'open-notebook', direct: 'http://127.0.0.1:9107', proxy: 'http://127.0.0.1:9207', login: { path: '/' }, live: null },
  { id: 'sendportal', direct: 'http://127.0.0.1:9108', proxy: 'http://127.0.0.1:9208',
    signup: { path: '/register', user: 'e2euser', email: 'e2e@example.com', pass: 'E2e-Pass-123!' },
    login: { path: '/login', user: 'e2e@example.com', email: 'e2e@example.com', pass: 'E2e-Pass-123!' }, live: null },
  { id: 'ghost', direct: 'http://127.0.0.1:9109', proxy: 'http://127.0.0.1:9209',
    signup: { path: '/ghost/#/setup', user: 'e2euser', email: 'e2e@example.com', pass: 'E2e-Pass-123!x' },
    login: { path: '/ghost/#/signin', user: 'e2e@example.com', email: 'e2e@example.com', pass: 'E2e-Pass-123!x' }, live: null },
  { id: 'actual', direct: 'http://127.0.0.1:9110', proxy: 'http://127.0.0.1:9210',
    signup: { path: '/bootstrap', user: 'e2euser', pass: PASS }, login: { path: '/login', user: 'e2euser', pass: PASS }, live: null },
  { id: 'gitea', direct: 'http://127.0.0.1:9111', proxy: 'http://127.0.0.1:9211',
    signup: { path: '/user/sign_up', user: 'e2euser', email: 'e2e@example.com', pass: PASS },
    login: { path: '/user/login', user: 'e2euser', pass: PASS }, live: { kind: 'sse', path: '/user/events' } },
  { id: 'supabase', direct: 'http://127.0.0.1:9112', proxy: 'http://127.0.0.1:9212', login: { path: '/' }, live: null,
    basicAuth: { username: 'supabase', password: 'this_password_is_insecure_and_should_be_updated' } },
  { id: 'appsmith', direct: 'http://127.0.0.1:9113', proxy: 'http://127.0.0.1:9213',
    signup: { path: '/setup/welcome', user: 'e2euser', email: 'e2e@example.com', pass: 'E2e-Pass-123!' },
    login: { path: '/user/login', user: 'e2e@example.com', email: 'e2e@example.com', pass: 'E2e-Pass-123!' }, live: null },
  { id: 'illa-builder', direct: 'http://127.0.0.1:9116', proxy: 'http://127.0.0.1:9216',
    login: { path: '/cloud/login', user: 'root', email: 'root', pass: 'password' }, live: null },
  { id: 'meilisearch', direct: 'http://127.0.0.1:9118', proxy: 'http://127.0.0.1:9218', login: { path: '/', user: 'e2e', pass: 'e2e-master-key-0123456789abcdef' }, live: null },
  { id: 'ntfy', direct: 'http://127.0.0.1:9122', proxy: 'http://127.0.0.1:9222', login: { path: '/' }, live: { kind: 'sse', path: '/e2etest/sse' } },
  { id: 'jitsi', direct: 'http://127.0.0.1:9120', proxy: 'http://127.0.0.1:9220', login: { path: '/' }, live: null },
  { id: 'indico', direct: 'http://127.0.0.1:9119', proxy: 'http://127.0.0.1:9219',
    signup: { path: '/bootstrap', user: 'e2euser', email: 'e2e@e2e-test.org', pass: 'E2e-Pass-123!x' },
    login: { path: '/login/', user: 'e2euser', pass: 'E2e-Pass-123!x' }, live: null },
];

// ---------- small helpers ----------
const sha = b => crypto.createHash('sha256').update(b).digest('hex').slice(0, 16);
const sleep = ms => new Promise(r => setTimeout(r, ms));
function raw(url, { method = 'GET', headers = {}, body = null, timeout = 60000, cookie = '' } = {}) {
  if (cookie) headers = { ...headers, cookie };
  return new Promise((resolve) => {
    const u = new URL(url); const t0 = Date.now(); let ttfb = null;
    const req = http.request({ hostname: '127.0.0.1', port: u.port, path: u.pathname + u.search, method,
      headers: { host: u.host, ...headers } }, res => {
      const chunks = []; res.on('data', c => { if (ttfb === null) ttfb = Date.now() - t0; chunks.push(c); });
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks), ttfb, ms: Date.now() - t0 }));
      res.on('error', e => resolve({ error: e.message }));
    });
    req.setTimeout(timeout, () => { req.destroy(new Error(`timeout after ${timeout}ms`)); });
    req.on('error', e => resolve({ error: e.message, ms: Date.now() - t0 }));
    if (body) req.end(body); else req.end();
  });
}
function decode(buf, enc) {
  enc = (enc || '').toLowerCase();
  if (enc === 'gzip') return zlib.gunzipSync(buf);
  if (enc === 'br') return zlib.brotliDecompressSync(buf);
  if (enc === 'deflate') return zlib.inflateSync(buf);
  return buf;
}
async function getFollow(base, p, opts = {}) {
  const chain = []; let r;
  for (let i = 0; i < 6; i++) {
    r = await raw(base + p, opts); chain.push(`${p} ${r.status || r.error}`);
    if (!(r.status >= 300 && r.status < 400 && r.headers.location)) break;
    const loc = new URL(r.headers.location, base + p);
    if (loc.origin !== new URL(base).origin) { chain.push(`-> leaves proxy: ${loc.href}`); break; }
    p = loc.pathname + loc.search;
  }
  return { ...r, chain: chain.join(' -> '), finalPath: p };
}
const cookieHeader = async ctx => (await ctx.cookies()).map(c => `${c.name}=${c.value}`).join('; ');
const HOOK_RE = /<script src="\/_cp\/port\.js" data-capability-hook="1" defer><\/script>/;

// ---------- neutral probe: the proxy against a plain test server (no app quirks) ----------
async function proxyProbe() {
  const out = {};
  const BIG = crypto.randomBytes(50 * 1024 * 1024);
  const srv = http.createServer((req, res) => {
    if (req.url === '/big') { res.writeHead(200, { 'content-type': 'application/octet-stream', 'content-length': BIG.length }); return res.end(BIG); }
    if (req.url === '/upload') { const h = crypto.createHash('sha256'); let n = 0; req.on('data', c => { h.update(c); n += c.length; });
      return req.on('end', () => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ bytes: n, sha: h.digest('hex').slice(0, 16) })); }); }
    if (req.url === '/stream') { res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' });
      let i = 0; const t = setInterval(() => { res.write(`data: tick ${i++}\n\n`); if (i > 20) { clearInterval(t); res.end(); } }, 500); return; }
    if (req.url === '/redirect') { res.writeHead(302, { location: `http://${req.headers.host}/landed` }); return res.end(); }
    if (req.url === '/echo-host') { res.writeHead(200, { 'content-type': 'application/json' });
      return res.end(JSON.stringify({ host: req.headers.host, origin: req.headers.origin || null, xff: req.headers['x-forwarded-for'] || null, xfh: req.headers['x-forwarded-host'] || null, xfp: req.headers['x-forwarded-proto'] || null })); }
    if (req.url === '/gz-html') { const html = Buffer.from('<html><head><title>gz</title></head><body>' + 'x'.repeat(5000) + '</body></html>');
      res.writeHead(200, { 'content-type': 'text/html', 'content-encoding': 'gzip' }); return res.end(zlib.gzipSync(html)); }
    if (req.url === '/zstd-html') { res.writeHead(200, { 'content-type': 'text/html', 'content-encoding': 'zstd' }); return res.end(Buffer.from('not really zstd')); }
    if (req.url === '/no-head-html') { res.writeHead(200, { 'content-type': 'text/html' }); return res.end('<p>fragment without a head</p>'); }
    res.writeHead(404); res.end();
  });
  srv.on('upgrade', (req, sock) => {   // minimal RFC6455 handshake + one text frame
    const acc = crypto.createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
    sock.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${acc}\r\n\r\n`);
    const msg = Buffer.from('hello-from-ws'); sock.write(Buffer.concat([Buffer.from([0x81, msg.length]), msg]));
  });
  await new Promise(r => srv.listen(9310, '127.0.0.1', r));
  const px = spawn('node', [path.join(BED, 'adapter/proxy__100_adapter.js'), '--app-id', 'probe', '--target', 'http://127.0.0.1:9310', '--port', '9311']);
  let pxLog = ''; px.stdout.on('data', d => pxLog += d); px.stderr.on('data', d => pxLog += d);
  await sleep(800);
  const D = 'http://127.0.0.1:9310', P = 'http://127.0.0.1:9311';

  // WebSocket
  const ws = async base => new Promise(resolve => {
    const w = new WebSocket(base.replace('http', 'ws') + '/ws'); const t = setTimeout(() => { try { w.close(); } catch {} resolve('no open/message within 5s'); }, 5000);
    w.onmessage = e => { clearTimeout(t); w.close(); resolve(`OK message "${e.data}"`); };
    w.onerror = e => { clearTimeout(t); resolve(`error: ${e.message || e.error?.message || 'connection failed'}`); };
  });
  out.websocket = { direct: await ws(D), proxy: await ws(P) };
  const up = await raw(P + '/ws', { headers: { connection: 'Upgrade', upgrade: 'websocket', 'sec-websocket-version': '13', 'sec-websocket-key': 'dGhlIHNhbXBsZSBub25jZQ==' }, timeout: 5000 });
  out.websocket.rawUpgradeViaProxy = up.error ? `error: ${up.error}` : `HTTP ${up.status} (101 expected)`;

  // 50 MB download / upload
  for (const [k, base] of [['direct', D], ['proxy', P]]) {
    const r = await raw(base + '/big');
    out[`download_${k}`] = r.error ? { error: r.error } : { status: r.status, bytes: r.body.length, sha: sha(r.body), ttfbMs: r.ttfb, totalMs: r.ms };
    const u = await raw(base + '/upload', { method: 'POST', headers: { 'content-type': 'application/octet-stream', 'content-length': BIG.length }, body: BIG });
    out[`upload_${k}`] = u.error ? { error: u.error } : { status: u.status, ...JSON.parse(u.body.toString() || '{}'), totalMs: u.ms };
  }
  out.bigSha = sha(BIG);
  // Streaming (SSE): when does the first event arrive?
  for (const [k, base] of [['direct', D], ['proxy', P]]) {
    const r = await raw(base + '/stream', { timeout: 20000 });
    out[`stream_${k}`] = r.error ? { error: r.error } : { status: r.status, firstByteMs: r.ttfb, totalMs: r.ms, events: (r.body.toString().match(/data:/g) || []).length };
  }
  // Headers the app sees, redirects, encodings, no-<head> pages
  const eh = await raw(P + '/echo-host', { headers: { origin: 'http://127.0.0.1:9311' } }); out.headersAppSees = JSON.parse(eh.body.toString());
  const rd = await raw(P + '/redirect'); out.redirectLocationViaProxy = rd.headers?.location;
  const gz = await raw(P + '/gz-html'); out.gzipHtml = { status: gz.status, enc: gz.headers['content-encoding'], hook: HOOK_RE.test(decode(gz.body, gz.headers['content-encoding']).toString()) };
  const zs = await raw(P + '/zstd-html'); out.zstdHtml = { status: zs.status, body: zs.body.toString().trim() };
  const nh = await raw(P + '/no-head-html'); out.noHeadHtml = { status: nh.status, hookInjected: HOOK_RE.test(nh.body.toString()) };
  px.kill(); srv.close(); out.proxyStdout = pxLog.trim();
  return out;
}

// ---------- per-app run ----------
async function runApp(browser, app) {
  const dir = path.join(E2E, 'evidence', app.id); fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true });
  const R = { app: app.id, direct: app.direct, proxy: app.proxy, checks: {} };
  const set = (k, status, evidence, error = null, pointsTo = null) => { R.checks[k] = { status, evidence, error, pointsTo }; console.log(`  ${app.id} ${k}: ${status} — ${(error || evidence || '').toString().slice(0, 160)}`); };
  const guard = async (k, fn) => { try { await fn(); } catch (e) { set(k, 'FAIL', null, `check crashed: ${e.message.split('\n')[0]}`); } };

  // a. Docker: containers for this app
  await guard('a', async () => {
    const { execSync } = await import('node:child_process');
    const ps = execSync(`docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep -iE '${app.id}' || true`).toString().trim();
    const r = await raw(app.direct + '/');
    set('a', ps && !r.error && r.status < 500 ? 'PASS' : 'FAIL', `${ps.replace(/\n/g, ' | ')} ; GET / -> ${r.status || r.error}`);
  });

  // Direct baseline browser session (own video)
  const dctx = await browser.newContext({ recordVideo: { dir: path.join(dir, 'video-direct') }, viewport: { width: 1280, height: 800 }, httpCredentials: app.basicAuth });
  const dpage = await dctx.newPage(); const directErrors = [];
  dpage.on('console', m => m.type() === 'error' && directErrors.push(m.text().slice(0, 300)));
  dpage.on('pageerror', e => directErrors.push('pageerror: ' + e.message.slice(0, 300)));
  let directTitle = null;
  await guard('b', async () => {
    const resp = await dpage.goto(app.direct + '/', { waitUntil: 'load', timeout: 45000 }); await dpage.waitForTimeout(2500);
    directTitle = await dpage.title(); await dpage.screenshot({ path: path.join(dir, 'b-direct-home.png') });
    const text = (await dpage.locator('body').innerText().catch(() => '')).trim();
    set('b', resp && resp.status() < 400 && text.length > 0 ? 'PASS' : 'FAIL', `HTTP ${resp?.status()} final ${dpage.url()} title "${directTitle}" (${text.length} chars visible); screenshot b-direct-home.png`);
  });
  await dctx.close();

  // Proxy session (own video) — c..k run here
  const ctx = await browser.newContext({ recordVideo: { dir: path.join(dir, 'video-proxy') }, viewport: { width: 1280, height: 800 }, httpCredentials: app.basicAuth });
  const page = await ctx.newPage(); const errs = []; const failed = []; const bad = []; const sockets = []; const assets = [];
  page.on('console', m => m.type() === 'error' && errs.push(m.text().slice(0, 300)));
  page.on('pageerror', e => errs.push('pageerror: ' + e.message.slice(0, 300)));
  page.on('requestfailed', r => failed.push(`${r.method()} ${r.url().slice(0, 150)} — ${r.failure()?.errorText}`));
  page.on('response', async r => { const u = r.url(); if (r.status() >= 500 || r.status() === 404 && u.includes('/_cp/')) bad.push(`${r.status()} ${u.slice(0, 150)}`);
    if (u.startsWith(app.proxy) && r.request().resourceType().match(/script|stylesheet|image|font/)) { const len = Number((await r.allHeaders())['content-length'] || 0); assets.push({ u, len }); } });
  page.on('websocket', ws => { const rec = { url: ws.url(), frames: 0, error: null, closed: false }; sockets.push(rec);
    ws.on('framereceived', () => rec.frames++); ws.on('socketerror', e => rec.error = String(e)); ws.on('close', () => rec.closed = true); });

  let proxyTitle = null;
  await guard('c', async () => {
    const resp = await page.goto(app.proxy + '/', { waitUntil: 'load', timeout: 45000 }); await page.waitForTimeout(2500);
    proxyTitle = await page.title(); await page.screenshot({ path: path.join(dir, 'c-proxy-home.png') });
    const text = (await page.locator('body').innerText().catch(() => '')).trim();
    const onProxy = new URL(page.url()).origin === new URL(app.proxy).origin;
    const ok = resp && resp.status() < 400 && text.length > 0 && onProxy && proxyTitle === directTitle;
    set('c', ok ? 'PASS' : 'FAIL', `HTTP ${resp?.status()} final ${page.url()} title "${proxyTitle}" (direct title "${directTitle}"); screenshot c-proxy-home.png`,
      !onProxy ? `browser left the proxy: ended on ${page.url()}` : proxyTitle !== directTitle ? 'title differs from direct' : (!ok ? 'page did not render' : null),
      !onProxy ? 'proxy: proxyRequest() forwards Location headers untouched and sets Host to the target' : null);
  });

  await guard('d', async () => {
    const home = await getFollow(app.proxy, '/', { headers: { accept: 'text/html' }, cookie: await cookieHeader(ctx) });
    const html = decode(home.body, home.headers['content-encoding']).toString(), note = ` (${home.chain})`;
    const tag = HOOK_RE.test(html);
    const pj = await raw(app.proxy + '/_cp/port.js'); const same = pj.body && Buffer.compare(pj.body, USER_PORT_JS) === 0;
    const inPage = await page.evaluate(() => ({ api: window.__capabilityPort?.version || null, fab: !!document.querySelector('#capability-port'),
      tag: !!document.querySelector('script[data-capability-hook]') })).catch(e => ({ err: e.message }));
    const csp = page.url() && (await raw(new URL(page.url()).href.replace(new URL(page.url()).origin, app.proxy))).headers?.['content-security-policy'];
    const cspErr = errs.filter(e => /Content Security Policy|Refused to load the script/i.test(e));
    const ok = tag && pj.status === 200 && same && inPage.api && inPage.fab;
    set('d', ok ? 'PASS' : 'FAIL',
      `HTML via proxy${note}: hook tag ${tag ? 'present' : 'MISSING'}; /_cp/port.js -> ${pj.status}, ${pj.body?.length}B, identical to adapter/port.js: ${same}; in browser: tag=${inPage.tag} window.__capabilityPort=${inPage.api} button=${inPage.fab}${csp ? `; CSP: ${csp.slice(0, 200)}` : ''}`,
      ok ? null : (cspErr[0] || (!inPage.tag ? 'script tag not in the live DOM' : !inPage.api ? 'port.js did not run in the page' : 'see evidence')),
      ok ? null : cspErr.length ? 'proxy: injectHook() adds <script src> with no CSP nonce and leaves the app\'s Content-Security-Policy header in place' : 'port.js / proxy injectHook()');
  });

  // e. login (signup first when the app needs a user created)
  const fillForm = async (p, f) => {
    for (const inp of await p.locator('input:visible').all()) {
      const t = (await inp.getAttribute('type')) || 'text'; const nm = ((await inp.getAttribute('name')) || '') + ((await inp.getAttribute('id')) || '') + ((await inp.getAttribute('autocomplete')) || '');
      if (t === 'password') await inp.fill(f.pass);
      else if (t === 'email' || /mail/i.test(nm)) await inp.fill(f.email || f.user);
      else if (['text', ''].includes(t)) await inp.fill(f.user);
      else if (t === 'checkbox' && /terms|agree|accept/i.test(nm)) await inp.check().catch(() => {});
    }
    const submit = p.locator('button[type=submit]:visible, input[type=submit]:visible').first();
    if (await submit.count()) await submit.click(); else await p.locator('input[type=password]:visible').last().press('Enter');
    await p.waitForLoadState('load').catch(() => {}); await p.waitForTimeout(4000);
  };
  await guard('e', async () => {
    const steps = [];
    if (app.signup) {
      await page.goto(app.proxy + app.signup.path, { waitUntil: 'load' }); await page.waitForTimeout(2500);
      await page.screenshot({ path: path.join(dir, 'e0-signup-form.png') });
      if (await page.locator('input[type=password]:visible').count()) { await fillForm(page, app.signup); steps.push(`signup submitted, now at ${page.url()}`); }
      else steps.push(`no signup form at ${app.signup.path} (now ${page.url()})`);
      await page.screenshot({ path: path.join(dir, 'e1-after-signup.png') });
    }
    const stillLoggedIn = !(await page.locator('input[type=password]:visible').count()) && app.signup && !/signup|login|auth/.test(new URL(page.url()).pathname);
    if (!stillLoggedIn) {
      await page.goto(app.proxy + app.login.path, { waitUntil: 'load' }); await page.waitForTimeout(2500);
      await page.screenshot({ path: path.join(dir, 'e2-login-form.png') });
      if (!(await page.locator('input[type=password]:visible').count())) { steps.push(`no password field at ${app.login.path} (now ${page.url()})`); }
      else { await fillForm(page, app.login); steps.push(`login submitted, now at ${page.url()}`); }
    } else steps.push('signup logged the user in');
    await page.screenshot({ path: path.join(dir, 'e3-after-login.png') });
    const pw = await page.locator('input[type=password]:visible').count();
    const onProxy = new URL(page.url()).origin === new URL(app.proxy).origin;
    const bodyText = (await page.locator('body').innerText().catch(() => '')).slice(0, 2000);
    const errLine = (bodyText.match(/.*(error|invalid|incorrect|forbidden|csrf|failed|denied|wrong)[^\n]*/i) || [null])[0];
    const recent = [...errs.slice(-4), ...bad.slice(-3), ...failed.slice(-3)].join(' | ');
    const ok = pw === 0 && onProxy;
    set('e', ok ? 'PASS' : 'FAIL', `${steps.join('; ')}; password field still shown: ${pw > 0}; final ${page.url()}; screenshots e*-*.png`,
      ok ? null : (!onProxy ? `left the proxy: ${page.url()}` : errLine ? `page says: "${errLine.trim().slice(0, 200)}"` : `still on login form. Recent errors: ${recent.slice(0, 400)}`));
  });

  // f. main navigation through the proxy
  await guard('f', async () => {
    const start = page.url();
    const links = await page.evaluate(origin => {
      const seen = new Set(), out = [];
      const nodes = document.querySelectorAll('nav a[href], header a[href], aside a[href], [role=navigation] a[href], [class*=sidebar] a[href], [class*=menu] a[href], a[href]');
      for (const a of nodes) { if (a.closest('#capability-port')) continue; const r = a.getBoundingClientRect(); if (!r.width || !r.height) continue;
        let u; try { u = new URL(a.href, location.href); } catch { continue; }
        if (u.origin !== origin || /logout|signout|sign-out|delete|\.(zip|tar|gz)$/i.test(u.pathname + u.search) || u.pathname === location.pathname && !u.search) continue;
        const k = u.pathname + u.search; if (seen.has(k)) continue; seen.add(k); out.push({ href: k, text: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 40) });
        if (out.length >= 6) break; }
      return out;
    }, new URL(app.proxy).origin);
    const visits = []; let fails = 0;
    for (const [i, l] of links.entries()) {
      const before = errs.length + bad.length;
      try {
        await page.goto(start, { waitUntil: 'load' }).catch(() => {}); await page.waitForTimeout(1200);
        const loc = page.locator(`a[href="${l.href}"]:visible`).first();
        let clickNote = '';
        if (await loc.count()) { try { await loc.click({ timeout: 8000 }); } catch (e) { const why = (e.message.match(/.*intercepts pointer events.*|.*not visible.*|.*outside of the viewport.*/) || [e.message.split('\n')[0]])[0].trim();
          clickNote = ` (CLICK FAILED: ${why.slice(0, 160)}; opened by URL instead)`; fails++; await page.goto(app.proxy + l.href, { waitUntil: 'load' }); } }
        else await page.goto(app.proxy + l.href, { waitUntil: 'load' });
        await page.waitForLoadState('load').catch(() => {}); await page.waitForTimeout(1800);
        const onProxy = new URL(page.url()).origin === new URL(app.proxy).origin;
        const portStill = await page.evaluate(() => !!window.__capabilityPort && !!document.querySelector('#capability-port')).catch(() => false);
        const newErr = [...errs, ...bad].slice(before);
        const banner = ((await page.locator('body').innerText().catch(() => '')).match(/.*(mismatch|csrf|forbidden|not allowed|unrecognized|disabled from|error \d{3}).*/i) || [''])[0].trim();
        const ok = onProxy && portStill && !bad.slice(before).length && !banner;
        if (!ok && !clickNote) fails++;
        await page.screenshot({ path: path.join(dir, `f${i + 1}.png`) });
        visits.push(`${ok ? 'ok' : 'FAIL'} "${l.text}" ${l.href} -> ${page.url()}${onProxy ? '' : ' (LEFT PROXY)'}${portStill ? '' : ' (port.js missing)'}${clickNote}${banner ? ` (PAGE SAYS: "${banner.slice(0, 160)}")` : ''}${newErr.length ? ' errors: ' + newErr.slice(0, 2).join(' / ').slice(0, 200) : ''}`);
      } catch (e) { fails++; visits.push(`FAIL "${l.text}" ${l.href}: ${e.message.split('\n')[0]}`); }
    }
    set('f', links.length && !fails ? 'PASS' : 'FAIL', `${links.length} links clicked from ${start}: ${visits.join(' || ')}; screenshots f*.png`,
      !links.length ? 'no clickable same-origin links found after login' : fails ? `${fails}/${links.length} navigations failed` : null);
    await page.goto(start, { waitUntil: 'load' }).catch(() => {}); await page.waitForTimeout(1500);
  });

  // g. live connections
  await guard('g', async () => {
    if (app.live?.kind === 'sse') {
      const probe = base => page.evaluate(async ({ url }) => {
        const t0 = performance.now(); const ac = new AbortController(); const t = setTimeout(() => ac.abort(), 12000);
        try { const r = await fetch(url, { headers: { Accept: 'text/event-stream' }, signal: ac.signal, credentials: 'include' });
          const rd = r.body.getReader(); const { value } = await rd.read(); clearTimeout(t); ac.abort();
          return { status: r.status, firstChunkMs: Math.round(performance.now() - t0), sample: new TextDecoder().decode(value).slice(0, 80) };
        } catch (e) { return { status: null, error: `${e.name}: no data within ${Math.round(performance.now() - t0)}ms` }; } }, { url: base + app.live.path });
      const viaProxy = await probe(app.proxy);
      const ok = viaProxy.status === 200 && viaProxy.firstChunkMs < 5000;
      set('g', ok ? 'PASS' : 'FAIL', `SSE ${app.live.path} through proxy: ${JSON.stringify(viaProxy)}; app's live-refresh WebSockets seen: ${sockets.length}`,
        ok ? null : `server-sent events never reach the browser through the proxy (${viaProxy.error || 'status ' + viaProxy.status})`,
        'proxy: proxyRequest() buffers the whole upstream body (chunks[] until "end") before writing anything');
    } else if (sockets.length) {
      const badWs = sockets.filter(s => s.error || !s.frames);
      set('g', badWs.length ? 'FAIL' : 'PASS', JSON.stringify(sockets).slice(0, 400), badWs.length ? `${badWs.length} websocket(s) failed` : null, 'proxy: no server.on("upgrade") handler');
    } else set('g', 'N/A', `app opened no WebSocket/SSE during the session (and its source has none for the UI); proxy behaviour covered by the neutral probe`);
  });

  // h. large download / upload through the proxy (largest real asset + 25 MB POST)
  await guard('h', async () => {
    assets.sort((a, b) => b.len - a.len); const big = assets[0];
    let dl = 'no asset with content-length seen';
    let dlOk = false;
    if (big) {
      const p = new URL(big.u).pathname + new URL(big.u).search;
      const a = await raw(app.direct + p, { headers: { 'accept-encoding': 'identity' } }); const b = await raw(app.proxy + p, { headers: { 'accept-encoding': 'identity' } });
      dlOk = a.status === b.status && sha(a.body) === sha(b.body);
      dl = `largest asset ${p} (${big.len}B): direct ${a.status} ${a.body?.length}B ${sha(a.body)} vs proxy ${b.status} ${b.body?.length}B ${sha(b.body)}`;
    }
    const BODY = crypto.randomBytes(25 * 1024 * 1024);
    const upPath = app.login.path;
    const ud = await raw(app.direct + upPath, { method: 'POST', headers: { 'content-type': 'application/octet-stream', 'content-length': BODY.length }, body: BODY });
    const up = await raw(app.proxy + upPath, { method: 'POST', headers: { 'content-type': 'application/octet-stream', 'content-length': BODY.length }, body: BODY });
    const upOk = !up.error && (up.status === ud.status || ud.error);
    set('h', dlOk && upOk ? 'PASS' : 'FAIL', `${dl}; 25 MB POST ${upPath}: direct -> ${ud.status || ud.error} in ${ud.ms}ms, proxy -> ${up.status || up.error} in ${up.ms}ms${up.body ? ` "${up.body.toString().slice(0, 80).replace(/\s+/g, ' ')}"` : ''}`,
      !dlOk ? 'downloaded bytes differ between direct and proxy' : !upOk ? `upload answered differently through proxy (${up.status || up.error})` : null);
  });

  // i. compressed responses
  await guard('i', async () => {
    const pagePath = new URL(page.url()).pathname + new URL(page.url()).search;
    const rows = []; let fails = 0;
    for (const enc of ['gzip', 'br']) {
      const ck = await cookieHeader(ctx);
      const d = await getFollow(app.direct, pagePath, { headers: { 'accept-encoding': enc, accept: 'text/html' }, cookie: ck });
      const p = await getFollow(app.proxy, pagePath, { headers: { 'accept-encoding': enc, accept: 'text/html' }, cookie: ck });
      let decOk = false, hook = false, lenOk = false, err = null;
      try { const body = decode(p.body, p.headers['content-encoding']); decOk = true; hook = HOOK_RE.test(body.toString()); lenOk = Number(p.headers['content-length']) === p.body.length; } catch (e) { err = e.message; }
      const isHtml = /text\/html/.test(p.headers['content-type'] || '');
      const ok = p.status < 500 && decOk && (!isHtml || hook) && (lenOk || !p.headers['content-length']);
      if (!ok) fails++;
      rows.push(`HTML ${p.chain} ae=${enc}: app sent ${d.headers?.['content-encoding'] || 'none'}, proxy sent ${p.status} ${p.headers?.['content-encoding'] || 'none'} decodes=${decOk} hook=${hook} length-ok=${lenOk}${err ? ' ERR ' + err : ''}`);
    }
    const asset = assets.find(a => /\.(js|css)(\?|$)/.test(a.u));
    if (asset) for (const enc of ['gzip', 'br']) {
      const p0 = new URL(asset.u).pathname + new URL(asset.u).search;
      const d = await raw(app.direct + p0, { headers: { 'accept-encoding': enc } }); const p = await raw(app.proxy + p0, { headers: { 'accept-encoding': enc } });
      let same = false; try { same = sha(decode(d.body, d.headers['content-encoding'])) === sha(decode(p.body, p.headers['content-encoding'])); } catch {}
      if (!same) fails++;
      rows.push(`asset ${p0.slice(0, 60)} ae=${enc}: app ${d.headers['content-encoding'] || 'none'}, proxy ${p.headers['content-encoding'] || 'none'}, decoded identical=${same}`);
    }
    const anyCompressed = rows.some(r => /app sent (gzip|br)|app (gzip|br)/.test(r));
    set('i', fails ? 'FAIL' : 'PASS', rows.join(' || ') + (anyCompressed ? '' : ' (app never compressed; only pass-through tested)'), fails ? `${fails} compressed response(s) broken` : null);
  });

  // j. button mover: attach through the port, drag a control, check it moved on screen and persists after reload
  await guard('j', async () => {
    await page.goto(page.url(), { waitUntil: 'load' }); await page.waitForTimeout(2500);
    const port = page.locator('#capability-port');
    if (!(await port.count())) { set('j', 'FAIL', null, 'no port button on the page, so the mover cannot be attached', 'see check d'); return; }
    await port.locator('button.fab').click(); await port.locator('textarea').fill(MOVER_URL); await port.locator('button.go').click();
    await page.waitForTimeout(2500);
    const msg = (await port.locator('.msg').innerText().catch(() => '')).trim();
    const toggle = port.locator('[data-capability="button-mover"] [data-t]');
    if (!(await toggle.count())) { await page.screenshot({ path: path.join(dir, 'j-attach-failed.png') });
      set('j', 'FAIL', `port message: "${msg}"`, `mover did not mount: "${msg}"`, 'port.js attach() / mover__100_adapter.js'); return; }
    await toggle.click(); await port.locator('.close').click(); await page.waitForTimeout(400);
    // pick a visible control the user would plausibly move (not inside the port, in the left 3/4 of the screen)
    const targets = await page.evaluate(() => {
      const sel = 'a,button,input[type=submit],input[type=button],[role=button],.ui.button,summary'; const out = [];
      for (const el of document.querySelectorAll(sel)) { if (el.closest('#capability-port')) continue; const r = el.getBoundingClientRect();
        if (r.width < 20 || r.height < 12 || r.left < 0 || r.top < 0 || r.right > 900 || r.bottom > 700) continue;
        const disp = getComputedStyle(el).display; out.push({ i: [...document.querySelectorAll(sel)].indexOf(el), tag: el.tagName.toLowerCase(), text: (el.innerText || el.value || '').trim().slice(0, 30), disp, x: r.left + r.width / 2, y: r.top + r.height / 2 }); }
      const block = out.find(t => t.disp !== 'inline'), inline = out.find(t => t.disp === 'inline');
      return [block, inline].filter(Boolean);
    });
    if (!targets.length) { set('j', 'FAIL', `mounted ("${msg}") but no visible control to drag`, 'nothing to move on this screen'); return; }
    const rows = []; let fails = 0;
    for (const t of targets) {
      const h = await page.evaluateHandle(i => document.querySelectorAll('a,button,input[type=submit],input[type=button],[role=button],.ui.button,summary')[i], t.i);
      const before = await h.evaluate(el => { const r = el.getBoundingClientRect(); return [r.left, r.top]; });
      await page.mouse.move(t.x, t.y); await page.mouse.down(); for (let s = 1; s <= 10; s++) await page.mouse.move(t.x + 12 * s, t.y + 6 * s); await page.mouse.up(); await page.waitForTimeout(400);
      const after = await h.evaluate(el => { const r = el.getBoundingClientRect(); return [r.left, r.top, el.style.translate, el.dataset.moved]; });
      const dx = Math.round(after[0] - before[0]), dy = Math.round(after[1] - before[1]);
      const moved = Math.abs(dx - 120) < 6 && Math.abs(dy - 60) < 6;
      if (!moved) fails++;
      rows.push(`<${t.tag}> "${t.text}" display:${t.disp}: dragged +120,+60 -> on screen moved ${dx},${dy}, style.translate="${after[2]}" saved=${after[3] === '1'}`);
    }
    await page.screenshot({ path: path.join(dir, 'j-after-drag.png') });
    await page.reload({ waitUntil: 'load' }); await page.waitForTimeout(4000);
    const persisted = await page.evaluate(() => document.querySelectorAll('[data-moved="1"]').length);
    await page.screenshot({ path: path.join(dir, 'j-after-reload.png') });
    if (!persisted) fails++;
    set('j', fails ? 'FAIL' : 'PASS', `attach: "${msg}"; ${rows.join(' || ')}; after reload ${persisted} element(s) still moved; screenshots j-*.png`,
      fails ? rows.filter(r => !/moved 1[12]\d,[56]\d/.test(r)).concat(persisted ? [] : ['moves were not restored after reload']).join(' / ') : null,
      fails ? 'mover__100_adapter.js place_() (uses CSS translate) / applySaved()' : null);
  });

  // k. console + proxy errors
  await guard('k', async () => {
    const proxyLog = fs.readFileSync(path.join(E2E, 'logs', `proxy-${app.id}.log`), 'utf8').trim();
    const proxyMade = [...bad].filter(b => /^502/.test(b));
    const onlyProxy = errs.filter(e => !directErrors.some(d => d.slice(0, 60) === e.slice(0, 60)));
    fs.writeFileSync(path.join(dir, 'console.txt'), `DIRECT session console errors (${directErrors.length}):\n${directErrors.join('\n')}\n\nPROXY session console errors (${errs.length}):\n${errs.join('\n')}\n\nFailed requests:\n${failed.join('\n')}\n\n5xx / missing /_cp responses:\n${bad.join('\n')}\n\nProxy process log:\n${proxyLog}\n`);
    set('k', onlyProxy.length || proxyMade.length ? 'FAIL' : 'PASS',
      `console errors: direct ${directErrors.length}, proxy ${errs.length} (${onlyProxy.length} only seen through proxy); failed requests ${failed.length}; 5xx ${bad.length}; proxy log: "${proxyLog.replace(/\n/g, ' ')}"; full list console.txt`,
      onlyProxy.length || proxyMade.length ? [...onlyProxy.slice(0, 3), ...proxyMade.slice(0, 2)].join(' | ') : null);
  });

  await ctx.close();
  for (const sub of ['video-direct', 'video-proxy']) for (const f of fs.readdirSync(path.join(dir, sub))) fs.renameSync(path.join(dir, sub, f), path.join(dir, `${sub}.webm`));
  for (const sub of ['video-direct', 'video-proxy']) fs.rmSync(path.join(dir, sub), { recursive: true, force: true });
  R.directConsoleErrors = directErrors; R.proxyConsoleErrors = errs; R.failedRequests = failed; R.badResponses = bad; R.websockets = sockets;
  fs.writeFileSync(path.join(dir, 'result.json'), JSON.stringify(R, null, 2));
  return R;
}

const only = process.argv.slice(2);
const browser = await chromium.launch({ args: ['--host-resolver-rules=MAP *.e2e.localhost 127.0.0.1'] });
if (!only.length || only.includes('probe')) { console.log('neutral proxy probe…'); const p = await proxyProbe(); fs.mkdirSync(path.join(E2E, 'evidence'), { recursive: true });
  fs.writeFileSync(path.join(E2E, 'evidence', 'proxy-probe.json'), JSON.stringify(p, null, 2)); console.log(JSON.stringify(p, null, 1)); }
for (const app of APPS.filter(a => !only.length || only.includes(a.id))) { console.log(`== ${app.id}`); await runApp(browser, app); }
await browser.close();
process.exit(0);
