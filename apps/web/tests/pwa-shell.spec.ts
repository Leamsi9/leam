import { test, expect } from "@playwright/test";
async function ready(page: any) {
  await page.goto("/");
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
}
test("first install precaches the complete shell and supports offline query navigation without caching private APIs", async ({
  page,
  context,
}) => {
  await ready(page);
  await page.evaluate(async () => {
    await fetch("/api/private-fixture");
  });
  const keys = await page.evaluate(async () => {
    const names = await caches.keys();
    return (
      await Promise.all(
        names.map(async (name) =>
          (await (await caches.open(name)).keys()).map(
            (r) => new URL(r.url).pathname,
          ),
        ),
      )
    ).flat();
  });
  expect(
    keys.some((k) => k.startsWith("/assets/") && k.endsWith(".js")),
  ).toBeTruthy();
  expect(
    keys.some((k) => k.startsWith("/assets/") && k.endsWith(".css")),
  ).toBeTruthy();
  expect(keys.some((k) => k.startsWith("/api/"))).toBeFalsy();
  await context.setOffline(true);
  await page.goto("/?view=today");
  await expect(page.locator('script[type="module"]')).toHaveCount(1);
  await expect(page.getByText(/Reconnect|offline/i).first()).toBeVisible();
});
test("immutable assets are served from the worker cache on later navigations", async ({
  page,
}) => {
  await ready(page);
  await page.reload();
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  const before = await page.request.get("/test/counts").then((r) => r.json());
  await page.reload();
  await expect(
    page.getByRole("navigation", { name: "Main navigation" }),
  ).toBeVisible();
  const after = await page.request.get("/test/counts").then((r) => r.json());
  expect(after.assets).toBe(before.assets);
});
test("manifest contains phone install icons at both standard sizes", async ({
  request,
}) => {
  const manifest = await request
    .get("/manifest.webmanifest")
    .then((r) => r.json());
  for (const size of ["192x192", "512x512"]) {
    const icon = manifest.icons.find((i: any) => i.sizes === size);
    expect(icon).toBeTruthy();
    const response = await request.get(icon.src);
    expect(response.ok()).toBeTruthy();
    expect(response.headers()["content-type"]).toBe("image/png");
  }
});
test("a waiting worker is activated only by explicit Reload Leam", async ({
  page,
}) => {
  await ready(page);
  await page.reload();
  await page.request.post("/test/upgrade");
  await page.evaluate(async () => {
    const r = await navigator.serviceWorker.getRegistration();
    await r?.update();
  });
  await expect
    .poll(() =>
      page.evaluate(async () =>
        Boolean((await navigator.serviceWorker.getRegistration())?.waiting),
      ),
    )
    .toBe(true);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("button", { name: "Reload Leam" })).toBeVisible();
  expect(
    await page.evaluate(async () =>
      Boolean((await navigator.serviceWorker.getRegistration())?.waiting),
    ),
  ).toBe(true);
  await Promise.all([
    page.waitForNavigation(),
    page.getByRole("button", { name: "Reload Leam" }).click(),
  ]);
  await expect
    .poll(() =>
      page.evaluate(async () =>
        Boolean((await navigator.serviceWorker.getRegistration())?.waiting),
      ),
    )
    .toBe(false);
  await expect(
    page.getByRole("navigation", { name: "Main navigation" }),
  ).toBeVisible();
});

test("a mismatched release asset fails worker installation and preserves the previous shell", async ({
  page,
  context,
}) => {
  await ready(page);
  await page.reload();
  const previous = await page.evaluate(() => caches.keys());
  await page.request.post("/test/corrupt?enabled=true");
  await page.request.post("/test/upgrade");
  try {
    await page.evaluate(async () => {
      const r = await navigator.serviceWorker.getRegistration();
      await r?.update();
    });
    await expect
      .poll(() =>
        page.evaluate(async () =>
          Boolean(
            (await navigator.serviceWorker.getRegistration())?.installing,
          ),
        ),
      )
      .toBe(false);
    expect(
      await page.evaluate(async () =>
        Boolean((await navigator.serviceWorker.getRegistration())?.waiting),
      ),
    ).toBe(false);
    const current = await page.evaluate(() => caches.keys());
    expect(current).toEqual(previous);
    await context.setOffline(true);
    await page.goto("/?view=settings");
    await expect(page.locator("#root")).not.toBeEmpty();
  } finally {
    await context.setOffline(false);
    await page.request.post("/test/corrupt?enabled=false");
  }
});

test("install prompt appears only after an explicit Settings action", async ({
  page,
}) => {
  await ready(page);
  await page.evaluate(() => {
    (window as any).installPrompts = 0;
    const event = new Event("beforeinstallprompt", { cancelable: true });
    Object.assign(event, {
      prompt: async () => {
        (window as any).installPrompts++;
      },
      userChoice: Promise.resolve({ outcome: "dismissed" }),
    });
    window.dispatchEvent(event);
  });
  expect(await page.evaluate(() => (window as any).installPrompts)).toBe(0);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page
    .locator("summary")
    .filter({ hasText: "App installation and offline" })
    .click();
  await page.getByRole("button", { name: "Install Leam", exact: true }).click();
  expect(await page.evaluate(() => (window as any).installPrompts)).toBe(1);
  await expect(
    page.getByRole("button", { name: "Install Leam", exact: true }),
  ).toHaveCount(0);
});
