// Probe one URL in Chromium; print status chain and body excerpt.
const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage();
  p.on('response', r => console.log(r.status(), r.request().method(), r.url(), JSON.stringify(r.headers()['server'] || '')));
  const r = await p.goto(process.argv[2], { waitUntil: 'domcontentloaded' }).catch(e => console.log('ERR', e.message));
  console.log('BODY:', (await p.content()).replace(/\s+/g, ' ').slice(0, 600)); await b.close(); })();
