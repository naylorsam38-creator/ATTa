const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); p.on('console', m => m.type() === 'error' && console.log('console: ' + m.text()));
  p.on('response', r => r.status() >= 400 && console.log(`${r.status()} ${r.url()}`));
  await p.goto('http://localhost:7700/'); await p.waitForTimeout(3000); const s = p.locator('input[type=search], input').first(); await s.fill('imaginary'); await p.waitForTimeout(2500);
  console.log('result shows doc: ' + /Another Imaginary Volume/.test(await p.innerText('body'))); await p.screenshot({ path: '/home/user/ATTa/evidence/meilisearch/dashboard-search.png' }); await b.close(); })();
