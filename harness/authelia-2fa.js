// Continue: register a TOTP device via the filesystem notifier, then access secure.example.com.
const launch = require('./launch'); const fs = require('fs'); const { execSync } = require('child_process');
const crypto = require('crypto');
function totp(secret) { // RFC 6238, base32 secret
  const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits = ''; for (const c of secret.replace(/=+$/, '')) bits += A.indexOf(c).toString(2).padStart(5, '0');
  const key = Buffer.from(bits.match(/.{8}/g).map(x => parseInt(x, 2)));
  const ctr = Buffer.alloc(8); ctr.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const h = crypto.createHmac('sha1', key).update(ctr).digest(); const o = h[19] & 15;
  return String(((h.readUInt32BE(o) & 0x7fffffff) % 1e6)).padStart(6, '0');
}
(async () => {
  const out = process.argv[2]; const notif = process.argv[3];
  const b = await launch(); const p = await (await b.newContext({ ignoreHTTPSErrors: true })).newPage();
  const log = []; p.on('console', m => m.type() === 'error' && log.push('console: ' + m.text()));
  p.on('response', r => r.status() >= 400 && log.push(`${r.status()} ${r.request().method()} ${r.url()}`));
  const step = async (name, fn) => { try { await fn(); log.push('OK ' + name + ' url=' + p.url()); } catch (e) { log.push('FAIL ' + name + ': ' + e.message.split('\n')[0]); await p.screenshot({ path: out + '/fail-' + name + '.png' }); } };
  await step('login', async () => { await p.goto('https://secure.example.com/'); await p.fill('#username-textfield', 'testuser'); await p.fill('#password-textfield', 'Test-Passw0rd!2026xyz'); await p.click('#sign-in-button'); await p.waitForURL(/2fa/, { timeout: 15000 }); });
  await step('click-register', async () => { await p.getByText('Register device', { exact: false }).click(); await p.waitForTimeout(2500); });
  await step('click-add-otp', async () => { await p.getByRole('button', { name: /^add$/i }).first().click(); await p.waitForTimeout(3500); log.push('dialog: ' + (await p.innerText('body')).replace(/\s+/g, ' ').slice(0, 300)); });
  await step('identity-code', async () => {
    const txt = fs.readFileSync(notif, 'utf8'); const codes = [...txt.matchAll(/^([A-Z0-9]{8})$/gm)]; const code = codes[codes.length - 1][1]; log.push('code=' + code);
    const inp = p.locator('input:visible').first(); await inp.click(); await p.keyboard.type(code);
    await p.getByRole('button', { name: /verify|confirm|next/i }).first().click(); await p.waitForTimeout(3500);
    log.push('after code: ' + (await p.innerText('body')).replace(/\s+/g, ' ').slice(0, 300));
  });
  await step('totp-setup', async () => {
    await p.getByRole('button', { name: /next/i }).first().click().catch(() => {}); await p.waitForTimeout(1500);
    const body = await p.content(); const m = body.match(/secret=([A-Z2-7]+)/); log.push('secret found=' + !!m); if (m) fs.writeFileSync(out + '/.totp', m[1]);
    await p.getByRole('button', { name: /next/i }).first().click().catch(() => {}); await p.waitForTimeout(1500);
    const inp = p.locator('input:visible').first(); await inp.click(); await p.keyboard.type(totp(m[1]));
    await p.waitForTimeout(3000); const fin = p.getByRole('button', { name: /finish|next|confirm/i }); if (await fin.count()) await fin.first().click(); await p.waitForTimeout(2500);
  });
  await step('access-secure', async () => { await p.goto('https://secure.example.com/'); await p.waitForTimeout(3000); log.push('secure body: ' + (await p.innerText('body')).replace(/\s+/g, ' ').slice(0, 200)); });
  await step('enter-otp', async () => { const sec = process.env.SECRET || (fs.existsSync(out + '/.totp') && fs.readFileSync(out + '/.totp', 'utf8')); await p.locator('input:visible').first().click(); await p.keyboard.type(totp(sec)); await p.waitForURL(/secure\.example\.com/, { timeout: 15000 }); log.push('protected body: ' + (await p.innerText('body')).slice(0, 120).replace(/\s+/g, ' ')); });
  log.push('page: ' + (await p.innerText('body')).replace(/\s+/g, ' ').slice(0, 400));
  await p.screenshot({ path: out + '/2fa-after-identity.png' });
  fs.writeFileSync(out + '/flow-2fa.txt', log.join('\n')); console.log(log.join('\n')); await b.close();
})();
