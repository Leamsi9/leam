import { chooseConversation } from "./navigation";
import { test, expect } from "@playwright/test";

// Transport fixtures exercise UI races; these are not live Codex acceptance.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    class FakeEvents {
      onmessage: any;
      onopen: any;
      constructor() {
        (window as any).events = this;
      }
      close() {}
    }
    (window as any).EventSource = FakeEvents;
  });
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    let body: any = {};
    if (p === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (p === "/api/codex/threads")
      body = {
        data: [
          { id: "a", name: "Thread A" },
          { id: "b", name: "Thread B" },
        ],
      };
    if (/\/threads\/[ab]$/.test(p)) body = { connected: true };
    if (p.endsWith("/turns")) body = { data: [], nextCursor: "older" };
    // Settings mounts the live permission catalog, whose contract always supplies items.
    if (p === "/api/companion/tools") body = { items: [], autoApprove: false };
    if (p.endsWith("/requests") || p === "/api/imports" || p === "/api/backups")
      body = { items: [] };
    if (p.includes("/submissions/")) body = { state: "notSubmitted" };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await chooseConversation(page,"a","coding");
  await expect(
    page.getByRole("textbox", { name: "Message Codex" }),
  ).toBeEnabled();
});

test("a delayed submission cannot clear or send another thread draft", async ({
  page,
}) => {
  let release!: () => void;
  const waiting = new Promise<void>((r) => (release = r));
  let started!: () => void;
  const entered = new Promise<void>((r) => (started = r));
  const sent: any[] = [];
  await page.route("**/api/codex/submissions/*", async (route) => {
    started();
    await waiting;
    await route.fulfill({ json: { state: "notSubmitted" } });
  });
  await page.route("**/api/codex/threads/a/turns", async (route) => {
    if (route.request().method() === "POST") {
      sent.push(route.request().postDataJSON());
      await route.fulfill({ json: { turn: { id: "turn-a" } } });
    } else await route.fallback();
  });
  await page
    .getByRole("textbox", { name: "Message Codex" })
    .fill("A private draft");
  await page.getByRole("button", { name: "Send message" }).click();
  await entered;
  await chooseConversation(page,"b","coding");
  await page
    .getByRole("textbox", { name: "Message Codex" })
    .fill("B untouched");
  release();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0].text).toBe("A private draft");
  expect(sent[0].requestId).toBeTruthy();
  await expect(
    page.getByRole("textbox", { name: "Message Codex" }),
  ).toHaveValue("B untouched");
});

test("older history arriving after navigation stays in its original thread", async ({
  page,
}) => {
  let release!: () => void;
  const waiting = new Promise<void>((r) => (release = r));
  let started!: () => void;
  const entered = new Promise<void>((r) => (started = r));
  await page.route("**/api/codex/threads/a/turns?cursor=*", async (route) => {
    started();
    await waiting;
    await route.fulfill({
      json: {
        data: [
          {
            id: "old",
            items: [
              { id: "old-item", type: "agentMessage", text: "Private old A" },
            ],
          },
        ],
      },
    });
  });
  await page.getByRole("button", { name: "Earlier messages" }).click();
  await entered;
  await chooseConversation(page,"b","coding");
  release();
  await page.waitForTimeout(100);
  await expect(page.getByText("Private old A")).toHaveCount(0);
});

test("completed streamed items reconcile separately and logout clears drafts", async ({
  page,
}) => {
  const event = async (method: string, params: any) =>
    page.evaluate(
      ({ method, params }) => {
        (window as any).events.onmessage({
          data: JSON.stringify({
            topic: "codex",
            payload: {
              method,
              params: { threadId: "a", turnId: "turn-a", ...params },
            },
          }),
        });
      },
      { method, params },
    );
  await event("turn/started", {
    turn: { id: "turn-a", status: "inProgress", items: [] },
  });
  await event("item/agentMessage/delta", {
    itemId: "one",
    delta: "First commentary",
  });
  await expect(page.getByText("First commentary", { exact: true })).toHaveCount(
    1,
  );
  await event("item/completed", {
    item: { id: "one", type: "agentMessage", text: "First commentary" },
  });
  await event("item/agentMessage/delta", {
    itemId: "two",
    delta: "Second answer",
  });
  await expect(page.getByText("First commentarySecond answer")).toHaveCount(0);
  await expect(page.getByText("Second answer", { exact: true })).toHaveCount(1);
  await page.evaluate(() =>
    sessionStorage.setItem(
      "leam-submission:a",
      JSON.stringify({ text: "private" }),
    ),
  );
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome back." }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => sessionStorage.getItem("leam-submission:a")),
  ).toBeNull();
});

test("reconnect replaces partial output instead of replaying it twice", async ({
  page,
}) => {
  await page.route("**/api/codex/threads/a/turns", (route) =>
    route.fulfill({
      json: {
        data: [
          {
            id: "turn-a",
            status: "inProgress",
            items: [
              { id: "one", type: "agentMessage", text: "Partial answer" },
            ],
          },
        ],
      },
    }),
  );
  await page.evaluate(() => (window as any).events.onopen());
  await expect(page.getByText("Partial answer", { exact: true })).toHaveCount(
    1,
  );
  await page.evaluate(() =>
    (window as any).events.onmessage({
      data: JSON.stringify({
        topic: "codex",
        payload: {
          method: "turn/started",
          params: { threadId: "a", turn: { id: "turn-a" } },
        },
      }),
    }),
  );
  await page.evaluate(() =>
    (window as any).events.onmessage({
      data: JSON.stringify({
        topic: "codex",
        payload: {
          method: "item/agentMessage/delta",
          params: {
            threadId: "a",
            turnId: "turn-a",
            itemId: "one",
            delta: "Partial answer",
          },
        },
      }),
    }),
  );
  await page.waitForTimeout(400);
  await expect(page.getByText("Partial answer", { exact: true })).toHaveCount(
    1,
  );
  await expect(page.getByText("Partial answerPartial answer")).toHaveCount(0);
});

test("terminal history waits for coherent current-thread state before publication", async ({
  page,
}) => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  let entered!: () => void;
  const started = new Promise<void>((resolve) => (entered = resolve));
  await page.route("**/api/codex/threads/a/turns", (route) =>
    route.fulfill({
      json: {
        data: [
          {
            id: "finished",
            status: "completed",
            items: [
              {
                id: "reply",
                type: "agentMessage",
                text: "Final coherent reply",
              },
            ],
          },
        ],
      },
    }),
  );
  await page.route("**/api/codex/threads/a", async (route) => {
    entered();
    await held;
    await route.fulfill({
      json: { connected: true, activeTurnId: null, thread: { id: "a" } },
    });
  });
  await page.evaluate(() => (window as any).events.onopen());
  await started;
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(
    page.getByText("Final coherent reply", { exact: true }),
  ).toHaveCount(0);
  release();
  await expect(
    page.getByText("Final coherent reply", { exact: true }),
  ).toBeVisible();
});
