// open-notebook: create a notebook and add a text source via the UI dialogs.
const launch = require('./launch');
(async () => { const b = await launch(); const p = await b.newPage(); const log = [];
  p.on('console', m => m.type() === 'error' && log.push('console: ' + m.text().split('\n')[0]));
  p.on('response', r => { if (r.status() >= 400) log.push(`${r.status()} ${r.request().method()} ${r.url()}`); else if (r.request().method() === 'POST') log.push(`POST ${r.status()} ${r.url()}`); });
  const dump = async t => log.push(t + ': ' + (await p.innerText('body')).replace(/\s+/g, ' ').slice(0, 350));
  await p.goto('http://localhost:8502/notebooks', { waitUntil: 'load' }); await p.waitForTimeout(2500);
  log.push('buttons: ' + (await p.$$eval('button', bs => bs.map(b => b.innerText.trim()).filter(Boolean))).join(' | '));
  await p.getByRole('button', { name: /new notebook|create notebook/i }).first().click(); await p.waitForTimeout(1000);
  const dlg = p.locator('[role=dialog]'); await dlg.locator('input').first().fill('Fictional Test Notebook'); const ta = dlg.locator('textarea'); if (await ta.count()) await ta.first().fill('Created by automated test.');
  await dlg.getByRole('button', { name: /create/i }).click(); await p.waitForTimeout(3000); log.push('url after create: ' + p.url()); await dump('after create');
  const add = p.getByRole('button', { name: /add source|add/i }).first(); if (await add.count()) { await add.click(); await p.waitForTimeout(1500); await dump('add-source dialog'); }
  await p.screenshot({ path: '/home/user/ATTa/evidence/open-notebook/notebook.png' }); console.log(log.join('\n')); await b.close(); })();
