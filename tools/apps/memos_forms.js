(async () => {
  const b = await require('../browser.js').launch(); const c = await b.newContext({ locale: 'en-US' }); const p = await c.newPage();
  const errs = []; p.on('console', m => m.type() === 'error' && errs.push('CONSOLE ' + p.url() + ' | ' + m.text().slice(0, 250)));
  p.on('pageerror', e => errs.push('PAGEERROR ' + p.url() + ' | ' + e.message));
  p.on('response', async r => { const m = r.request().method(); if (r.url().includes('/api/') && m !== 'GET') console.log('API', r.status(), m, r.url().slice(21, 110), (await r.text().catch(() => '')).slice(0, 160).replace(/\s+/g, ' ')); if (r.status() >= 400) errs.push('HTTP ' + r.status() + ' ' + m + ' ' + r.url().slice(0, 150)); });
  await require('./memos_pre.js')(p, c, { auth: [] });
  await p.goto('http://localhost:5230/'); await p.waitForTimeout(3000);
  const ed = p.locator('textarea, [contenteditable=true]').first(); await ed.click(); await p.keyboard.type('Test memo alpha #testtag\n\n- [ ] first task\n- **bold** item');
  const fi = p.locator('input[type=file]'); console.log('file inputs', await fi.count()); if (await fi.count()) { await fi.first().setInputFiles('tools/test-image.png'); await p.waitForTimeout(3000); }
  const save = p.locator('button:has-text("Save")').first(); console.log('save btn', await save.count()); await save.click().catch(e => console.log('save click', e.message.split('\n')[0])); await p.waitForTimeout(4000);
  console.log('HOME AFTER', (await p.locator('main, body').first().innerText()).replace(/\s+/g, ' ').slice(0, 400));
  console.log('IMGS', (await p.$$eval('img', is => is.map(i => i.src.slice(0, 80) + ' w=' + i.naturalWidth))).join(' | '));
  await p.screenshot({ path: 'logs/05-memos/memo-created.png', fullPage: true });
  // second user signup in fresh context
  const c2 = await b.newContext({ locale: 'en-US' }); const q = await c2.newPage();
  q.on('response', async r => { if (r.url().includes('/api/') && r.request().method() !== 'GET') console.log('API2', r.status(), r.request().method(), r.url().slice(21, 110), (await r.text().catch(() => '')).slice(0, 160)); });
  await q.goto('http://localhost:5230/auth/signup'); await q.waitForTimeout(3000); console.log('SIGNUP2 PAGE', q.url(), (await q.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 200));
  if (await q.locator('input[type=password]').count()) { await q.locator('input[type=text]').first().fill('testerbeta'); await q.locator('input[type=password]').first().fill('Zx9!test-Passw0rd-2026'); await q.locator('button[type=submit]').first().click(); await q.waitForTimeout(4000); console.log('SIGNUP2 AFTER', q.url(), (await q.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 200)); }
  console.log('ERRORS', JSON.stringify(errs, null, 1)); await b.close();
})();
