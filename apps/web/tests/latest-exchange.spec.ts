import { test, expect } from "@playwright/test";
import { chooseConversation, navigate } from "./navigation";

for (const kind of ["companion", "coding"] as const)
  for (const width of [390, 1440]) {
    test(`${kind} opens latest, preserves reading position and resets on navigation ${width}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 850 });
      await page.addInitScript(() => {
        const streams: any[] = [];
        (window as any).streams = streams;
        (window as any).EventSource = class extends EventTarget {
          url: string;
          onopen: any;
          onmessage: any;
          close() {}
          constructor(url: string) {
            super();
            this.url = url;
            streams.push(this);
            setTimeout(() => this.onopen?.(), 0);
          }
        };
      });
      let count = 35;
      await page.route("**/api/**", async (route) => {
        const p = new URL(route.request().url()).pathname;
        let body: any = {
          items: [],
          data: [],
          threads: [],
          providers: [],
          models: [],
          accounts: [],
        };
        if (p === "/api/auth/status") body = { authenticated: true };
        if (p === "/api/companion/threads")
          body = {
            threads: ["a", "b"].map((id) => ({
              thread_id: id,
              title: "Conversation " + id,
            })),
          };
        if (p === "/api/codex/threads")
          body = {
            data: ["a", "b"].map((id) => ({ id, name: "Conversation " + id })),
          };
        const id = p.split("/")[4];
        if (p === `/api/companion/threads/${id}`) {
          await new Promise((r) => setTimeout(r, 100));
          body = {
            messages: Array.from({ length: count }, (_, n) => ({
              message_id: id + n,
              sequence: n,
              kind: n % 2 ? "assistant" : "user",
              status: "finalized",
              content:
                `Exchange ${n} ${id}\n\n` +
                "A reasonably detailed message. ".repeat(12),
            })),
          };
        }
        if (p === `/api/codex/threads/${id}`)
          body = {
            thread: { id, name: "Conversation " + id },
            connected: true,
          };
        if (p === `/api/codex/threads/${id}/turns`) {
          await new Promise((r) => setTimeout(r, 100));
          body = {
            data: Array.from({ length: count }, (_, n) => ({
              id: id + n,
              status: "completed",
              items: [
                {
                  id: "item" + n,
                  type: "agentMessage",
                  text:
                    `Exchange ${n} ${id}\n\n` +
                    "A reasonably detailed message. ".repeat(12),
                },
              ],
            })).reverse(),
          };
        }
        await route.fulfill({ json: body });
      });
      await page.goto("/?view=" + kind);
      await chooseConversation(page, "a", kind);
      const box = page.locator(
        kind === "companion" ? ".companion-messages" : ".messages",
      );
      const gap = () =>
        box.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);
      await expect(
        box.getByText("Exchange 34 a", { exact: false }),
      ).toBeVisible();
      await expect.poll(gap).toBeLessThan(5);
      await box.evaluate((el) => {
        el.scrollTop = 100;
        el.dispatchEvent(new Event("scroll"));
      });
      const before = await box.evaluate((el) => el.scrollTop);
      count = 36;
      // A native stream snapshot refresh appends without dragging a reader down.
      await page.evaluate((kind) => {
        const streams = (window as any).streams;
        if (kind === "coding")
          streams
            .findLast((s: any) => !s.url.includes("/companion/"))
            .onopen?.();
        else
          streams
            .findLast((s: any) => s.url.includes("/companion/"))
            .dispatchEvent(
              new MessageEvent("final_reply", {
                data: JSON.stringify({ type: "final_reply" }),
              }),
            );
      }, kind);
      await expect(
        box.getByText("Exchange 35 a", { exact: false }),
      ).toBeVisible();
      await expect.poll(() => box.evaluate((el) => el.scrollTop)).toBe(before);
      await chooseConversation(page, "b", kind);
      await expect(
        box.getByText("Exchange 35 b", { exact: false }),
      ).toBeVisible();
      await expect.poll(gap).toBeLessThan(5);
      await box.evaluate((el) => {
        el.scrollTop = 0;
        el.dispatchEvent(new Event("scroll"));
      });
      await navigate(page, "Today");
      await navigate(page, kind === "companion" ? "Companion" : "Coding");
      await expect.poll(gap).toBeLessThan(5);
    });
  }

for (const width of [390, 1440])
  test(`ticket chat opens at latest and keeps earlier reading ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 850 });
    await page.addInitScript(() => {
      (window as any).EventSource = class {
        onopen: any;
        onmessage: any;
        close() {}
      };
    });
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: any = { items: [], data: [], threads: [] };
      if (path === "/api/auth/status") body = { authenticated: true };
      if (path === "/api/updates")
        body = {
          items: [
            {
              id: "ticket",
              title: "Scroll fixture",
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
        };
      if (path === "/api/updates/ticket/chat")
        body = { state: "ready", threadId: "ticket-thread", connected: true };
      if (path === "/api/codex/threads/ticket-thread/turns")
        body = {
          data: Array.from({ length: 30 }, (_, n) => ({
            id: "turn" + n,
            status: "completed",
            items: [
              {
                id: "item" + n,
                type: "agentMessage",
                text:
                  "Ticket exchange " +
                  n +
                  "\n\n" +
                  "A detailed reply. ".repeat(20),
              },
            ],
          })).reverse(),
        };
      await route.fulfill({ json: body });
    });
    await page.goto("/?view=updates");
    const card = page.getByRole("article", {
      name: "Scroll fixture",
      exact: true,
    });
    await card.getByText("Chat about this update", { exact: true }).click();
    const box = card.getByLabel("Ticket messages");
    await expect(
      box.getByText("Ticket exchange 29", { exact: false }),
    ).toBeVisible();
    await expect
      .poll(() =>
        box.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight),
      )
      .toBeLessThan(5);
    await box.evaluate((el) => {
      el.scrollTop = 100;
      el.dispatchEvent(new Event("scroll"));
    });
    await card
      .getByLabel("Message about this update")
      .fill("Draft while reading");
    await expect.poll(() => box.evaluate((el) => el.scrollTop)).toBe(100);
    await card.getByText("Chat about this update", { exact: true }).click();
    await card.getByText("Chat about this update", { exact: true }).click();
    await expect
      .poll(() =>
        box.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight),
      )
      .toBeLessThan(5);
  });
