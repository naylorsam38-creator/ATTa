// Alexandrie step H: create document (Ctrl+S), upload file to CDN, open uploaded file URL.
(async () => {
  const b = await require('../browser.js').launch(); const c = await b.newContext({ locale: 'en-US' }); const p = await c.newPage();
  const errs = []; p.on('console', m => m.type() === 'error' && errs.push('CONSOLE ' + p.url() + ' | ' + m.text().slice(0, 250)));
  p.on('pageerror', e => errs.push('PAGEERROR ' + p.url() + ' | ' + e.message));
  p.on('requestfailed', r => errs.push('FAILED ' + r.method() + ' ' + r.url().slice(0, 150) + ' ' + r.failure().errorText));
  p.on('response', async r => { if (r.url().includes(':8201') && r.request().method() !== 'GET' && r.request().method() !== 'OPTIONS') console.log('API', r.status(), r.request().method(), r.url().slice(0, 90), (await r.text().catch(() => '')).slice(0, 250)); if (r.status() >= 400) errs.push('HTTP ' + r.status() + ' ' + r.request().method() + ' ' + r.url().slice(0, 150)); });
  await require('./alexandrie_pre.js')(p, c, { auth: [] });
  // Document
  await p.goto('http://localhost:8200/dashboard/docs/new'); await p.waitForTimeout(3000);
  await p.fill('input.meta-title', 'Test Doc Alpha'); await p.fill('input.meta-description', 'Made-up test document');
  await p.click('.cm-content'); await p.keyboard.type('# Heading\n\nSome **bold** text and $x^2$ math.\n\n- [ ] task one\n');
  await p.keyboard.press('Control+s'); await p.waitForTimeout(4000);
  console.log('DOC AFTER SAVE', p.url(), '|', (await p.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 200));
  await p.goto('http://localhost:8200/dashboard/docs'); await p.waitForTimeout(3000);
  console.log('DOCS LIST', (await p.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 300));
  // CDN upload
  await p.goto('http://localhost:8200/dashboard/cdn'); await p.waitForTimeout(3000);
  const fi = p.locator('input[type=file]'); console.log('file inputs', await fi.count());
  if (await fi.count()) { await fi.first().setInputFiles('tools/test-image.png'); await p.waitForTimeout(2000);
    await p.locator('button:has-text("Upload 1 file")').first().click().catch(e => console.log('upload click', e.message.split('\n')[0])); await p.waitForTimeout(6000);
    await p.goto('http://localhost:8200/dashboard/cdn'); await p.waitForTimeout(4000);
    const links = await p.$$eval('a[href], img[src]', es => es.map(e => e.href || e.src).filter(u => /9005|9000|alexandrie\//.test(u)));
    console.log('CDN LINKS', links.join(' '));
    for (const u of links.slice(0, 2)) { const q = await c.newPage(); try { const r = await q.goto(u, { timeout: 10000 }); console.log('OPEN', u, r && r.status()); } catch (e) { console.log('OPEN FAIL', u, e.message.split('\n')[0]); } await q.close(); }
  }
  console.log('CDN AFTER', (await p.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 300));
  const imgs = await p.$$eval('img', is => is.map(i => i.src + ' natural=' + i.naturalWidth)); console.log('IMGS', imgs.join('\n'));
  await p.screenshot({ path: 'logs/04-alexandrie/cdn-after-upload.png', fullPage: true });
  console.log('ERRORS', JSON.stringify(errs, null, 1)); await b.close();
})();
