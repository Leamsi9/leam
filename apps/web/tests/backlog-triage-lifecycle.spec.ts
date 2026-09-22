import { test, expect, type Page, type Route } from "@playwright/test";

async function navigate(page: Page, label: string) {
  const nav = page.locator('nav[aria-label="Main navigation"]:visible');
  const target = nav.getByRole("button", { name: new RegExp(`^${label}(?: |$)`) });
  if (await target.count()) await target.click();
  else {
    await nav.getByRole("button", { name: /^More/ }).click();
    await page.getByRole("dialog", { name: "More from Leam" })
      .getByRole("button", { name: new RegExp(`^${label}(?: |$)`) }).click();
  }
}

for (const width of [390, 1440]) test(`leaving triage before Main admission cannot send a second request ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
  const writes: any[] = [];
  let delayed: Route | undefined;
  let delayNextMain = false;
  const main = { main: { threadId: "main-1", revision: 2 }, bindingValid: true };
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [], threads: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/backlog") body = { items: [], ordering: { revision: 4 }, review: { current: true } };
    if (path === "/api/coding/main") {
      if (delayNextMain) { delayNextMain = false; delayed = route; return; }
      body = main;
    }
    if (path === "/api/coding/main/handoffs") {
      writes.push(route.request().postDataJSON());
      body = { state: "accepted", requestId: writes.at(-1).requestId };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/?view=backlog");
  const panel = page.getByRole("region", { name: "Backlog triage", exact: true });
  delayNextMain = true;
  await panel.getByRole("button", { name: "Triage backlog", exact: true }).click();
  await expect.poll(() => !!delayed).toBe(true);
  expect(writes).toHaveLength(0);
  await navigate(page, "Goals");
  await expect(panel).toHaveCount(0);
  await navigate(page, "Backlog");
  await panel.getByRole("button", { name: "Triage backlog", exact: true }).click();
  await expect(panel).toContainText("not yet confirmed complete");
  expect(writes).toHaveLength(1);
  const acceptedId = writes[0].requestId;
  // Finish the old component's GET after the new component accepted its request.
  const completed = page.waitForResponse(response => new URL(response.url()).pathname === "/api/coding/main");
  await delayed!.fulfill({ json: main });
  await (await completed).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(writes).toHaveLength(1);
  expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem("leam-backlog-triage")!).body.requestId)).toBe(acceptedId);
  await page.reload();
  await expect(panel).toContainText("not yet confirmed complete");
  expect(writes).toHaveLength(1);
});
