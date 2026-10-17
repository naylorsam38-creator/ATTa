// Supabase Studio: clean visits (no clicks) + run `select 1` in SQL editor.
const launch = require('./launch');
(async () => { const b = await launch(); const ctx = await b.newContext({ httpCredentials: { username: 'supabase', password: 'TestDashPass2026' } }); const p = await ctx.newPage(); const log = [];
  p.on('requestfailed', r => log.push(`FAILED ${r.failure().errorText} ${r.url().slice(0, 140)} [on ${p.url()}]`));
  p.on('response', r => { if (r.status() >= 400) log.push(`HTTP ${r.status()} ${r.request().method()} ${r.url().slice(0, 140)}`); });
  p.on('console', m => m.type() === 'error' && log.push('CONSOLE ' + m.text().split('\n')[0] + ` [on ${p.url()}]`));
  for (const path of ['editor', 'database/schemas', 'auth/users', 'storage/files', 'functions', 'realtime/inspector', 'advisors/security', 'observability/query-performance', 'settings/jwt', 'database/tables', 'database/roles']) {
    await p.goto('http://localhost:8000/project/default/' + path, { waitUntil: 'load' }); await p.waitForTimeout(6000); log.push('visited ' + path + ' title=' + (await p.title()));
  }
  await p.goto('http://localhost:8000/project/default/sql/new', { waitUntil: 'load' }); await p.waitForTimeout(6000);
  await p.locator('.monaco-editor').first().click(); await p.keyboard.type('select 1 as ok;');
  await p.getByRole('button', { name: /^run/i }).first().click(); await p.waitForTimeout(5000);
  log.push('sql result area: ' + (await p.innerText('body')).match(/ok[\s\S]{0,40}/)?.[0]?.replace(/\s+/g, ' '));
  await p.screenshot({ path: '/home/user/ATTa/evidence/supabase/sql-result.png' });
  console.log(log.join('\n')); await b.close(); })();
