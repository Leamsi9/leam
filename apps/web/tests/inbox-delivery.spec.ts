import { test, expect } from "@playwright/test";

for (const width of [390, 844]) test(`Inbox notification failure is collapsed and retries only delivery ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: width === 390 ? 844 : 390 });
  let failed = true;
  const writes: string[] = [];
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [], threads: [], providers: [], messages: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/agenda") { await route.fulfill({ status: 503, json: { detail: "Saved agenda unavailable" } }); return; }
    if (path === "/api/inbox") body = { items: [], total: 0, unreadCount: 0, throughSequence: 0, nextCursor: null };
    if (path === "/api/inbox-mail/read") body = { readKeys: [] };
    if (route.request().method() === "POST") {
      writes.push(path);
      expect(path).toBe("/api/inbox/automation/41/retry");
      failed = false;
      body = { state: "queued", eventId: 41 };
    }
    if (path === "/api/inbox/status") body = { total: 0, unreadCount: 0, throughSequence: 0, automationDelivery: { queued: 0, retrying: 0, failed: failed ? 1 : 0, paused: false, failures: failed ? [{ eventId: 41, attempts: 3, nextTry: 0, error: "Generic failure" }] : [] } };
    await route.fulfill({ json: body });
  });
  await page.goto("/?view=today#today/inbox/2026-09-22");
  const panel = page.locator(".inbox-delivery-status");
  await expect(panel.locator("summary")).toHaveText("1 action notes awaiting delivery");
  const button = panel.getByRole("button", { name: "Retry notification only" });
  await expect(button).not.toBeVisible();
  await panel.locator("summary").click();
  await expect(panel).toContainText("The original actions completed");
  await button.click();
  await expect(panel).toHaveCount(0);
  expect(writes).toEqual(["/api/inbox/automation/41/retry"]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
