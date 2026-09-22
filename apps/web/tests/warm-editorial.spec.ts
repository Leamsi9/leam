import { test, expect, type Locator, type Page } from "@playwright/test";
import { chooseConversation } from "./navigation";

// Run after candidate deployment. Browser fixtures prove layout/caller behavior,
// not backend acceptance, device safe areas, or whole-product WCAG conformance.
async function fixture(page: Page) {
  const writes: { path: string; input: unknown }[] = [];
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET")
      writes.push({ path, input: route.request().postDataJSON() });
    const thread = {
      id: "editorial",
      name: "Editorial session",
      transport: "ide-owner",
      generation: "binding-1",
    };
    let body: any = {
      items: [],
      data: [],
      threads: [],
      providers: [],
      models: [],
      accounts: [],
    };
    if (path === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (path === "/api/codex/threads") body = { data: [thread] };
    if (path === "/api/codex/threads/editorial")
      body = {
        thread,
        connected: true,
        generation: "binding-1",
        activeTurnId: "turn-1",
      };
    if (path === "/api/codex/threads/editorial/turns")
      body = { data: [{ id: "turn-1", status: "inProgress", items: [] }] };
    if (path === "/api/codex/shared/interrupt")
      body = { state: "requested", detail: "Stop requested for this turn." };
    if (path === "/api/companion/threads")
      body = {
        threads: [
          {
            thread_id: "editorial",
            title: "Editorial conversation",
            created_at: "2026-09-21T10:00:00Z",
          },
        ],
      };
    if (path === "/api/companion/threads/editorial")
      body = {
        messages: [
          {
            message_id: "m",
            kind: "assistant",
            status: "finalized",
            sequence: 1,
            content:
              "A readable answer that keeps its place when you rotate your screen.",
          },
        ],
      };
    await route.fulfill({ json: body });
  });
  return writes;
}

async function reachable(control: Locator) {
  await control.focus();
  await expect(control).toBeFocused();
  await expect
    .poll(() =>
      control.evaluate((el) => {
        const r = el.getBoundingClientRect();
        return (
          r.width > 0 &&
          r.height > 0 &&
          r.left >= 0 &&
          r.right <= innerWidth &&
          r.top >= 0 &&
          r.bottom <= innerHeight &&
          el.contains(
            document.elementFromPoint(
              r.left + r.width / 2,
              r.top + r.height / 2,
            ),
          )
        );
      }),
    )
    .toBe(true);
}

const viewports = [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
  { width: 1280, height: 480 },
  { width: 1440, height: 1000 },
];

for (const viewport of viewports) {
  test(`editorial shell and form focus at ${viewport.width}x${viewport.height}`, async ({
    page,
  }, info) => {
    const writes = await fixture(page);
    await page.setViewportSize(viewport);
    await page.goto("/?view=goals");
    const mobile = viewport.width <= 720;
    const nav = page.locator(
      mobile ? ".mobile-navigation" : ".desktop-navigation",
    );
    await expect(page.locator(".sidebar")).toHaveCSS(
      "background-color",
      mobile ? "rgb(255, 254, 249)" : "rgb(240, 238, 229)",
    );
    await expect(nav.locator(".selected")).toHaveCSS(
      "color",
      mobile ? "rgb(41, 79, 64)" : "rgb(255, 254, 249)",
    );
    await expect(page.locator(".page h1").first()).toHaveCSS(
      "font-family",
      /Georgia/,
    );
    if (!mobile)
      await expect(page.locator(".sidebar .brand-icon")).toBeVisible();
    for (const button of await nav.getByRole("button").all()) {
      await reachable(button);
      await expect(button).toHaveCSS("outline-width", "3px");
    }
    await page
      .getByRole("button", { name: "Plan a commitment", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "Commitment editor" });
    const field = dialog.locator('input:not([type="hidden"])').first();
    await reachable(field);
    await expect(field).toHaveCSS("border-top-color", "rgb(129, 145, 120)");
    await expect(field).toHaveCSS("outline-color", "rgb(101, 72, 118)");
    await page.keyboard.press("Escape");
    expect(writes).toEqual([]);
    await page.screenshot({
      path: info.outputPath("editorial-shell.png"),
      fullPage: true,
    });
  });
}

test("editorial conversation preserves draft, readable answer and reachable controls across rotation", async ({
  page,
}, info) => {
  const writes = await fixture(page);
  await page.setViewportSize(viewports[0]);
  await page.goto("/?view=companion");
  await chooseConversation(page, "editorial");
  const composer = page.locator(".composer textarea");
  await composer.fill("Keep this unsent draft");
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await reachable(composer);
    await expect(composer).toHaveValue("Keep this unsent draft");
    await expect(composer).toHaveCSS("font-size", "16px");
    await expect(page.locator(".prose").first()).toHaveCSS("font-size", "16px");
    await expect(composer).toHaveCSS("outline-width", "3px");
    for (const control of await page
      .locator(".composer button:not(:disabled)")
      .all())
      if (await control.isVisible()) await reachable(control);
    await page.screenshot({
      path: info.outputPath(`editorial-chat-${viewport.width}.png`),
    });
  }
  expect(writes).toEqual([]);
});

test("editorial short landscape keeps active shared Stop and steering draft usable", async ({
  page,
}) => {
  const writes = await fixture(page);
  await page.setViewportSize({ width: 844, height: 390 });
  await page.goto("/?view=coding");
  await chooseConversation(page, "editorial", "coding");
  const composer = page.locator(".composer textarea");
  await composer.fill("Preserve steering draft");
  const stop = page.getByRole("button", { name: "Stop current shared turn" });
  await reachable(stop);
  await stop.click();
  await expect(page.getByRole("status")).toContainText("Stop requested");
  expect(writes).toEqual([
    {
      path: "/api/codex/shared/interrupt",
      input: { turnId: "turn-1", generation: "binding-1" },
    },
  ]);
  await reachable(composer);
  await expect(composer).toHaveValue("Preserve steering draft");
});
