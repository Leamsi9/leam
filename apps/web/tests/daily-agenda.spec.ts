import { test, expect, type Page } from "@playwright/test";
import { navigate } from "./navigation";
async function todayPage(page: Page, name: string) {
  await page
    .getByRole("navigation", { name: "Today pages" })
    .getByRole("link", { name, exact: true })
    .click();
  if (name === "Inbox") {
    // Existing mail regressions intentionally inspect controls now collapsed in
    // the unified Inbox; the dedicated Inbox suite verifies compact defaults.
    await page.getByText("Review saved mail", { exact: true }).click();
    for (const summary of await page.locator(".inbox-mail-card > details > summary").all()) await summary.click();
  }
}
async function dayOptions(page: Page) {
  await page.getByRole("button", { name: "Day options", exact: true }).click();
}

// Canonical /agenda contract: daily commitment/log, opaque key and independent triage.
async function fixture(page: Page) {
  await page.clock.setFixedTime(new Date("2026-09-21T08:00:00Z"));
  const state = {
    hold: false,
    done: false,
    measuredValue: 7,
    paginated: false,
    mail: false,
    mixedMail: false,
    unclassifiedMail: false,
    missingClassification: false,
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
            log: { value: 7, done: state.done, revision: 1 },
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
              value: date.endsWith("22") ? 2 : state.measuredValue,
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
            triage: state.triageRows[date + ":event:calendar:event"] || {
              revision: 0,
              disposition: "none",
            },
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
          actionability: {
            state: "action",
            kind: "reply",
            basis: "explicit",
            reason: "The venue asks you to confirm a booking.",
            action: "Confirm the booking",
            evidence: "Please confirm the booking.",
            classifiedAt: 1700000000,
          },
          triage: state.triageRows[
            u.searchParams.get("date") + ":email:account:message"
          ] || { revision: 0, disposition: "none" },
        },
      ];
      if (state.mixedMail) {
        const base = data.emails[0];
        data.emails.push(
          {
            ...base,
            key: "email:newsletter",
            subject: "Weekly newsletter",
            actionability: { state: "ignore", reason: "Newsletter" },
          },
          {
            ...base,
            key: "email:unclear",
            subject: "Unclear obligation",
            actionability: { state: "review", reason: "Insufficient evidence" },
          },
          {
            ...base,
            key: "email:pending",
            subject: "Not classified yet",
            actionability: undefined,
          },
        );
      }
      data.sources.email = {
        classification: {
          state: state.mixedMail ? "unclassified" : "ready",
          counts: {
            action: 1,
            ignore: state.mixedMail ? 1 : 0,
            review: state.mixedMail ? 1 : 0,
            pending: state.mixedMail ? 1 : 0,
          },
        },
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
    if (u.pathname === "/api/agenda" && state.mail && state.unclassifiedMail) {
      data.emails = [];
      data.total.emails = 0;
      data.sources.email.classification = {
        state: "unclassified",
        counts: { action: 0, ignore: 0, review: 0, pending: 1 },
        error: null,
      };
    }
    if (
      u.pathname === "/api/agenda" &&
      state.mail &&
      state.missingClassification
    ) {
      delete data.sources.email.classification;
      for (const item of data.emails) delete item.actionability;
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
    await todayPage(page, "Schedule");
    await expect(page.getByRole("heading", { name: "Dentist" })).toBeVisible();
    await todayPage(page, "Inbox");
    await expect(
      page.getByText("Connect Gmail in Email settings to include mail.", { exact: false }),
    ).toBeVisible();
    await todayPage(page, "Schedule");
    await page.getByText("Calendar sources · partial", { exact: true }).click();
    await expect(
      page.getByText("Provider unavailable", { exact: false }),
    ).toBeVisible();
    expect(state.starts).toHaveLength(0);
    await todayPage(page, "Overview");
    await page
      .getByRole("button", { name: "Focus Walk", exact: true })
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
    await navigate(page, "Settings");
    await expect(page.locator(".context-overview > summary")).toHaveText("Across Leam");
  });
test("held daily decision does not rewrite a different selected day", async ({
  page,
}) => {
  const state = await fixture(page);
  state.hold = true;
  await page.goto("/?view=today");
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await page
    .getByRole("button", { name: "Focus Walk", exact: true })
    .click();
  await expect.poll(() => state.writes.length).toBe(1);
  await page.getByLabel("Viewing date").fill("2026-09-22");
  await expect(page.getByRole("button", { name: "Focus Walk", exact: true })).toBeVisible();
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
  await todayPage(page, "Chat");
  await expect.poll(() => state.starts.length).toBe(1);
  await expect(
    page.getByRole("textbox", { name: "Message Leam" }),
  ).toBeVisible();
  await todayPage(page, "Overview");
  await expect(page.getByRole("textbox", { name: "Message Leam" })).toHaveCount(
    0,
  );
  await page.getByLabel("Viewing date").fill("2026-09-22");
  await todayPage(page, "Chat");
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
  await todayPage(page, "Schedule");
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await expect(page.getByRole("heading", { name: "Dentist" })).toBeVisible();
  const before = refreshed;
  await page
    .getByRole("button", { name: "Sync calendars", exact: true })
    .click();
  await expect(page.getByRole("status").filter({ hasText: "1 calendar synchronized" })).toContainText(
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
  await todayPage(page, "Schedule");
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await page
    .getByRole("button", { name: "Sync calendars", exact: true })
    .click();
  await expect
    .poll(() => calls.includes("/api/calendar/accounts/a/sync"))
    .toBe(true);
  await page.getByLabel("Viewing date").fill("2026-09-22");
  await todayPage(page, "Overview");
  await page.getByRole("button", { name: "Focus Walk", exact: true }).click();
  await expect(
    page.getByRole("spinbutton", { name: "Progress for Walk" }),
  ).toHaveValue("2");
  await todayPage(page, "Schedule");
  release();
  await expect(
    page.getByRole("button", { name: "Sync calendars", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByText(/calendar.*synchronized for this day/),
  ).toHaveCount(0);
  expect(calls).toEqual(["/api/calendar", "/api/calendar/accounts/a/sync"]);
});

test("completed commitments stay out of Overview and reopen through Goals with canonical progress revision", async ({
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
  await navigate(page, "Goals");
  await page.getByRole("button", { name: "Reopen Walk", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Complete Walk", exact: true }),
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
  await todayPage(page, "Schedule");
  await expect(
    page.getByText(
      "More calendar events are available on the next agenda pages.",
    ),
  ).toBeVisible();
  await expect(page.getByText("No saved events for this day.")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Load more agenda items", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: "Dentist" })).toBeVisible();
  await todayPage(page, "Overview");
  await expect(page.getByRole("button", { name: "Focus Walk", exact: true })).toHaveCount(1);
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
  await todayPage(page, "Inbox");
  const inbox = page.getByRole("region", { name: "Inbox", exact: true });
  await expect(
    inbox.getByRole("heading", { name: "Reply to the venue" }),
  ).toBeVisible();
  await page.getByText("Inbox settings & sources", { exact: true }).click();
  await expect(page.getByText(/independent of this agenda date/)).toBeVisible();
  await expect(page.getByText(/Email is not connected to Leam/)).toHaveCount(0);
  await expect(
    inbox.getByRole("link", { name: "Open email in Gmail" }),
  ).toHaveAttribute("href", "https://mail.google.com/mail/u/0/#inbox/message");
  await page.getByText("Email sources · attention", { exact: true }).click();
  await expect(page.getByText(/Saved data is stale/)).toBeVisible();
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
  await todayPage(page, "Inbox");
  await page.getByRole("button", { name: "Sync mail", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Gmail account synchronized" }),
  ).toContainText("1 Gmail account synchronized. 1 account could not sync");
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
  await todayPage(page, "Inbox");
  await expect(
    page.getByRole("button", { name: "Sync mail", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText(/Connect Gmail in Email settings to include mail/),
  ).toBeVisible();
  expect(calls).toEqual([]);
});

test("Rebuild action inbox uses the explicit local refill route and promises no Gmail changes", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  const calls: string[] = [];
  await page.route("**/api/email**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/email")
      return route.fulfill({
        json: {
          accounts: [{ accountId: "a", granted: true, state: "ready" }],
        },
      });
    calls.push(path);
    return route.fulfill({
      json: { state: "ready", syncedAt: Date.now() / 1000, limit: 100 },
    });
  });
  let approved = false,
    dialogs = 0;
  page.on("dialog", async (dialog) => {
    expect(dialog.message()).toContain("your local mail review choices");
    dialogs++;
    await (approved ? dialog.accept() : dialog.dismiss());
  });
  await page.goto("/?view=today");
  await todayPage(page, "Inbox");
  await page.getByText("Inbox settings & sources", { exact: true }).click();
  await page
    .getByRole("button", { name: "Rebuild action inbox", exact: true })
    .click();
  await expect.poll(() => dialogs).toBe(1);
  expect(calls).toEqual([]);
  approved = true;
  await page
    .getByRole("button", { name: "Rebuild action inbox", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Gmail is unchanged" }),
  ).toContainText("up to 100 recent messages");
  expect(calls).toEqual(["/api/email/accounts/a/rebuild"]);
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
  await todayPage(page, "Inbox");
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await page.getByRole("button", { name: "Sync mail", exact: true }).click();
  await expect.poll(() => calls.length).toBe(1);
  await page.getByLabel("Viewing date").fill("2026-09-22");
  await todayPage(page, "Overview");
  await page.getByRole("button", { name: "Focus Walk", exact: true }).click();
  await expect(
    page.getByRole("spinbutton", { name: "Progress for Walk" }),
  ).toHaveValue("2");
  await todayPage(page, "Inbox");
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
  await todayPage(page, "Schedule");
  await page.getByText("Event details", { exact: true }).click();
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
  await todayPage(page, "Schedule");
  await page.getByText("Event details", { exact: true }).click();
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
    .getByRole("button", { name: "Focus Walk", exact: true })
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
    page.getByRole("button", { name: "Focus Walk", exact: true }),
  ).toHaveCount(1);
});

test("Today page and date survive reload and Focus returns to the priority list", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto("/?view=today");
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await todayPage(page, "Schedule");
  await page.reload();
  await expect(page.getByLabel("Viewing date")).toHaveValue("2026-09-21");
  await expect(
    page
      .getByRole("navigation", { name: "Today pages" })
      .getByRole("link", { name: "Schedule", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await page
    .getByLabel("Plan Dentist", { exact: true })
    .getByRole("button", { name: "Focus", exact: true })
    .click();
  const focus = page.getByRole("region", { name: "Daily focus" });
  await expect(
    focus.getByRole("heading", { name: "Dentist", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    focus.getByRole("heading", { name: "Dentist", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Daily schedule" }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("Action inbox admits only classified obligations and exposes uncertainty without rendering excluded cards", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  state.mixedMail = true;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=today");
  await todayPage(page, "Inbox");
  const inbox = page.getByRole("region", { name: "Inbox", exact: true });
  await expect(
    inbox.getByRole("heading", { name: "Reply to the venue", exact: true }),
  ).toBeVisible();
  await expect(
    inbox.getByRole("heading", { name: "Weekly newsletter", exact: true }),
  ).toHaveCount(0);
  await expect(
    inbox.getByRole("heading", { name: "Unclear obligation", exact: true }),
  ).toHaveCount(0);
  await expect(
    inbox.getByRole("heading", { name: "Not classified yet", exact: true }),
  ).toHaveCount(0);
  await expect(
    inbox.getByRole("heading", { name: "Reply to the venue", exact: true }),
  ).toBeVisible();
  await expect(
    inbox.getByText("Confirm the booking", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/1 need review/)).toBeVisible();
  await expect(page.getByText(/1 not yet classified/)).toBeVisible();
  await expect(
    inbox.getByRole("link", { name: "Open email in Gmail" }),
  ).toHaveAttribute("href", "https://mail.google.com/mail/u/0/#inbox/message");
});

test("Untriaged mail is explicit, classification starts once and completed results refresh the action list", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  state.unclassifiedMail = true;
  let posts = 0,
    gets = 0;
  await page.route("**/api/email/triage", async (route) => {
    const post = route.request().method() === "POST";
    if (post) posts++;
    else gets++;
    const running = post || gets < 2;
    if (!running) state.unclassifiedMail = false;
    await route.fulfill({
      status: post ? 202 : 200,
      json: {
        classification: {
          state: running ? "running" : "ready",
          counts: {
            action: running ? 0 : 1,
            ignore: 0,
            review: 0,
            pending: running ? 1 : 0,
          },
          error: null,
        },
        items: [],
        truncated: false,
      },
    });
  });
  await page.goto("/?view=today");
  await todayPage(page, "Inbox");
  await expect(
    page.getByRole("status").filter({ hasText: "Not triaged yet" }),
  ).toBeVisible();
  await expect(page.getByText(/1 not yet classified/)).toBeVisible();
  await expect(page.getByText(/No actionable messages/)).toHaveCount(0);
  await page
    .getByRole("button", { name: "Triage saved mail", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Triage saved mail", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("heading", { name: "Reply to the venue", exact: true }),
  ).toBeVisible({ timeout: 10000 });
  expect(posts).toBe(1);
  expect(gets).toBeGreaterThanOrEqual(2);
});

test("Excluded mail review is lazy and uses only saved metadata and Gmail links", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  let reads = 0,
    posts = 0;
  await page.route("**/api/email/triage", async (route) => {
    if (route.request().method() === "POST") posts++;
    else reads++;
    await route.fulfill({
      json: {
        classification: {
          state: "ready",
          counts: { action: 1, ignore: 1, review: 0, pending: 0 },
        },
        items: [
          {
            id: "ignored",
            accountId: "a",
            from: "<b>Untrusted sender</b>",
            subject: "Weekly news",
            url: "https://mail.google.com/mail/u/0/#inbox/ignored",
            actionability: {
              state: "ignore",
              reason: "Newsletter",
              pending: false,
            },
          },
        ],
        truncated: false,
      },
    });
  });
  await page.goto("/?view=today");
  await todayPage(page, "Inbox");
  await expect(
    page.getByRole("heading", { name: "Reply to the venue", exact: true }),
  ).toBeVisible();
  expect(reads).toBe(0);
  await page
    .locator("summary")
    .filter({ hasText: /^Excluded/ })
    .click();
  await expect(
    page.getByRole("heading", { name: "Weekly news", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("<b>Untrusted sender</b>", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Review in Gmail", exact: true }),
  ).toHaveAttribute("href", "https://mail.google.com/mail/u/0/#inbox/ignored");
  expect(posts).toBe(0);
});

test("Missing classification stays visibly untriaged rather than an empty finished inbox", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  state.missingClassification = true;
  await page.goto("/?view=today");
  await todayPage(page, "Inbox");
  await expect(
    page.getByRole("status").filter({ hasText: "Not triaged yet" }),
  ).toBeVisible();
  await expect(page.getByText(/1 not yet classified/)).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Reply to the venue", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText(/No actionable messages/)).toHaveCount(0);
});

test("Changing day retires a held classification start without polling or reporting old results", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  state.unclassifiedMail = true;
  let posts = 0,
    gets = 0,
    settled = false,
    release!: () => void;
  await page.route("**/api/email/triage", async (route) => {
    if (route.request().method() !== "POST") {
      gets++;
      return route.fulfill({
        json: { classification: { state: "ready", counts: {} }, items: [] },
      });
    }
    posts++;
    await new Promise<void>((resolve) => (release = resolve));
    try {
      await route.fulfill({
        status: 202,
        json: {
          classification: {
            state: "running",
            counts: { action: 0, ignore: 0, review: 0, pending: 99 },
          },
          items: [],
        },
      });
    } finally {
      settled = true;
    }
  });
  await page.goto("/?view=today");
  await todayPage(page, "Inbox");
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await page
    .getByRole("button", { name: "Triage saved mail", exact: true })
    .click();
  await expect.poll(() => posts).toBe(1);
  await page.getByLabel("Viewing date").fill("2026-09-22");
  release();
  await expect.poll(() => settled).toBe(true);
  await page.waitForTimeout(2200);
  expect(gets).toBe(0);
  await expect(page.getByText(/99 not yet classified/)).toHaveCount(0);
  await page.getByText("Review saved mail", { exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Triage saved mail", exact: true }),
  ).toBeEnabled();
});

test("A lost triage response times out visibly without automatically starting it again", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  state.unclassifiedMail = true;
  await page.clock.install();
  let posts = 0,
    release!: () => void;
  await page.route("**/api/email/triage", async (route) => {
    posts++;
    await new Promise<void>((resolve) => (release = resolve));
    try {
      await route.fulfill({
        status: 202,
        json: { classification: { state: "running", counts: {} }, items: [] },
      });
    } catch {
      /* Client timed out. */
    }
  });
  await page.goto("/?view=today");
  await todayPage(page, "Inbox");
  await page
    .getByRole("button", { name: "Triage saved mail", exact: true })
    .click();
  await expect.poll(() => posts).toBe(1);
  await page.clock.fastForward(16000);
  await expect(page.getByRole("alert")).toContainText(
    "Refresh status before trying again",
  );
  expect(posts).toBe(1);
  await expect(
    page.getByRole("button", { name: "Refresh triage status", exact: true }),
  ).toBeEnabled();
  release();
});

test("Refreshing changed source mail retires a held review read and loads the new classification", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  let reads = 0,
    release!: () => void;
  await page.route("**/api/email/triage", async (route) => {
    reads++;
    if (reads === 1) {
      await new Promise<void>((resolve) => (release = resolve));
      try {
        await route.fulfill({
          json: {
            classification: { state: "ready", counts: {} },
            items: [
              {
                accountId: "a",
                id: "old",
                subject: "Old review item",
                actionability: { state: "review", reason: "Old source" },
              },
            ],
          },
        });
      } catch {
        /* Retired read. */
      }
      return;
    }
    await route.fulfill({
      json: {
        classification: {
          state: "unclassified",
          counts: { action: 0, ignore: 0, review: 0, pending: 1 },
        },
        items: [
          {
            accountId: "a",
            id: "new",
            subject: "New saved mail",
            actionability: { state: "review", pending: true },
          },
        ],
        truncated: false,
      },
    });
  });
  await page.goto("/?view=today");
  await todayPage(page, "Inbox");
  await page
    .locator("summary")
    .filter({ hasText: /^Needs review/ })
    .click();
  await expect.poll(() => reads).toBe(1);
  state.unclassifiedMail = true;
  await dayOptions(page);
  await page
    .getByRole("button", { name: "Refresh saved view", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Close Day options", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Not triaged yet" }),
  ).toBeVisible();
  release();
  await page
    .locator("summary")
    .filter({ hasText: /^Not yet classified/ })
    .click();
  await expect(
    page.getByRole("heading", { name: "New saved mail", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Old review item", exact: true }),
  ).toHaveCount(0);
});

test("Needs review separates uncertain mail from excluded and pending saved messages", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  let reads = 0,
    posts = 0;
  await page.route("**/api/email/triage", async (route) => {
    if (route.request().method() === "POST") posts++;
    else reads++;
    await route.fulfill({
      json: {
        classification: {
          state: "unclassified",
          counts: { action: 0, ignore: 1, review: 1, pending: 1 },
        },
        items: [
          {
            accountId: "a",
            id: "review",
            subject: "Unclear appointment",
            actionability: {
              state: "review",
              pending: false,
              reason: "Missing context",
            },
          },
          {
            accountId: "a",
            id: "ignore",
            subject: "Weekly subscription",
            actionability: {
              state: "ignore",
              pending: false,
              reason: "Newsletter",
            },
          },
          {
            accountId: "a",
            id: "pending",
            subject: "New arrival",
            actionability: {
              state: "review",
              pending: true,
              reason: "Not classified yet",
            },
          },
        ],
        truncated: false,
      },
    });
  });
  await page.goto("/?view=today");
  await todayPage(page, "Inbox");
  expect(reads).toBe(0);
  const review = page.locator("details").filter({
    has: page.locator(":scope > summary", { hasText: /^Needs review/ }),
  });
  await review.locator(":scope > summary").click();
  await expect(
    review.getByRole("heading", { name: "Unclear appointment", exact: true }),
  ).toBeVisible();
  await expect(review.locator("article")).toHaveCount(1);
  await expect(
    page.getByRole("heading", { name: "Weekly subscription", exact: true }),
  ).not.toBeVisible();
  await expect(
    page.getByRole("heading", { name: "New arrival", exact: true }),
  ).not.toBeVisible();
  const excluded = page.locator("details").filter({
    has: page.locator(":scope > summary", { hasText: /^Excluded/ }),
  });
  await excluded.locator(":scope > summary").click();
  await expect(
    excluded.getByRole("heading", { name: "Weekly subscription", exact: true }),
  ).toBeVisible();
  await expect(excluded.locator("article")).toHaveCount(1);
  expect(reads).toBe(1);
  expect(posts).toBe(0);
});

test("Owner review promotes a confirmed action into Today without setting Focus", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  state.unclassifiedMail = true;
  const writes: any[] = [];
  let approved = false;
  await page.route("**/api/email/triage", (route) =>
    route.fulfill({
      json: {
        classification: {
          state: "ready",
          counts: {
            action: approved ? 1 : 0,
            ignore: 0,
            review: approved ? 0 : 1,
            pending: 0,
          },
        },
        items: approved
          ? []
          : [
              {
                accountId: "a",
                id: "message",
                subject: "Reply to the venue",
                reviewRevision: "a".repeat(64),
                actionability: {
                  state: "review",
                  pending: false,
                  reason: "Please review this request",
                },
              },
            ],
        truncated: false,
      },
    }),
  );
  await page.route("**/api/email/triage/review", async (route) => {
    const body = route.request().postDataJSON();
    writes.push(body);
    approved = true;
    state.unclassifiedMail = false;
    await route.fulfill({
      json: {
        accountId: "a",
        messageId: "message",
        reviewRevision: "b".repeat(64),
        actionability: {
          state: "action",
          kind: body.kind,
          action: body.action,
          reviewedBy: "user",
          basis: null,
          pending: false,
        },
      },
    });
  });
  await page.goto("/?view=today");
  await todayPage(page, "Inbox");
  await page
    .locator("summary")
    .filter({ hasText: /^Needs review/ })
    .click();
  const card = page.locator("article").filter({
    has: page.getByRole("heading", {
      name: "Reply to the venue",
      exact: true,
    }),
  });
  await card
    .getByRole("button", { name: "Add to Today…", exact: true })
    .click();
  await card.getByLabel("Action for Today").fill("Confirm the booking");
  await card.getByLabel("Action type").selectOption("reply");
  await card.getByRole("button", { name: "Add to Today", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Added to the Action inbox" }),
  ).toBeVisible();
  const promoted = page.locator(".inbox-mail-card").filter({ has: page.getByRole("heading", { name: "Reply to the venue", exact: true }) });
  await promoted.locator(":scope > details > summary").click();
  await expect(
    page
      .locator("article")
      .filter({
        has: page.getByRole("heading", {
          name: "Reply to the venue",
          exact: true,
        }),
      })
      .getByRole("button", { name: "Focus", exact: true }),
  ).toBeVisible();
  expect(writes).toEqual([
    {
      accountId: "a",
      messageId: "message",
      reviewRevision: "a".repeat(64),
      decision: "action",
      kind: "reply",
      action: "Confirm the booking",
    },
  ]);
  expect(state.writes).toEqual([]);
});

test("Stale mail approval keeps the draft and never retries automatically", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  let writes = 0;
  await page.route("**/api/email/triage", (route) =>
    route.fulfill({
      json: {
        classification: {
          state: "ready",
          counts: { action: 1, ignore: 0, review: 1, pending: 0 },
        },
        items: [
          {
            accountId: "a",
            id: "unclear",
            subject: "Unclear request",
            reviewRevision: "a".repeat(64),
            actionability: {
              state: "review",
              pending: false,
              reason: "Needs context",
            },
          },
        ],
        truncated: false,
      },
    }),
  );
  await page.route("**/api/email/triage/review", async (route) => {
    writes++;
    await route.fulfill({
      status: 409,
      json: { detail: "Mail changed. Refresh and review again." },
    });
  });
  await page.goto("/?view=today");
  await todayPage(page, "Inbox");
  await page
    .locator("summary")
    .filter({ hasText: /^Needs review/ })
    .click();
  const card = page.locator("article").filter({
    has: page.getByRole("heading", { name: "Unclear request", exact: true }),
  });
  await card
    .getByRole("button", { name: "Add to Today…", exact: true })
    .click();
  await card.getByLabel("Action for Today").fill("My unsaved action");
  await card.getByRole("button", { name: "Add to Today", exact: true }).click();
  await expect(card.getByRole("alert")).toContainText("Mail changed");
  await expect(card.getByLabel("Action for Today")).toHaveValue(
    "My unsaved action",
  );
  expect(writes).toBe(1);
});

test("Excluding reviewed mail uses its exact source revision and updates the groups", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  let ignored = false;
  const writes: any[] = [];
  await page.route("**/api/email/triage", (route) =>
    route.fulfill({
      json: {
        classification: {
          state: "ready",
          counts: {
            action: 1,
            ignore: ignored ? 1 : 0,
            review: ignored ? 0 : 1,
            pending: 0,
          },
        },
        items: [
          {
            accountId: "a",
            id: "unclear",
            subject: "Optional sales call",
            reviewRevision: (ignored ? "b" : "a").repeat(64),
            actionability: {
              state: ignored ? "ignore" : "review",
              pending: false,
              reason: "Uncertain request",
            },
          },
        ],
        truncated: false,
      },
    }),
  );
  await page.route("**/api/email/triage/review", async (route) => {
    writes.push(route.request().postDataJSON());
    ignored = true;
    await route.fulfill({
      json: {
        accountId: "a",
        messageId: "unclear",
        reviewRevision: "b".repeat(64),
        actionability: {
          state: "ignore",
          kind: null,
          action: "",
          reviewedBy: "user",
          basis: null,
          pending: false,
        },
      },
    });
  });
  await page.goto("/?view=today");
  await todayPage(page, "Inbox");
  await page
    .locator("summary")
    .filter({ hasText: /^Needs review/ })
    .click();
  const review = page.locator("details").filter({
    has: page.locator(":scope > summary", { hasText: /^Needs review/ }),
  });
  await review.getByRole("button", { name: "Exclude", exact: true }).click();
  await expect(review.locator("article")).toHaveCount(0);
  await expect(
    page.getByRole("status").filter({ hasText: "Excluded from Today" }),
  ).toBeVisible();
  await page
    .locator("summary")
    .filter({ hasText: /^Excluded/ })
    .click();
  await expect(
    page.getByRole("heading", { name: "Optional sales call", exact: true }),
  ).toBeVisible();
  expect(writes).toEqual([
    {
      accountId: "a",
      messageId: "unclear",
      reviewRevision: "a".repeat(64),
      decision: "ignore",
      kind: null,
      action: "",
    },
  ]);
  expect(state.writes).toEqual([]);
});

test("Refreshing the same message never silently rebases an open approval draft", async ({
  page,
}) => {
  const state = await fixture(page);
  state.mail = true;
  let revision = "a".repeat(64),
    reads = 0;
  const writes: any[] = [];
  await page.route("**/api/email/triage", (route) => {
    reads++;
    return route.fulfill({
      json: {
        classification: {
          state: "ready",
          counts: { action: 1, ignore: 0, review: 1, pending: 0 },
        },
        items: [
          {
            accountId: "a",
            id: "unclear",
            subject: revision.startsWith("a")
              ? "Original message"
              : "Changed message",
            reviewRevision: revision,
            actionability: {
              state: "review",
              pending: false,
              reason: "Needs context",
            },
          },
        ],
        truncated: false,
      },
    });
  });
  await page.route("**/api/email/triage/review", (route) => {
    writes.push(route.request().postDataJSON());
    return route.fulfill({
      json: {
        accountId: "a",
        messageId: "unclear",
        reviewRevision: "c".repeat(64),
        actionability: { state: "action", reviewedBy: "user" },
      },
    });
  });
  await page.goto("/?view=today");
  await todayPage(page, "Inbox");
  await page
    .locator("summary")
    .filter({ hasText: /^Needs review/ })
    .click();
  await page
    .getByRole("button", { name: "Add to Today…", exact: true })
    .click();
  await page.getByLabel("Action for Today").fill("Retained original draft");
  revision = "b".repeat(64);
  await page
    .getByRole("button", { name: "Refresh triage status", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Changed message", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Action for Today")).toHaveValue(
    "Retained original draft",
  );
  await expect(
    page.getByRole("button", { name: "Add to Today", exact: true }),
  ).toBeDisabled();
  expect(writes).toEqual([]);
  await page
    .getByRole("button", { name: "Confirm updated message", exact: true })
    .click();
  await page.getByRole("button", { name: "Add to Today", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].reviewRevision).toBe("b".repeat(64));
  expect(writes[0].action).toBe("Retained original draft");
});

// Catalog shape matches GET /api/procedures: a generic template plus separate adoption.
const procedureFixture = {
  id: "what-now",
  version: 1,
  title: "What now?",
  summary: "Choose one useful next action from the current day.",
  steps: ["Read the current dailyAgenda; disclose gaps."],
  limits: "Recommendation only.",
  source: { kind: "product_template", id: "leam:procedure/what-now@1" },
  personalSourceMapping: "not_linked",
  adoption: { state: "proposed", revision: 0, version: 1, sourceRefs: [] },
};
test("Mobile explicit procedure is one turn and a failed send preserves its draft", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  const sent: any[] = [];
  let fail = true;
  await page.route("**/api/procedures", (route) =>
    route.fulfill({
      json: { items: [procedureFixture], automaticInvocation: false },
    }),
  );
  await page.route("**/api/companion/threads/day-thread/messages", (route) => {
    sent.push(route.request().postDataJSON());
    return route.fulfill(
      fail
        ? { status: 503, json: { detail: "Synthetic delivery unavailable" } }
        : {
            json: {
              outcome: "submitted",
              run_id: "11111111-1111-4111-8111-111111111111",
              thread_id: "day-thread",
            },
          },
    );
  });
  await page.goto("/?view=today");
  await todayPage(page, "Chat");
  await page
    .getByRole("button", { name: "Companion chat options", exact: true })
    .click();
  await page.getByLabel("Procedure for this message").selectOption("what-now");
  await expect(page.getByLabel("Message Leam")).toHaveValue(
    "What should I do now?",
  );
  expect(sent).toEqual([]);
  await page
    .getByRole("button", { name: "Close Companion chat options", exact: true })
    .click();
  await page.getByRole("button", { name: "Send to Leam", exact: true }).click();
  await expect(
    page
      .getByRole("complementary", { name: "Message delivery recovery" })
      .getByText("No confirmed receipt found.", { exact: false }),
  ).toBeVisible();
  await expect(page.getByLabel("Message Leam")).toHaveValue(
    "What should I do now?",
  );
  expect(sent[0].procedure).toEqual({ id: "what-now", version: 1 });
  fail = false;
  await page
    .getByRole("button", { name: "Retry saved message", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Companion chat options", exact: true })
    .click();
  await expect(page.getByLabel("Procedure for this message")).toHaveValue("");
  expect(sent[1]).toEqual(sent[0]);
  await page
    .getByRole("button", { name: "Close Companion chat options", exact: true })
    .click();
  await page.getByLabel("Message Leam").fill("Just chatting");
  await page.getByRole("button", { name: "Send to Leam", exact: true }).click();
  await expect.poll(() => sent.length).toBe(3);
  expect(sent[2].procedure).toBeUndefined();
});

test("Mobile catalog separates proposed templates from explicit adoption", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  let item = structuredClone(procedureFixture);
  const writes: any[] = [];
  await page.route("**/api/procedures", (route) =>
    route.fulfill({ json: { items: [item], automaticInvocation: false } }),
  );
  await page.route("**/api/procedures/what-now", (route) => {
    const body = route.request().postDataJSON();
    writes.push(body);
    item = { ...item, adoption: { ...item.adoption, ...body, revision: 1 } };
    return route.fulfill({ json: item });
  });
  await page.goto("/?view=settings");
  await page
    .locator("summary")
    .filter({ hasText: "Operating procedures" })
    .click();
  await expect(
    page.getByText("Personal source not linked.", { exact: false }),
  ).toBeVisible();
  expect(writes).toEqual([]);
  await page.getByRole("button", { name: "Adopt v1", exact: true }).click();
  await expect(page.getByText("Adoption: adopted · revision 1")).toBeVisible();
  expect(writes).toEqual([
    { version: 1, revision: 0, state: "adopted", sourceRefs: [] },
  ]);
});

test("compact Today measured progress uses its exact day and both revisions", async ({
  page,
}) => {
  const state = await fixture(page);
  const writes: any[] = [];
  await page.route("**/api/commitments/walk/progress/*", async (route) => {
    const input = route.request().postDataJSON();
    writes.push({ path: new URL(route.request().url()).pathname, input });
    state.measuredValue = input.value;
    await route.fulfill({ json: { saved: true } });
  });
  state.disposition = "focus";
  await page.goto("/?view=today#today/plan/2026-09-21");
  await page.getByRole("spinbutton", { name: "Progress for Walk" }).fill("13");
  await page
    .getByRole("button", { name: "Save progress", exact: true })
    .click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes).toEqual([
    {
      path: "/api/commitments/walk/progress/2026-09-21",
      input: {
        operation: "set",
        revision: 1,
        commitmentRevision: 1,
        value: 13,
      },
    },
  ]);
  await expect(
    page.getByRole("spinbutton", { name: "Progress for Walk" }),
  ).toHaveValue("13");
});
