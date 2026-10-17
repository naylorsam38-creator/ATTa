// Render a JS docs page and print its main text.
const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); await p.goto(process.argv[2], { waitUntil: 'networkidle', timeout: 60000 }).catch(() => {}); await p.waitForTimeout(2000);
  const t = await p.innerText('main').catch(() => p.innerText('body')); require('fs').writeFileSync(process.argv[3], t); console.log(t.slice(0, +process.argv[4] || 6000)); await b.close(); })();
