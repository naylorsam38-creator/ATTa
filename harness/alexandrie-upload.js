// Alexandrie: login with the test account, upload a generated 8x8 PNG on /dashboard/cdn, record media URLs.
const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); const log = [];
  p.on('console', m => m.type() === 'error' && log.push('console: ' + m.text())); p.on('requestfailed', r => log.push('FAILED ' + r.url() + ' ' + r.failure().errorText));
  p.on('response', r => { if (r.status() >= 400) log.push(`${r.status()} ${r.request().method()} ${r.url()}`); if (/upload|ressources|resources|cdn/i.test(r.url()) && r.request().method() !== 'GET') log.push(`UPLOAD-RESP ${r.status()} ${r.url()}`); });
  await p.goto('http://localhost:8200/login'); await p.fill('input[type=password]', 'Test-Passw0rd!2026xyz');
  const user = p.locator('input:not([type=password]):visible').first(); await user.fill('testuser');
  await p.getByRole('button', { name: /^login$/i }).click(); await p.waitForURL(/dashboard/, { timeout: 15000 });
  await p.goto('http://localhost:8200/dashboard/cdn'); await p.waitForTimeout(2000);
  const fi = p.locator('input[type=file]'); log.push('file inputs: ' + await fi.count());
  await fi.first().setInputFiles('/home/user/ATTa/harness/test.png'); await p.waitForTimeout(1500);
  await p.getByRole('button', { name: /upload 1 file/i }).click(); await p.waitForTimeout(4000);
  await p.goto('http://localhost:8200/dashboard/cdn'); await p.waitForTimeout(3000);
  const links = await p.$$eval('a[href]', as => as.map(a => a.href).filter(h => /9000|9005|alexandrie\//.test(h))); log.push('media links: ' + links.join(' '));
  const imgs = await p.$$eval('img', is => is.map(i => i.src + ' loaded=' + (i.complete && i.naturalWidth > 0)));
  log.push('imgs: ' + imgs.filter(s => !s.startsWith('data:')).join('\n  '));
  log.push('body: ' + (await p.innerText('body')).replace(/\s+/g, ' ').slice(0, 300));
  await p.screenshot({ path: '/home/user/ATTa/evidence/alexandrie/cdn-after-upload.png' }); console.log(log.join('\n')); await b.close(); })();
