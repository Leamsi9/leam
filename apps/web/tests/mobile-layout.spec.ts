import { chooseConversation } from "./navigation";
import { test, expect } from "@playwright/test";
for (const width of [360, 390, 768, 1440]) {
  test(`navigation keeps mobile controls usable at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.route("**/api/**", (route) => {
      const p = new URL(route.request().url()).pathname;
      return route.fulfill({
        json: p.endsWith("/auth/status")
          ? { authenticated: true, configured: true }
          : p.endsWith("/codex/threads")
            ? { data: [] }
            : p.endsWith("/updates/status")
              ? { unreadCount: 2 }
              : { items: [], data: [], providers: [], available: false },
      });
    });
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    if (width <= 720) {
      await expect(nav.getByRole("button")).toHaveCount(4);
      for (const button of await nav.getByRole("button").all()) {
        const b = await button.boundingBox();
        expect(b!.width).toBeGreaterThanOrEqual(44);
      }
      await nav.getByRole("button", { name: /More/ }).click();
      const dialog = page.getByRole("dialog", { name: "More from Leam" });
      await expect(dialog).toBeVisible();
      await dialog
        .getByRole("button", { name: "Settings", exact: true })
        .click();
      await expect(dialog).not.toBeVisible();
      await expect(
        page.getByRole("heading", { name: "Settings", exact: true }),
      ).toBeVisible();
      await nav.getByRole("button", { name: /More/ }).click();
      await page.keyboard.press("Escape");
      await expect(dialog).not.toBeVisible();
      await expect(nav.getByRole("button", { name: /More/ })).toBeFocused();
    } else {
      await expect(nav.getByRole("button")).toHaveCount(8);
    }
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({ path: `/tmp/leam-mobile-layout-${width}.png` });
  });
}
for (const module of ["coding", "companion"]) {
  test(`${module} composer stays above navigation with long history and an offline banner`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 360, height: 740 });
    await page.addInitScript(() => {
      (window as any).EventSource = class extends EventTarget {
        onopen: any;
        onmessage: any;
        close() {}
      };
    });
    await page.route("**/api/**", (route) => {
      const p = new URL(route.request().url()).pathname;
      let body: any = { items: [], data: [] };
      if (p === "/api/auth/status") body = { authenticated: true };
      if (p === "/api/codex/threads")
        body = {
          data: [{ id: "a", name: "Layout fixture", transport: "ide-owner" }],
        };
      if (p === "/api/codex/threads/a")
        body = { connected: true, thread: { id: "a", transport: "ide-owner" } };
      if (p.endsWith("/turns"))
        body = {
          data: [
            {
              id: "old",
              status: "completed",
              items: [
                {
                  id: "answer",
                  type: "agentMessage",
                  text: "Long readable response. ".repeat(250),
                },
              ],
            },
          ],
        };
      if (p === "/api/companion/threads")
        body = {
          threads: [
            {
              thread_id: "a",
              title: "Layout fixture",
              created_at: "2026-09-20T10:00:00Z",
            },
          ],
        };
      if (p === "/api/companion/threads/a")
        body = {
          messages: [
            {
              message_id: "a",
              kind: "assistant",
              status: "finalized",
              content: "Long readable response. ".repeat(250),
            },
          ],
        };
      return route.fulfill({ json: body });
    });
    await page.goto(`/?view=${module}`);
    if (module === "coding")
      await page.getByRole("button", { name: /^Open Layout fixture/ }).click();
    else await chooseConversation(page, "a");
    await page.evaluate(() => {
      Object.defineProperty(navigator, "onLine", {
        get: () => false,
        configurable: true,
      });
      window.dispatchEvent(new Event("offline"));
    });
    await expect(
      page.getByText("You’re offline.", { exact: false }),
    ).toBeVisible();
    const composer = page.locator(".composer");
    await expect(composer).toBeVisible();
    const b = (await composer.boundingBox())!;
    const nav = (await page
      .getByRole("navigation", { name: "Main navigation" })
      .boundingBox())!;
    expect(b.y + b.height).toBeLessThanOrEqual(nav.y + 1);
    const transcript = page.locator(
      module === "coding" ? ".messages" : ".companion-messages",
    );
    expect((await transcript.boundingBox())!.height).toBeGreaterThan(220);
    for (const name of ["Dictate", "Conversation"]) {
      const box = (await page
        .getByRole("button", { name, exact: true })
        .boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(360);
      expect(box.y).toBeGreaterThanOrEqual(0);
    }
    await page.screenshot({
      path: `/tmp/leam-mobile-${module}-offline-360.png`,
    });
  });
}
