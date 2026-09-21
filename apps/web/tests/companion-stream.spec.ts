import { chooseConversation } from "./navigation";
import { test, expect } from "@playwright/test";

for (const width of [390, 1440])
  test(`companion live progress, reconciliation and thread isolation ${width}`, async ({
    page,
  }) => {
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
    await expect(page.getByText(/Live progress reconnecting/)).toBeVisible();
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
    page.getByText("Stop requested…", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Stop response", exact: true }),
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
    page.getByText("Stop requested…", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Stop response", exact: true }),
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
  await expect(
    page.getByRole("button", { name: "Stop response", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Partial retained", { exact: true }),
  ).toBeVisible();
});
