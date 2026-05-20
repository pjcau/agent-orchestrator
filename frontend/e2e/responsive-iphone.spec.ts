import { test, expect } from "@playwright/test";

/**
 * Visual smoke check for iPhone 12 Pro viewport (390×844 logical, 3× scale).
 *
 * Guards three previous regressions that recurred when shipping the cost
 * footer / responsive layout work:
 *   1. Dashboard root height being larger than the visible viewport on iOS
 *      Safari (100vh → must use 100dvh) — content slid under the URL bar.
 *   2. Header wrapping to a second row on narrow viewports (≤390px) so the
 *      first row gets hidden under the iOS status bar.
 *   3. Chat input footer ("New Chat" + gear) touching Safari's bottom bar
 *      with no breathing room.
 */
test.describe("iPhone 12 Pro layout", () => {
  test.use({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 3,
  });

  test("header stays on a single row", async ({ page }) => {
    await page.goto("/");
    const header = page.locator(".app-header");
    await expect(header).toBeVisible();

    // The Logs / SSE buttons must be on the same visual line as the title.
    // We check that the top of the second sub-group equals the top of the first.
    const title = page.locator(".app-header__title");
    const logsBtn = page
      .locator(".app-header .btn-sidebar-toggle", { hasText: "Logs" })
      .first();
    await expect(title).toBeVisible();
    await expect(logsBtn).toBeVisible();

    const titleBox = await title.boundingBox();
    const logsBox = await logsBtn.boundingBox();
    expect(titleBox).not.toBeNull();
    expect(logsBox).not.toBeNull();
    // Tolerate sub-pixel rounding (3× DPR) — the centers should be within 12px.
    const titleCenter = titleBox!.y + titleBox!.height / 2;
    const logsCenter = logsBox!.y + logsBox!.height / 2;
    expect(Math.abs(titleCenter - logsCenter)).toBeLessThan(12);
  });

  test("header is fully inside the viewport (not clipped at the top)", async ({ page }) => {
    await page.goto("/");
    const header = page.locator(".app-header");
    const box = await header.boundingBox();
    expect(box).not.toBeNull();
    // Header must not start above y=0 — that would mean it's under the notch.
    expect(box!.y).toBeGreaterThanOrEqual(0);
    // And the bottom of the header must be visible above the fold.
    expect(box!.y + box!.height).toBeLessThanOrEqual(844);
  });

  test("chat input footer has breathing room above the viewport bottom", async ({
    page,
  }) => {
    await page.goto("/");
    const actions = page.locator(".chat-input__actions");
    await expect(actions).toBeVisible();
    const box = await actions.boundingBox();
    expect(box).not.toBeNull();
    // The actions row must end at least 8px above the viewport bottom so
    // Safari's URL bar (when reappearing) doesn't cover the gear button.
    const distanceFromBottom = 844 - (box!.y + box!.height);
    expect(distanceFromBottom).toBeGreaterThanOrEqual(8);
  });
});
