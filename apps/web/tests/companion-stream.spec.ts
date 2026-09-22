import { chooseConversation } from "./navigation";
import { test, expect } from "@playwright/test";

for (const sameRunFollowup of [false, true])
  test(`canonical saved final retires mismatched old projection; same-run followup ${sameRunFollowup}`, async ({ page }) => {
    await page.addInitScript(() => {
      class Stream extends EventTarget {
        constructor(url: string) { super(); if (url.includes("/companion/threads/")) (window as any).replyStream = this; }
        close() {}
      }
      (window as any).EventSource = Stream;
    });
    const currentRun = sameRunFollowup ? "old-run" : "new-run";
    await page.route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      let body: any = { items: [], data: [], threads: [], messages: [] };
      if (path === "/api/auth/status") body = { authenticated: true };
      if (path === "/api/companion/threads") body = { threads: [{ thread_id: "a", title: "A" }] };
      if (path === "/api/companion/threads/a") body = { messages: [
        { message_id: "old-input", turn_run_id: "old-run", kind: "user", status: "submitted", sequence: 169, content: "Earlier request" },
        { message_id: "old-final", turn_run_id: "old-run", kind: "assistant", status: "finalized", sequence: 170, content: "Saved earlier answer" },
        { message_id: "latest-input", turn_run_id: currentRun, kind: "user", status: "submitted", sequence: 171, content: "Latest request" },
      ] };
      await route.fulfill({ json: body });
    });
    await page.goto("/?view=companion");
    await chooseConversation(page, "a");
    await expect(page.getByText("Latest request", { exact: true })).toBeVisible();
    await page.waitForFunction(() => !!(window as any).replyStream);
    await page.evaluate(({ currentRun, sameRunFollowup }) => {
      (window as any).replyStream.dispatchEvent(new MessageEvent("projection_update", { data: JSON.stringify({
        type: "projection_update", state: { thread_id: "a", items: [
          ...(!sameRunFollowup ? [{ text: { id: "old-partial", run_id: "old-run", body: "Replayed old partial" } }] : []),
          { text: { id: "current-partial", run_id: currentRun, body: "Current unfinished answer" } },
        ] },
      }) }));
    }, { currentRun, sameRunFollowup });
    await expect(page.getByText("Replayed old partial", { exact: true })).toHaveCount(0);
    await expect(page.getByLabel("Live companion response")).toHaveCount(1);
    await expect(page.getByLabel("Live companion response")).toContainText("Current unfinished answer");
    await expect(page.getByText("Saved earlier answer", { exact: true })).toHaveCount(1);
  });

for (const draft of [true, false])
  test(`active reply remains when saved row is ${draft ? "nonfinal" : "missing canonical ordering"}`, async ({ page }) => {
  await page.addInitScript(() => {
    class Stream extends EventTarget {
      constructor(url: string) { super(); if (url.includes("/companion/threads/")) (window as any).replyStream = this; }
      close() {}
    }
    (window as any).EventSource = Stream;
  });
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [], threads: [], messages: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/companion/threads") body = { threads: [{ thread_id: "a", title: "A" }] };
    if (path === "/api/companion/threads/a") body = { messages: [
      { message_id: "user", turn_run_id: "run-a", kind: "user", status: "submitted", sequence: 1, content: "Request" },
      { message_id: "draft", turn_run_id: "run-a", kind: "assistant", status: draft ? "draft" : "finalized", sequence: draft ? 2 : undefined, content: "Still working" },
    ] };
    await route.fulfill({ json: body });
  });
  await page.goto("/?view=companion");
  await chooseConversation(page, "a");
  await page.waitForFunction(() => !!(window as any).replyStream);
  await page.evaluate(() => (window as any).replyStream.dispatchEvent(new MessageEvent("projection_update", { data: JSON.stringify({
    type: "projection_update", state: { thread_id: "a", items: [{ text: { id: "partial", run_id: "run-a", body: "Still working" } }] },
  }) })));
  await expect(page.getByLabel("Live companion response")).toContainText("Still working");
});

for (const width of [390, 1440])
  test(`companion live progress, reconciliation and thread isolation ${width}`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => {
      const streams: any[] = [];
      class FakeEventSource extends EventTarget {
        url: string;
        closed = false;
        onopen: any;
        onerror: any;
        constructor(url: string) {
          super();
          this.url = url;
          if (url.includes("/companion/")) streams.push(this);
          setTimeout(() => this.onopen?.(), 0);
        }
        close() {
          this.closed = true;
        }
      }
      (window as any).EventSource = FakeEventSource;
      (window as any).streams = streams;
      (window as any).emit = (index: number, type: string, data: any) =>
        streams[index].dispatchEvent(
          new MessageEvent(type, { data: JSON.stringify(data) }),
        );
    });
    let finalized = false,
      posts = 0;
    await page.route("**/api/**", async (route) => {
      const u = new URL(route.request().url());
      let body: any = {};
      if (route.request().method() === "POST") posts++;
      if (u.pathname === "/api/auth/status") body = { authenticated: true };
      if (u.pathname === "/api/proposals") body = { items: [] };
      if (u.pathname === "/api/codex/threads") body = { data: [] };
      if (u.pathname === "/api/companion/threads")
        body = {
          threads: [
            { thread_id: "a", title: "A", created_at: "2026-09-20T21:00:00Z" },
            { thread_id: "b", title: "B", created_at: "2026-09-20T20:00:00Z" },
          ],
        };
      if (u.pathname === "/api/companion/threads/a")
        body = {
          messages: finalized
            ? [
                {
                  message_id: "final-a",
                  turn_run_id: "run-a",
                  kind: "assistant",
                  sequence: 2,
                  content: "Complete answer",
                  status: "finalized",
                },
              ]
            : [],
        };
      if (u.pathname === "/api/companion/threads/b") body = { messages: [] };
      await route.fulfill({ json: body });
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Companion", exact: true }).click();
    await chooseConversation(page, "a");
    const expectedDate = await page.evaluate(() =>
      new Date("2026-09-20T21:00:00Z").toLocaleString(),
    );
    await page.locator(".conversation-list-toggle").click();
    await expect(
      page
        .locator('[data-thread-id="a"]')
        .getByText(expectedDate, { exact: true }),
    ).toBeVisible();
    await page.locator(".conversation-list-toggle").click();
    await page.waitForFunction(() => (window as any).streams.length === 1);
    await page.evaluate(() =>
      (window as any).emit(0, "projection_update", {
        type: "projection_update",
        state: {
          thread_id: "a",
          items: [
            { text: { id: "live-a", run_id: "run-a", body: "Partial answer" } },
            {
              capability_activity: {
                turn_run_id: "run-a",
                capability_id: "mcp-leam.leam_context",
                status: "started",
              },
            },
          ],
        },
      }),
    );
    await expect(
      page.getByText("Partial answer", { exact: true }),
    ).toBeVisible();
    const liveAnswer = page.getByLabel("Live companion response");
    await expect(liveAnswer.getByRole("button", { name: "Read aloud", exact: true })).toBeVisible();
    expect(await liveAnswer.evaluate(el => {
      const prose = el.querySelector(".prose")!, speaker = el.querySelector('[aria-label="Read aloud"]')!;
      return !!(prose.compareDocumentPosition(speaker) & Node.DOCUMENT_POSITION_FOLLOWING) &&
        speaker.getBoundingClientRect().top >= prose.getBoundingClientRect().bottom;
    })).toBe(true);
    await page.screenshot({ path: info.outputPath("companion-live-speaker.png") });

    await expect(
      page
        .getByRole("status")
        .filter({ hasText: "Using mcp-leam.leam_context" }),
    ).toBeVisible();
    await page.waitForTimeout(2200);
    await expect(
      page.getByText("Partial answer", { exact: true }),
    ).toBeVisible();
    finalized = true;
    await page.evaluate(() =>
      (window as any).emit(0, "projection_update", {
        type: "projection_update",
        state: {
          thread_id: "a",
          items: [
            {
              text: { id: "live-a", run_id: "run-a", body: "Complete answer" },
            },
            { run_status: { run_id: "run-a", status: "completed" } },
          ],
        },
      }),
    );
    await expect(
      page.getByText("Complete answer", { exact: true }),
    ).toHaveCount(1);
    await expect(page.getByLabel("Live companion response")).toHaveCount(0);
    await chooseConversation(page, "b");
    await page.waitForFunction(() => (window as any).streams.length === 2);
    await page.evaluate(() =>
      (window as any).emit(0, "projection_update", {
        type: "projection_update",
        state: {
          thread_id: "a",
          items: [
            { text: { id: "late", run_id: "late", body: "Wrong thread" } },
          ],
        },
      }),
    );
    await expect(page.getByText("Wrong thread", { exact: true })).toHaveCount(
      0,
    );
    await page.evaluate(() =>
      (window as any).emit(1, "projection_update", {
        type: "projection_update",
        state: {
          thread_id: "b",
          items: [
            {
              run_status: {
                run_id: "failed-b",
                status: "failed",
                failure_summary: "Provider unavailable",
              },
            },
          ],
        },
      }),
    );
    await expect(
      page.getByText("Provider unavailable", { exact: true }),
    ).toBeVisible();
    await page.evaluate(() => (window as any).streams[1].onerror());
    await expect(page.getByRole("status").filter({ hasText: /Live progress reconnecting/ })).toBeVisible();
    expect(await page.evaluate(() => (window as any).streams[0].closed)).toBe(
      true,
    );
    expect(posts).toBe(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });

test("Stop targets the displayed run, retries one action ID and waits for cancellation", async ({
  page,
}) => {
  await page.addInitScript(() => {
    class Stream extends EventTarget {
      onopen: any;
      onerror: any;
      constructor(url: string) {
        super();
        if (url.includes("/companion/")) (window as any).companionStream = this;
      }
      close() {}
    }
    (window as any).EventSource = Stream;
  });
  const calls: { path: string; body: any }[] = [];
  const run = "19e9c46c-a47b-4e01-8cb4-f4c5c4c26c63";
  await page.route("**/api/**", async (route) => {
    const u = new URL(route.request().url());
    let body: any = {};
    if (u.pathname === "/api/auth/status") body = { authenticated: true };
    if (u.pathname === "/api/proposals") body = { items: [] };
    if (u.pathname === "/api/companion/threads")
      body = { threads: [{ thread_id: "a", title: "A" }] };
    if (u.pathname === "/api/companion/threads/a") body = { messages: [] };
    if (route.request().method() === "POST") {
      calls.push({ path: u.pathname, body: route.request().postDataJSON() });
      if (calls.length === 1) {
        await route.fulfill({
          status: 503,
          json: { detail: "Connection interrupted; retry Stop" },
        });
        return;
      }
      body = {
        run_id: run,
        status: "CancelRequested",
        event_cursor: 23,
        already_terminal: false,
      };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Companion", exact: true }).click();
  await chooseConversation(page, "a");
  await page.waitForFunction(() => !!(window as any).companionStream);
  await page.evaluate(
    (run) =>
      (window as any).companionStream.dispatchEvent(
        new MessageEvent("projection_update", {
          data: JSON.stringify({
            type: "projection_update",
            state: {
              thread_id: "a",
              items: [
                { text: { id: "live", run_id: run, body: "Partial retained" } },
              ],
            },
          }),
        }),
      ),
    run,
  );
  await page
    .getByRole("button", { name: "Stop response", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText(
    "Connection interrupted; retry Stop",
  );
  await page
    .getByRole("button", { name: "Stop response", exact: true })
    .click();
  await expect(
    page.getByLabel("Live companion response").getByText("Stop requested…", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".composer").getByRole("button", { name: "Stop requested…", exact: true }),
  ).toBeDisabled();
  await page.evaluate(
    (run) =>
      (window as any).companionStream.dispatchEvent(
        new MessageEvent("projection_update", {
          data: JSON.stringify({
            type: "projection_update",
            state: {
              thread_id: "a",
              items: [
                {
                  capability_activity: {
                    turn_run_id: run,
                    capability_id: "mcp-leam.leam_context",
                    status: "started",
                  },
                },
                {
                  work_summary: { run_id: run, body: "Finishing a tool call" },
                },
              ],
            },
          }),
        }),
      ),
    run,
  );
  await expect(
    page.getByLabel("Live companion response").getByText("Stop requested…", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator(".composer").getByRole("button", { name: "Stop requested…", exact: true }),
  ).toBeDisabled();
  expect(calls).toHaveLength(2);
  expect(calls[0].path).toBe(`/api/companion/threads/a/runs/${run}/cancel`);
  expect(calls[1]).toEqual(calls[0]);
  expect(calls[0].body.requestId).toMatch(/^[0-9a-f-]{36}$/);
  await page.evaluate(
    (run) =>
      (window as any).companionStream.dispatchEvent(
        new MessageEvent("projection_update", {
          data: JSON.stringify({
            type: "projection_update",
            state: {
              thread_id: "a",
              items: [{ run_status: { run_id: run, status: "cancelled" } }],
            },
          }),
        }),
      ),
    run,
  );
  await expect(page.locator(".composer .companion-stop")).toHaveCount(0);
  await expect(
    page.getByText("Partial retained", { exact: true }),
  ).toBeVisible();
});

test('composer Stop remains available with a nonfinal saved draft and retries exact cancellation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const run = '19e9c46c-a47b-4e01-8cb4-f4c5c4c26c63';
  await page.addInitScript(() => {
    const w = window as any;
    w.EventSource = class extends EventTarget {
      constructor(public url: string) { super(); if(url.includes('/companion/')) w.source = this; }
      close() {}
    };
  });
  const cancels: any[] = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === '/api/auth/status') body = { authenticated: true };
    if (path === '/api/companion/threads') body = { threads: [{ thread_id: 'a', title: 'A' }] };
    if (path === '/api/companion/threads/a') body = { messages: [{ message_id: 'draft-a', kind: 'assistant', turn_run_id: run, content: 'Partial answer', status: 'draft', sequence: 2 }] };
    if (path.endsWith('/cancel')) {
      cancels.push({ path, ...route.request().postDataJSON() });
      if (cancels.length === 1) return route.fulfill({ status: 503, json: { detail: 'Temporary cancellation failure' } });
      body = { run_id: run, status: 'CancelRequested', already_terminal: false, event_cursor: 5 };
    }
    await route.fulfill({ json: body });
  });
  await page.goto('/?view=companion');
  await chooseConversation(page, 'a');
  await page.waitForFunction(() => !!(window as any).source);
  await page.evaluate((run) => (window as any).source.dispatchEvent(new MessageEvent('projection_update', { data: JSON.stringify({ type: 'projection_update', state: { thread_id: 'a', items: [{ text: { run_id: run, body: 'Partial answer' } }, { run_status: { run_id: run, status: 'running' } }] } }) })), run);
  await expect(page.getByLabel('Live companion response')).toHaveCount(1);
  const stop = page.locator('.composer').getByRole('button', { name: 'Stop response', exact: true });
  await expect(stop).toBeVisible();
  await stop.click();
  await expect(page.getByText('Temporary cancellation failure')).toBeVisible();
  await expect(stop).toBeEnabled();
  await stop.click();
  await expect(page.locator('.composer').getByRole('button', { name: 'Stop requested…' })).toBeDisabled();
  expect(cancels).toHaveLength(2);
  expect(cancels[0]).toEqual(cancels[1]);
  expect(cancels[0].path).toBe(`/api/companion/threads/a/runs/${run}/cancel`);
  await page.evaluate((run) => (window as any).source.dispatchEvent(new MessageEvent('cancelled', { data: JSON.stringify({ type: 'cancelled', run_state: { turn_run_id: run } }) })), run);
  await expect(page.locator('.companion-stop')).toHaveCount(0);
});
