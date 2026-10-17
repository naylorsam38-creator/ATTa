module.exports = async (page, ctx, report) => {
  const t = async () => (await page.locator('body').innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 200);
  await page.goto('http://localhost:5230/'); await page.waitForTimeout(3000);
  if (await page.locator('#signup-username').count()) {
    await page.fill('#signup-username', 'testeralpha'); await page.fill('#signup-password', 'Zx9!test-Passw0rd-2026');
    await page.click('button[type=submit]'); await page.waitForTimeout(4000);
    report.auth.push({ kind: 'signup(admin)', url: '/auth/signup', afterUrl: page.url(), result: await t() });
  }
  if (/\/auth/.test(page.url())) {
    await page.goto('http://localhost:5230/auth'); await page.waitForTimeout(2000);
    await page.locator('input[type=text]').first().fill('testeralpha'); await page.locator('input[type=password]').first().fill('Zx9!test-Passw0rd-2026');
    await page.locator('button[type=submit]').first().click(); await page.waitForTimeout(4000);
    report.auth.push({ kind: 'login', url: '/auth', afterUrl: page.url(), result: await t() });
  }
};
