// Gitea: create a repo with valid fictional data, then open an issue in it.
const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); const log = [];
  p.on('console', m => m.type() === 'error' && log.push('console: ' + m.text())); p.on('response', r => r.status() >= 400 && log.push(`${r.status()} ${r.request().method()} ${r.url()}`));
  await p.goto('http://localhost:3000/user/login'); await p.fill('[name=user_name]', 'testuser'); await p.fill('[name=password]', 'Test-Passw0rd!2026xyz'); await p.click('form button.primary'); await p.waitForURL('http://localhost:3000/');
  await p.goto('http://localhost:3000/repo/create'); await p.fill('[name=repo_name]', 'fictional-test-repo'); await p.fill('textarea[name=description]', 'Automated test repo');
  const ar = p.locator('[name=auto_init]'); if (await ar.count()) await ar.check({ force: true }).catch(() => {});
  await p.locator('form.ui.form button.primary').last().click(); await p.waitForTimeout(4000); log.push('repo url: ' + p.url() + ' | ' + await p.title());
  await p.goto('http://localhost:3000/testuser/fictional-test-repo/issues/new'); await p.fill('[name=title]', 'Fictional test issue');
  await p.locator('textarea').first().fill('Created by automated test.'); await p.getByRole('button', { name: /create issue/i }).click(); await p.waitForTimeout(3000);
  log.push('issue url: ' + p.url() + ' | ' + await p.title()); await p.screenshot({ path: '/home/user/ATTa/evidence/gitea/issue.png' });
  console.log(log.join('\n')); await b.close(); })();
