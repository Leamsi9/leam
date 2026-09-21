import { test, expect } from "@playwright/test";

test("new client prompts reload without replacing the open page", async ({ page }) => {
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/threads") body = { data: [] };
    await route.fulfill({ json: body });
  });
  await page.route("**/", async route => {
    if (route.request().isNavigationRequest()) { await route.continue(); return; }
    await route.fulfill({ contentType: "text/html", body: '<html><script type="module" src="/assets/new-release.js"></script></html>' });
  });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Reload Leam", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.querySelector<HTMLScriptElement>('script[type="module"]')?.src)).not.toContain("new-release.js");
});
