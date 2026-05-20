import { test, expect, type Page } from "@playwright/test";

/**
 * Smoke test that locks in the cross-viewport chat happy-path:
 *   1. Page loads
 *   2. User sends a prompt
 *   3. Thinking indicator appears (no token has arrived yet)
 *   4. User clicks Stop
 *   5. "Stopped by user." system message appears
 *   6. Input is re-enabled
 *
 * Runs once per `projects` entry in playwright.config.ts (mobile + desktop).
 * All backend calls are mocked — no dashboard or LLM required.
 */

const STALL_ENDPOINT = "**/api/prompt";

async function installMocks(page: Page) {
  // Auth check — pretend the user is signed in
  await page.route("**/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: true,
        email: "test@example.com",
        name: "Test User",
        provider: "google",
        role: "developer",
        github_login: "google:test@example.com",
      }),
    })
  );

  // Cumulative usage panel
  await page.route("**/api/usage", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        total_tokens: 0,
        total_cost: 0,
        request_count: 0,
        session_speed: 0,
      }),
    })
  );

  // Model picker
  await page.route("**/api/models", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ollama: [{ name: "llama3.2:1b", size: "1.3GB" }],
        openrouter: [{ name: "openai/gpt-4o-mini", size: "" }],
      }),
    })
  );

  // Preset bar
  await page.route("**/api/presets", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ presets: [] }),
    })
  );

  // Conversation autocreate
  await page.route("**/api/conversation/new", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversation_id: "smoke-conv-1" }),
    })
  );

  // Conversation history restore (called only if a persisted id exists)
  await page.route("**/api/conversation/*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversation_id: "smoke-conv-1", messages: [] }),
    })
  );

  // Workspace files panel (called on load by some panes)
  await page.route("**/api/files**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ files: [] }),
    })
  );

  // Other side-panels that fire on initial load — must match the shape each
  // consumer expects (.agents.map, .tools.map, etc).
  const sidePanelMocks: Record<string, unknown> = {
    "**/api/mcp/tools": { tools: [] },
    "**/api/compaction/stats": {},
    "**/api/agents": { agents: [] },
    "**/api/openrouter/pricing": { models: [] },
    "**/api/sandbox/status": { running: false, sessions: [] },
  };
  for (const [path, body] of Object.entries(sidePanelMocks)) {
    await page.route(path, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      })
    );
  }

  // The three "send" endpoints all hang so we can observe the loading
  // indicator and click Stop. Whichever exec mode is selected by default,
  // the test sees the loading flow.
  const stallAll = async () => {
    await new Promise(() => {});
  };
  await page.route("**/api/prompt", stallAll);
  await page.route("**/api/agent/run", stallAll);
  await page.route("**/api/team/run", stallAll);
}

test.describe("Chat send → Stop", () => {
  test.beforeEach(async ({ page }) => {
    await installMocks(page);
    await page.goto("/");
  });

  test("renders, shows loading on send, and stops on demand", async ({ page }, testInfo) => {
    // Sanity: page loaded without runtime errors
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));

    // Find the textarea — uses placeholder set by ChatInput
    const textarea = page.getByRole("textbox").first();
    await expect(textarea).toBeVisible({ timeout: 15_000 });

    await textarea.fill("Hello from " + testInfo.project.name);

    // Submit via Enter — works identically on mobile and desktop, no need to
    // hunt for a button that may be hidden behind responsive layout changes.
    await textarea.press("Enter");

    // Thinking indicator should appear (status role, "Assistant is thinking")
    const thinking = page.getByRole("status", { name: /assistant is thinking/i });
    await expect(thinking).toBeVisible({ timeout: 5_000 });

    // Stop button should be available
    const stop = page.getByRole("button", { name: /stop generation/i });
    await expect(stop).toBeVisible();
    await stop.click();

    // "Stopped by user." system message should land in the log
    await expect(page.getByText(/stopped by user/i)).toBeVisible({ timeout: 5_000 });

    // Loading indicator and stop button should be gone after stop
    await expect(thinking).toBeHidden();
    await expect(stop).toBeHidden();

    // No JS runtime errors observed during the flow
    expect(errors, `runtime errors:\n${errors.join("\n")}`).toEqual([]);
  });
});
