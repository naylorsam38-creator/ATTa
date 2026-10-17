const launch = require('./launch'); const fs = require('fs'); const crypto = require('crypto');
function totp(secret, t) { const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits = ''; for (const c of secret) bits += A.indexOf(c).toString(2).padStart(5, '0');
  const key = Buffer.from(bits.match(/.{8}/g).map(x => parseInt(x, 2))); const ctr = Buffer.alloc(8); ctr.writeBigUInt64BE(BigInt(Math.floor(t / 30000)));
  const h = crypto.createHmac('sha1', key).update(ctr).digest(); const o = h[19] & 15; return String(((h.readUInt32BE(o) & 0x7fffffff) % 1e6)).padStart(6, '0'); }
(async () => {
  const out = process.argv[2]; const sec = fs.readFileSync(out + '/.totp', 'utf8').trim();
  const b = await launch(); const p = await (await b.newContext({ ignoreHTTPSErrors: true })).newPage(); const log = [];
  p.on('response', r => r.status() >= 400 && log.push(`${r.status()} ${r.request().method()} ${r.url()}`));
  await p.goto('https://secure.example.com/'); await p.fill('#username-textfield', 'testuser'); await p.fill('#password-textfield', 'Test-Passw0rd!2026xyz'); await p.click('#sign-in-button'); await p.waitForURL(/2fa/);
  await p.waitForTimeout(1500); await p.locator('input:visible').first().click(); await p.keyboard.type(totp(sec, Date.now()));
  try { await p.waitForURL(u => u.hostname === 'secure.example.com', { timeout: 15000 }); log.push('REACHED protected: ' + p.url() + ' :: ' + (await p.innerText('body')).slice(0, 150).replace(/\s+/g, ' ')); } catch (e) { log.push('NOT reached: ' + p.url()); }
  await p.screenshot({ path: out + '/secure-reached.png' }); fs.writeFileSync(out + '/flow-otp.txt', log.join('\n')); console.log(log.join('\n')); await b.close();
})();
