const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); await p.goto('http://localhost/setup/welcome'); await p.waitForTimeout(4000);
  console.log(await p.$$eval('input,button,select', es => es.map(e => `${e.tagName} name=${e.name} type=${e.type} ph=${e.placeholder || ''} text=${(e.innerText || '').trim().slice(0, 40)} visible=${e.offsetParent !== null}`).join('\n')));
  await b.close(); })();
