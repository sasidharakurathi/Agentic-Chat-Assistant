import { defineConfig, devices } from "@playwright/test";

/** Browser tests for the employee flows (Phase 8.11): `npm run e2e`.
 *
 *  Playwright starts its own stack and throws it away after: the API on
 *  8020 (e2e/api-server.mjs: a fresh SQLite file, the fake driver) and the
 *  web app on 3020 (its own build folder, `.next-e2e`). `e2e/global-setup.ts`
 *  seeds an owner, two employees and a live assistant through the API. One
 *  worker: the tests share that seed, in order. Nothing here spends or
 *  touches the dev stack (8000, 8010, 3000). */
const WEB = "http://localhost:3020";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: WEB,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "node e2e/api-server.mjs",
      url: "http://127.0.0.1:8020/healthz",
      timeout: 180_000,
      reuseExistingServer: false,
      // The API's log, server errors included, in the run's output.
      stdout: "pipe",
    },
    {
      command: "npx next dev -p 3020",
      url: `${WEB}/login`,
      timeout: 240_000,
      reuseExistingServer: false,
      env: { NEXT_PUBLIC_API_URL: "http://localhost:8020", NEXT_DIST_DIR: ".next-e2e" },
    },
  ],
});
