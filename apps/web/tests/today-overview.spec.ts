import { test, expect, type Page } from "@playwright/test";
test.use({ timezoneId: "Europe/London" });
const date = "2026-09-22";
async function fixture(
  page: Page,
  mode: "normal" | "few" | "unavailable" = "normal",
) {
  await page.clock.setFixedTime(new Date("2026-09-22T09:00:00Z"));
  const writes: { path: string; body: any }[] = [];
  const task = (id: string, extra: any = {}) => ({
    id,
    key: `commitment:${id}`,
    title: id,
    kind: "task",
    measure: "boolean",
    target: 1,
    owner: "user",
    status: "active",
    capacityId: "work",
    revision: 4,
    log: { value: 0, done: false, revision: 2 },
    triage: { disposition: "none", revision: 3 },
    ...extra,
  });
  const agenda = {
    date,
    timezone: "Europe/London",
    observedAt: 1790067600,
    partial: true,
    nextOffset: null,
    total: { commitments: 10, events: 2, emails: 3 },
    window: { start: date + "T00:00:00Z", end: date + "T23:59:59Z" },
    commitments: [
      task("Chosen focus", { triage: { disposition: "focus", revision: 2 } }),
      task("Overdue invoice", { dueDate: "2026-09-21" }),
      task("Due proposal", { dueDate: date, owner: "leam" }),
      task("Important task", { priority: "high" }),
      task("Ordinary unscheduled task"),
      task("Completed task", { status: "completed", dueDate: date }),
      task("Hidden task", { hidden: true, dueDate: date }),
      task("Deferred task", {
        triage: { disposition: "later", revision: 1 },
        dueDate: date,
      }),
      task("Declined task", { status: "declined", dueDate: date }),
      task("Daily walk", { kind: "habit" }),
    ],
    events: [
      {
        key: "event:c:meeting",
        title: "Team meeting",
        start: date + "T10:00:00Z",
        end: date + "T11:00:00Z",
        allDay: false,
        triage: { disposition: "none", revision: 0 },
      },
      {
        key: "event:c:ended",
        title: "Ended meeting",
        start: date + "T06:00:00Z",
        end: date + "T07:00:00Z",
        allDay: false,
        triage: { disposition: "none", revision: 0 },
      },
    ],
    emails: [
      {
        key: "email:action",
        subject: "Reply to client",
        receivedAt: date,
        actionability: { state: "action", reviewedBy: "user" },
        triage: { disposition: "none", revision: 0 },
      },
      {
        key: "email:review",
        subject: "Uncertain mail",
        actionability: { state: "review" },
      },
      {
        key: "email:ignore",
        subject: "Newsletter",
        actionability: { state: "ignore" },
      },
    ],
    sources: {
      calendar: { state: "partial", accounts: [], snapshots: [] },
      email: { state: "not_connected", accounts: [] },
    },
  };
  if (mode === "few") {
    agenda.commitments = [task("Due proposal", { dueDate: date })];
    agenda.events = [];
    agenda.emails = [];
  }
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    let data: any = { items: [], data: [], accounts: [], providers: [] };
    if (request.method() !== "GET")
      writes.push({ path, body: request.postDataJSON() });
    if (path === "/api/auth/status")
      data = { authenticated: true, configured: true };
    if (path === "/api/inbox/status")
      data = { unreadCount: 2, total: 2, throughSequence: 2 };
    if (path === "/api/capacities")
      data = { items: [{ id: "work", name: "Work", revision: 1 }] };
    if (path === "/api/agenda") {
      if (mode === "unavailable")
        return route.fulfill({
          status: 503,
          json: { detail: "Saved agenda unavailable" },
        });
      data = {
        ...agenda,
        date: new URL(request.url()).searchParams.get("date"),
      };
    }
    if (path === "/api/agenda/triage") {
      data = { ...request.postDataJSON(), revision: 4 };
      for (const item of agenda.commitments)
        if (item.key === data.key) item.triage = data;
    }
    if (path === "/api/companion/status")
      data = { configured: true, available: true };
    if (path === "/api/agenda/chat") data = { threadId: "day-thread" };
    if (path.endsWith("/timeline")) data = { messages: [], runs: [] };
    await route.fulfill({ json: data });
  });
  return { agenda, writes };
}
async function open(page: Page) {
  await page.goto(`/?view=today#today/plan/${date}`);
}
test("Overview ranks a bounded explained shortlist, excludes unaccepted work and makes no model or mutation call", async ({
  page,
}) => {
  const state = await fixture(page);
  await open(page);
  const suggestions = page.getByRole("region", {
    name: "Suggested next priorities",
  });
  await expect(suggestions.locator("li strong")).toHaveText([
    "Overdue invoice",
    "Due proposal",
    "Important task",
    "Ordinary unscheduled task",
  ]);
  await expect(
    suggestions.getByText("Task · Leam", { exact: true }),
  ).toBeVisible();
  await expect(
    suggestions.getByText("Calendar · appointment", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page
      .getByRole("region", { name: "Daily focus" })
      .getByRole("heading", { name: "Chosen focus" }),
  ).toBeVisible();
  await suggestions.getByText("Why these suggestions?").click();
  await expect(
    suggestions.getByText(/Only loaded items are considered/),
  ).toBeVisible();
  await expect(suggestions.getByText(/source sync may be older/)).toBeVisible();
  expect(state.writes).toEqual([]);
  await page
    .getByRole("button", { name: "Focus Due proposal", exact: true })
    .click();
  expect(state.writes).toEqual([
    {
      path: "/api/agenda/triage",
      body: {
        date,
        timezone: "Europe/London",
        key: "commitment:Due proposal",
        revision: 3,
        disposition: "focus",
      },
    },
  ]);
  await expect(
    page
      .getByRole("region", { name: "Daily focus" })
      .getByRole("heading", { name: "Due proposal" }),
  ).toBeVisible();
});
test("Overview does not pad a short list; visible name changes while old plan links and selected date persist", async ({
  page,
}) => {
  await fixture(page, "few");
  await open(page);
  await expect(
    page
      .getByRole("navigation", { name: "Today pages" })
      .getByRole("link", { name: "Overview", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".overview-shortlist li")).toHaveCount(1);
  await page.getByLabel("Viewing date").fill("2026-09-24");
  await expect(page.locator(".overview-eyebrow")).toContainText("24");
  await page
    .getByRole("button", { name: "Open Schedule", exact: true })
    .click();
  await expect(page).toHaveURL(/#today\/schedule\/2026-09-24$/);
  await page.reload();
  await expect(page.getByLabel("Viewing date")).toHaveValue("2026-09-24");
  await page
    .getByRole("navigation", { name: "Today pages" })
    .getByRole("link", { name: "Overview", exact: true })
    .click();
  await expect(page).toHaveURL(/#today\/plan\/2026-09-24$/);
});
test("unavailable agenda keeps four destinations usable with explicit unknown coverage", async ({
  page,
}) => {
  await fixture(page, "unavailable");
  await open(page);
  await expect(
    page.getByText(
      "Suggestions are unavailable until your saved view returns.",
    ),
  ).toBeVisible();
  await expect(
    page
      .locator(".overview-destination")
      .filter({
        has: page.getByRole("heading", { name: "Schedule", exact: true }),
      }),
  ).toContainText("Calendar view unavailable");
  await expect(
    page
      .locator(".overview-destination")
      .filter({
        has: page.getByRole("heading", { name: "Inbox", exact: true }),
      }),
  ).toContainText("Mail coverage unknown");
  await expect(page.locator(".overview-destination")).toHaveCount(4);
  await page.getByRole("button", { name: "Open Tasks", exact: true }).click();
  await expect(page).toHaveURL(/#today\/boards\//);
});
for (const size of [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
])
  test(`Overview stays navigable at ${size.width}x${size.height}`, async ({
    page,
  }, info) => {
    await page.setViewportSize(size);
    await fixture(page);
    await open(page);
    await expect(page.locator(".overview-shortlist li")).toHaveCount(4);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await page.screenshot({
      path: info.outputPath(`overview-top-${size.width}x${size.height}.png`),
    });
    await page
      .getByRole("heading", { name: "Suggested next priorities" })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath(
        `overview-priorities-${size.width}x${size.height}.png`,
      ),
    });
    const cards = page.locator(".overview-destination");
    for (const card of await cards.all()) {
      await card.scrollIntoViewIfNeeded();
      const box = await card.boundingBox();
      expect(box!.width).toBeGreaterThan(100);
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(size.width);
    }
    await page.screenshot({
      path: info.outputPath(`overview-${size.width}x${size.height}.png`),
      fullPage: true,
    });
    await page.getByRole("button", { name: "Open Chat", exact: true }).click();
    await expect(
      page.getByRole("textbox", { name: "Message Leam" }),
    ).toBeVisible();
  });

for (const viewport of [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
]) {
  test(`priority triage applies custom ordering and retains real completion at ${viewport.width}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const state = await fixture(page);
    let check: any = null;
    let release: (() => void) | null = null;
    await page.route("**/api/agenda/priorities**", async (route) => {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON();
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        const chosen = state.agenda.commitments.find(
          (i) => i.title === "Important task",
        )!;
        check = {
          requestId: body.requestId,
          request: body,
          state: "completed",
          completedAt: 1790067600,
          suggestions: [
            {
              key: chosen.key,
              revision: chosen.revision,
              kind: "task",
              reason: "Marked important",
            },
          ],
          sources: { calendar: "stale", email: "ready" },
        };
        return route.fulfill({ json: check });
      }
      await route.fulfill({ json: { check } });
    });
    await open(page);
    const region = page.getByRole("region", {
      name: "Suggested next priorities",
    });
    await region.getByText("Custom priorities", { exact: true }).click();
    await region
      .getByRole("combobox", { name: "Prioritise", exact: true })
      .selectOption("important");
    await region
      .getByRole("button", { name: "Triage priorities", exact: true })
      .click();
    await expect(
      region.getByRole("button", { name: "Checking saved priorities…" }),
    ).toBeDisabled();
    await expect(region.getByText(/Check complete/)).toHaveCount(0);
    await expect.poll(() => !!release).toBe(true);
    release!();
    await expect(region.getByText(/Check complete/)).toBeVisible();
    expect(check.request.order).toBe("important");
    await expect(region.locator("li strong")).toHaveText(["Important task"]);
    await page.reload();
    await expect(region.locator("li strong")).toHaveText(["Important task"]);
    expect(
      state.writes.filter((w) => /\/triage$|\/messages$/.test(w.path)),
    ).toHaveLength(0);
  });
}

test("triage renders off-page canonical tasks and exclusions persist only for the selected day", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  const excluded: Record<string, string[]> = {};
  const item = { id: "off-page", key: "off-page-key", title: "Task beyond saved agenda", kind: "task", revision: 8, status: "active", stage: "in_progress", owner: "user", triage: { disposition: "none", revision: 0 }, focusEligible: true };
  const check = (day: string) => ({ state: "completed", completedAt: 1790067600, requestId: "saved-check", request: { date: day, timezone: "Europe/London", order: "urgent", owner: "all", capacityId: null }, coverage: { canonicalTasks: 1101, alreadyFocused: 2, excludedForDay: excluded[day]?.length || 0 }, suggestions: excluded[day]?.length ? [] : [{ key: item.key, entityId: item.id, currentItem: item, kind: "task", revision: 8, reason: "In progress" }] });
  await page.route("**/api/agenda/priorities**", async route => {
    const method = route.request().method();
    const body = method !== "GET" ? route.request().postDataJSON() : null;
    const day = body?.date || new URL(route.request().url()).searchParams.get("date")!;
    if (method === "PUT") excluded[day] = body.excluded ? [body.key] : [];
    await route.fulfill({ json: method === "POST" ? { ...check(day), excludedKeys: excluded[day] || [] } : { check: check(day), excludedKeys: excluded[day] || [] } });
  });
  await open(page);
  const region = page.getByRole("region", { name: "Suggested next priorities" });
  await expect(region.locator("li strong")).toHaveText([item.title]);
  await expect(region.getByText(/Checked 1101 canonical/)).toBeVisible();
  await region.getByRole("button", { name: `Exclude ${item.title} for this day` }).click();
  await expect(region.locator("li strong")).toHaveCount(0);
  await page.reload();
  await expect(region.getByText("1 excluded for this day", { exact: true })).toBeVisible();
  await region.getByRole("button", { name: "Triage priorities", exact: true }).click();
  await expect(region.locator("li strong")).toHaveCount(0);
  await page.getByRole("button", { name: "Next day", exact: true }).click();
  await expect(region.locator("li strong")).toHaveText([item.title]);
});

test("triage reload distinguishes persisted pending, failure and retry completion", async ({ page }) => {
  await fixture(page);
  let state = "pending";
  const request = { requestId: "retry-check", date, timezone: "Europe/London", order: "urgent", owner: "all", capacityId: null };
  await page.route("**/api/agenda/priorities**", async route => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON()).toEqual(request);
      state = "completed";
    }
    const check = { requestId: request.requestId, request, state, error: state === "failed" ? "Could not check saved priorities. Retry this check." : undefined, completedAt: 1790067600, suggestions: [] };
    await route.fulfill({ json: route.request().method() === "POST" ? check : { check, excludedKeys: [] } });
  });
  await open(page);
  const status = page.getByRole("status", { name: "Priority triage status" });
  await expect(status).toContainText("Check pending");
  await page.reload();
  await expect(status).toContainText("Check pending");
  state = "failed";
  await page.getByRole("button", { name: "Refresh check status" }).click();
  await expect(status).toContainText("Check needs attention");
  await expect(page.getByRole("alert").filter({ hasText: "Could not check saved priorities" })).toBeVisible();
  await page.getByRole("button", { name: "Retry priority check" }).click();
  await expect(status).toContainText("Triage completed");
});

test("changing day during exclusion resets controls and ignores the older response", async ({ page }) => {
  await fixture(page);
  let release: (() => void) | null = null;
  await page.route("**/api/agenda/priorities**", async route => {
    if (route.request().method() === "PUT") {
      await new Promise<void>(resolve => { release = resolve; });
      return route.fulfill({ json: { check: null, excludedKeys: ["commitment:Important task"] } });
    }
    await route.fulfill({ json: { check: null, excludedKeys: [] } });
  });
  await open(page);
  const region = page.getByRole("region", { name: "Suggested next priorities" });
  const remove = region.getByRole("button", { name: "Exclude Important task for this day", exact: true });
  await remove.click();
  await expect(remove).toBeDisabled();
  await expect.poll(() => !!release).toBe(true);
  await page.getByRole("button", { name: "Next day", exact: true }).click();
  await expect(remove).toBeEnabled();
  release!();
  await expect(remove).toBeEnabled();
  await expect(region.getByText("1 excluded for this day", { exact: true })).toHaveCount(0);
  await expect(region.locator(".today-priority-controls > button + .today-triage-status")).toBeVisible();
});

test("old mixed-source triage receipts cannot render email appointments habits or goals", async ({ page }) => {
  const fixtureState = await fixture(page);
  const task = fixtureState.agenda.commitments.find(item => item.title === "Important task")!;
  await page.route("**/api/agenda/priorities**", route => route.fulfill({ json: { excludedKeys: [], check: { state: "completed", completedAt: 1790067600, suggestions: [
    { key: task.key, kind: "task", revision: task.revision, reason: "Marked important" },
    { key: "email:action", kind: "email", reason: "Old mail" },
    { key: "event:c:meeting", kind: "appointment", reason: "Old meeting" },
    { key: "commitment:Daily walk", kind: "habit", reason: "Old habit" },
    { entityId: "goal", key: "goal", kind: "task", currentItem: { id: "goal", key: "goal", kind: "goal", status: "active", title: "Old goal" }, reason: "Old goal" }
  ] } } }));
  await open(page);
  await expect(page.getByRole("region", { name: "Suggested next priorities" }).locator("li strong")).toHaveText(["Important task"]);
});
