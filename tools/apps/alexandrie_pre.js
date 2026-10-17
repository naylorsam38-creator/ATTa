module.exports = async (page, ctx, report) => {
  const t = async () => (await page.locator('body').innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 200);
  await page.goto('http://localhost:8200/signup'); await page.waitForTimeout(3000);
  await page.fill('#username', 'testeralpha'); await page.fill('#email', 'tester.alpha@example.test');
  await page.fill('#password', 'Zx9!test-Passw0rd-2026'); await page.fill('#confirmPassword', 'Zx9!test-Passw0rd-2026');
  await page.click('button[type=submit]:has-text("Sign up")'); await page.waitForTimeout(4000);
  report.auth.push({ kind: 'signup', url: '/signup', afterUrl: page.url(), result: await t() });
  if (/login|signup/.test(page.url())) {
    await page.goto('http://localhost:8200/login'); await page.waitForTimeout(3000);
    const user = page.locator('#username, input[type=text], input[type=email]').first(); await user.fill('testeralpha');
    await page.locator('input[type=password]').first().fill('Zx9!test-Passw0rd-2026');
    await page.locator('button.btn:has-text("Login")').first().click(); await page.waitForTimeout(4000);
    report.auth.push({ kind: 'login', url: '/login', afterUrl: page.url(), result: await t() });
  }
};
