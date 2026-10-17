const { chromium } = require('/opt/node-tools/node_modules/playwright');
(async () => {
  const b = await chromium.launch({ args: ['--no-proxy-server'] }); const c = await b.newContext(); const p = await c.newPage();
  p.on('requestfailed', r => console.log('FAILED', r.url(), r.failure().errorText, r.resourceType()));
  p.on('requestfinished', async r => { if (r.url().includes('probe')) { const s = await r.response(); console.log('OK', r.url(), s && s.status(), r.resourceType()); } });
  p.on('console', m => console.log('CONSOLE', m.type(), m.text().slice(0, 300)));
  p.on('pageerror', e => console.log('PAGEERROR', e.message));
  await p.goto('http://admin.archivebox.localhost:5797/admin/login/');
  await p.fill('input[name=username]', 'testeralpha'); await p.fill('input[name=password]', 'Zx9!test-Passw0rd-2026');
  await p.click('input[type=submit]'); await p.waitForTimeout(12000);
  console.log('URL', p.url());
  const t = await p.locator('body').innerText(); console.log(t.slice(0, 3000));
  await p.screenshot({ path: process.argv[2], fullPage: true });
  await b.close();
})();
