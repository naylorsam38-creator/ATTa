// ArchiveBox step H: submit the main Add URLs form and the Tag add form via the UI.
const { chromium } = require('/opt/node-tools/node_modules/playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage(); const base = 'http://admin.archivebox.localhost:5797';
  const errs = []; p.on('console', m => m.type() === 'error' && errs.push(m.text().slice(0, 200))); p.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  p.on('response', r => r.status() >= 400 && errs.push(r.status() + ' ' + r.url().slice(0, 150)));
  await p.goto(base + '/admin/login/'); await p.fill('input[name=username]', 'testeralpha'); await p.fill('input[name=password]', 'Zx9!test-Passw0rd-2026'); await p.click('input[type=submit]'); await p.waitForTimeout(3000);
  await p.goto(base + '/add/'); await p.waitForTimeout(2000);
  console.log('ADD forms:', await p.$$eval('form', fs => fs.map(f => (f.id || '') + ' action=' + f.getAttribute('action') + ' fields=' + [...f.elements].map(e => e.name).filter(Boolean).join(',')).join('\n')));
  const ta = p.locator('textarea[name=url], textarea').first();
  await ta.fill('http://neverssl.com/test-page-alpha');
  const btn = p.locator('#submit');
  console.log('submit button:', await btn.innerText().catch(() => btn.getAttribute('value')));
  await btn.click(); await p.waitForTimeout(6000);
  console.log('AFTER ADD URL:', p.url()); console.log((await p.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 600));
  await p.goto(base + '/admin/core/tag/add/'); await p.fill('input[name=name]', 'test-tag-alpha'); await p.locator('[name=_save], button:has-text("Save"), input[value*=Save]').first().click(); await p.waitForTimeout(2000);
  console.log('AFTER TAG ADD:', p.url(), (await p.locator('.messagelist, .errornote').allInnerTexts()).join(' / '));
  console.log('ERRORS:', JSON.stringify(errs, null, 1));
  await b.close();
})();
