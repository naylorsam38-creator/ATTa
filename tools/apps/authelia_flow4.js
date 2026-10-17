// Authelia: OTP registration w/ identity verification code from notification.txt, then 2FA access to secure.example.com, reset password.
const fs = require('fs'); const crypto = require('crypto');
const NOTIF = process.argv[2]; const OUT = process.argv[3];
function totp(secretB32, offset = 0) {
  const alph = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits = '';
  for (const ch of secretB32.replace(/=+$/, '').toUpperCase()) bits += alph.indexOf(ch).toString(2).padStart(5, '0');
  const key = Buffer.from(bits.match(/.{8}/g).map(b => parseInt(b, 2)));
  const ctr = Buffer.alloc(8); ctr.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000) + offset));
  const h = crypto.createHmac('sha1', key).update(ctr).digest(); const o = h[h.length - 1] & 15;
  return String(((h.readUInt32BE(o) & 0x7fffffff) % 1e6)).padStart(6, '0');
}
const lastCode = () => { const t = fs.readFileSync(NOTIF, 'utf8'); const m = [...t.matchAll(/\b([A-Z0-9]{8})\b/g)].map(x => x[1]); return { m, tail: t.slice(-700) }; };
(async () => {
  const b = await require('../browser.js').launch(); const c = await b.newContext({ locale: 'en-US', ignoreHTTPSErrors: true, storageState: OUT + '/state.json' }); const p = await c.newPage();
  const errs = []; p.on('console', m => m.type() === 'error' && errs.push('CONSOLE ' + p.url() + ' | ' + m.text().slice(0, 250)));
  p.on('pageerror', e => errs.push('PAGEERROR ' + p.url() + ' | ' + e.message));
  p.on('response', r => r.status() >= 400 && errs.push('HTTP ' + r.status() + ' ' + r.request().method() + ' ' + r.url().slice(0, 150)));
  const text = async () => (await p.locator('body').innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 700);
  await p.goto('https://authelia.example.com/settings/two-factor-authentication'); await p.waitForTimeout(3000);
  if (await p.locator('#password-textfield').count()) { await p.fill('#username-textfield', 'testeralpha'); await p.fill('#password-textfield', 'Zx9!test-Passw0rd-2026'); await p.click('#sign-in-button'); await p.waitForTimeout(3000);
    if (await p.locator('#password-textfield').count()) { await p.fill('#password-textfield', 'Yt8!new-Passw0rd-2026'); await p.click('#sign-in-button'); await p.waitForTimeout(3000); } await p.goto('https://authelia.example.com/settings/two-factor-authentication'); await p.waitForTimeout(3000); }
  console.log('SETTINGS URL', p.url());
  await p.click('#one-time-password-add'); await p.waitForTimeout(4000);
  const nc = lastCode(); console.log('NOTIF TAIL', nc.tail.replace(/\s+/g, ' '));
  const code = nc.m[nc.m.length - 1]; console.log('one-time code', code);
  await p.fill('#one-time-code', code); await p.click('#dialog-verify'); await p.waitForTimeout(4000);
  console.log('AFTER VERIFY', await text());
  console.log('INPUTS', await p.$$eval('input, button', es => es.map(e => e.tagName + '#' + e.id + ' ' + (e.innerText || e.type || '').trim().slice(0, 30)).join(' | ')));
  await p.screenshot({ path: OUT + '/12-otp-dialog.png' });
  // Walk the OTP wizard: Next until a secret is visible
  let secret = null;
  for (let i = 0; i < 6 && !secret; i++) {
    const html = await p.content();
    const m = html.match(/secret=([A-Z2-7]{16,})/) || html.match(/\b([A-Z2-7]{32,})\b/);
    if (m) { secret = m[1]; break; }
    const nxt = p.locator('button:has-text("Next")').first();
    if (await nxt.count()) { await nxt.click(); await p.waitForTimeout(2000); } else break;
  }
  if (!secret) { const btn = p.locator('#secret-url, button:has-text("secret"), [aria-label*=ecret]').first(); if (await btn.count()) { await btn.click().catch(() => {}); await p.waitForTimeout(1000); const html = await p.content(); const m = html.match(/secret=([A-Z2-7]{16,})/); if (m) secret = m[1]; } }
  console.log('SECRET found:', !!secret);
  await p.screenshot({ path: OUT + '/13-otp-secret.png' });
  if (secret) {
    for (let i = 0; i < 4; i++) { const nxt = p.locator('button:has-text("Next")').first(); if (await nxt.count() && await nxt.isEnabled()) { await nxt.click(); await p.waitForTimeout(1500); } }
    const code6 = totp(secret); console.log('TOTP', code6);
    const otpInputs = p.locator('input[type=text], input[type=tel], input[inputmode=numeric]');
    const k = await otpInputs.count(); console.log('otp inputs', k);
    if (k >= 6) { for (let i = 0; i < 6; i++) await otpInputs.nth(i).fill(code6[i]); } else if (k) { await otpInputs.first().fill(code6); }
    await p.waitForTimeout(3000);
    for (const label of ['Finish', 'Next', 'Submit', 'Close']) { const bt = p.locator(`button:has-text("${label}")`).first(); if (await bt.count() && await bt.isEnabled().catch(() => false)) { await bt.click().catch(() => {}); await p.waitForTimeout(1500); } }
    console.log('AFTER OTP REGISTER', await text()); await p.screenshot({ path: OUT + '/14-otp-registered.png' });
    // 2FA access
    require('fs').writeFileSync(OUT + '/totp-secret.txt', secret);
    const waitMs = 30000 - (Date.now() % 30000) + 1500; console.log('waiting for next TOTP window', waitMs); await p.waitForTimeout(waitMs);
    await p.goto('https://secure.example.com/'); await p.waitForTimeout(4000); console.log('SECURE URL', p.url(), '|', (await text()).slice(0, 300));
    if (p.url().includes('authelia.example.com')) {
      const ins = p.locator('input'); const cnt = await ins.count(); const code2 = totp(secret); console.log('2fa inputs', cnt);
      if (cnt >= 6) { for (let i = 0; i < 6; i++) await ins.nth(i).fill(code2[i]); } else await ins.first().fill(code2);
      await p.waitForTimeout(5000); console.log('AFTER 2FA', p.url(), '|', (await text()).slice(0, 300));
    }
    await p.screenshot({ path: OUT + '/15-secure.png' });
  }
  // Reset password flow (new context)
  const c2 = await b.newContext({ locale: 'en-US', ignoreHTTPSErrors: true }); const q = await c2.newPage();
  q.on('response', r => r.status() >= 400 && errs.push('HTTP ' + r.status() + ' ' + r.request().method() + ' ' + r.url().slice(0, 150)));
  q.on('pageerror', e => errs.push('PAGEERROR ' + q.url() + ' | ' + e.message));
  await q.goto('https://authelia.example.com/'); await q.waitForTimeout(2000); await q.click('#reset-password-button'); await q.waitForTimeout(2000);
  console.log('RESET PAGE', q.url(), (await q.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 300));
  await q.fill('#username-textfield', 'testeralpha').catch(async () => { await q.locator('input').first().fill('testeralpha'); });
  await q.locator('button:has-text("Reset")').first().click().catch(e => console.log('reset click', e.message)); await q.waitForTimeout(3000);
  console.log('AFTER RESET REQ', (await q.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 300));
  const t = fs.readFileSync(NOTIF, 'utf8'); const link = [...t.matchAll(/https:\/\/authelia\.example\.com\/reset-password\/step2\?token=[^\s"<]+/g)].pop();
  console.log('reset link found:', !!link);
  if (link) {
    await q.goto(link[0]); await q.waitForTimeout(3000); console.log('STEP2', (await q.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 300));
    const pw = q.locator('input[type=password]'); const np = 'Zx9!test-Passw0rd-2026';
    if (await pw.count() >= 2) { await pw.nth(0).fill(np); await pw.nth(1).fill(np); await q.locator('button:has-text("Reset")').first().click(); await q.waitForTimeout(3000); }
    console.log('AFTER RESET', q.url(), (await q.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 300));
  }
  fs.writeFileSync(OUT + '/errors-part4.json', JSON.stringify(errs, null, 1));
  await b.close();
})();
