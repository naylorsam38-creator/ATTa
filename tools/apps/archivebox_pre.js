// ArchiveBox: create first admin (wizard) or log in, then finish first-time setup wizard.
module.exports = async (page, ctx, report, cfg) => {
  const base = cfg.base; const u = 'testeralpha', pw = 'Zx9!test-Passw0rd-2026';
  await page.goto(base + '/admin/login/'); await page.waitForTimeout(1500);
  if (await page.locator('input[name=password1]').count()) {
    await page.fill('input[name=username]', u); await page.fill('input[name=password1]', pw); await page.fill('input[name=password2]', pw);
  } else { await page.fill('input[name=username]', u); await page.fill('input[name=password]', pw); }
  await page.click('input[type=submit]'); await page.waitForTimeout(12000);
  report.auth.push({ kind: 'login', url: '/admin/login/', afterUrl: page.url() });
  if (await page.locator('#archivebox-setup-review').isVisible().catch(() => false)) {
    await page.click('#archivebox-setup-review'); await page.waitForTimeout(3000);
    const save = page.locator('input[type=submit][name=_save], button:has-text("Save"), input[value*=Save]').first();
    await save.click({ timeout: 5000 }).catch(e => report.notes.push('wizard save click failed: ' + e.message.split('\n')[0]));
    await page.waitForTimeout(3000);
    report.notes.push('wizard saved -> ' + page.url() + ' | ' + (await page.locator('.messagelist, .errornote, .errorlist').allInnerTexts()).join(' / '));
    await page.goto(base + '/admin/'); await page.waitForTimeout(6000);
    report.notes.push('wizard still visible after save: ' + await page.locator('#archivebox-setup-review').isVisible().catch(() => false));
  }
};
