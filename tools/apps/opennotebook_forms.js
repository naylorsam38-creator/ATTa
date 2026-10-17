(async () => {
  const b = await require('../browser.js').launch(); const c = await b.newContext({ locale: 'en-US' }); const p = await c.newPage();
  const errs = []; p.on('console', m => m.type() === 'error' && errs.push('CONSOLE ' + p.url() + ' | ' + m.text().slice(0, 250)));
  p.on('pageerror', e => errs.push('PAGEERROR ' + p.url() + ' | ' + e.message));
  p.on('response', async r => { const m = r.request().method(); if (r.url().includes('/api/') && m !== 'GET') console.log('API', r.status(), m, r.url().replace(/^https?:\/\/[^/]+/, '').slice(0, 80), (await r.text().catch(() => '')).slice(0, 200).replace(/\s+/g, ' ')); if (r.status() >= 400) errs.push('HTTP ' + r.status() + ' ' + m + ' ' + r.url().slice(0, 150)); });
  const T = async () => (await p.locator('body').innerText()).replace(/\s+/g, ' ');
  const dialogInputs = async () => p.$$eval('[role=dialog] input, [role=dialog] textarea, [role=dialog] button, [role=dialog] [role=combobox]', es => es.map(e => e.tagName + '#' + e.id + '[' + (e.type || '') + '] ' + (e.innerText || e.placeholder || e.getAttribute('aria-label') || '').trim().slice(0, 30)).join(' | '));
  // 1 notebook
  await p.goto('http://localhost:8502/notebooks'); await p.waitForTimeout(3000);
  await p.locator('button:has-text("New Notebook")').first().click(); await p.waitForTimeout(1500);
  console.log('NB DIALOG', await dialogInputs());
  await p.locator('[role=dialog] input').first().fill('Test Notebook Alpha'); const ta = p.locator('[role=dialog] textarea'); if (await ta.count()) await ta.first().fill('Made-up notebook for testing');
  await p.locator('[role=dialog] button:has-text("Create")').first().click(); await p.waitForTimeout(4000);
  console.log('AFTER NB', p.url(), (await T()).slice(0, 200));
  // 2 add source (text)
  const add = p.locator('button:has-text("Add Source"), button:has-text("Add")').first(); if (await add.count()) { await add.click(); await p.waitForTimeout(1500); console.log('ADD MENU', (await p.$$eval('[role=menu] [role=menuitem], [role=dialog] button', es => es.map(e => e.innerText.trim()).join(' | '))).slice(0, 300)); }
  const newSrc = p.locator('[role=menuitem]:has-text("Add Source"), [role=menuitem]:has-text("New Source"), [role=menuitem]:has-text("Source")').first(); if (await newSrc.count()) { await newSrc.click(); await p.waitForTimeout(1500); }
  console.log('SRC DIALOG', await dialogInputs());
  const txtTab = p.locator('[role=dialog] [role=tab]:has-text("Text"), [role=dialog] button:has-text("Text")').first(); if (await txtTab.count()) { await txtTab.click(); await p.waitForTimeout(800); }
  const srcTa = p.locator('[role=dialog] textarea').first(); if (await srcTa.count()) await srcTa.fill('Alpha test source. The quick brown fox jumps over the lazy dog. This is made-up test content.');
  const title = p.locator('[role=dialog] input[type=text]').first(); if (await title.count()) await title.fill('Test Source Alpha').catch(() => {});
  for (let i = 0; i < 4; i++) { const nx = p.locator('[role=dialog] button:has-text("Next"), [role=dialog] button:has-text("Done"), [role=dialog] button:has-text("Add Source"), [role=dialog] button:has-text("Process"), [role=dialog] button:has-text("Create")').last(); if (await nx.count() && await nx.isEnabled().catch(() => false)) { const t = await nx.innerText(); await nx.click(); console.log('clicked', t); await p.waitForTimeout(2500); } else break; }
  await p.keyboard.press('Escape'); await p.waitForTimeout(5000);
  console.log('AFTER SRC', (await T()).slice(0, 400));
  await p.screenshot({ path: 'logs/07-open-notebook/notebook.png', fullPage: true });
  // 3 chat
  const chat = p.locator('textarea[placeholder*="sk" i], textarea[placeholder*="message" i], textarea').last();
  if (await chat.count()) { await chat.fill('What is this source about?'); await p.keyboard.press('Enter'); await p.waitForTimeout(6000); console.log('AFTER CHAT', (await T()).slice(-400)); }
  // 4 models: add provider with fake key + Test
  await p.goto('http://localhost:8502/settings/models'); await p.waitForTimeout(3000); console.log('MODELS PAGE', (await T()).slice(0, 300));
  const addCfg = p.locator('button:has-text("Add Configuration")').first();
  if (await addCfg.count()) { await addCfg.click(); await p.waitForTimeout(1500); console.log('CFG DIALOG', await dialogInputs());
    const ins = p.locator('[role=dialog] input'); const n = await ins.count(); for (let i = 0; i < n; i++) { const t = await ins.nth(i).getAttribute('type'); await ins.nth(i).fill(i === 0 ? 'Fake OpenAI' : (t === 'password' ? 'sk-test-made-up-key-0000' : 'sk-test-made-up-key-0000')).catch(() => {}); }
    await p.locator('[role=dialog] button:has-text("Add Configuration")').last().click().catch(e => console.log('cfg add', e.message.split('\n')[0])); await p.waitForTimeout(3000);
    const test = p.locator('button:has-text("Test")').first(); if (await test.count()) { await test.click(); await p.waitForTimeout(8000); }
    console.log('AFTER TEST', (await T()).slice(0, 500)); }
  await p.screenshot({ path: 'logs/07-open-notebook/models.png', fullPage: true });
  console.log('ERRORS', JSON.stringify(errs, null, 1)); await b.close();
})();
