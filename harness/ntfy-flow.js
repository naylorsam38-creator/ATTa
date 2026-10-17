// ntfy: open a topic in the web UI, publish to it via HTTP, verify it appears.
const launch = require('./launch'); const http = require('http');
(async () => { const b = await launch(); const p = await b.newPage(); p.on('console', m => m.type() === 'error' && console.log('console: ' + m.text().split('\n')[0]));
  p.on('response', r => r.status() >= 400 && console.log(`${r.status()} ${r.url()}`));
  await p.goto('http://localhost/fictional-test-topic'); await p.waitForTimeout(4000);
  await new Promise(res => { const rq = http.request({ host: 'localhost', port: 80, path: '/fictional-test-topic', method: 'POST' }, r => { console.log('publish status ' + r.statusCode); r.resume(); r.on('end', res); }); rq.end('Fictional test notification'); });
  await p.waitForTimeout(4000); console.log('message shown in UI: ' + await p.getByText('Fictional test notification').count());
  await p.screenshot({ path: '/home/user/ATTa/evidence/ntfy/topic.png' }); await b.close(); })();
