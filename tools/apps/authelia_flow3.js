const fs = require('fs');
const NOTIF = process.argv[2]; const OUT = process.argv[3];
(async () => {
  const b = await require('../browser.js').launch(); const c = await b.newContext({ ignoreHTTPSErrors: true, storageState: OUT + '/state.json' }); const p = await c.newPage();
  const errs = []; p.on('console', m => m.type() === 'error' && errs.push('CONSOLE ' + p.url() + ' | ' + m.text().slice(0, 250)));
  p.on('pageerror', e => errs.push('PAGEERROR ' + p.url() + ' | ' + e.message));
  p.on('response', r => r.status() >= 400 && errs.push('HTTP ' + r.status() + ' ' + r.request().method() + ' ' + r.url().slice(0, 150)));
  const text = async () => (await p.locator('body').innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 700);
  await p.goto('https://authelia.example.com/settings/two-factor-authentication'); await p.waitForTimeout(3000);
  await p.click('#one-time-password-add'); await p.waitForTimeout(4000);
  console.log('AFTER ADD', p.url(), '|', await text());
  console.log('INPUTS', await p.$$eval('input, button', es => es.map(e => e.tagName + '#' + e.id + ' ' + (e.innerText || e.type || '').trim().slice(0, 30)).join(' | ')));
  await p.screenshot({ path: OUT + '/11-otp-add.png' });
  console.log('NOTIFICATION FILE:\n' + (fs.existsSync(NOTIF) ? fs.readFileSync(NOTIF, 'utf8').slice(-1500) : '<none>'));
  fs.writeFileSync(OUT + '/errors-part3.json', JSON.stringify(errs, null, 1));
  await b.close();
})();
