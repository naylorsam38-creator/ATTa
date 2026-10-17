(async () => {
  const b = await require('../browser.js').launch(); const c = await b.newContext({ locale: 'en-US' }); const p = await c.newPage();
  const errs = []; p.on('console', m => m.type() === 'error' && errs.push('CONSOLE ' + m.text().slice(0, 300))); p.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  p.on('response', async r => { const m = r.request().method(); if (r.url().includes('/api/') && m !== 'GET') console.log('API', r.status(), m, r.url().replace(/^https?:\/\/[^/]+/, '').slice(0, 80), (await r.text().catch(() => '')).slice(0, 300).replace(/\s+/g, ' ')); });
  await p.goto('http://localhost:8502/notebooks'); await p.waitForTimeout(3000); await p.locator('text=Test Notebook Alpha').first().click(); await p.waitForTimeout(4000);
  await p.locator('button:has-text("Add Source")').first().click(); await p.waitForTimeout(800); await p.locator('[role=menuitem]:has-text("Add Source")').first().click(); await p.waitForTimeout(1500);
  await p.locator('[role=dialog] button:has-text("Enter Text")').click(); await p.waitForTimeout(800);
  await p.fill('#content', 'Alpha test source. The quick brown fox jumps over the lazy dog. Made-up test content.'); await p.fill('#source-title', 'Test Source Alpha');
  for (const name of ['Next', 'Next', 'Done']) {
    const bt = p.locator(`[role=dialog] button:has-text("${name}")`).last(); const en = await bt.isEnabled().catch(() => 'n/a');
    console.log('button', name, 'enabled', en, '| dialog:', (await p.locator('[role=dialog]').innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 250));
    if (en === true) { await bt.click(); await p.waitForTimeout(2500); }
  }
  await p.waitForTimeout(15000); await p.keyboard.press('Escape'); await p.reload(); await p.waitForTimeout(4000);
  console.log('PAGE', (await p.locator('body').innerText()).replace(/\s+/g, ' ').slice(300, 900));
  const chat = p.locator('textarea[placeholder*="Ask anything"]').first(); await chat.fill('What is this source about?');
  const send = p.locator('textarea[placeholder*="Ask anything"] ~ button, button[type=submit]:near(textarea[placeholder*="Ask anything"])').last();
  console.log('send enabled', await send.isEnabled().catch(() => 'n/a')); await send.click().catch(e => console.log('send click', e.message.split('\n')[0])); await p.waitForTimeout(10000);
  console.log('CHAT', (await p.locator('body').innerText()).replace(/\s+/g, ' ').slice(-600));
  await p.screenshot({ path: 'logs/07-open-notebook/source-chat.png', fullPage: true }); console.log('ERRORS', JSON.stringify(errs, null, 1)); await b.close();
})();
