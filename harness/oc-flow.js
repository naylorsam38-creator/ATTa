// OpenCloud: clean visits with locale en-US, then create a folder in Personal.
const launch = require('./launch');
(async () => { const b = await launch(); const ctx = await b.newContext({ ignoreHTTPSErrors: true, locale: process.env.LOC || 'en-US' }); const p = await ctx.newPage(); p.setDefaultTimeout(15000);
  p.on('console', m => m.type() === 'error' && console.log('console: ' + m.text().split('\n')[0] + ' [' + p.url().split('?')[0] + ']'));
  p.on('pageerror', e => console.log('PAGEERROR: ' + String(e).slice(0, 150)));
  p.on('response', r => r.status() >= 400 && console.log(`${r.status()} ${r.request().method()} ${r.url().split('?')[0]}`));
  await p.goto('https://localhost:9200/'); await p.waitForURL(/signin/); await p.fill('input[name=username], #oc-login-username', 'admin'); await p.fill('input[type=password]', 'admin');
  await p.locator('button[type=submit]').click(); await p.waitForURL(/files/, { timeout: 30000 }); await p.waitForTimeout(4000); console.log('logged in: ' + p.url().split('?')[0]);
  for (const path of ['files/spaces/projects', 'files/favorites', 'files/shares/with-me', 'files/trash/overview', 'admin-settings', 'account']) { await p.goto('https://localhost:9200/' + path); await p.waitForTimeout(5000); console.log('visited ' + path + ' -> ' + p.url().split('?')[0]); }
  if (process.env.MKDIR) { await p.goto('https://localhost:9200/files/spaces/personal/admin'); await p.waitForTimeout(4000);
    await p.getByRole('button', { name: /^new$/i }).first().click(); await p.waitForTimeout(800); await p.getByText(/^folder$/i).first().click(); await p.waitForTimeout(800);
    const inp = p.locator('[role=dialog] input, .oc-modal input').first(); await inp.fill('Fictional Test Folder'); await p.keyboard.press('Enter'); await p.waitForTimeout(3000);
    console.log('folder visible: ' + await p.getByText('Fictional Test Folder').count()); await p.screenshot({ path: '/home/user/ATTa/evidence/opencloud/folder.png' }); }
  await b.close(); })();
