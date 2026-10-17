const { chromium } = require('/opt/node-tools/node_modules/playwright');
(async () => {
  const b = await require('../browser.js').launch(); const c = await b.newContext({ ignoreHTTPSErrors: true }); const p = await c.newPage();
  p.on('console', m => m.type() === 'error' && console.log('CONSOLE', p.url(), m.text().slice(0, 200)));
  p.on('pageerror', e => console.log('PAGEERROR', e.message));
  await p.goto('https://traefik.example.com/'); await p.waitForTimeout(3000);
  console.log('URL', p.url());
  console.log(await p.$$eval('input, button', es => es.map(e => e.tagName + '#' + e.id + '[' + (e.name || '') + '] ' + (e.innerText || e.type)).join('\n')));
  console.log((await p.locator('body').innerText()).slice(0, 500));
  await b.close();
})();
