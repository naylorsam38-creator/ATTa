import { defineConfig } from "@playwright/test";

/**
 * Full-stack end-to-end: global-setup starts the real platform through the service manager (ATTA_ENV=test), then
 * the built Next.js web app is served against that API and driven in Chromium.
 * Prerequisites: INSTALL done, PostgreSQL reachable, `npm run build:web`.
 */
export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts/,
  timeout: 60_000,
  retries: 0,
  reporter: [["list"]],
  globalSetup: "./global-setup.ts",
  globalTeardown: "./global-teardown.ts",
  use: { baseURL: "http://127.0.0.1:3010", trace: "off" },
  webServer: {
    command: "npx next start --hostname 127.0.0.1 --port 3010",
    cwd: "../../apps/web",
    url: "http://127.0.0.1:3010",
    reuseExistingServer: false,
    timeout: 60_000,
    env: { ATTA_API_URL: "http://127.0.0.1:8810", NEXT_TELEMETRY_DISABLED: "1" },
  },
});
