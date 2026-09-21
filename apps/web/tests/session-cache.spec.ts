import { chooseConversation } from "./navigation";
import { test, expect } from "@playwright/test";
import { navigate } from "./navigation";
for (const surface of ["companion", "coding"])
  test(`${surface} restores history and draft before held revalidation`, async ({
    page,
  }) => {
    await page.addInitScript(() => {
      const streams: any[] = [];
      (window as any).clockOffset = 0;
      const now = Date.now.bind(Date);
      Date.now = () => now() + (window as any).clockOffset;
      class Source extends EventTarget {
        onopen: any;
        onmessage: any;
        closed = false;
        constructor(public url: string) {
          super();
          streams.push(this);
        }
        close() {
          this.closed = true;
        }
      }
      (window as any).EventSource = Source;
      (window as any).streams = streams;
    });
    let hold = false;
    let releases: (() => void)[] = [];
    const path =
      surface === "companion"
        ? "/api/companion/threads/cached"
        : "/api/codex/threads/cached/turns";
    await page.route("**/api/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      let data: any = { items: [], data: [], threads: [] };
      if (p === "/api/auth/status")
        data = { authenticated: true, configured: true };
      if (p === "/api/codex/threads")
        data = { data: [{ id: "cached", name: "Cached coding", cwd: "/tmp" }] };
      if (p === "/api/companion/threads")
        data = {
          threads: [
            {
              thread_id: "cached",
              title: "Cached companion",
              created_at: "2026-09-20T10:00:00Z",
            },
          ],
        };
      if (p === "/api/codex/threads/cached")
        data = {
          thread: { id: "cached", name: "Cached coding" },
          connected: true,
        };
      if (p === path) {
        if (hold) await new Promise<void>((r) => releases.push(r));
        data =
          surface === "companion"
            ? {
                messages: [
                  {
                    message_id: "m",
                    kind: "assistant",
                    sequence: 1,
                    content: "Previously loaded answer",
                    status: "finalized",
                  },
                ],
              }
            : {
                data: [
                  {
                    id: "turn",
                    status: "completed",
                    items: [
                      {
                        id: "m",
                        type: "agentMessage",
                        text: "Previously loaded answer",
                      },
                    ],
                  },
                ],
              };
      }
      await route.fulfill({ json: data });
    });
    await page.goto("/?view=" + surface);
    if (surface === "companion")
      await chooseConversation(page, "cached");
    else await page.getByRole("button", { name: /^Open Cached coding/ }).click();
    await expect(
      page.getByText("Previously loaded answer", { exact: true }),
    ).toBeVisible();
    const input = page.getByRole("textbox", {
      name: surface === "companion" ? "Message Leam" : "Message Codex",
      exact: true,
    });
    await input.fill("Unsent draft stays here");
    hold = true;
    await navigate(page, "Settings");
    expect(
      await page.evaluate(() =>
        (window as any).streams.every((s: any) => s.closed),
      ),
    ).toBe(true);
    await navigate(page, surface === "companion" ? "Companion" : "Coding");
    await expect(
      page.getByText("Previously loaded answer", { exact: true }),
    ).toBeVisible();
    await expect(input).toHaveValue("Unsent draft stays here");
    expect(releases).toHaveLength(0);
    await navigate(page, "Settings");
    await page.evaluate(() => {
      (window as any).clockOffset = 6000;
    });
    await navigate(page, surface === "companion" ? "Companion" : "Coding");
    await expect(
      page.getByText("Previously loaded answer", { exact: true }),
    ).toBeVisible();
    await expect.poll(() => releases.length).toBe(1);
    if (surface === "coding") {
      await page.evaluate(() => {
        const stream = (window as any).streams
          .filter((s: any) => !s.closed)
          .at(-1);
        stream.onmessage({
          data: JSON.stringify({
            topic: "codex",
            payload: {
              method: "turn/started",
              params: {
                threadId: "cached",
                turn: { id: "live", status: "inProgress", items: [] },
              },
            },
          }),
        });
        stream.onmessage({
          data: JSON.stringify({
            topic: "codex",
            payload: {
              method: "item/agentMessage/delta",
              params: {
                threadId: "cached",
                turnId: "live",
                itemId: "partial",
                delta: "Newest live partial",
              },
            },
          }),
        });
      });
      await expect(
        page.getByText("Newest live partial", { exact: true }),
      ).toBeVisible();
    }
    releases.splice(0).forEach((r) => r());
    if (surface === "coding")
      await expect(
        page.getByText("Newest live partial", { exact: true }),
      ).toBeVisible();
    hold = false;
    expect(
      await page.evaluate(
        () => (window as any).streams.filter((s: any) => !s.closed).length,
      ),
    ).toBe(1);
  });

test("authenticated API loss clears private display even with blocked sessionStorage", async ({
  page,
}) => {
  await page.addInitScript(() => {
    class Source extends EventTarget {
      onopen: any;
      onmessage: any;
      close() {}
    }
    (window as any).EventSource = Source;
  });
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/api/companion/threads" && route.request().method() === "POST") {
      await route.fulfill({ status: 401, json: { detail: "Sign in to Leam" } });
      return;
    }
    let data: any = { items: [], data: [] };
    if (p === "/api/auth/status")
      data = { authenticated: true, configured: true };
    if (p === "/api/companion/threads")
      data = { threads: [{ thread_id: "private", title: "Private chat" }] };
    if (p === "/api/companion/threads/private")
      data = {
        messages: [
          {
            message_id: "m",
            kind: "assistant",
            sequence: 1,
            content: "Private cached transcript",
          },
        ],
      };
    await route.fulfill({ json: data });
  });
  await page.goto("/?view=companion");
  await chooseConversation(page, "private");
  await expect(
    page.getByText("Private cached transcript", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("Message Leam").fill("Private draft");
  await page.evaluate(() =>
    Object.defineProperty(window, "sessionStorage", {
      configurable: true,
      get() {
        throw new DOMException("Blocked", "SecurityError");
      },
    }),
  );
  await page
    .getByRole("button", { name: "New conversation", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Welcome back." }),
  ).toBeVisible();
  await expect(
    page.getByText("Private cached transcript", { exact: true }),
  ).toHaveCount(0);
});

test("offline startup offers reconnect rather than first-time pairing", async ({
  page,
}) => {
  await page.addInitScript(() =>
    Object.defineProperty(navigator, "onLine", { get: () => false }),
  );
  await page.route("**/api/**", (route) => route.abort("internetdisconnected"));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Reconnect to Leam" }),
  ).toBeVisible();
  await expect(page.getByLabel("Pairing code")).toHaveCount(0);
});

test("offline to online rechecks authentication without displaying a new-install form", async ({
  page,
}) => {
  await page.addInitScript(() => {
    (window as any).online = false;
    Object.defineProperty(navigator, "onLine", {
      get: () => (window as any).online,
    });
  });
  let checking = false;
  let release!: () => void;
  const wait = new Promise<void>((r) => (release = r));
  await page.route("**/api/auth/status", async (route) => {
    if (!checking) {
      await route.abort("internetdisconnected");
      return;
    }
    await wait;
    await route.fulfill({ json: { authenticated: false, configured: true } });
  });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Reconnect to Leam" }),
  ).toBeVisible();
  checking = true;
  await page.evaluate(() => {
    (window as any).online = true;
    window.dispatchEvent(new Event("online"));
  });
  await expect(
    page.getByRole("heading", { name: "Connecting to Leam" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Make yourself at home." }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Pairing code")).toHaveCount(0);
  release();
  await expect(
    page.getByRole("heading", { name: "Welcome back." }),
  ).toBeVisible();
});

test("late authentication body cannot restore the signed-in display after logout", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    let reads = 0;
    window.fetch = ((input: any, init?: RequestInit) => {
      const path = typeof input === "string" ? input : input.url;
      if (path === "/api/auth/status" && ++reads > 1)
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers(),
          json: () =>
            new Promise((resolve) => {
              (window as any).releaseOldAuth = () =>
                resolve({ authenticated: true, configured: true });
            }),
        } as Response);
      return original(input, init);
    }) as typeof fetch;
    class Source extends EventTarget {
      onopen: any;
      onmessage: any;
      close() {}
    }
    (window as any).EventSource = Source;
  });
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    await route.fulfill({
      json:
        p === "/api/auth/status"
          ? { authenticated: true, configured: true }
          : p === "/api/auth/logout"
            ? { authenticated: false, configured: true }
            : { items: [], data: [], threads: [], providers: [] },
    });
  });
  await page.goto("/?view=settings");
  await page.getByRole("button", { name: "Sign out" }).waitFor();
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await page.waitForFunction(
    () => typeof (window as any).releaseOldAuth === "function",
  );
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome back." }),
  ).toBeVisible();
  await page.evaluate(() => {
    (window as any).releaseOldAuth();
  });
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(
    page.getByRole("heading", { name: "Welcome back." }),
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Main navigation" }),
  ).toHaveCount(0);
});
