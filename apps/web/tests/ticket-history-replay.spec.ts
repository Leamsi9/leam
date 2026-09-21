import { test, expect } from "@playwright/test";
import { navigate } from "./navigation";
for (const replayMode of [
  "missing-start",
  "retained-start",
  "terminal-snapshot",
])
  test(`partial history/replay overlap: ${replayMode}`, async ({ page }) => {
    await page.addInitScript(() => {
      (window as any).EventSource = class {
        onmessage: any;
        constructor() {
          (window as any).ticketStream = this;
        }
        close() {}
      };
    });
    await page.route("**/api/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      let body: any = { items: [], data: [] };
      if (p === "/api/auth/status") body = { authenticated: true };
      if (p === "/api/updates")
        body = {
          items: [
            {
              id: "ticket",
              title: "Continuation",
              summary: "Fixture",
              stage: "UAT",
              revision: 1,
              sequence: 1,
              deploymentId: "fixture",
              deployedAt: "2026-09-20T20:00:00Z",
              qa: { state: "passed" },
              uat: { state: "pending" },
            },
          ],
          unreadCount: 0,
          nextCursor: null,
        };
      if (p === "/api/updates/ticket/chat")
        body = {
          state: "ready",
          threadId: "dedicated-ticket",
          connected: true,
          eventCursor: 100,
        };
      if (p === "/api/codex/threads/dedicated-ticket/turns")
        body = {
          data: [
            {
              id: "active",
              status:
                replayMode === "terminal-snapshot" ? "completed" : "inProgress",
              items: [
                {
                  id: "question",
                  type: "userMessage",
                  content: [{ type: "text", text: "Preserved user question" }],
                },
                { id: "answer", type: "agentMessage", text: "Hello" },
              ],
            },
          ],
        };
      await route.fulfill({ json: body });
    });
    await page.goto("/");
    await navigate(page, "Updates");
    const card = page.getByRole("article", {
      name: "Continuation",
      exact: true,
    });
    await card.getByText("Chat about this update", { exact: true }).click();
    await expect(card.getByText("Hello", { exact: true })).toBeVisible();
    await page.evaluate((mode) => {
      if (mode !== "missing-start")
        (window as any).ticketStream.onmessage({
          data: JSON.stringify({
            id: 101,
            topic: "codex",
            payload: {
              method: "turn/started",
              params: {
                threadId: "dedicated-ticket",
                turn: { id: "active", status: "inProgress", items: [] },
              },
            },
          }),
        });
      (window as any).ticketStream.onmessage({
        data: JSON.stringify({
          id: 102,
          topic: "codex",
          payload: {
            method: "item/agentMessage/delta",
            params: {
              threadId: "dedicated-ticket",
              turnId: "active",
              itemId: "answer",
              delta: "Hello",
            },
          },
        }),
      });
    }, replayMode);
    await expect(card.locator(".markdown")).toHaveText("Hello");
    await expect(
      card.getByText("Preserved user question", { exact: true }),
    ).toBeVisible();
    if (replayMode === "retained-start") {
      await page.evaluate(() =>
        (window as any).ticketStream.onmessage({
          data: JSON.stringify({
            id: 103,
            topic: "codex",
            payload: {
              method: "item/agentMessage/delta",
              params: {
                threadId: "dedicated-ticket",
                turnId: "active",
                itemId: "answer",
                delta: " world",
              },
            },
          }),
        }),
      );
      await expect(card.locator(".markdown")).toHaveText("Hello world");
    }
    if (replayMode === "terminal-snapshot")
      await expect(
        card.getByText("Codex is working…", { exact: true }),
      ).toHaveCount(0);
  });
