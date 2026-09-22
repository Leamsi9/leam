import { test, expect, type Page } from "@playwright/test";
import { chooseConversation } from "./navigation";

// Actual rendered caller geometry, using canonical conversation/main fixtures.
// Run only after root deploys this candidate. Physical keyboard remains device UAT.
async function fixture(page: Page) {
  const writes: string[] = [];
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") writes.push(path);
    const thread = {
      id: "space",
      name: "A long session title that must not consume response space",
      cwd: "/home/fixture/long/project/path",
      model: "gpt-5.6-sol",
      reasoningEffort: "medium",
    };
    let body: any = {
      data: [],
      items: [],
      threads: [],
      providers: [],
      models: [],
      accounts: [],
    };
    if (path === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (path === "/api/codex/threads") body = { data: [thread] };
    if (path === "/api/codex/threads/space") body = { thread, connected: true };
    if (path === "/api/codex/threads/space/goal")
      body = {
        goal: {
          status: "active",
          objective: "A detailed goal that belongs in options.",
        },
      };
    if (path === "/api/codex/threads/space/turns")
      body = {
        data: [
          {
            id: "t",
            status: "completed",
            items: [
              {
                id: "m",
                type: "agentMessage",
                text: "Readable response.\n\n".repeat(80),
              },
            ],
          },
        ],
      };
    if (path === "/api/coding/main")
      body = {
        main: { threadId: "main", name: "Coordinator", revision: 1 },
        bindingValid: true,
        limitation: "Coordination policy only.",
      };
    if (path === "/api/companion/threads")
      body = {
        threads: [
          {
            thread_id: "space",
            title: "Companion conversation",
            created_at: "2026-09-21T10:00:00Z",
          },
        ],
      };
    if (path === "/api/companion/threads/space")
      body = {
        messages: [
          {
            message_id: "m",
            kind: "assistant",
            status: "finalized",
            sequence: 1,
            content: "Readable response.\n\n".repeat(80),
          },
        ],
      };
    if (path === "/api/proposals/status")
      body = { unreadCount: 0, pendingCount: 0 };
    await route.fulfill({ json: body });
  });
  return writes;
}

for (const kind of ["coding", "companion"] as const) {
  for (const viewport of [
    { width: 390, height: 844 },
    { width: 844, height: 390 },
    { width: 1280, height: 480 },
    { width: 1440, height: 1000 },
  ]) {
    test(`${kind} gives responses priority at ${viewport.width}x${viewport.height}`, async ({
      page,
    }, info) => {
      const writes = await fixture(page);
      await page.setViewportSize(viewport);
      await page.goto(`/?view=${kind}`);
      await chooseConversation(page, "space", kind);
      const region = page.locator(
        kind === "coding"
          ? ".coding-layout"
          : ".companion-page.has-conversation",
      );
      const messages = page.locator(
        kind === "coding" ? ".messages" : ".companion-messages",
      );
      const composer = page.locator(".composer textarea");
      await composer.fill("Keep this draft when closing controls");
      await expect(page.locator("dialog[open]")).toHaveCount(0);
      const geometry = await region.evaluate((el) => {
        const box = el.getBoundingClientRect();
        const response = el
          .querySelector(".messages, .companion-messages")!
          .getBoundingClientRect();
        const meta = el.querySelector(".chat-toolbar")!.getBoundingClientRect();
        const input = el.querySelector(".composer")!.getBoundingClientRect();
        return {
          response: response.height / box.height,
          metadata: meta.height / box.height,
          input: input.height / box.height,
          bottom: input.bottom,
          height: box.height,
          responseHeight: response.height,
        };
      });
      await info.attach("geometry", {
        body: JSON.stringify({ kind, viewport, ...geometry }),
        contentType: "application/json",
      });
      await page.screenshot({
        path: info.outputPath("chat-space-geometry.png"),
      });
      expect(geometry.response).toBeGreaterThanOrEqual(0.65);
      expect(geometry.metadata).toBeLessThanOrEqual(0.15);
      expect(geometry.input).toBeLessThanOrEqual(0.28);
      expect(geometry.responseHeight).toBeGreaterThan(120);
      expect(geometry.bottom).toBeLessThanOrEqual(
        viewport.height - (viewport.width <= 720 ? 65 : 0),
      );
      expect(
        await messages.evaluate((el) => getComputedStyle(el).overflowY),
      ).toMatch(/auto|scroll/);
      await messages.evaluate((el) => {
        el.scrollTop = 0;
      });
      await expect.poll(() => messages.evaluate((el) => el.scrollTop)).toBe(0);
      await messages.evaluate((el) => {
        el.scrollTop = el.scrollHeight;
      });
      expect(await messages.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
      const options = page.getByRole("button", {
        name: `${kind === "coding" ? "Coding" : "Companion"} chat options`,
        exact: true,
      });
      await options.click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(options).toBeFocused();
      await expect(composer).toHaveValue(
        "Keep this draft when closing controls",
      );
      expect(writes).toEqual([]);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({ path: info.outputPath("chat-space.png") });
    });
  }
}

test("Main handoff edits survive close without dispatching or changing the chat draft", async ({
  page,
}) => {
  const writes = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=coding");
  await chooseConversation(page, "space", "coding");
  const composer = page.getByRole("textbox", { name: "Message Codex" });
  await composer.fill("Discuss this first");
  const main = page.getByRole("button", {
    name: "Main coding coordinator",
    exact: true,
  });
  await main.click();
  const dialog = page.getByRole("dialog", { name: "Main coding coordinator" });
  await dialog
    .getByRole("button", { name: "Send to main", exact: true })
    .click();
  await dialog
    .getByLabel("Implementation task")
    .fill("Reviewed implementation task");
  await page.keyboard.press("Escape");
  await expect(main).toBeFocused();
  await expect(composer).toHaveValue("Discuss this first");
  await main.click();
  await expect(dialog.getByLabel("Implementation task")).toHaveValue(
    "Reviewed implementation task",
  );
  expect(writes).toEqual([]);
});

test("new-session failure stays in dialog; repeated submit is guarded; success closes", async ({
  page,
}) => {
  await fixture(page);
  let posts = 0,
    release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/codex/threads", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    posts++;
    expect(route.request().postDataJSON()).toEqual({ cwd: "/fixture/project" });
    if (posts === 1) {
      await held;
      return route.fulfill({
        status: 503,
        json: { detail: "Provider unavailable" },
      });
    }
    return route.fulfill({
      json: {
        thread: {
          id: "created",
          name: "Created session",
          cwd: "/fixture/project",
        },
      },
    });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=coding");
  await chooseConversation(page, "space", "coding");
  await page.getByLabel("Message Codex").fill("Preserve original draft");
  const opener = page.getByRole("button", {
    name: "Start a new coding session",
    exact: true,
  });
  await opener.click();
  const dialog = page.getByRole("dialog", {
    name: "Start a new coding session",
  });
  await dialog.getByLabel("Workspace directory").fill("/fixture/project");
  await dialog
    .getByRole("button", { name: "Create session", exact: true })
    .click();
  await expect(
    dialog.getByRole("button", { name: "Creating…", exact: true }),
  ).toBeDisabled();
  await dialog
    .locator("form")
    .evaluate((form) =>
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      ),
    );
  expect(posts).toBe(1);
  release();
  await expect(dialog.getByRole("alert")).toHaveText("Provider unavailable");
  await expect(dialog.getByLabel("Workspace directory")).toHaveValue(
    "/fixture/project",
  );
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("Message Codex")).toHaveValue(
    "Preserve original draft",
  );
  await opener.click();
  await dialog
    .getByRole("button", { name: "Create session", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator(".conversation-list-toggle")).toContainText(
    "Created session",
  );
  expect(posts).toBe(2);
  await chooseConversation(page, "space", "coding");
  await expect(page.getByLabel("Message Codex")).toHaveValue(
    "Preserve original draft",
  );
});

test("catalog failure is visible inside chat options", async ({ page }) => {
  await fixture(page);
  await page.goto("/?view=coding");
  await chooseConversation(page, "space", "coding");
  await page.route("**/api/codex/threads", (route) =>
    route.fulfill({ status: 503, json: { detail: "Catalog unavailable" } }),
  );
  await page
    .getByRole("button", { name: "Coding chat options", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Coding chat options" });
  await dialog
    .getByRole("button", { name: "Refresh sessions", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toHaveText("Catalog unavailable");
});
