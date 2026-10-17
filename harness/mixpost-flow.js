// Mixpost: login, write a draft post, save settings; record outcome.
const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); const log = [];
  p.on('console', m => m.type() === 'error' && log.push('console: ' + m.text())); p.on('response', r => r.status() >= 400 && log.push(`${r.status()} ${r.request().method()} ${r.url()}`));
  await p.goto('http://localhost:9000/mixpost/login'); await p.fill('input[type=email]', 'admin@example.com'); await p.fill('input[type=password]', 'changeme'); await p.click('button[type=submit]'); await p.waitForURL(/mixpost$/);
  await p.goto('http://localhost:9000/mixpost/posts/create'); await p.waitForTimeout(2000);
  const btns = await p.$$eval('button', bs => bs.map(b => b.innerText.trim()).filter(Boolean)); log.push('buttons: ' + btns.join(' | '));
  const ed = p.locator('[contenteditable=true]').first(); log.push('editors: ' + await p.locator('[contenteditable=true]').count());
  await ed.click(); await p.keyboard.type('Fictional draft post for testing.'); await p.waitForTimeout(2500);
  log.push('after typing url=' + p.url());
  await p.goto('http://localhost:9000/mixpost/posts'); await p.waitForTimeout(1500); log.push('posts page: ' + (await p.innerText('main').catch(() => p.innerText('body'))).replace(/\s+/g, ' ').slice(0, 300));
  await p.screenshot({ path: process.argv[2] + '/posts-after-draft.png' }); console.log(log.join('\n')); await b.close(); })();
