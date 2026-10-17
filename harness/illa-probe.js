const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); p.setDefaultTimeout(15000);
  await p.goto('http://localhost:8000/cloud/login'); await p.locator('input').nth(0).fill('root'); await p.locator('input[type=password]').fill('password');
  await p.getByRole('button', { name: /sign in/i }).click(); await p.waitForURL(/workspace/); await p.waitForTimeout(3000);
  const before = await p.innerText('body'); await p.getByRole('button', { name: /create new/i }).first().click(); await p.waitForTimeout(2500);
  const after = await p.innerText('body'); console.log('NEW TEXT:', after.replace(before, '').replace(/\s+/g, ' ').slice(0, 600)); console.log('url', p.url());
  await p.screenshot({ path: '/home/user/ATTa/evidence/illa-builder/create-menu.png' }); await b.close(); })();
