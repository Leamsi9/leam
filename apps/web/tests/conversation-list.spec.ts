import { test, expect } from "@playwright/test";
import { chooseConversation, navigate } from "./navigation";
async function setup(page: any, kind = "companion") {
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      onopen: any;
      onmessage: any;
      close() {}
    };
  });
  const calls: any[] = [],
    deleted = new Set<string>();
  let title = "First conversation",
    revision = 0;
  await page.route("**/api/**", async (route: any) => {
    const req = route.request(),
      u = new URL(req.url()),
      p = u.pathname,
      method = req.method();
    calls.push({ path: p, query: u.search, method, body: req.postDataJSON() });
    let body: any = {
      items: [],
      data: [],
      threads: [],
      providers: [],
      models: [],
      accounts: [],
    };
    if (p === "/api/auth/status") body = { authenticated: true };
    const base =
      kind === "companion" ? "/api/companion/threads" : "/api/codex/threads";
    if (p === base) {
      const cursor = u.searchParams.get("cursor");
      const items = (
        cursor
          ? [
              {
                id: "b",
                thread_id: "b",
                title: "Second conversation",
                name: "Second conversation",
                created_at: "2026-09-20T09:00:00Z",
              },
            ]
          : [
              {
                id: "a",
                thread_id: "a",
                title,
                name: title,
                title_revision: revision,
                created_at: "2026-09-20T10:00:00Z",
              },
            ]
      ).filter((item) => !deleted.has(item.id));
      body =
        kind === "companion"
          ? { threads: items, next_cursor: cursor ? null : "next /&+" }
          : { data: items, nextCursor: cursor ? null : "next /&+" };
    }
    if (p === base + "/a" && method === "PATCH") {
      title = req.postDataJSON().title;
      revision++;
      body = {
        thread_id: "a",
        id: "a",
        title,
        name: title,
        title_revision: revision,
      };
    }
    if (p.startsWith(base + "/") && method === "DELETE") {
      deleted.add(p.split("/").pop()!);
      body = { deleted: true };
    }
    if (p === base + "/a" && method === "GET")
      body =
        kind === "companion"
          ? {
              messages: [
                {
                  message_id: "m",
                  kind: "assistant",
                  status: "finalized",
                  sequence: 1,
                  content: "First answer",
                },
              ],
            }
          : { thread: { id: "a", name: title }, connected: true };
    if (p === base + "/b" && method === "GET")
      body =
        kind === "companion"
          ? { messages: [] }
          : {
              thread: { id: "b", name: "Second conversation" },
              connected: true,
            };
    if (p.endsWith("/turns")) body = { data: [] };
    await route.fulfill({ json: body });
  });
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/?view=" + kind);
  return { calls, deleted };
}
test("Companion lists lazily, title tap renames with Enter/Escape, and selection preserves draft", async ({
  page,
}) => {
  const f = await setup(page);
  await expect(
    page.getByRole("button", { name: "Rename First conversation" }),
  ).toBeVisible();
  expect(
    f.calls.filter((c) => c.path === "/api/companion/threads/a"),
  ).toHaveLength(0);
  await expect(page.getByText(/9\/20\/2026/)).toBeVisible();
  await page.getByRole("button", { name: "Rename First conversation" }).click();
  await page.getByLabel("Conversation title").fill("Cancelled title");
  await page.keyboard.press("Escape");
  expect(f.calls.filter((c) => c.method === "PATCH")).toHaveLength(0);
  await page.getByRole("button", { name: "Rename First conversation" }).click();
  await page.getByLabel("Conversation title").fill("Renamed conversation");
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("button", { name: "Rename Renamed conversation" }),
  ).toBeVisible();
  expect(f.calls.find((c) => c.method === "PATCH").body).toEqual({
    title: "Renamed conversation",
    revision: 0,
  });
  await chooseConversation(page, "a");
  await page.getByLabel("Message Leam").fill("Keep this draft");
  await expect(page.locator(".conversation-list-toggle")).toHaveAttribute(
    "aria-expanded",
    "false",
  );
  await page.locator(".conversation-list-toggle").click();
  await page.getByRole("button", { name: "More conversations" }).click();
  await expect(
    page.getByRole("button", { name: "Open Second conversation" }),
  ).toBeVisible();
  expect(f.calls.find((c) => c.query.includes("cursor=")).query).toContain(
    "cursor=next%20%2F%26%2B",
  );
  expect(
    f.calls.filter((c) => c.path === "/api/companion/threads/b"),
  ).toHaveLength(0);
  await chooseConversation(page, "b");
  await chooseConversation(page, "a");
  await expect(page.getByLabel("Message Leam")).toHaveValue("Keep this draft");
  await expect(page.locator(".conversation-list-toggle")).toContainText(
    "Renamed conversation",
  );
});
test("confirmed Companion removal clears selection even if draft storage becomes unavailable", async ({
  page,
}) => {
  const f = await setup(page);
  await chooseConversation(page, "a");
  await page.getByLabel("Message Leam").fill("Discard with confirmation");
  await page.locator(".conversation-list-toggle").click();
  await page
    .getByRole("button", { name: "Delete First conversation", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Delete conversation",
    exact: true,
  });
  await expect(dialog).toContainText("delivery records and backups remain");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(f.deleted.size).toBe(0);
  await page
    .getByRole("button", { name: "Delete First conversation", exact: true })
    .click();
  await page.evaluate(() => {
    Storage.prototype.removeItem = function () {
      throw new DOMException("Blocked", "SecurityError");
    };
  });
  await dialog
    .getByRole("button", { name: "Delete conversation", exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByLabel("Message Leam")).toHaveCount(0);
  expect([...f.deleted]).toEqual(["a"]);
  expect(f.calls.find((c) => c.method === "DELETE").body).toEqual({
    confirmed: true,
  });
});
test("Coding uses separate open/rename controls and confirms native deletion semantics", async ({
  page,
}) => {
  const f = await setup(page, "coding");
  await page.getByRole("button", { name: "Rename First conversation" }).click();
  await page.getByLabel("Conversation title").fill("Coding title");
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("button", { name: "Open Coding title" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Delete Coding title" }).click();
  const dialog = page.getByRole("dialog", {
    name: "Delete conversation",
    exact: true,
  });
  await expect(dialog).toContainText("stops their running work");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(f.calls.find((c) => c.method === "PATCH").path).toBe(
    "/api/codex/threads/a",
  );
  await chooseConversation(page, "a", "coding");
  await expect(page.getByLabel("Message Codex")).toBeVisible();
  await page.locator(".conversation-list-toggle").click();
  await page
    .getByRole("button", { name: "Delete Coding title", exact: true })
    .click();
  await page
    .getByRole("dialog", { name: "Delete conversation", exact: true })
    .getByRole("button", { name: "Delete conversation", exact: true })
    .click();
  await expect(page.getByLabel("Message Codex")).toHaveCount(0);
  expect(f.calls.find((c) => c.method === "DELETE")).toMatchObject({
    path: "/api/codex/threads/a",
    body: { confirmed: true, deleteChildren: true, stopRunning: true },
  });
});
test("mobile controls fit and list scroll does not consume conversation area", async ({
  page,
}, testInfo) => {
  await setup(page);
  for (const width of [360, 390, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    for (const label of [
      "Rename First conversation",
      "Open First conversation",
      "Delete First conversation",
    ]) {
      const b = (await page
        .getByRole("button", { name: label, exact: true })
        .boundingBox())!;
      expect(b.height).toBeGreaterThanOrEqual(44);
      expect(b.width).toBeGreaterThanOrEqual(44);
      expect(b.x + b.width).toBeLessThanOrEqual(width);
    }
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    const row = (await page.locator(".conversation-row").first().boundingBox())!;
    const title = (await page
      .getByRole("button", { name: "Rename First conversation", exact: true })
      .boundingBox())!;
    expect(title.width).toBeGreaterThanOrEqual(row.width - 100);
  }
  await page.setViewportSize({ width: 360, height: 844 });
  await page.screenshot({
    path: testInfo.outputPath("conversation-list-360.png"),
  });
});

test("cached list pagination retains all 100 rows and its matching cursor", async ({
  page,
}) => {
  let hold = false;
  let release: (() => void) | undefined;
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      onopen: any;
      onmessage: any;
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const u = new URL(route.request().url());
    let body: any = { items: [], data: [], threads: [] };
    if (u.pathname === "/api/auth/status") body = { authenticated: true };
    if (u.pathname === "/api/companion/threads") {
      if (hold && !u.search) await new Promise<void>((r) => (release = r));
      const offset = Number(u.searchParams.get("cursor") || 0);
      body = {
        threads: Array.from({ length: 50 }, (_, n) => ({
          thread_id: "c" + (n + offset),
          title: "Conversation " + (n + offset),
        })),
        next_cursor: String(offset + 50),
      };
    }
    if (u.pathname === "/api/companion/threads/c75") body = { messages: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/?view=companion");
  await page.getByRole("button", { name: "More conversations" }).click();
  await chooseConversation(page, "c75");
  await page.getByLabel("Message Leam").fill("Keep selected draft");
  hold = true;
  await navigate(page, "Today");
  await navigate(page, "Companion");
  await expect(page.locator(".conversation-list-toggle")).toContainText(
    "Conversation 75",
  );
  await page.locator(".conversation-list-toggle").click();
  await expect(page.locator('[data-thread-id="c99"]')).toHaveCount(1);
  await expect(page.locator(".conversation-row")).toHaveCount(100);
  await expect.poll(() => !!release).toBe(true);
  release?.();
  hold = false;
  await expect(page.getByLabel("Message Leam")).toHaveValue(
    "Keep selected draft",
  );
  await expect(page.locator(".conversation-list-toggle")).toContainText(
    "Conversation 75",
  );
});

test("deleting a Coding parent retires selected-child display while preserving unrelated drafts", async ({
  page,
}) => {
  let removed = false,
    lists = 0;
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      onopen: any;
      onmessage: any;
      close() {}
    };
    sessionStorage.setItem(
      "leam-view:coding:draft:unrelated",
      JSON.stringify("Keep unrelated draft"),
    );
  });
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [] };
    if (p === "/api/auth/status") body = { authenticated: true };
    if (p === "/api/codex/threads") {
      lists++;
      body = {
        data: removed
          ? []
          : [
              { id: "parent", name: "Parent" },
              { id: "child", name: "Child", parentThreadId: "parent" },
            ],
      };
    }
    if (p === "/api/codex/threads/child")
      body = { thread: { id: "child", name: "Child" }, connected: true };
    if (
      p === "/api/codex/threads/parent" &&
      route.request().method() === "DELETE"
    ) {
      removed = true;
      body = { deleted: true };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/?view=coding");
  await chooseConversation(page, "child", "coding");
  await page.getByLabel("Message Codex").fill("Child draft");
  await page.locator(".conversation-list-toggle").click();
  await page
    .getByRole("button", { name: "Delete Parent", exact: true })
    .click();
  await page
    .getByRole("dialog", { name: "Delete conversation", exact: true })
    .getByRole("button", { name: "Delete conversation", exact: true })
    .click();
  await expect(page.getByLabel("Message Codex")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Open Child", exact: true }),
  ).toHaveCount(0);
  await expect.poll(() => lists).toBe(2);
  expect(
    await page.evaluate(() =>
      JSON.parse(sessionStorage.getItem("leam-view:coding:draft:unrelated")!),
    ),
  ).toBe("Keep unrelated draft");
  await navigate(page, "Today");
  await navigate(page, "Coding");
  await expect(page.getByLabel("Message Codex")).toHaveCount(0);
});
