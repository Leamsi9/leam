import { test, expect, type Locator, type Page } from "@playwright/test";
import { chooseConversation } from "./navigation";

// Shapes are the canonical fixtures used by conversation-list/mobile-forms.
// These are browser regressions; physical keyboards/safe areas remain device UAT.
async function fixture(page: Page) {
  const writes: string[] = [];
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget { close() {} };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") writes.push(path);
    let body: any = { items: [], data: [], threads: [], providers: [], models: [], accounts: [] };
    if (path === "/api/auth/status") body = { authenticated: true, configured: true };
    if (path === "/api/codex/threads") body = { data: [{ id: "a", name: "Viewport coding", cwd: "/fixture" }] };
    if (path === "/api/codex/threads/a") body = { thread: { id: "a", name: "Viewport coding", cwd: "/fixture" }, connected: true };
    if (path === "/api/companion/threads") body = { threads: [{ thread_id: "a", title: "Viewport companion", created_at: "2026-09-21T10:00:00Z" }] };
    if (path === "/api/companion/threads/a") body = { messages: [{ message_id: "m", kind: "assistant", status: "finalized", sequence: 1, content: "A visible answer" }] };
    await route.fulfill({ json: body });
  });
  return writes;
}

// Visibility alone misses clipped descendants and controls covered by navigation.
async function reachable(control: Locator, focus = true) {
  if (focus) await control.focus();
  await expect(control).toBeFocused();
  await expect.poll(() => control.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    return r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= innerHeight &&
      r.left >= 0 && r.right <= innerWidth && el.contains(document.elementFromPoint(x, y));
  })).toBe(true);
}

const viewports = [
  { width: 320, height: 568 }, { width: 390, height: 844 },
  { width: 844, height: 390 }, { width: 915, height: 412 },
  { width: 1280, height: 480 }, { width: 1440, height: 900 },
];
for (const viewport of viewports) {
  test(`navigation and native editor remain reachable at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    const writes = await fixture(page);
    await page.setViewportSize(viewport);
    await page.goto("/?view=goals");
    await reachable(page.getByRole("link", { name: "Skip to content" }));
    await page.keyboard.press("Enter");
    await expect(page.locator("#main-content")).toBeFocused();
    const mobile = viewport.width <= 720;
    const nav = page.locator(mobile ? ".mobile-navigation" : ".desktop-navigation");
    for (const button of await nav.getByRole("button").all()) await reachable(button);
    if (mobile) {
      await nav.getByRole("button", { name: /^More/ }).click();
      const sheet = page.getByRole("dialog", { name: "More from Leam" });
      for (const button of await sheet.getByRole("button").all()) await reachable(button);
      await page.keyboard.press("Escape");
    }
    const opener = page.getByRole("button", { name: "Plan a commitment", exact: true });
    await opener.click();
    const dialog = page.getByRole("dialog", { name: "Commitment editor" });
    await expect(dialog).toBeVisible();
    const fields = dialog.locator('button:not(:disabled), input:not([type="hidden"]):not(:disabled), select:not(:disabled), textarea:not(:disabled)');
    for (const field of await fields.all()) if (await field.isVisible()) await reachable(field);
    await page.keyboard.press("Escape");
    await expect(opener).toBeFocused();
    expect(writes).toEqual([]);
  });
}
for (const kind of ["coding", "companion"] as const) {
  test(`${kind} draft and controls survive portrait, landscape and short desktop`, async ({ page }) => {
    await fixture(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/?view=" + kind);
    await chooseConversation(page, "a", kind);
    const composer = page.locator(".composer textarea");
    await composer.fill("Preserve this unsent draft through rotation");
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      await reachable(composer);
      await expect(composer).toHaveValue("Preserve this unsent draft through rotation");
      const transcript = page.locator(kind === "coding" ? ".messages" : ".companion-messages");
      expect(await transcript.evaluate(el => el.clientHeight)).toBeGreaterThanOrEqual(80);
      for (const control of await page.locator(".composer button:not(:disabled)").all())
        if (await control.isVisible()) await reachable(control);
      await reachable(page.locator(".conversation-list-toggle"));
    }
  });
}


test("Tab traversal keeps page actions above the mobile navigation", async ({ page }) => {
  await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=goals");
  await page.getByRole("link", { name: "Skip to content" }).focus();
  await page.keyboard.press("Enter");
  for (let index = 0; index < 35; index++) {
    await page.keyboard.press("Tab");
    const focused = page.locator(":focus");
    if (await focused.count()) await reachable(focused, false);
  }
});
