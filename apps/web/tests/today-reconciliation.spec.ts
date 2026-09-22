import { test, expect, type Page } from "@playwright/test";

const runId = "11111111-1111-4111-8111-111111111111";
const job = (changes: Record<string, unknown> = {}) => ({
  id: "check-1",
  turnId: runId,
  revision: 1,
  state: "queued",
  outcome: null,
  proposalIds: [],
  clarification: null,
  error: null,
  attempts: 0,
  nextRetryAt: null,
  ...changes,
});

async function fixture(page: Page) {
  const state = {
    coverage: { error: null as string | null, retryable: false, state: "idle" },
    scanRetries: [] as any[],
    items: [] as any[],
    afterSend: [] as any[],
    reads: [] as string[],
    messages: [] as any[],
    retries: [] as any[],
    failRetry: false,
    agendaReads: 0,
    pendingApprovals: 0,
    bindingWrites: 0,
    noBinding: false,
    holdThread: "",
    release: () => {},
    held: false,
    threadItems: {} as Record<string, any[]>,
  };
  await page.addInitScript(() => {
    const streams: any[] = [];
    class Events extends EventTarget {
      url: string;
      closed = false;
      constructor(url: string) {
        super();
        this.url = url;
        streams.push(this);
      }
      close() {
        this.closed = true;
      }
    }
    (window as any).EventSource = Events;
    (window as any).emitToday = (type: string, frame: any) => {
      for (const stream of streams.filter(
        (s) => !s.closed && s.url.includes("/companion/threads/"),
      )) {
        stream.dispatchEvent(
          new MessageEvent(type, { data: JSON.stringify(frame) }),
        );
      }
    };
    (window as any).fixtureVisibility = "visible";
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => (window as any).fixtureVisibility,
    });
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname,
      method = route.request().method();
    let body: any = {
      items: [],
      data: [],
      messages: [],
      threads: [],
      models: [],
      providers: [],
    };
    if (path === "/api/auth/status")
      body = { configured: true, authenticated: true };
    if (path === "/api/agenda") {
      state.agendaReads++;
      const date = url.searchParams.get("date");
      body = {
        date,
        timezone: url.searchParams.get("timezone"),
        observedAt: Date.now() / 1000,
        window: { start: `${date}T00:00:00Z`, end: `${date}T23:59:59Z` },
        commitments: [],
        events: [],
        emails: [],
        total: { commitments: 0, events: 0, emails: 0 },
        nextOffset: null,
        partial: false,
        sources: {
          calendar: { state: "not_connected", accounts: [], snapshots: [] },
          email: { state: "not_connected", accounts: [] },
        },
      };
    }
    if (path === "/api/agenda/chat") {
      if (method === "POST") {
        state.bindingWrites++;
        state.noBinding = false; // Canonical creation makes subsequent GET resolve the saved binding.
      }
      const date =
        method === "POST"
          ? route.request().postDataJSON().date
          : url.searchParams.get("date");
      body = {
        threadId: state.noBinding && method === "GET" ? null : `day-${date}`,
      };
    }
    if (path === "/api/proposals/status")
      body = {
        pendingCount: state.pendingApprovals,
        unreadCount: state.pendingApprovals,
      };
    if (path === "/api/companion/status")
      body = { configured: true, available: true };
    if (path.endsWith("/messages") && method === "POST") {
      state.messages.push(route.request().postDataJSON());
      state.items = state.afterSend;
      body = {
        outcome: "submitted",
        run_id: runId,
        thread_id: path.split("/")[4],
      };
    }
    if (path === "/api/agenda/reconciliation") {
      const thread = url.searchParams.get("threadId")!;
      state.reads.push(thread);
      const items = state.threadItems[thread] || state.items;
      if (thread === state.holdThread) {
        state.held = true;
        await new Promise<void>((resolve) => {
          state.release = resolve;
        });
      }
      body = {
        items,
        coverage: {
          enabledAt: 1790000000,
          scope: "New Today exchanges since enabledAt",
          ...state.coverage,
        },
      };
    }
    if (path === "/api/agenda/reconciliation/scan/retry") {
      state.scanRetries.push(route.request().postDataJSON());
      state.coverage = { error: null, retryable: false, state: "queued" };
      body = { queued: true };
    }
    if (
      path !== "/api/agenda/reconciliation/scan/retry" &&
      /\/agenda\/reconciliation\/[^/]+\/retry$/.test(path)
    ) {
      state.retries.push(route.request().postDataJSON());
      if (state.failRetry) return route.abort("failed");
      body = job({
        state: "checking",
        revision: state.items[0].revision + 1,
        attempts: 1,
      });
      state.items = [body];
    }
    await route.fulfill({ json: body }).catch(() => {});
  });
  await page.goto("/?view=today");
  await expect(
    page.getByRole("navigation", { name: "Today pages", exact: true }),
  ).toBeVisible();
  return state;
}
const checks = (page: Page) =>
  page.getByRole("region", { name: "Today conversation checks" });
const summary = (page: Page) =>
  checks(page).locator(".contextual-check-details > summary");
async function visit(page: Page, name: "Overview" | "Chat" | "Schedule") {
  await page
    .getByRole("navigation", { name: "Today pages" })
    .getByRole("link", { name, exact: true })
    .click();
  if (name === "Overview") {
    const details = page.locator(".overview-conversation-checks");
    await expect(details).not.toHaveAttribute("open");
    await details.locator(":scope > summary").click();
  }
}
async function expand(page: Page) {
  await summary(page).click();
}

for (const [outcome, expected] of [
  [
    "no_action",
    "No Today action identified. This does not mark a task complete.",
  ],
  [
    "proposals_pending",
    "Suggestions need review. They are not active tracking yet.",
  ],
  [
    "changes_confirmed_complete",
    "Today changes confirmed by saved action receipts.",
  ],
])
  test(`completed exchange ${outcome} is observable in Overview and absent from chat`, async ({
    page,
  }) => {
    const state = await fixture(page);
    state.afterSend = [
      job({
        state: "done",
        outcome,
        proposalIds: outcome === "proposals_pending" ? ["proposal-1"] : [],
      }),
    ];
    await visit(page, "Chat");
    const draft = page.getByRole("textbox", { name: "Message Leam" });
    await draft.fill("Review this for Today");
    await page
      .getByRole("button", { name: "Send to Leam", exact: true })
      .click();
    await expect(draft).toHaveValue("");
    await expect(checks(page)).toHaveCount(0);
    await expect(page.locator(".approvals-chat-link")).toHaveCount(0);
    await visit(page, "Overview");
    await expect(checks(page)).toContainText(expected);
    await expect(
      checks(page).locator(".contextual-check-details"),
    ).not.toHaveAttribute("open");
    if (outcome === "proposals_pending")
      await expect(summary(page)).toContainText("Review needed");
    await expand(page);
    await expect(checks(page).getByRole("status")).toContainText(expected);
    expect(state.messages).toHaveLength(1);
    expect(state.retries).toEqual([]);
  });

test("Overview never creates a missing day chat and binding lookup cannot leak another day", async ({
  page,
}) => {
  const state = await fixture(page);
  state.noBinding = true;
  await page.getByLabel("Viewing date").fill("2026-09-25");
  await page.locator(".overview-conversation-checks > summary").click();
  await expect(
    page.getByText("No day conversation to check yet.", { exact: true }),
  ).toBeVisible();
  expect(state.bindingWrites).toBe(0);
  await visit(page, "Chat");
  await expect(
    page.getByRole("textbox", { name: "Message Leam" }),
  ).toBeVisible();
  expect(state.bindingWrites).toBe(1);
  await visit(page, "Overview");
  await expect(checks(page)).toBeVisible();
});

test("Overview retains failure indication and exact lost-response retry without resending chat", async ({
  page,
}) => {
  const state = await fixture(page);
  state.items = [
    job({
      state: "failed",
      outcome: "check_failed",
      error: "PRIVATE_BACKEND_DETAIL",
    }),
  ];
  await visit(page, "Schedule");
  await visit(page, "Overview");
  await expect(summary(page)).toContainText("1 failed");
  await expect(summary(page).locator(".semantic-badge")).toHaveAttribute(
    "data-tone",
    "danger",
  );
  await expand(page);
  state.failRetry = true;
  await checks(page)
    .getByRole("button", { name: "Retry check", exact: true })
    .click();
  await expect(checks(page)).toContainText("Retry could not be confirmed");
  state.failRetry = false;
  await checks(page)
    .getByRole("button", { name: "Retry check", exact: true })
    .click();
  await expect(checks(page).getByRole("status")).toContainText(
    "Checking this exchange",
  );
  expect(state.retries).toHaveLength(2);
  expect(state.retries[0]).toEqual(state.retries[1]);
  expect(state.retries[0].revision).toBe(1);
  expect(state.retries[0].requestId).toMatch(/^[0-9a-f-]{36}$/);
  await expect(checks(page)).not.toContainText("PRIVATE_BACKEND_DETAIL");
  expect(state.messages).toEqual([]);
});

test("Overview coverage failure and clarification stay discoverable and keyboard retry remains scoped", async ({
  page,
}) => {
  const state = await fixture(page);
  state.coverage = {
    error: "PRIVATE_PROVIDER_DETAIL",
    retryable: true,
    state: "failed",
  };
  state.items = [
    job({
      state: "done",
      outcome: "clarification_needed",
      clarification: "Which day should this start?",
    }),
  ];
  await visit(page, "Schedule");
  await visit(page, "Overview");
  await expect(summary(page)).toContainText(
    "Status unavailable · Clarification needed",
  );
  await summary(page).focus();
  await page.keyboard.press("Enter");
  await expect(
    checks(page).getByText("Which day should this start?", { exact: true }),
  ).toBeVisible();
  await checks(page)
    .getByRole("button", { name: "Retry conversation check" })
    .click();
  expect(state.scanRetries).toHaveLength(1);
  expect(state.scanRetries[0].threadId).toMatch(/^day-/);
  expect(state.scanRetries[0].requestId).toMatch(/^[0-9a-f-]{36}$/);
  await expect(checks(page)).not.toContainText("PRIVATE_PROVIDER_DETAIL");
  expect(state.messages).toEqual([]);
});

test("old Overview check response cannot overwrite selected day or its chat draft", async ({
  page,
}) => {
  const state = await fixture(page);
  const first = await page.getByLabel("Viewing date").inputValue();
  state.holdThread = `day-${first}`;
  state.threadItems[state.holdThread] = [
    job({
      state: "done",
      outcome: "clarification_needed",
      clarification: "Old private question",
    }),
  ];
  await visit(page, "Schedule");
  await visit(page, "Overview");
  await expect.poll(() => state.held).toBe(true);
  const second = first === "2026-09-22" ? "2026-09-23" : "2026-09-22";
  state.threadItems[`day-${second}`] = [
    job({ id: "check-2", state: "done", outcome: "no_action" }),
  ];
  await page.getByLabel("Viewing date").fill(second);
  await expect(checks(page)).toContainText("No Today action identified");
  await visit(page, "Chat");
  await page
    .getByRole("textbox", { name: "Message Leam" })
    .fill("Second untouched");
  state.release();
  await expect(page.getByRole("textbox", { name: "Message Leam" })).toHaveValue(
    "Second untouched",
  );
  await expect(
    page.getByText("Old private question", { exact: true }),
  ).toHaveCount(0);
  expect(state.messages).toEqual([]);
});

test("check observation pauses when hidden or outside Overview; worker is not restarted from chat", async ({
  page,
}) => {
  const state = await fixture(page);
  state.items = [job({ state: "checking" })];
  await visit(page, "Schedule");
  await visit(page, "Overview");
  await expect(summary(page)).toContainText("Checking…");
  await page.evaluate(() => {
    (window as any).fixtureVisibility = "hidden";
    document.dispatchEvent(new Event("visibilitychange"));
  });
  const hidden = state.reads.length;
  await page.waitForTimeout(3300);
  expect(state.reads).toHaveLength(hidden);
  await page.evaluate(() => {
    (window as any).fixtureVisibility = "visible";
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => state.reads.length).toBeGreaterThan(hidden);
  await visit(page, "Chat");
  const inChat = state.reads.length;
  await page.waitForTimeout(3300);
  expect(state.reads).toHaveLength(inChat);
  await expect(checks(page)).toHaveCount(0);
  expect(state.scanRetries).toEqual([]);
});

for (const viewport of [
  { width: 360, height: 800 },
  { width: 844, height: 390 },
])
  test(`chat has no administrative footer at ${viewport.width} and preserves draft across Overview`, async ({
    page,
  }, info) => {
    await page.setViewportSize(viewport);
    const state = await fixture(page);
    state.pendingApprovals = 2;
    await visit(page, "Chat");
    const draft = page.getByRole("textbox", { name: "Message Leam" });
    await draft.fill("Retain this draft");
    await expect(checks(page)).toHaveCount(0);
    await expect(page.locator(".approvals-chat-link")).toHaveCount(0);
    await page.evaluate(() =>
      window.dispatchEvent(new Event("leam:approvals-changed")),
    );
    await expect(page.getByLabel("2 unread approvals").first()).toBeAttached();
    await info.attach(`chat-${viewport.width}`, {
      body: await page.screenshot(),
      contentType: "image/png",
    });
    await visit(page, "Overview");
    await expect(summary(page)).toBeVisible();
    await visit(page, "Chat");
    await expect(draft).toHaveValue("Retain this draft");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    expect(state.messages).toEqual([]);
  });
