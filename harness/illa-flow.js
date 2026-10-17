// ILLA: login, clean-visit the guide/builder page, then create a new app from the apps page.
const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); p.setDefaultTimeout(15000);
  p.on('console', m => m.type() === 'error' && console.log('console: ' + m.text().split('\n')[0]));
  p.on('pageerror', e => console.log('PAGEERROR: ' + String(e).slice(0, 200) + ' ' + JSON.stringify(e).slice(0, 300)));
  p.on('response', async r => { if (r.status() >= 400) console.log(`${r.status()} ${r.request().method()} ${r.url()} body=${(await r.text().catch(() => '')).slice(0, 200)}`); });
  await p.goto('http://localhost:8000/cloud/login'); await p.locator('input').nth(0).fill('root'); await p.locator('input[type=password]').fill('password');
  await p.getByRole('button', { name: /sign in/i }).click(); await p.waitForURL(/workspace/); await p.waitForTimeout(3000); console.log('logged in: ' + p.url());
  
  console.log('--- create app'); await p.goto('http://localhost:8000/cloud/workspace/0/apps'); await p.waitForTimeout(3000);
  console.log('buttons: ' + (await p.$$eval('button', bs => bs.map(b => b.innerText.trim()).filter(Boolean))).join(' | '));
  await p.getByRole('button', { name: /create new/i }).first().click(); await p.waitForTimeout(1500);
  console.log('menu: ' + (await p.$$eval('[role=menuitem],[role=option],li', es => es.map(e => e.innerText.trim()).filter(t => t && t.length < 40))).slice(0, 15).join(' | '));
  const item = p.getByText(/^(create )?(app|blank app|new app)$/i); console.log('items: ' + await item.count()); if (await item.count()) { await item.first().click(); await p.waitForTimeout(8000); }
  console.log('after create: ' + p.url() + ' title=' + await p.title());
  await p.screenshot({ path: '/home/user/ATTa/evidence/illa-builder/after-create.png' }); await b.close(); })();
