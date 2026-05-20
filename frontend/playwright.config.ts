import { defineConfig, devices } from "@playwright/test";

/**
 * Cross-viewport smoke tests for the dashboard UI.
 *
 * Each test in `e2e/` runs twice — once on a mobile-sized viewport and once
 * on a desktop-sized viewport. The breakpoints match `frontend/src/lib/breakpoints.ts`.
 *
 * Run locally:
 *   npm run e2e
 * Run a single viewport:
 *   npm run e2e -- --project=desktop
 * Open the HTML report:
 *   npx playwright show-report
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI
    ? [["html", { open: "never" }], ["github"]]
    : [["html", { open: "on-failure" }]],

  use: {
    // Default to vite dev server; override via PLAYWRIGHT_BASE_URL in CI/staging.
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    ignoreHTTPSErrors: true,
    // Disable CSS animations in tests so visual states are deterministic.
    reducedMotion: "reduce",
    // Wait for app to settle before each test.
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
  },

  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "mobile",
      // Use Chromium with iPhone SE viewport so the test runs on the same
      // engine as desktop (only Chromium is installed in CI to keep image small).
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 375, height: 667 },
        isMobile: true,
        hasTouch: true,
        deviceScaleFactor: 2,
      },
    },
  ],

  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command: "npm run dev",
        url: "http://localhost:5173",
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
        stdout: "ignore",
        stderr: "pipe",
      },
});
