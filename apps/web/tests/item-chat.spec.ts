import { test, expect } from "@playwright/test";
import { navigate, chooseConversation } from "./navigation";
for (const width of [390, 1440])
  test(`item chats reuse Companion without changing selection ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const requests: string[] = [];
    const posts: any[] = [];
    const item = {
      id: "walk",
      title: "Walk",
      kind: "habit",
      revision: 1,
      status: "active",
      capacityId: "health",
      measure: "boolean",
      target: 1,
      date: "2026-09-21",
      log: { value: 0, revision: 0, done: false },
    };
    await page.addInitScript(() => {
      (window as any).EventSource = class extends EventTarget {
        close() {}
      };
    });
    await page.route("**/api/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      requests.push(p);
      let body: any = { items: [], data: [] };
      if (p === "/api/auth/status") body = { authenticated: true };
      if (p === "/api/commitments" || p === "/api/today")
        body = { items: [item] };
      if (p === "/api/capacities")
        body = { items: [{ id: "health", name: "Health", revision: 1 }] };
      if (p === "/api/companion/threads")
        body = { threads: [{ thread_id: "main", title: "Main chat" }] };
      if (p.endsWith("/chat"))
        body = {
          threadId: p.includes("/capacities/")
            ? "capacity-thread"
            : "commitment-thread",
        };
      if (p.startsWith("/api/companion/threads/") && !p.endsWith("/messages"))
        body = { messages: [] };
      if (p.endsWith("/messages")) {
        posts.push({ path: p, body: route.request().postDataJSON() });
        body = {
          accepted: true,
          status: "accepted",
          run_id: "run",
          thread_id: p.split("/")[4],
        };
      }
      if (p === "/api/companion/system")
        body = {
          available: true,
          activeModel: { model: "gpt-6-astra", reasoning_effort: "high" },
        };
      await route.fulfill({ json: body });
    });
    await page.goto("/");
    await navigate(page, "Companion");
    await chooseConversation(page, "main");
    await navigate(page, "Today");
    await expect(
      page.getByText("Chat about Walk", { exact: true }),
    ).toBeVisible();
    expect(requests.filter((p) => p.endsWith("/chat"))).toHaveLength(0);
    await page.getByText("Chat about Walk", { exact: true }).click();
    const panel = page.getByRole("region", { name: "Chat about Walk" });
    await expect(panel.getByLabel("Message Leam")).toBeVisible();
    await expect(
      panel.getByText(/Configured model: gpt-6-astra/),
    ).toBeVisible();
    await expect(
      panel.getByRole("button", { name: "Dictate", exact: true }),
    ).toBeVisible();
    await expect(panel.getByLabel("Attach files")).toBeVisible();
    await panel.getByLabel("Message Leam").fill("My retained draft");
    await page.getByText("Chat about Walk", { exact: true }).click();
    await expect(panel.getByLabel("Message Leam")).toHaveCount(0);
    await page.getByText("Chat about Walk", { exact: true }).click();
    await expect(panel.getByLabel("Message Leam")).toHaveValue(
      "My retained draft",
    );
    await panel
      .getByRole("button", { name: "Send to Leam", exact: true })
      .click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0].path).toContain("/commitment-thread/messages");
    await page.getByRole("button", { name: "Manage capacities" }).click();
    await page.getByText("Chat about Health", { exact: true }).click();
    await expect(
      page
        .getByRole("region", { name: "Chat about Health" })
        .getByLabel("Message Leam"),
    ).toBeVisible();
    await navigate(page, "Companion");
    await expect(
      page
        .getByRole("region", { name: "Companion conversations" })
        .getByRole("button", { name: "Main chat", exact: true }),
    ).toContainText("Main chat");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
