// Appsmith: log in, create a new application from the workspace page, wait for the editor.
const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); p.setDefaultTimeout(15000);
  p.on('console', m => m.type() === 'error' && !/cloud services|PKIX/.test(m.text()) && console.log('console: ' + m.text().split('\n')[0]));
  p.on('response', r => r.status() >= 400 && console.log(`${r.status()} ${r.request().method()} ${r.url()}`));
  await p.goto('http://localhost/user/login'); await p.fill('[name=username]', 'tester@example.com'); await p.fill('[name=password]', 'Test-Passw0rd!2026xyz');
  await p.getByRole('button', { name: /sign in/i }).click(); await p.waitForURL(/applications/); await p.waitForTimeout(3000);
  const skip = p.getByText(/skip this step/i); if (await skip.count()) { await skip.first().click(); await p.waitForTimeout(1500); }
  console.log('buttons: ' + (await p.$$eval('button', bs => bs.map(b => b.innerText.trim()).filter(Boolean))).join(' | '));
  await p.getByRole('button', { name: /create new/i }).first().click(); await p.waitForTimeout(1000);
  await p.getByText(/^application$/i).first().click(); await p.waitForURL(/\/app\/.*\/edit/, { timeout: 60000 }).catch(e => console.log('editor wait: ' + e.message.split('\n')[0]));
  await p.waitForTimeout(8000); console.log('editor url: ' + p.url() + ' title=' + await p.title());
  await p.screenshot({ path: '/home/user/ATTa/evidence/appsmith/editor.png' }); await b.close(); })();
