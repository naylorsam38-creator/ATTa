const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); await p.goto('http://localhost/setup/welcome?error=Please%20enter%20a%20valid%20parameter%20email.'); await p.waitForTimeout(5000);
  const t = await p.innerText('body'); console.log('error text shown:', /valid parameter email/i.test(t)); console.log(t.replace(/\s+/g, ' ').slice(0, 300));
  await p.screenshot({ path: '/home/user/ATTa/evidence/appsmith/setup-error-not-shown.png' }); await b.close(); })();
