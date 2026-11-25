import { expect, test } from "@playwright/test";

test("web status page shows live readiness from the real backend", async ({ page, request }) => {
  const api = await request.get("http://127.0.0.1:8810/v1/health/ready");
  const report = await api.json();
  expect(report.ready).toBe(true);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "ATTa" })).toBeVisible();
  await expect(page.getByTestId("status")).toHaveText(/^(Ready|Degraded)$/);
  for (const dep of ["postgres", "redis"]) {
    await expect(page.getByTestId(`dep-${dep}`)).toContainText("healthy");
  }
});

test("protected API operations are denied by default", async ({ request }) => {
  const r = await request.get("http://127.0.0.1:8810/v1/capabilities");
  expect(r.status()).toBe(403);
  expect((await r.json()).error.code).toBe("permission_denied");
});
