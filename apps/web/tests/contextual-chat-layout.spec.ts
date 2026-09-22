import { test, expect, type Page, type Locator } from "@playwright/test";
import { navigate } from "./navigation";

async function fixture(page: Page) {
  const posts: string[] = [];
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST") posts.push(path);
    let body: any = { items: [], data: [], threads: [], messages: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/capacities")
      body = { items: [{ id: "care", name: "Care", revision: 1 }] };
    if (path === "/api/capacities/care/chat")
      body = { threadId: "item-thread" };
    if (path === "/api/companion/threads/item-thread")
      body = {
        messages: [
          {
            message_id: "reply",
            sequence: 1,
            kind: "assistant",
            status: "finalized",
            content: "Your latest capacity response.",
            turn_run_id: "run",
          },
        ],
        next_cursor: null,
      };
    if (path === "/api/companion/system")
      body = {
        available: true,
        activeModel: { model: "gpt-6-astra", reasoning_effort: "high" },
      };
    if (path === "/api/updates")
      body = {
        items: [
          {
            id: "ticket",
            title: "Layout",
            summary: "Reading space",
            stage: "UAT",
            revision: 1,
            sequence: 1,
            deploymentId: "fixture",
            deployedAt: "2026-09-22T10:00:00Z",
            qa: { state: "passed" },
            uat: { state: "pending" },
          },
        ],
        unreadCount: 0,
        nextCursor: null,
      };
    if (path === "/api/updates/ticket/chat")
      body = { state: "ready", threadId: "ticket-thread", connected: true };
    if (path === "/api/codex/threads/ticket-thread/turns")
      body = {
        data: [
          {
            id: "run",
            status: "completed",
            items: [
              {
                id: "reply",
                type: "agentMessage",
                text: "Your latest ticket response.",
              },
            ],
          },
        ],
      };
    if (path === "/api/coding/main") body = { main: null, bindingValid: true };
    await route.fulfill({ json: body });
  });
  return posts;
}

async function geometry(panel: Locator, messages: Locator, minRatio: number) {
  const bounds = await panel.boundingBox(),
    transcript = await messages.boundingBox();
  expect(bounds).not.toBeNull();
  expect(transcript).not.toBeNull();
  expect(transcript!.height / bounds!.height).toBeGreaterThan(minRatio);
  expect(await panel.evaluate((e) => e.scrollWidth <= e.clientWidth + 2)).toBe(
    true,
  );
  const composer = panel.locator(".composer");
  const input = composer.locator("textarea");
  await expect(input).toBeVisible();
  const inputBounds = await input.boundingBox();
  expect(inputBounds!.y).toBeGreaterThanOrEqual(
    transcript!.y + transcript!.height - 2,
  );
  expect(inputBounds!.y + inputBounds!.height).toBeLessThanOrEqual(
    bounds!.y + bounds!.height + 2,
  );
}

for (const viewport of [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
  { width: 1440, height: 900 },
]) {
  test(`Goals and ticket chats keep response space and draft at ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const posts = await fixture(page);
    await page.goto("/");
    await navigate(page, "Goals");
    await page.getByText("Chat about Care", { exact: true }).click();
    const item = page.getByRole("region", {
      name: "Chat about Care",
      exact: true,
    });
    await expect(
      item.getByText("Your latest capacity response."),
    ).toBeVisible();
    await geometry(
      item.locator(".companion-page"),
      item.locator(".companion-messages"),
      viewport.height < 500 ? 0.38 : 0.58,
    );
    await item.getByLabel("Message Leam").fill("Preserve my capacity draft");
    await item
      .getByRole("button", { name: "Companion chat options", exact: true })
      .click();
    await page
      .getByRole("button", {
        name: "Close Companion chat options",
        exact: true,
      })
      .click();
    await expect(item.getByLabel("Message Leam")).toHaveValue(
      "Preserve my capacity draft",
    );
    await navigate(page, "Updates");
    await page.getByText("Chat about this update", { exact: true }).click();
    const ticket = page.getByRole("region", {
      name: "Chat about Layout",
      exact: true,
    });
    await expect(
      ticket.getByText("Your latest ticket response."),
    ).toBeVisible();
    await geometry(
      ticket,
      ticket.getByLabel("Ticket messages"),
      viewport.height < 500 ? 0.38 : 0.58,
    );
    await expect(
      ticket.getByRole("button", { name: "Refresh ticket conversation" }),
    ).not.toBeVisible();
    await ticket
      .getByLabel("Message about this update")
      .fill("Preserve my ticket draft");
    await ticket
      .getByRole("button", { name: "Ticket chat options", exact: true })
      .click();
    const options = page.getByRole("dialog", {
      name: "Ticket chat options",
      exact: true,
    });
    await expect(
      options.getByRole("button", { name: "Refresh ticket conversation" }),
    ).toBeVisible();
    await options
      .getByRole("button", { name: "Close Ticket chat options", exact: true })
      .click();
    await expect(ticket.getByLabel("Message about this update")).toHaveValue(
      "Preserve my ticket draft",
    );
    await expect(
      ticket.getByRole("button", { name: "Dictate", exact: true }),
    ).toBeVisible();
    await expect(
      ticket.getByRole("button", { name: "Send to Codex", exact: true }),
    ).toBeVisible();
    expect(
      posts.filter(
        (path) => path.endsWith("/messages") || path.includes("/turns"),
      ),
    ).toEqual([]);
  });
}

for (const viewport of [{ width: 390, height: 844 }, { width: 844, height: 390 }]) {
  for (const kind of ["item", "ticket"] as const) test(`empty ${kind} composer is compact until first send and preserves full history ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await fixture(page);
    let posted = false, completed = false;
    let release!: () => void;
    const waiting = new Promise<void>(resolve => { release = resolve; });
    const run = "19e9c46c-a47b-4e01-8cb4-f4c5c4c26c63";
    const messages = () => completed ? [{ message_id: "reply-new", sequence: 2, kind: "assistant", status: "finalized", content: "First actual response.", turn_run_id: run }] : [];
    const turns = () => completed ? [{ id: run, status: "completed", items: [{ id: "reply-new", type: "agentMessage", text: "First actual response." }] }] : [];
    await page.route("**/api/companion/threads/item-thread", route => route.fulfill({ json: { messages: messages(), next_cursor: null } }));
    await page.route("**/api/codex/threads/ticket-thread/turns", async route => {
      if (route.request().method() === "POST") {
        posted = true;
        await waiting;
        completed = true;
        await route.fulfill({ json: { turn: { id: run, status: "inProgress" } } });
      } else await route.fulfill({ json: { data: turns() } });
    });
    await page.route("**/api/companion/threads/item-thread/messages", async route => {
      posted = true;
      await waiting;
      completed = true;
      await route.fulfill({ json: { outcome: "submitted", run_id: run } });
    });
    await page.goto("/");
    await navigate(page, kind === "item" ? "Goals" : "Updates");
    const summary = page.getByText(kind === "item" ? "Chat about Care" : "Chat about this update", { exact: true });
    await summary.click();
    const region = page.getByRole("region", { name: kind === "item" ? "Chat about Care" : "Chat about Layout", exact: true });
    const panel = kind === "item" ? region.locator(".companion-page") : region;
    const transcript = panel.locator(kind === "item" ? ".companion-messages" : ".contextual-chat-messages");
    const input = panel.getByRole("textbox", { name: kind === "item" ? "Message Leam" : "Message about this update", exact: true });
    await expect(input).toBeVisible();
    await expect(panel).toHaveAttribute("data-has-exchanges", "false");
    await expect(transcript).toBeHidden();
    const compact = await panel.boundingBox();
    expect(compact!.height).toBeLessThan(viewport.height * 0.6);
    const inputBounds = await input.boundingBox();
    expect(inputBounds!.y - compact!.y).toBeLessThan(145);
    await input.fill("First message");
    await expect(panel).toHaveAttribute("data-has-exchanges", "false");
    await panel.getByRole("button", { name: kind === "item" ? "Send to Leam" : "Send to Codex", exact: true }).click();
    await expect.poll(() => posted).toBe(true);
    await expect(panel).toHaveAttribute("data-has-exchanges", "true");
    await expect(transcript).toBeVisible();
    expect((await panel.boundingBox())!.height).toBeGreaterThan(compact!.height + 60);
    release();
    await expect(panel.getByText("First actual response.", { exact: true })).toBeVisible();
    await expect(panel).toHaveAttribute("data-has-exchanges", "true");
    await summary.click();
    await summary.click();
    await expect(panel.getByText("First actual response.", { exact: true })).toBeVisible();
    await expect(panel).toHaveAttribute("data-has-exchanges", "true");
    await geometry(panel, transcript, viewport.height < 500 ? 0.38 : 0.58);
  });
}
