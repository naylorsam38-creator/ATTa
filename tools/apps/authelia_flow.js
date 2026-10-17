// Authelia: login negative/positive, 1FA (traefik), 2FA registration via TOTP (secure), reset password, settings.
const fs = require('fs'); const crypto = require('crypto');
const NOTIF = process.argv[2]; const OUT = process.argv[3];
const U = 'testeralpha', PW = 'Zx9!test-Passw0rd-2026';
const log = (...a) => console.log(...a);
function totp(secretB32) {
  const alph = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits = '';
  for (const ch of secretB32.replace(/=+$/, '').toUpperCase()) bits += alph.indexOf(ch).toString(2).padStart(5, '0');
  const key = Buffer.from(bits.match(/.{8}/g).map(b => parseInt(b, 2)));
  const ctr = Buffer.alloc(8); ctr.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const h = crypto.createHmac('sha1', key).update(ctr).digest(); const o = h[h.length - 1] & 15;
  return String(((h.readUInt32BE(o) & 0x7fffffff) % 1e6)).padStart(6, '0');
}
(async () => {
  const b = await require('../browser.js').launch(); const c = await b.newContext({ ignoreHTTPSErrors: true }); const p = await c.newPage();
  const errs = []; p.on('console', m => m.type() === 'error' && errs.push('CONSOLE ' + p.url() + ' | ' + m.text().slice(0, 250)));
  p.on('pageerror', e => errs.push('PAGEERROR ' + p.url() + ' | ' + e.message));
  p.on('response', r => r.status() >= 400 && errs.push('HTTP ' + r.status() + ' ' + r.request().method() + ' ' + r.url().slice(0, 150)));
  const text = async () => (await p.locator('body').innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 400);
  let n = 0; const shot = async (name) => p.screenshot({ path: `${OUT}/${String(++n).padStart(2, '0')}-${name}.png` }).catch(() => {});
  const login = async (user, pw) => { await p.fill('#username-textfield', user); await p.fill('#password-textfield', pw); await p.click('#sign-in-button'); await p.waitForTimeout(3000); };

  log('--- 1. bad password'); await p.goto('https://traefik.example.com/'); await p.waitForTimeout(2000);
  await login(U, 'wrong-password-123'); log('URL', p.url(), '|', await text()); await shot('bad-login');
  log('--- 2. good login -> traefik (1FA)'); await p.goto('https://traefik.example.com/'); await p.waitForTimeout(2000);
  await login(U, PW); await p.waitForTimeout(3000); log('URL', p.url(), '|', await text()); await shot('traefik');
  log('--- 3. secure (2FA)'); await p.goto('https://secure.example.com/'); await p.waitForTimeout(4000);
  log('URL', p.url(), '|', await text()); await shot('secure-2fa-prompt');
  log('BUTTONS', await p.$$eval('button, a', es => es.map(e => e.id + ':' + (e.innerText || '').trim().slice(0, 30)).join(' | ')));
  fs.writeFileSync(OUT + '/errors-part1.json', JSON.stringify(errs, null, 1));
  await c.storageState({ path: OUT + '/state.json' });
  await b.close();
})();
