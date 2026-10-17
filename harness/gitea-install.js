// Gitea: complete the install wizard with defaults + a fictional admin account.
const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); const log = [];
  p.on('console', m => m.type() === 'error' && log.push('console: ' + m.text())); p.on('response', r => r.status() >= 400 && log.push(`${r.status()} ${r.request().method()} ${r.url()}`));
  await p.goto('http://localhost:3000/'); 
  await p.evaluate(() => document.querySelectorAll('details').forEach(d => d.open = true));
  await p.fill('[name=admin_name]', 'testuser'); await p.fill('[name=admin_email]', 'tester@example.test');
  await p.fill('[name=admin_passwd]', 'Test-Passw0rd!2026xyz'); await p.fill('[name=admin_confirm_passwd]', 'Test-Passw0rd!2026xyz');
  await p.locator('form button.primary, form button[type=submit]').last().click();
  await p.waitForTimeout(15000); log.push('after install: ' + p.url() + ' title=' + await p.title());
  log.push('body: ' + (await p.innerText('body')).replace(/\s+/g, ' ').slice(0, 250));
  console.log(log.join('\n')); await b.close(); })();
