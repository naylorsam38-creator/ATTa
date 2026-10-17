const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); const log = [];
  p.on('console', m => m.type() === 'error' && log.push('console: ' + m.text().split('\n')[0]));
  p.on('response', r => { if (r.status() >= 400) log.push(`${r.status()} ${r.request().method()} ${r.url()}`); else if (r.request().method() === 'POST') log.push(`POST ${r.status()} ${r.url()}`); });
  const dump = async t => log.push(t + ': ' + (await p.innerText('body')).replace(/\s+/g, ' ').slice(0, 500));
  await p.goto('http://localhost:8502/notebooks', { waitUntil: 'load' }); await p.waitForTimeout(2000);
  await p.getByText('Fictional Test Notebook').first().click(); await p.waitForTimeout(3000); log.push('nb url: ' + p.url());
  log.push('buttons: ' + (await p.$$eval('button', bs => bs.map(b => b.innerText.trim()).filter(Boolean))).join(' | '));
  await p.getByRole('button', { name: /^add$|add source/i }).first().click(); await p.waitForTimeout(1200);
  log.push('menu: ' + (await p.$$eval('[role=menuitem],[role=option]', bs => bs.map(b => b.innerText.trim()))).join(' | '));
  const item = p.getByRole('menuitem', { name: /add source|new source/i }); if (await item.count()) { await item.first().click(); await p.waitForTimeout(1500); }
  await dump('dialog');
  const textTab = p.getByRole('tab', { name: /text/i }); if (await textTab.count()) await textTab.first().click(); else { const t = p.getByText(/^text$/i); if (await t.count()) await t.first().click(); }
  await p.waitForTimeout(800); const dlg = p.locator('[role=dialog]');
  const ta = dlg.locator('textarea:visible'); if (await ta.count()) await ta.first().fill('Fictional source text for testing purposes. Lorem ipsum.');
  const ti = dlg.locator('input[type=text]:visible, input:not([type]):visible'); if (await ti.count()) await ti.first().fill('Fictional Source');
  for (let i = 0; i < 4; i++) { const nx = dlg.getByRole('button', { name: /next|done|add source|submit|create|process/i }); if (!(await nx.count())) break; const lbl = await nx.last().innerText(); log.push('click ' + lbl); await nx.last().click().catch(e => log.push('click fail ' + e.message.split('\n')[0])); await p.waitForTimeout(2500); if (!(await dlg.count())) break; }
  await p.waitForTimeout(8000); await dump('after add');
  await p.screenshot({ path: '/home/user/ATTa/evidence/open-notebook/source.png' }); console.log(log.join('\n')); await b.close(); })();
