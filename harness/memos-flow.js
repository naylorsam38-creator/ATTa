// memos: sign in as the admin created by the crawl, clean-visit pages, create a memo.
const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); const log = [];
  p.on('console', m => m.type() === 'error' && log.push('console[' + p.url() + ']: ' + m.text().split('\n')[0]));
  p.on('response', r => r.status() >= 400 && log.push(`${r.status()} ${r.request().method()} ${r.url()}`));
  p.on('requestfailed', r => log.push('FAILED ' + r.url() + ' ' + r.failure().errorText));
  await p.goto('http://localhost:5230/auth', { waitUntil: 'load' }); log.push('auth page: ' + p.url());
  await p.locator('input:visible').nth(0).fill('testuser'); await p.locator('input[type=password]:visible').fill('Test-Passw0rd!2026xyz');
  await p.getByRole('button', { name: /sign in/i }).click(); await p.waitForURL(u => !/auth/.test(u.pathname), { timeout: 15000 }); log.push('after sign in: ' + p.url());
  for (const path of ['/', '/explore', '/views', '/calendar', '/map', '/attachments', '/inbox', '/archived', '/setting', '/u/testuser']) {
    await p.goto('http://localhost:5230' + path, { waitUntil: 'load' }).catch(e => log.push('goto ' + path + ' ' + e.message)); await p.waitForTimeout(2500); log.push('visited ' + path + ' -> ' + p.url());
  }
  await p.goto('http://localhost:5230/', { waitUntil: 'load' });
  const ed = p.locator('textarea:visible, [contenteditable=true]:visible').first(); await ed.click(); await p.keyboard.type('Fictional test memo #testing');
  await p.getByRole('button', { name: /^save$/i }).click(); await p.waitForTimeout(2000);
  log.push('memo visible: ' + await p.getByText('Fictional test memo').count());
  await p.screenshot({ path: '/home/user/ATTa/evidence/memos/after-memo.png' }); console.log(log.join('\n')); await b.close(); })();
