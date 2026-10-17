module.exports = async (page, ctx, report) => {
  await page.goto('https://authelia.example.com/'); await page.waitForTimeout(2000);
  for (const pw of ['Zx9!test-Passw0rd-2026', 'Yt8!new-Passw0rd-2026']) {
    if (!(await page.locator('#password-textfield').count())) break;
    await page.fill('#username-textfield', 'testeralpha'); await page.fill('#password-textfield', pw); await page.click('#sign-in-button'); await page.waitForTimeout(3000);
  }
  report.auth.push({ kind: 'login', url: '/', afterUrl: page.url(), result: (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 120) });
};
