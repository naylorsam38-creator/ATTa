(async () => {
  const b = await require('../browser.js').launch(); const c = await b.newContext({ locale: 'en-US' }); const p = await c.newPage();
  await require('./alexandrie_pre.js')(p, c, { auth: [] });
  for (const u of process.argv.slice(2)) {
    await p.goto('http://localhost:8200' + u); await p.waitForTimeout(3000);
    console.log('==', u, '->', p.url());
    console.log(await p.$$eval('input,button,textarea,select,[contenteditable=true],a', es => es.filter(e => e.offsetParent).map(e => e.tagName + (e.id ? '#' + e.id : '') + (e.className && typeof e.className === 'string' ? '.' + e.className.split(' ')[0] : '') + '[' + (e.type || '') + (e.getAttribute('href') ? ' href=' + e.getAttribute('href') : '') + '] ' + (e.innerText || e.placeholder || '').trim().replace(/\s+/g, ' ').slice(0, 30)).join('\n')));
  }
  await b.close();
})();
