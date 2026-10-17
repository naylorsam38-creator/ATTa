(async () => {
  const b = await require('../browser.js').launch(); const c = await b.newContext({ locale: 'en-US' }); const p = await c.newPage();
  const errs = []; p.on('console', m => m.type() === 'error' && errs.push('CONSOLE ' + p.url() + ' | ' + m.text().slice(0, 300)));
  p.on('pageerror', e => errs.push('PAGEERROR ' + p.url() + ' | ' + e.message));
  p.on('response', async r => { const m = r.request().method(); if (r.url().includes('/api/') && m !== 'GET') console.log('API', r.status(), m, r.url().replace(/^https?:\/\/[^/]+/, '').slice(0, 80), (await r.text().catch(() => '')).slice(0, 220).replace(/\s+/g, ' ')); if (r.status() >= 400) errs.push('HTTP ' + r.status() + ' ' + m + ' ' + r.url().slice(0, 150)); });
  const T = async () => (await p.locator('body').innerText()).replace(/\s+/g, ' ');
  const dlg = async () => p.$$eval('[role=dialog] input, [role=dialog] textarea, [role=dialog] button, [role=dialog] [role=tab]', es => es.map(e => e.tagName + '#' + e.id + '[' + (e.type || '') + '] ' + (e.innerText || e.placeholder || '').trim().slice(0, 30)).join(' | '));
  await p.goto('http://localhost:8502/notebooks'); await p.waitForTimeout(3000); await p.locator('text=Test Notebook Alpha').first().click(); await p.waitForTimeout(4000);
  const nb = p.url();
  await p.locator('button:has-text("Add Source")').first().click(); await p.waitForTimeout(1000);
  await p.locator('[role=menuitem]:has-text("Add Source")').first().click(); await p.waitForTimeout(2000);
  console.log('SRC DIALOG', await dlg());
  const tt = p.locator('[role=dialog] [role=tab]:has-text("Text"), [role=dialog] button:has-text("Text")').first(); if (await tt.count()) { await tt.click(); await p.waitForTimeout(800); }
  console.log('SRC DIALOG(text)', await dlg());
  const ta = p.locator('[role=dialog] textarea').first(); if (await ta.count()) await ta.fill('Alpha test source. The quick brown fox jumps over the lazy dog. Made-up test content for open-notebook.');
  const ti = p.locator('[role=dialog] input[type=text]').first(); if (await ti.count()) await ti.fill('Test Source Alpha').catch(() => {});
  for (let i = 0; i < 5; i++) { const nx = p.locator('[role=dialog] button:has-text("Next"), [role=dialog] button:has-text("Done"), [role=dialog] button:has-text("Add Source"), [role=dialog] button:has-text("Process"), [role=dialog] button:has-text("Create"), [role=dialog] button:has-text("Submit")').last(); if (await nx.count() && await nx.isEnabled().catch(() => false)) { const t = await nx.innerText(); await nx.click(); console.log('clicked', t, '|', (await dlg()).slice(0, 300)); await p.waitForTimeout(3000); } else break; }
  await p.keyboard.press('Escape'); await p.waitForTimeout(8000);
  console.log('AFTER SRC', (await T()).slice(0, 600));
  // note
  await p.locator('button:has-text("Write Note")').first().click().catch(() => {}); await p.waitForTimeout(1500); console.log('NOTE DIALOG', await dlg());
  const nt = p.locator('[role=dialog] input[type=text]').first(); if (await nt.count()) await nt.fill('Test Note Alpha');
  const nb2 = p.locator('[role=dialog] textarea, [role=dialog] [contenteditable=true]').first(); if (await nb2.count()) { await nb2.click(); await p.keyboard.type('Made-up note body'); }
  const sv = p.locator('[role=dialog] button:has-text("Save"), [role=dialog] button:has-text("Create")').last(); if (await sv.count()) { await sv.click(); await p.waitForTimeout(3000); }
  await p.keyboard.press('Escape');
  // chat
  const chat = p.locator('textarea[placeholder*="Ask anything"]').first(); if (await chat.count()) { await chat.fill('What is this source about?'); await p.keyboard.press('Enter'); await p.waitForTimeout(8000); console.log('AFTER CHAT', (await T()).slice(-500)); }
  await p.screenshot({ path: 'logs/07-open-notebook/notebook-detail.png', fullPage: true });
  // provider config: name + fake key only
  await p.goto('http://localhost:8502/settings/models'); await p.waitForTimeout(3000);
  await p.locator('button:has-text("Add Configuration")').first().click(); await p.waitForTimeout(1500);
  await p.fill('#cred-name', 'Fake OpenAI'); await p.fill('#api-key', 'sk-test-made-up-key-0000');
  await p.locator('[role=dialog] button[type=submit]:has-text("Add Configuration")').click(); await p.waitForTimeout(3000);
  console.log('AFTER CFG ADD', (await T()).slice(0, 700));
  const test = p.locator('button:has-text("Test")').first(); if (await test.count()) { await test.click(); await p.waitForTimeout(10000); console.log('AFTER TEST', (await T()).slice(0, 900)); }
  await p.screenshot({ path: 'logs/07-open-notebook/models-after-test.png', fullPage: true });
  console.log('ERRORS', JSON.stringify(errs, null, 1)); await b.close();
})();
