import { test, expect, type Page } from "@playwright/test";
import { navigate } from "./navigation";
// Canonical /agenda contract: daily commitment/log, opaque key and independent triage.
async function fixture(page: Page) {
  const state = {
    hold: false,
    done: false,
    paginated: false,
    mail: false,
    hidden: false,
    visibilityRevision: 0,
    visibilityStale: false,
    eventMissing: false,
    visibilityWrites: [] as any[],
    release: () => {},
    starts: [] as any[],
    writes: [] as any[],
    disposition: "none",
    revision: 0,
    triageRows: {} as Record<string, any>,
  };
  const item = {
    id: "walk",
    title: "Walk",
    revision: 1,
    kind: "habit",
    measure: "minutes",
    target: 20,
    status: "active",
    timezone: "Europe/London",
    notes: "",
    capacityId: "health",
  };
  await page.route("**/api/**", async (route) => {
    const u = new URL(route.request().url()),
      method = route.request().method();
    let data: any = { items: [], data: [], providers: [] };
    if (u.pathname === "/api/auth/status")
      data = { authenticated: true, configured: true };
    if (u.pathname === "/api/capacities")
      data = {
        items: [
          {
            id: "health",
            name: "Health",
            note: "Movement and rest",
            record: "",
            revision: 1,
          },
        ],
      };
    if (u.pathname === "/api/commitments") data = { items: [item] };
    if (u.pathname === "/api/today")
      data = {
        items: [
          {
            ...item,
            date: "2026-09-21",
            log: { value: 7, done: false, revision: 1 },
          },
        ],
      };
    if (u.pathname === "/api/agenda") {
      const date = u.searchParams.get("date")!;
      data = {
        date,
        timezone: u.searchParams.get("timezone"),
        observedAt: Date.now() / 1000,
        window: {
          start: date + "T00:00:00+00:00",
          end: date + "T23:59:59+00:00",
        },
        total: { commitments: 1, events: 1 },
        partial: true,
        nextOffset: null,
        commitments: [
          {
            ...item,
            key: "commitment:walk",
            date,
            log: {
              value: date.endsWith("22") ? 2 : 7,
              done: state.done,
              revision: 1,
            },
            triage: {
              revision: state.revision,
              disposition: state.disposition,
            },
          },
        ],
        events: [
          {
            key: "event:calendar:event",
            visibility: { hidden: false, revision: state.visibilityRevision },
            calendarId: "calendar",
            eventId: "event",
            title: "Dentist",
            allDay: false,
            start: date + "T09:00:00Z",
            end: date + "T09:30:00Z",
            url: "https://calendar.google.com/fixture",
            triage: { revision: 0, disposition: "none" },
          },
        ],
        sources: {
          calendar: {
            state: "partial",
            accounts: [{ id: "a", calendarsListedAt: 1 }],
            snapshots: [
              {
                calendarId: "calendar",
                name: "Personal",
                coverage: "partial",
                state: "error",
                syncedAt: 1700000000,
                error: "Provider unavailable",
              },
            ],
          },
          email: { state: "not_connected", reason: "mail_consent_not_granted" },
        },
      };
    }
    if (u.pathname === "/api/agenda") {
      data.hiddenEventCount = state.hidden && !state.eventMissing ? 1 : 0;
      if (state.hidden || state.eventMissing) {
        data.events = [];
        data.total.events = 0;
      }
    }
    if (u.pathname === "/api/agenda/calendar-visibility") {
      if (method === "PUT") {
        const body = route.request().postDataJSON();
        state.visibilityWrites.push(body);
        if (state.visibilityStale || body.revision !== state.visibilityRevision)
          return route.fulfill({
            status: 409,
            json: {
              detail: "Calendar visibility changed; refresh before editing",
            },
          });
        state.hidden = body.hidden;
        state.visibilityRevision++;
        data = {
          key: body.key,
          hidden: state.hidden,
          revision: state.visibilityRevision,
        };
      } else
        data = {
          items: state.hidden
            ? [
                {
                  key: "event:calendar:event",
                  calendarId: "calendar",
                  eventId: "event",
                  title: "Dentist",
                  calendarName: "Personal",
                  hidden: true,
                  revision: state.visibilityRevision,
                  sourceAvailable: !state.eventMissing,
                },
              ]
            : [],
          total: state.hidden ? 1 : 0,
          nextOffset: null,
        };
    }
    if (u.pathname === "/api/agenda" && state.mail) {
      data.total.emails = 1;
      data.emails = [
        {
          key: "email:account:message",
          id: "message",
          threadId: "mail-thread",
          accountId: "a",
          subject: "Reply to the venue",
          from: "Venue <venue@example.test>",
          snippet: "Please confirm the booking.",
          receivedAt: "2026-09-21T09:00:00+00:00",
          unread: true,
          important: false,
          url: "https://mail.google.com/mail/u/0/#inbox/message",
          syncedAt: 1700000000,
          sourceState: "ready",
          triage: state.triageRows[
            u.searchParams.get("date") + ":email:account:message"
          ] || { revision: 0, disposition: "none" },
        },
      ];
      data.sources.email = {
        state: "attention",
        supportedProvider: "google",
        truncated: true,
        accounts: [
          {
            accountId: "a",
            identity: "fixture@example.test",
            provider: "google",
            state: "ready",
            granted: true,
            syncedAt: 1700000000,
            stale: true,
            truncated: true,
            error: null,
            windowDays: 30,
            limit: 20,
          },
        ],
      };
    }
    if (u.pathname === "/api/agenda" && state.paginated) {
      if (u.searchParams.get("offset") === "0") {
        data.events = [];
        data.nextOffset = 50;
      } else {
        data.commitments = [];
        data.nextOffset = null;
      }
    }
    if (u.pathname === "/api/agenda/triage") {
      const body = route.request().postDataJSON();
      state.writes.push(body);
      if (state.hold)
        await new Promise<void>((r) => {
          state.release = r;
        });
      state.disposition = body.disposition;
      state.revision++;
      data = {
        key: body.key,
        disposition: state.disposition,
        revision: state.revision,
      };
      state.triageRows[body.date + ":" + body.key] = data;
    }
    if (u.pathname === "/api/agenda/chat" && method === "POST") {
      state.starts.push(route.request().postDataJSON());
      data = { threadId: "day-thread" };
    }
    if (u.pathname === "/api/companion/threads/day-thread/timeline")
      data = { messages: [], runs: [] };
    if (u.pathname === "/api/companion/status")
      data = { configured: true, available: true };
    await route.fulfill({ json: data });
  });
  return state;
}
for (const width of [390, 1440])
  test(`agenda and Goals remain usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await fixture(page);
    await page.goto("/?view=today");
    await expect(
      page.getByRole("heading", { name: "Today", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "Dentist" })).toBeVisible();
    await expect(
      page.getByText("Email is not connected to Leam.", { exact: false }),
    ).toBeVisible();
    await page.getByText("Calendar sources · partial", { exact: true }).click();
    await expect(
      page.getByText("Provider unavailable", { exact: false }),
    ).toBeVisible();
    expect(state.starts).toHaveLength(0);
    await page
      .getByLabel("Plan Walk", { exact: true })
      .getByRole("button", { name: "Focus", exact: true })
      .click();
    await expect(
      page
        .getByRole("region", { name: "Daily focus" })
        .getByRole("heading", { name: "Walk", exact: true }),
    ).toBeVisible();
    expect(state.writes[0]).toMatchObject({
      key: "commitment:walk",
      revision: 0,
      disposition: "focus",
    });
    await navigate(page, "Today");
    await navigate(page, "Goals");
    await expect(
      page.getByRole("heading", { name: "Goals", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Plan a commitment", exact: true })
      .click();
    await page
      .getByLabel("Commitment title", { exact: true })
      .fill("Preserved draft");
    await page.getByRole("button", { name: "Close commitment editor" }).click();
    await page
      .getByRole("button", { name: "Plan a commitment", exact: true })
      .click();
    await expect(
      page.getByLabel("Commitment title", { exact: true }),
    ).toHaveValue("Preserved draft");
    await page.keyboard.press("Escape");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await navigate(page, "Across Leam");
    await expect(
      page.getByRole("heading", { name: "Across Leam" }),
    ).toBeVisible();
  });
test("held daily decision does not rewrite a different selected day", async ({
  page,
}) => {
  const state = await fixture(page);
  state.hold = true;
  await page.goto("/?view=today");
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await page
    .getByLabel("Plan Walk", { exact: true })
    .getByRole("button", { name: "Focus", exact: true })
    .click();
  await expect.poll(() => state.writes.length).toBe(1);
  await page.getByLabel("Viewing date").fill("2026-09-22");
  await expect(
    page.getByRole("spinbutton", { name: "Progress for Walk" }),
  ).toHaveValue("2");
  state.release();
  await page.waitForTimeout(100);
  await expect(
    page
      .getByRole("region", { name: "Daily focus" })
      .getByRole("heading", { name: "Walk" }),
  ).toHaveCount(0);
});
test("day chat opens only on request without a model submission", async ({
  page,
}) => {
  const state = await fixture(page);
  const modelPosts: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/messages"))
      modelPosts.push(r.url());
  });
  await page.goto("/?view=today");
  await page.getByRole("button", { name: "Chat about this day" }).click();
  await expect.poll(() => state.starts.length).toBe(1);
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByLabel("Viewing date").fill("2026-09-22");
  await page.getByRole("button", { name: "Chat about this day" }).click();
  await expect.poll(() => state.starts.length).toBe(2);
  expect(state.starts[1].date).toBe("2026-09-22");
  expect(modelPosts).toHaveLength(0);
});

test("explicit calendar sync keeps partial failures visible and refreshes saved agenda", async ({
  page,
}) => {
  await fixture(page);
  const calls: any[] = [];
  let refreshed = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/agenda") refreshed++;
  });
  await page.route("**/api/calendar**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    calls.push({
      path,
      body:
        route.request().method() === "POST"
          ? route.request().postDataJSON()
          : null,
    });
    if (path === "/api/calendar")
      return route.fulfill({
        json: {
          accounts: [{ id: "a", state: "connected" }],
          items: [
            { id: "good", accountId: "a" },
            { id: "bad", accountId: "a" },
          ],
        },
      });
    if (path === "/api/calendar/bad/sync")
      return route.fulfill({
        status: 502,
        json: { detail: "Provider unavailable; old snapshot retained" },
      });
    return route.fulfill({ json: { items: [], syncedAt: Date.now() / 1000 } });
  });
  await page.goto("/?view=today");
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await expect(page.getByRole("heading", { name: "Dentist" })).toBeVisible();
  const before = refreshed;
  await page
    .getByRole("button", { name: "Sync calendars", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText(
    "1 calendar synchronized for this day. 1 sync request failed",
  );
  await expect.poll(() => refreshed).toBeGreaterThan(before);
  await expect(page.getByRole("heading", { name: "Dentist" })).toBeVisible();
  expect(
    calls.filter((c) => c.path.endsWith("/sync")).map((c) => c.path),
  ).toEqual([
    "/api/calendar/accounts/a/sync",
    "/api/calendar/good/sync",
    "/api/calendar/bad/sync",
  ]);
  expect(calls.find((c) => c.path === "/api/calendar/good/sync").body).toEqual({
    start: "2026-09-21T00:00:00+00:00",
    end: "2026-09-21T23:59:59+00:00",
  });
});

test("changing day retires in-flight sync UI and stops further sync requests", async ({
  page,
}) => {
  await fixture(page);
  let release!: () => void;
  const calls: string[] = [];
  await page.route("**/api/calendar**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    calls.push(path);
    if (path === "/api/calendar")
      return route.fulfill({
        json: { accounts: [{ id: "a", state: "connected" }], items: [] },
      });
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    try {
      await route.fulfill({ json: { items: [] } });
    } catch {
      /* Aborted page request. */
    }
  });
  await page.goto("/?view=today");
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await page
    .getByRole("button", { name: "Sync calendars", exact: true })
    .click();
  await expect
    .poll(() => calls.includes("/api/calendar/accounts/a/sync"))
    .toBe(true);
  await page.getByLabel("Viewing date").fill("2026-09-22");
  await expect(
    page.getByRole("spinbutton", { name: "Progress for Walk" }),
  ).toHaveValue("2");
  release();
  await expect(
    page.getByRole("button", { name: "Sync calendars", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByText(/calendar.*synchronized for this day/),
  ).toHaveCount(0);
  expect(calls).toEqual(["/api/calendar", "/api/calendar/accounts/a/sync"]);
});

test("completed commitments start collapsed and can be reopened with canonical progress revision", async ({
  page,
}) => {
  const state = await fixture(page);
  state.done = true;
  const writes: any[] = [];
  await page.route("**/api/commitments/walk/progress/*", async (route) => {
    writes.push(route.request().postDataJSON());
    state.done = false;
    await route.fulfill({ json: { saved: true } });
  });
  await page.goto("/?view=today");
  await expect(
    page.getByRole("heading", { name: "Walk", exact: true }),
  ).not.toBeVisible();
  await page.getByText("Completed · 1", { exact: true }).click();
  await page.getByRole("button", { name: "Reopen Walk", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "Eligible commitments" })
      .getByRole("heading", { name: "Walk" }),
  ).toBeVisible();
  expect(writes).toEqual([
    { operation: "toggle", revision: 1, commitmentRevision: 1 },
  ]);
});

test("later-page calendar events never appear as an empty day", async ({
  page,
}) => {
  const state = await fixture(page);
  state.paginated = true;
  await page.goto("/?view=today");
  await expect(
    page.getByText(
      "More calendar events are available on the next agenda pages.",
    ),
  ).toBeVisible();
  await expect(page.getByText("No saved events for this day.")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Load more calendar items", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: "Dentist" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Walk", exact: true }),
  ).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Load more agenda items" }),
  ).toHaveCount(0);
});

test("saved recent email uses explicit mail provenance and the same local daily triage", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=today");
  const inbox = page.getByRole("region", { name: "Recent email inbox" });
  await expect(
    inbox.getByRole("heading", { name: "Reply to the venue" }),
  ).toBeVisible();
  await expect(
    inbox.getByText(/independent of this agenda date/),
  ).toBeVisible();
  await expect(page.getByText(/Email is not connected to Leam/)).toHaveCount(0);
  await expect(
    inbox.getByRole("link", { name: "Open email in Gmail" }),
  ).toHaveAttribute("href", "https://mail.google.com/mail/u/0/#inbox/message");
  await inbox.getByText("Email sources · attention", { exact: true }).click();
  await expect(inbox.getByText(/Saved data is stale/)).toBeVisible();
  await inbox.getByRole("button", { name: "Focus", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "Daily focus" })
      .getByRole("heading", { name: "Reply to the venue" }),
  ).toBeVisible();
  expect(state.writes[0]).toMatchObject({
    key: "email:account:message",
    revision: 0,
    disposition: "focus",
  });
  expect(state.starts).toHaveLength(0);
});

test("Today mail sync rechecks grants, skips reconnects and reports partial failures without losing saved mail", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  const calls: any[] = [];
  await page.route("**/api/email**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/email")
      return route.fulfill({
        json: {
          accounts: [
            { accountId: "ready", granted: true, state: "never_synced" },
            { accountId: "reconnect", granted: true, state: "reconnect" },
            {
              accountId: "calendar-only",
              granted: false,
              state: "not_connected",
            },
            { accountId: "failed", granted: true, state: "error" },
          ],
        },
      });
    calls.push({
      path,
      method: route.request().method(),
      body: route.request().postDataJSON(),
    });
    if (path.includes("/failed/"))
      return route.fulfill({
        status: 502,
        json: { detail: "Mailbox unavailable" },
      });
    return route.fulfill({
      json: { state: "ready", syncedAt: Date.now() / 1000, error: null },
    });
  });
  await page.goto("/?view=today");
  await page.getByRole("button", { name: "Sync mail", exact: true }).click();
  await expect(page.getByRole("status")).toContainText(
    "1 Gmail account synchronized. 1 account could not sync",
  );
  await expect(
    page.getByRole("heading", { name: "Reply to the venue" }),
  ).toBeVisible();
  expect(calls).toEqual([
    { path: "/api/email/accounts/ready/sync", method: "POST", body: {} },
    { path: "/api/email/accounts/failed/sync", method: "POST", body: {} },
  ]);
});

test("calendar-only access never enables mail sync or implies mailbox consent", async ({
  page,
}) => {
  await fixture(page);
  const calls: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/email"))
      calls.push(request.url());
  });
  await page.goto("/?view=today");
  await expect(
    page.getByRole("button", { name: "Sync mail", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText(/Enable read-only Gmail access in Email settings/),
  ).toBeVisible();
  expect(calls).toEqual([]);
});

test("date change aborts mail continuation before syncing a second account", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  let release!: () => void;
  const calls: string[] = [];
  await page.route("**/api/email**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/email")
      return route.fulfill({
        json: {
          accounts: [
            { accountId: "a", granted: true, state: "ready" },
            { accountId: "b", granted: true, state: "ready" },
          ],
        },
      });
    calls.push(path);
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    try {
      await route.fulfill({ json: { state: "ready", error: null } });
    } catch {
      /* User navigation aborted the browser request. */
    }
  });
  await page.goto("/?view=today");
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await page.getByRole("button", { name: "Sync mail", exact: true }).click();
  await expect.poll(() => calls.length).toBe(1);
  await page.getByLabel("Viewing date").fill("2026-09-22");
  await expect(
    page.getByRole("spinbutton", { name: "Progress for Walk" }),
  ).toHaveValue("2");
  release();
  await expect(
    page.getByRole("button", { name: "Sync mail", exact: true }),
  ).toBeEnabled();
  await expect(page.getByText(/Gmail account.*synchronized/)).toHaveCount(0);
  expect(calls).toEqual(["/api/email/accounts/a/sync"]);
});

test("Hide always persists across days and Show again retires an unavailable source preference without resurrecting it", async ({
  page,
}) => {
  const state = await fixture(page);
  const providerCalls: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/calendar/"))
      providerCalls.push(request.url());
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=today");
  await page.getByRole("button", { name: "Hide always", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Dentist", exact: true }),
  ).toHaveCount(0);
  expect(state.visibilityWrites).toEqual([
    { key: "event:calendar:event", revision: 0, hidden: true },
  ]);
  await page.getByLabel("Viewing date").fill("2026-09-22");
  await expect(
    page.getByRole("heading", { name: "Dentist", exact: true }),
  ).toHaveCount(0);
  state.eventMissing = true;
  await page.reload();
  await page.getByText("Hidden calendar events", { exact: true }).click();
  await expect(
    page.getByText(/No longer in the saved calendar snapshot/),
  ).toBeVisible();
  await page.getByRole("button", { name: "Show again", exact: true }).click();
  await expect(
    page.getByText("No calendar events are hidden.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Dentist", exact: true }),
  ).toHaveCount(0);
  expect(state.visibilityWrites[1]).toEqual({
    key: "event:calendar:event",
    revision: 1,
    hidden: false,
  });
  expect(providerCalls).toEqual([]);
});

test("stale persistent hide fails visibly and retains the calendar entry", async ({
  page,
}) => {
  const state = await fixture(page);
  state.visibilityStale = true;
  await page.goto("/?view=today");
  await page.getByRole("button", { name: "Hide always", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Calendar visibility changed",
  );
  await expect(
    page.getByRole("heading", { name: "Dentist", exact: true }),
  ).toBeVisible();
  expect(state.hidden).toBe(false);
  expect(state.visibilityWrites).toHaveLength(1);
});

test("mobile Focus confirms the saved priority, brings it into view and supports removal", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto("/?view=today");
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await page
    .getByLabel("Plan Walk", { exact: true })
    .getByRole("button", { name: "Focus", exact: true })
    .click();
  const focus = page.getByRole("region", { name: "Daily focus" });
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "Walk added to Focus for 2026-09-21" }),
  ).toBeVisible();
  await expect(
    focus.getByRole("button", { name: "Remove from focus", exact: true }),
  ).toBeVisible();
  await expect(
    focus.getByRole("button", { name: "Complete Walk", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Walk", exact: true }),
  ).toHaveCount(1);
  await expect
    .poll(async () => (await focus.boundingBox())!.y)
    .toBeGreaterThanOrEqual(0);
  await expect
    .poll(async () => (await focus.boundingBox())!.y)
    .toBeLessThan(200);
  await focus
    .getByRole("button", { name: "Remove from focus", exact: true })
    .click();
  await expect(
    focus.getByRole("heading", { name: "Walk", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("status").filter({ hasText: "Walk removed from Focus" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Walk", exact: true }),
  ).toHaveCount(1);
});

test("Today section collapse persists on mobile and adding Focus reopens the priority list", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto("/?view=today");
  const focus = page.getByRole("region", { name: "Daily focus" });
  const schedule = page.getByRole("region", { name: "Daily schedule" });
  const inbox = page.getByRole("region", { name: "Recent email inbox" });
  const commitments = page.getByRole("region", {
    name: "Eligible commitments",
  });
  for (const section of [focus, schedule, inbox, commitments]) {
    await section.locator(":scope > details > summary").click();
    await expect(section.locator(":scope > details")).not.toHaveAttribute(
      "open",
    );
  }
  await page.reload();
  for (const section of [focus, schedule, inbox, commitments])
    await expect(section.locator(":scope > details")).not.toHaveAttribute(
      "open",
    );
  await commitments.locator(":scope > details > summary").click();
  await commitments.getByRole("button", { name: "Focus", exact: true }).click();
  await expect(focus.locator(":scope > details")).toHaveAttribute("open", "");
  await expect(
    focus.getByRole("heading", { name: "Walk", exact: true }),
  ).toBeVisible();
  await expect(schedule.locator(":scope > details")).not.toHaveAttribute(
    "open",
  );
  const preference = await page.evaluate(() =>
    localStorage.getItem("leam:today-sections:v1"),
  );
  expect(JSON.parse(preference!)).toEqual({
    focus: true,
    schedule: false,
    email: false,
    commitments: true,
  });
  await page.reload();
  await expect(
    focus.getByRole("heading", { name: "Walk", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
