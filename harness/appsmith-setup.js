// Appsmith: complete admin setup with fictional data, step through onboarding, report where we land.
const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); const log = { push: x => console.log(x) }; p.setDefaultTimeout(5000);
  p.on('console', m => m.type() === 'error' && log.push('console: ' + m.text().split('\n')[0])); p.on('response', r => r.status() >= 400 && log.push(`${r.status()} ${r.request().method()} ${r.url()}`));
  await p.goto('http://localhost/setup/welcome'); await p.waitForTimeout(3000);
  await p.fill('[name=firstName]', 'Testy'); await p.fill('[name=lastName]', 'McTestface'); await p.fill('[name=email]', 'tester@example.com');
  await p.fill('[name=password]', 'Test-Passw0rd!2026xyz'); await p.fill('[name=verifyPassword]', 'Test-Passw0rd!2026xyz');
  await p.getByRole('button', { name: /continue/i }).click(); await p.waitForTimeout(3000);
  await p.getByText('Brand New', { exact: true }).click(); await p.getByText('Personal Project', { exact: true }).click(); await p.waitForTimeout(500);
  const gs = p.getByRole('button', { name: /get started/i }); log.push('get started enabled: ' + await gs.isEnabled());
  await gs.click({ timeout: 10000 }); await p.waitForURL(u => !/setup/.test(u.pathname), { timeout: 60000 }).catch(e => log.push('wait: ' + e.message.split('\n')[0]));
  await p.waitForTimeout(5000); log.push('after setup: ' + p.url() + ' :: ' + (await p.innerText('body')).replace(/\s+/g, ' ').slice(0, 300));
  log.push('final: ' + p.url() + ' title=' + await p.title()); await p.screenshot({ path: '/home/user/ATTa/evidence/appsmith/after-setup.png' });
  await b.close(); })();
