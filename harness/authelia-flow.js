// Targeted Authelia flow: 1FA to traefik dashboard, then try 2FA (secure.example.com).
const launch = require('./launch');
const fs = require('fs');
(async () => {
  const b = await launch();
  const p = await (await b.newContext({ ignoreHTTPSErrors: true })).newPage();
  const log = []; p.on('console', m => m.type() === 'error' && log.push('console: ' + m.text()));
  p.on('response', r => r.status() >= 400 && log.push(`${r.status()} ${r.url()}`));
  await p.goto('https://traefik.example.com/');
  log.push('landed: ' + p.url());
  await p.fill('#username-textfield', 'testuser'); await p.fill('#password-textfield', 'Test-Passw0rd!2026xyz');
  await p.click('#sign-in-button'); await p.waitForURL(/traefik\.example\.com\/dashboard/, { timeout: 15000 }).catch(e => log.push('1FA redirect wait: ' + e.message.split('\n')[0]));
  log.push('after 1FA: ' + p.url() + ' title=' + await p.title());
  await p.goto('https://secure.example.com/'); await p.waitForTimeout(2500);
  log.push('secure ->: ' + p.url());
  await p.screenshot({ path: process.argv[2] + '/2fa-page.png' });
  log.push('2fa page text: ' + (await p.innerText('body')).replace(/\s+/g, ' ').slice(0, 300));
  fs.writeFileSync(process.argv[2] + '/flow.txt', log.join('\n')); console.log(log.join('\n'));
  await b.close();
})();
