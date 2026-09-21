import { test, expect, type Page } from "@playwright/test";
import { chooseConversation, navigate } from "./navigation";

async function fixture(page: Page, snapshot: any[] = []) {
  await page.addInitScript(() => {
    (window as any).streams = [];
    (window as any).eventSequence = 100;
    (window as any).EventSource = class {
      onmessage: any;
      onopen: any;
      closed = false;
      constructor(public url: string) {
        (window as any).streams.push(this);
      }
      close() {
        this.closed = true;
      }
    };
  });
  const reads: string[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    reads.push(path);
    let body: any = {
      items: [],
      data: [],
      threads: [],
      accounts: [],
      providers: [],
    };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/threads")
      body = {
        data: [
          { id: "a", name: "Thread A" },
          { id: "b", name: "Thread B" },
        ],
      };
    if (/\/threads\/[ab]$/.test(path))
      body = { connected: true, thread: { id: path.split("/").pop() } };
    if (path.endsWith("/turns"))
      body = { data: path.includes("/a/") ? snapshot : [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await chooseConversation(page, "a", "coding");
  await expect(
    page.getByRole("textbox", { name: "Message Codex" }),
  ).toHaveAttribute("placeholder", "Continue with Codex…");
  return reads;
}
async function event(
  page: Page,
  method: string,
  params: any,
  sequence?: number,
  streamIndex?: number,
) {
  await page.evaluate(
    ({ method, params, sequence, streamIndex }) => {
      const streams = (window as any).streams;
      const stream =
        streamIndex === undefined ? streams.at(-1) : streams[streamIndex];
      stream.onmessage({
        data: JSON.stringify({
          id: sequence ?? ++(window as any).eventSequence,
          topic: "codex",
          payload: {
            method,
            params: { threadId: "a", turnId: "turn-a", ...params },
          },
        }),
      });
    },
    { method, params, sequence, streamIndex },
  );
}
async function open(page: Page) {
  await page.evaluate(() => (window as any).streams.at(-1).onopen());
}

test("ordinary replies stream while a history read is held, without per-delta history polling", async ({
  page,
}) => {
  await fixture(page);
  let heldReads = 0;
  let release!: () => void, entered!: () => void;
  const held = new Promise<void>((r) => (release = r)),
    started = new Promise<void>((r) => (entered = r));
  await page.route("**/api/codex/threads/a/turns", async (route) => {
    heldReads++;
    entered();
    await held;
    await route.fulfill({ json: { data: [] } });
  });
  await open(page);
  await started;
  await event(page, "turn/started", {
    turn: { id: "turn-a", status: "inProgress", items: [] },
  });
  await event(page, "item/agentMessage/delta", {
    itemId: "reply",
    delta: "Incremental",
  });
  await expect(page.getByText("Incremental", { exact: true })).toBeVisible();
  for (const delta of [" reply", " arrives", " now"])
    await event(page, "item/agentMessage/delta", { itemId: "reply", delta });
  await page.waitForTimeout(1100);
  await expect(
    page.getByText("Incremental reply arrives now", { exact: true }),
  ).toBeVisible();
  expect(heldReads).toBe(1);
  const reconciled = page.waitForResponse((response) =>
    response.url().endsWith("/api/codex/threads/a/turns"),
  );
  release();
  await reconciled;
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(
    page.getByText("Incremental reply arrives now", { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => (window as any).streams.at(-1).url),
  ).toContain("thread_id=a");
});

test("replay replaces snapshot partials, deduplicates events and keeps tool activity through refresh", async ({
  page,
}) => {
  await fixture(page, [
    {
      id: "turn-a",
      status: "inProgress",
      items: [
        {
          id: "user",
          type: "userMessage",
          content: [{ type: "text", text: "Original question" }],
        },
        { id: "reply", type: "agentMessage", text: "Hello" },
      ],
    },
  ]);
  await open(page);
  await expect(page.getByText("Hello", { exact: true })).toBeVisible();
  await event(
    page,
    "turn/started",
    { turn: { id: "turn-a", status: "inProgress", items: [] } },
    101,
  );
  await event(
    page,
    "item/agentMessage/delta",
    { itemId: "reply", delta: "Hello" },
    102,
  );
  await event(
    page,
    "item/agentMessage/delta",
    { itemId: "reply", delta: "Hello" },
    102,
  );
  await event(
    page,
    "item/started",
    {
      item: {
        id: "tool",
        type: "commandExecution",
        command: "printf fixture",
        status: "inProgress",
      },
    },
    103,
  );
  await event(
    page,
    "item/completed",
    {
      item: {
        id: "tool",
        type: "commandExecution",
        command: "printf fixture",
        status: "completed",
        aggregatedOutput: "Fixture result",
      },
    },
    104,
  );
  await open(page);
  await event(
    page,
    "item/agentMessage/delta",
    { itemId: "reply", delta: " world" },
    105,
  );
  await expect(page.getByText("Hello world", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Original question", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("HelloHello")).toHaveCount(0);
  await page.getByText(/Activity ·/).click();
  await page.getByText("command Execution completed", { exact: false }).click();
  await expect(page.getByText("printf fixture", { exact: true })).toBeVisible();
});

test("terminal history replaces partial exactly and replay cannot downgrade a completed turn", async ({
  page,
}) => {
  await fixture(page);
  await open(page);
  await event(page, "turn/started", {
    turn: { id: "turn-a", status: "inProgress", items: [] },
  });
  await event(page, "item/agentMessage/delta", {
    itemId: "reply",
    delta: "Draft answer",
  });
  await expect(page.getByText("Draft answer", { exact: true })).toBeVisible();
  await page.route("**/api/codex/threads/a/turns", (route) =>
    route.fulfill({
      json: {
        data: [
          {
            id: "turn-a",
            status: "completed",
            items: [
              {
                id: "reply",
                type: "agentMessage",
                text: "Authoritative final answer",
              },
            ],
          },
        ],
      },
    }),
  );
  await event(page, "turn/completed", {
    turn: { id: "turn-a", status: "completed", items: [] },
  });
  await expect(
    page.getByText("Authoritative final answer", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Stop turn", exact: true }),
  ).toHaveCount(0);
  await event(page, "turn/started", {
    turn: { id: "turn-a", status: "inProgress", items: [] },
  });
  await event(page, "item/agentMessage/delta", {
    itemId: "reply",
    delta: "Draft answer",
  });
  await expect(
    page.getByText("Authoritative final answer", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Draft answer", { exact: true })).toHaveCount(0);
});

test("unknown-baseline deltas reconcile without duplicating snapshot text", async ({
  page,
}) => {
  await fixture(page, [
    {
      id: "turn-a",
      status: "inProgress",
      items: [{ id: "reply", type: "agentMessage", text: "Already present" }],
    },
  ]);
  await open(page);
  await event(page, "item/agentMessage/delta", {
    itemId: "reply",
    delta: "Already present",
  });
  await expect(
    page.getByText("Already present", { exact: true }),
  ).toBeVisible();
  await page.waitForTimeout(1200);
  await expect(page.getByText("Already presentAlready present")).toHaveCount(0);
});

test("navigation closes and fences the old selected stream and late history", async ({
  page,
}) => {
  await fixture(page);
  let release!: () => void, entered!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  const started = new Promise<void>((resolve) => (entered = resolve));
  await page.route("**/api/codex/threads/a/turns", async (route) => {
    entered();
    await held;
    await route.fulfill({
      json: {
        data: [
          {
            id: "late",
            status: "completed",
            items: [
              { id: "late-item", type: "agentMessage", text: "Late history A" },
            ],
          },
        ],
      },
    });
  });
  await open(page);
  await started;
  const index = await page.evaluate(() => (window as any).streams.length - 1);
  await event(page, "turn/started", {
    turn: { id: "turn-a", status: "inProgress", items: [] },
  });
  await event(page, "item/agentMessage/delta", {
    itemId: "reply",
    delta: "Private A",
  });
  await chooseConversation(page, "b", "coding");
  const reconciled = page.waitForResponse((response) =>
    response.url().endsWith("/api/codex/threads/a/turns"),
  );
  release();
  await reconciled;
  await event(
    page,
    "item/agentMessage/delta",
    { threadId: "b", itemId: "reply", delta: "Wrong stream" },
    undefined,
    index,
  );
  await expect(page.getByText("Private A", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Wrong stream", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Late history A", { exact: true })).toHaveCount(
    0,
  );
  expect(
    await page.evaluate((i) => (window as any).streams[i].closed, index),
  ).toBe(true);
  await navigate(page, "Today");
  expect(await page.evaluate(() => (window as any).streams.at(-1).closed)).toBe(
    true,
  );
});
