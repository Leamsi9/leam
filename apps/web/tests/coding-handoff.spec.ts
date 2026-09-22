import { test, expect } from "@playwright/test";
import { navigate, chooseConversation } from "./navigation";
async function expandSuggestions(page: import("@playwright/test").Page) {
  const summary = page.locator(".proposal-disclosure > summary");
  await expect(summary).toBeVisible();
  await expect(page.getByLabel("Exact task for Codex")).toHaveCount(0);
  await summary.click();
}
for (const width of [390, 1440])
  test(`reviewed handoff preserves exact text and opens its dedicated Coding session at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => {
      (window as any).__approvalRefreshes = 0;
      window.addEventListener("leam:approvals-changed", () => {
        (window as any).__approvalRefreshes++;
      });
    });
    const original = "  Inspect exactly.\nDo not rewrite this.  ";
    let reviewText = "",
      starts = 0,
      reviews = 0;
    let proposal: any = {
      id: "p",
      thread_id: "companion-source",
      operation: "coding.handoff",
      state: "pending",
      input: {
        title: "Inspect product",
        instructions: "Suggested draft",
        context: "Quoted context",
      },
      reason: "Coding requested",
      review: { after: {}, approval: { mode: "manual" } },
    };
    let handoff: any = {
      id: "p",
      state: "not_reviewed",
      taskStatus: "not_started",
    };
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let data: any = { items: [], data: [], threads: [] };
      if (path === "/api/auth/status") data = { authenticated: true };
      if (path === "/api/companion/threads")
        data = {
          threads: [{ thread_id: "companion-source", title: "Planning" }],
        };
      if (path === "/api/companion/threads/companion-source")
        data = { messages: [] };
      if (path === "/api/proposals") data = { items: [proposal] };
      if (path === "/api/coding/handoffs/p") data = handoff;
      if (path === "/api/coding/handoffs/p/review") {
        reviews++;
        reviewText = route.request().postDataJSON().text;
        data = handoff = {
          id: "p",
          state: "reviewed",
          text: reviewText,
          previewToken: "a".repeat(64),
          workspace: "/workspace",
          model: "gpt-5.6-sol",
          reasoningEffort: "medium",
          protocolIdentity: "b".repeat(64),
        };
      }
      if (path === "/api/coding/handoffs/p/start") {
        starts++;
        expect(route.request().postDataJSON()).toEqual({
          previewToken: "a".repeat(64),
          confirmed: true,
        });
        data = handoff = {
          ...handoff,
          state: "accepted",
          threadId: "dedicated",
          taskStatus: "inProgress",
        };
        proposal = { ...proposal, state: "complete", handoff };
      }
      if (path === "/api/codex/threads")
        data = {
          data: [], // Native indexing can lag the accepted handoff.
        };
      if (path === "/api/codex/threads/dedicated")
        data = {
          thread: {
            id: "dedicated",
            name: "Inspect product",
            cwd: "/workspace",
            status: { type: "idle" },
          },
          connected: true,
        };
      if (path === "/api/codex/threads/dedicated/turns")
        data = {
          data: [
            {
              id: "turn",
              status: "completed",
              items: [
                {
                  type: "userMessage",
                  content: [{ type: "text", text: original }],
                },
              ],
            },
          ],
          nextCursor: null,
        };
      await route.fulfill({ json: data });
    });
    await page.goto("/");
    await navigate(page, "Companion");
    await chooseConversation(page, "companion-source");
    await expandSuggestions(page);
    await expect(page.locator(".proposal-disclosure > summary")).toContainText("1 pending review");
    await expect(page.getByRole("article", { name: "Coding handoff" })).toBeVisible();
    expect(starts).toBe(0);
    expect(reviews).toBe(0);
    await page.getByLabel("Exact task for Codex").fill(original);
    await page.locator(".proposal-disclosure > summary").click();
    await expect(page.getByLabel("Exact task for Codex")).toHaveCount(0);
    await expect(page.getByLabel("Message Leam")).toBeVisible();
    await page.locator(".proposal-disclosure > summary").click();
    await expect(page.getByLabel("Exact task for Codex")).toHaveValue(original);
    await page
      .getByRole("button", { name: "Review coding task", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Start in Coding", exact: true }),
    ).toBeVisible();
    expect(starts).toBe(0);
    expect(reviewText).toBe(original);
    await page.getByLabel("Exact task for Codex").fill(original + "change");
    await expect(
      page.getByRole("button", { name: "Start in Coding", exact: true }),
    ).toHaveCount(0);
    await page.getByLabel("Exact task for Codex").fill(original);
    await page
      .getByRole("button", { name: "Review coding task", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Start in Coding", exact: true })
      .click();
    await expect(
      page.getByRole("region", { name: "Coding sessions", exact: true }),
    ).toBeVisible();
    await page.locator(".conversation-list-toggle").click();
    await expect(page.locator('[data-thread-id="dedicated"]')).toHaveCount(1);
    expect(starts).toBe(1);
    expect(await page.evaluate(() => (window as any).__approvalRefreshes)).toBe(1);
    expect(reviewText).toBe(original);
    // The visible title confirms the source handoff chose its own target, not the build owner.
    await expect(
      page
        .getByRole("region", { name: "Coding sessions", exact: true })
        .getByRole("button", { name: "Inspect product", exact: true }),
    ).toBeVisible();
    await navigate(page, "Companion");
    await expandSuggestions(page);
    await chooseConversation(page, "companion-source");
    await expect(
      page.getByRole("button", { name: "Open linked Coding session" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Start in Coding", exact: true }),
    ).toHaveCount(0);
    expect(starts).toBe(1);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  });

test("uncertain handoff survives navigation without another Start", async ({
  page,
}) => {
  let starts = 0;
  let proposal: any = {
    id: "p",
    thread_id: "t",
    operation: "coding.handoff",
    state: "pending",
    input: { title: "Inspect", instructions: "Inspect only", context: "" },
    review: { after: {} },
    reason: "Requested",
  };
  let state: any = { state: "not_reviewed" };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let value: any = { items: [], data: [], threads: [] };
    if (path === "/api/auth/status") value = { authenticated: true };
    if (path === "/api/companion/threads")
      value = { threads: [{ thread_id: "t", title: "Planning" }] };
    if (path === "/api/companion/threads/t") value = { messages: [] };
    if (path === "/api/proposals") value = { items: [proposal] };
    if (path === "/api/coding/handoffs/p") value = state;
    if (path.endsWith("/review"))
      value = {
        state: "reviewed",
        previewToken: "a".repeat(64),
        text: "Inspect only",
        workspace: "/workspace",
        model: "gpt-5.6-sol",
        reasoningEffort: "medium",
        protocolIdentity: "b".repeat(64),
      };
    if (path.endsWith("/start")) {
      starts++;
      state = {
        state: "uncertain",
        taskStatus: "unknown",
        threadId: "owned",
        workspace: "/workspace",
      };
      proposal = { ...proposal, state: "executing", handoff: state };
      await route.fulfill({
        status: 503,
        json: { detail: "Delivery reply lost" },
      });
      return;
    }
    await route.fulfill({ json: value });
  });
  await page.goto("/?view=companion");
  await chooseConversation(page, "t");
  await expandSuggestions(page);
  await page
    .getByRole("button", { name: "Review coding task", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Start in Coding", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Delivery is uncertain" }),
  ).toBeVisible();
  await navigate(page, "Today");
  await navigate(page, "Companion");
  await chooseConversation(page, "t");
  await expandSuggestions(page);
  await expect(
    page.getByRole("button", { name: "Open linked Coding session" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Start in Coding", exact: true }),
  ).toHaveCount(0);
  expect(starts).toBe(1);
});
