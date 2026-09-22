import { test, expect, type Page } from "@playwright/test";

async function fixture(page: Page) {
  const state = {
    agendaReads: [] as string[],
    bindings: [] as any[],
    writes: [] as any[],
    sends: [] as any[],
    disposition: "none",
    triageRevision: 3,
    done: false,
  };
  await page.addInitScript(() => {
    const streams: any[] = [];
    (window as any).todayStreams = streams;
    (window as any).EventSource = class extends EventTarget {
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
    };
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname,
      method = route.request().method();
    let body: any = {
      items: [],
      data: [],
      threads: [],
      messages: [],
      models: [],
      providers: [],
    };
    if (path === "/api/auth/status")
      body = { configured: true, authenticated: true };
    if (path === "/api/capacities")
      body = { items: [{ id: "capacity-id", name: "Health" }] };
    if (path === "/api/agenda") {
      const date = url.searchParams.get("date")!;
      state.agendaReads.push(date);
      body = {
        date,
        timezone: url.searchParams.get("timezone"),
        nextOffset: null,
        partial: true,
        total: { commitments: 1, events: 2, emails: 1 },
        window: { start: date + "T00:00:00Z", end: date + "T23:59:59Z" },
        commitments: [
          {
            id: "walk",
            key: "commitment:walk",
            title: "Take a walk",
            date,
            revision: 7,
            kind: "task",
            measure: "boolean",
            status: state.done ? "completed" : "active",
            capacityId: "capacity-id",
            log: { value: 0, done: state.done, revision: 5 },
            triage: { disposition: "none", revision: 0 },
          },
        ],
        events: [
          {
            key: "event:calendar:late",
            eventId: "late",
            title: "Afternoon review",
            start: date + "T15:00:00Z",
            end: date + "T15:30:00Z",
            triage: { disposition: "later", revision: 1 },
            visibility: { hidden: false, revision: 0 },
          },
          {
            key: "event:calendar:early",
            eventId: "early",
            title: "Morning appointment",
            start: date + "T09:00:00Z",
            end: date + "T09:30:00Z",
            url: "https://calendar.google.com/fixture",
            triage: {
              disposition: state.disposition,
              revision: state.triageRevision,
            },
            visibility: { hidden: false, revision: 0 },
          },
        ],
        emails: [
          {
            key: "email:a:1",
            subject: "Confirm the booking",
            from: "Alex",
            receivedAt: date + "T08:00:00Z",
            actionability: {
              state: "action",
              action: "Reply with your availability",
            },
            triage: { disposition: "none", revision: 0 },
            url: "https://mail.google.com/fixture",
          },
        ],
        sources: {
          calendar: { state: "partial", accounts: [], snapshots: [] },
          email: {
            state: "ready",
            accounts: [],
            classification: {
              state: "ready",
              counts: { action: 1, review: 2, ignore: 0, pending: 0 },
            },
          },
        },
      };
    }
    if (path === "/api/agenda/triage") {
      const input = route.request().postDataJSON();
      state.writes.push(input);
      state.disposition = input.disposition;
      state.triageRevision++;
      body = { ...input, revision: state.triageRevision };
    }
    if (path.includes("/progress/")) {
      state.writes.push(route.request().postDataJSON());
      state.done = true;
      body = { saved: true };
    }
    if (path === "/api/agenda/chat" && method === "GET") {
      const date = url.searchParams.get("date");
      body = {
        threadId: state.bindings.some((binding) => binding.date === date)
          ? `day-${date}`
          : null,
      };
    }
    if (path === "/api/agenda/chat" && method === "POST") {
      const input = route.request().postDataJSON();
      state.bindings.push(input);
      body = { threadId: `day-${input.date}` };
    }
    if (path === "/api/companion/status")
      body = { configured: true, available: true };
    if (
      path.includes("/companion/threads/") &&
      /^\/api\/companion\/threads\/day-[^/]+$/.test(path)
    )
      body = {
        messages: [
          {
            message_id: "reply",
            kind: "assistant",
            status: "finalized",
            sequence: 1,
            turn_run_id: "reply-run",
            content: "Let us make room for what matters today.",
          },
        ],
      };
    if (path.endsWith("/messages") && method === "POST") {
      state.sends.push(route.request().postDataJSON());
      body = {
        outcome: "submitted",
        run_id: "run",
        thread_id: path.split("/")[4],
      };
    }
    if (path === "/api/agenda/reconciliation")
      body = {
        items: [],
        coverage: {
          enabledAt: 1,
          scope: "New Today exchanges since enabledAt",
          state: "idle",
        },
      };
    if (path === "/api/proposals/status")
      body = { pendingCount: 0, unreadCount: 0 };
    if (path === "/api/attachments")
      body = {
        id: "file-1",
        filename: "draft.txt",
        mimeType: "text/plain",
        sizeBytes: 4,
        state: "uploaded",
        sha256: "fixture",
      };
    await route.fulfill({ json: body });
  });
  await page.goto("/?view=today#today/plan/2026-09-21");
  await expect(
    page.getByRole("heading", { name: "Take a walk" }),
  ).toBeVisible();
  return state;
}
const navigation = (page: Page, name: string) =>
  page
    .getByRole("navigation", { name: "Today pages" })
    .getByRole("link", { name, exact: true });

test("local pages reuse the agenda, and Back restores exact page and date", async ({
  page,
}) => {
  const state = await fixture(page);
  const reads = state.agendaReads.length;
  await navigation(page, "Schedule").click();
  await expect(navigation(page, "Schedule")).toHaveAttribute(
    "aria-current",
    "page",
  );
  await navigation(page, "Inbox").click();
  await expect(
    page.getByRole("heading", { name: "Confirm the booking" }),
  ).toBeVisible();
  expect(state.agendaReads).toHaveLength(reads);
  await page.getByLabel("Viewing date").fill("2026-09-22");
  await expect.poll(() => state.agendaReads.at(-1)).toBe("2026-09-22");
  await page.goBack();
  await expect(page.getByLabel("Viewing date")).toHaveValue("2026-09-21");
  await expect(navigation(page, "Inbox")).toHaveAttribute(
    "aria-current",
    "page",
  );
  await page.goBack();
  await expect(navigation(page, "Schedule")).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(state.bindings).toHaveLength(0);
  expect(state.writes).toHaveLength(0);
  expect(state.sends).toHaveLength(0);
});

test("Focus uses exact revisions and retains chronological appointments in Schedule", async ({
  page,
}) => {
  const state = await fixture(page);
  await navigation(page, "Schedule").click();
  await expect(page.locator(".today-timeline h3")).toHaveText([
    "Morning appointment",
    "Afternoon review",
  ]);
  await page
    .getByLabel("Plan Morning appointment", { exact: true })
    .getByRole("button", { name: "Focus", exact: true })
    .click();
  await expect(navigation(page, "Plan")).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByRole("region", { name: "Daily focus" })).toContainText(
    "Morning appointment",
  );
  expect(state.writes).toEqual([
    {
      date: "2026-09-21",
      timezone: expect.any(String),
      key: "event:calendar:early",
      revision: 3,
      disposition: "focus",
    },
  ]);
  await navigation(page, "Schedule").click();
  await expect(page.locator(".today-timeline h3")).toHaveText([
    "Morning appointment",
    "Afternoon review",
  ]);
  await expect(page.locator(".today-timeline")).toContainText("In Focus");
});

test("compact commitment completion preserves both concurrency revisions", async ({
  page,
}) => {
  const state = await fixture(page);
  const complete = page.getByRole("button", {
    name: "Complete Take a walk",
    exact: true,
  });
  const hitbox = await complete.boundingBox();
  expect(hitbox!.width).toBeGreaterThanOrEqual(44);
  expect(hitbox!.height).toBeGreaterThanOrEqual(44);
  await complete.click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]).toEqual({
    operation: "toggle",
    revision: 5,
    commitmentRevision: 7,
  });
  await expect(page.getByText("Completed · 1", { exact: true })).toBeVisible();
});

test("Chat restores same day draft and attachments without rebinding, sending or hidden streams", async ({
  page,
}) => {
  const state = await fixture(page);
  await navigation(page, "Chat").click();
  const input = page.getByRole("textbox", { name: "Message Leam" });
  await input.fill("Keep my plan draft");
  await page.locator('input[type="file"]').setInputFiles({
    name: "draft.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("kept"),
  });
  await expect(page.getByText("draft.txt", { exact: true })).toBeVisible();
  await navigation(page, "Plan").click();
  await expect(input).toHaveCount(0);
  expect(
    await page.evaluate(
      () =>
        (window as any).todayStreams.filter(
          (stream: any) =>
            stream.url.includes("/companion/threads/") && !stream.closed,
        ).length,
    ),
  ).toBe(0);
  await navigation(page, "Chat").click();
  await expect(input).toHaveValue("Keep my plan draft");
  await expect(page.getByText("draft.txt", { exact: true })).toBeVisible();
  expect(state.bindings).toHaveLength(1);
  expect(state.sends).toHaveLength(0);
  await page.getByLabel("Viewing date").fill("2026-09-22");
  await expect(input).toHaveValue("");
  await page.goBack();
  await expect(input).toHaveValue("Keep my plan draft");
  expect(state.bindings.map((value) => value.date)).toEqual([
    "2026-09-21",
    "2026-09-22",
  ]);
  expect(state.sends).toHaveLength(0);
});

for (const viewport of [
  { width: 360, height: 800 },
  { width: 390, height: 844 },
  { width: 844, height: 390 },
  { width: 1440, height: 1000 },
])
  test(`Today pages fit ${viewport.width}×${viewport.height} with a full chat response area`, async ({
    page,
  }, info) => {
    await page.setViewportSize(viewport);
    await fixture(page);
    for (const name of ["Plan", "Schedule", "Inbox", "Chat", "Boards"]) {
      await navigation(page, name).click();
      await expect(navigation(page, name)).toHaveAttribute(
        "aria-current",
        "page",
      );
      if (name === "Chat")
        await expect(
          page.getByRole("textbox", { name: "Message Leam" }),
        ).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      const bounds = await navigation(page, name).boundingBox();
      expect(bounds!.height).toBeGreaterThanOrEqual(44);
      if (name === "Chat") {
        const geometry = await page
          .locator(".today-chat-page")
          .evaluate((element) => {
            const transcript = element
                .querySelector(".companion-messages")!
                .getBoundingClientRect(),
              conversation = element
                .querySelector(".companion-page")!
                .getBoundingClientRect();
            return {
              response: transcript.height,
              conversation: conversation.height,
              fraction: transcript.height / conversation.height,
              bottom: conversation.bottom,
              viewport: innerHeight,
              children: [
                ...element.querySelector(".companion-page")!.children,
              ].map((child) => ({
                tag: child.tagName,
                classes: child.className,
                height: child.getBoundingClientRect().height,
                top: child.getBoundingClientRect().top,
                margin: getComputedStyle(child).margin,
                padding: getComputedStyle(child).padding,
              })),
            };
          });
        expect(geometry.fraction).toBeGreaterThanOrEqual(0.65);
        expect(geometry.bottom).toBeLessThanOrEqual(viewport.height);
        await info.attach("chat-geometry", {
          body: JSON.stringify(geometry),
          contentType: "application/json",
        });
      }
      await page.screenshot({
        path: info.outputPath(`${name.toLowerCase()}-${viewport.width}.png`),
      });
    }
  });

test("Skip to content preserves the selected Today page and date", async ({
  page,
}) => {
  await fixture(page);
  await navigation(page, "Schedule").click();
  await page.getByLabel("Viewing date").fill("2026-09-24");
  await page.getByRole("link", { name: "Skip to content" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Viewing date")).toHaveValue("2026-09-24");
  await expect(navigation(page, "Schedule")).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.locator("#main-content")).toBeFocused();
});

test("independent reminders remain available when the saved agenda is unavailable", async ({
  page,
}) => {
  await fixture(page);
  const reads: string[] = [];
  await page.route("**/api/agenda?**", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Saved agenda unavailable" },
    }),
  );
  page.on("request", (request) => {
    if (/\/api\/(notifications|routines)/.test(request.url()))
      reads.push(new URL(request.url()).pathname);
  });
  await page.route("**/api/routines/notifications", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: "routine-fixture",
            title: "Review your day",
            message: "Saved independent reminder",
            dueAt: 1790000000,
          },
        ],
      },
    }),
  );
  await page.reload();
  await expect(page.getByRole("alert")).toContainText(
    "Saved agenda unavailable",
  );
  expect(reads).toEqual([]);
  await page.getByText("Reminders", { exact: true }).click();
  await expect.poll(() => reads.length).toBeGreaterThanOrEqual(2);
  await expect(
    page.getByRole("heading", { name: "Routine reminders", exact: true }),
  ).toBeVisible();
  await navigation(page, "Chat").click();
  await expect(
    page.getByRole("heading", { name: "Routine reminders", exact: true }),
  ).toHaveCount(0);
});

async function arrangePages(page: Page) {
  await page.getByRole("button", { name: "Day options", exact: true }).click();
  await page.getByText("Arrange Today pages", { exact: true }).click();
  return page.getByRole("dialog", { name: "Day options", exact: true });
}
const pageOrder = (page: Page) =>
  page.getByRole("navigation", { name: "Today pages" }).getByRole("link");

test("Today page order uses accessible controls, persists enum-only data, and preserves day/history", async ({
  page,
}) => {
  const state = await fixture(page);
  const reads = state.agendaReads.length;
  await navigation(page, "Schedule").click();
  const before = page.url();
  const dialog = await arrangePages(page);
  await dialog
    .getByRole("button", { name: "Move Chat earlier", exact: true })
    .click();
  await expect(pageOrder(page)).toHaveText([
    "Plan",
    "Schedule",
    "Chat",
    "Inbox",
    "Boards",
  ]);
  expect(page.url()).toBe(before);
  expect(state.agendaReads).toHaveLength(reads);
  expect(state.writes).toHaveLength(0);
  expect(
    await page.evaluate(() =>
      JSON.parse(localStorage.getItem("leam-today-page-order-v1")!),
    ),
  ).toEqual({
    version: 1,
    order: ["plan", "schedule", "chat", "inbox", "boards"],
  });
  await page.reload();
  await expect(pageOrder(page)).toHaveText([
    "Plan",
    "Schedule",
    "Chat",
    "Inbox",
    "Boards",
  ]);
  await expect(navigation(page, "Schedule")).toHaveAttribute(
    "aria-current",
    "page",
  );
  await navigation(page, "Inbox").click();
  await page.goBack();
  await expect(navigation(page, "Schedule")).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByLabel("Viewing date")).toHaveValue("2026-09-21");
  const again = await arrangePages(page);
  await again
    .getByRole("button", { name: "Reset page order", exact: true })
    .click();
  await expect(pageOrder(page)).toHaveText([
    "Plan",
    "Schedule",
    "Inbox",
    "Chat",
    "Boards",
  ]);
});

for (const touch of [false, true])
  test(`Today page reorder supports ${touch ? "touch" : "mouse"} handles without navigation or requests`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const state = await fixture(page);
    const reads = state.agendaReads.length;
    const before = page.url();
    const dialog = await arrangePages(page);
    const source = dialog.getByRole("button", {
      name: "Drag Schedule to reorder",
      exact: true,
    });
    await source.scrollIntoViewIfNeeded();
    const start = (await source.boundingBox())!;
    const end = (await dialog
      .locator('[data-today-order-id="plan"]')
      .boundingBox())!;
    const a = { x: start.x + start.width / 2, y: start.y + start.height / 2 };
    const b = { x: end.x + end.width / 2, y: end.y + end.height / 2 };
    if (touch) {
      const client = await page.context().newCDPSession(page);
      await client.send("Input.dispatchTouchEvent", {
        type: "touchStart",
        touchPoints: [a],
      });
      await client.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [b],
      });
      await client.send("Input.dispatchTouchEvent", {
        type: "touchEnd",
        touchPoints: [],
      });
      await client.detach();
    } else {
      await page.mouse.move(a.x, a.y);
      await page.mouse.down();
      await page.mouse.move(b.x, b.y, { steps: 5 });
      await page.mouse.up();
    }
    await expect(pageOrder(page)).toHaveText([
      "Schedule",
      "Plan",
      "Inbox",
      "Chat",
      "Boards",
    ]);
    expect(page.url()).toBe(before);
    expect(state.agendaReads).toHaveLength(reads);
    expect(state.writes).toHaveLength(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
  });

for (const [name, raw] of [
  [
    "duplicates and unknown pages",
    JSON.stringify({
      version: 1,
      order: ["chat", "chat", "secret", "boards", "plan"],
    }),
  ],
  ["oversized data", "x".repeat(300)],
])
  test(`invalid saved tab order (${name}) falls back to all known pages`, async ({
    page,
  }) => {
    await page.addInitScript(
      (value) => localStorage.setItem("leam-today-page-order-v1", value),
      raw,
    );
    await fixture(page);
    await expect(pageOrder(page)).toHaveText([
      "Plan",
      "Schedule",
      "Inbox",
      "Chat",
      "Boards",
    ]);
  });

for (const viewport of [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
])
  test(`Today page colours and order controls stay readable at ${viewport.width}×${viewport.height}`, async ({
    page,
  }, info) => {
    await page.setViewportSize(viewport);
    await fixture(page);
    const ratios = await pageOrder(page).evaluateAll((elements) => {
      const luminance = (color: string) => {
        const rgb = color
          .match(/[\d.]+/g)!
          .slice(0, 3)
          .map(Number)
          .map((value) => {
            const v = value / 255;
            return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
          });
        return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
      };
      return elements.map((element) => {
        const style = getComputedStyle(element),
          a = luminance(style.color),
          b = luminance(style.backgroundColor);
        return {
          page: element.textContent,
          ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
        };
      });
    });
    for (const item of ratios)
      expect(item.ratio, item.page!).toBeGreaterThanOrEqual(4.5);
    await info.attach("page-colour-contrast", {
      body: JSON.stringify(ratios),
      contentType: "application/json",
    });
    const dialog = await arrangePages(page);
    await page.screenshot({
      path: info.outputPath(`page-order-${viewport.width}.png`),
    });
    for (const label of ["Move Plan later", "Move Boards earlier"]) {
      const button = dialog.getByRole("button", { name: label, exact: true });
      await button.scrollIntoViewIfNeeded();
      const control = (await button.boundingBox())!,
        panel = (await dialog.boundingBox())!;
      expect(control.width).toBeGreaterThanOrEqual(44);
      expect(control.height).toBeGreaterThanOrEqual(44);
      expect(control.y).toBeGreaterThanOrEqual(panel.y);
      expect(control.y + control.height).toBeLessThanOrEqual(
        panel.y + panel.height,
      );
    }
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
  });

for (const viewport of [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
  { width: 1280, height: 900 },
]) {
  test(`visible Today tabs drag without navigating at ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const state = await fixture(page);
    await navigation(page, "Schedule").click();
    const before = page.url();
    for (const tab of await pageOrder(page).all()) {
      const rect = (await tab.boundingBox())!;
      expect(rect.x).toBeGreaterThanOrEqual(0);
      expect(rect.x + rect.width).toBeLessThanOrEqual(viewport.width);
      expect(rect.height).toBeGreaterThanOrEqual(44);
    }
    const from = (await navigation(page, "Boards").boundingBox())!;
    const to = (await navigation(page, "Plan").boundingBox())!;
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, {
      steps: 8,
    });
    await expect(navigation(page, "Plan")).toHaveAttribute(
      "data-drop-target",
      "true",
    );
    await page.mouse.up();
    await expect(pageOrder(page)).toHaveText([
      "Boards",
      "Plan",
      "Schedule",
      "Inbox",
      "Chat",
    ]);
    expect(page.url()).toBe(before);
    await expect(navigation(page, "Schedule")).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(
      page.locator(".today-navigation [data-drop-target]"),
    ).toHaveCount(0);
    expect(state.writes).toHaveLength(0);
    await page.reload();
    await expect(pageOrder(page)).toHaveText([
      "Boards",
      "Plan",
      "Schedule",
      "Inbox",
      "Chat",
    ]);
    await expect(navigation(page, "Schedule")).toHaveAttribute(
      "aria-current",
      "page",
    );
    await navigation(page, "Plan").click();
    await expect(navigation(page, "Plan")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
}

test("visible Today tabs support keyboard moves and cancelled drags", async ({
  page,
}) => {
  await fixture(page);
  const before = page.url();
  const boards = navigation(page, "Boards");
  await boards.focus();
  await boards.press("Alt+ArrowLeft");
  await expect(pageOrder(page)).toHaveText([
    "Plan",
    "Schedule",
    "Inbox",
    "Boards",
    "Chat",
  ]);
  await expect(boards).toBeFocused();
  expect(page.url()).toBe(before);
  const from = (await boards.boundingBox())!;
  const to = (await navigation(page, "Plan").boundingBox())!;
  await page.mouse.move(from.x + 20, from.y + 20);
  await page.mouse.down();
  await page.mouse.move(to.x + 20, to.y + 20, { steps: 5 });
  await page.keyboard.press("Escape");
  await page.mouse.up();
  await expect(pageOrder(page)).toHaveText([
    "Plan",
    "Schedule",
    "Inbox",
    "Boards",
    "Chat",
  ]);
  expect(page.url()).toBe(before);
  await expect(
    page.locator(".today-navigation [data-drop-target]"),
  ).toHaveCount(0);
});

test("visible Today tabs accept real touch dragging and retain ordinary taps", async ({
  browser,
}) => {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
    serviceWorkers: "block",
    baseURL: test.info().project.use.baseURL as string,
  });
  const page = await context.newPage();
  await fixture(page);
  const before = page.url();
  const from = (await navigation(page, "Boards").boundingBox())!;
  const to = (await navigation(page, "Plan").boundingBox())!;
  const cdp = await context.newCDPSession(page);
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [{ x: from.x + from.width / 2, y: from.y + from.height / 2 }],
  });
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchMove",
    touchPoints: [{ x: to.x + to.width / 2, y: to.y + to.height / 2 }],
  });
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
  await expect(pageOrder(page)).toHaveText([
    "Boards",
    "Plan",
    "Schedule",
    "Inbox",
    "Chat",
  ]);
  expect(page.url()).toBe(before);
  await navigation(page, "Schedule").tap();
  await expect(navigation(page, "Schedule")).toHaveAttribute(
    "aria-current",
    "page",
  );
  await context.close();
});
