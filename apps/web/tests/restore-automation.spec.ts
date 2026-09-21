import { test, expect } from "@playwright/test";
for (const width of [390, 1440])
  test(`reviewed future-only automation resume at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    let held = true;
    const requests: any[] = [];
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: any = {
        items: [],
        data: [],
        models: [],
        threads: [],
        providers: [],
      };
      if (path === "/api/auth/status")
        body = { configured: true, authenticated: true };
      if (path === "/api/automation/restore") body = { held, invalid: false };
      if (path === "/api/automation/restore/preview")
        body = {
          previewToken: "a".repeat(64),
          cutoff: 1800000000,
          pendingPushes: 2,
          pastReminders: 3,
          overdueRoutines: 1,
          warning: "Skip past work; future schedules remain.",
        };
      if (path === "/api/automation/restore/resume") {
        requests.push(route.request().postDataJSON());
        held = false;
        body = {
          held: false,
          pendingPushes: 2,
          pastReminders: 3,
          overdueRoutines: 1,
        };
      }
      await route.fulfill({ json: body });
    });
    await page.goto("/?view=settings");
    await page
      .locator("summary")
      .filter({ hasText: /^Backups/ })
      .click();
    const panel = page.locator('[aria-label="Restored automations"]');
    await expect(panel.getByText("Paused after restore.")).toBeVisible();
    await panel
      .getByRole("button", { name: "Review future schedules" })
      .click();
    await expect(
      panel.getByText("2 pending pushes will be skipped."),
    ).toBeVisible();
    await expect(
      panel.getByRole("button", { name: "Confirm future-only resume" }),
    ).toBeDisabled();
    await panel.getByRole("checkbox").check();
    await panel
      .getByRole("button", { name: "Confirm future-only resume" })
      .click();
    await expect(
      panel.getByText("Scheduled automations are enabled."),
    ).toBeVisible();
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      previewToken: "a".repeat(64),
      cutoff: 1800000000,
      confirmed: true,
    });
    expect(requests[0].requestId).toMatch(/^[a-f0-9-]{36}$/);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
test("failed resume is never automatically sent again and requires status refresh", async ({
  page,
}) => {
  let writes = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {
      items: [],
      data: [],
      threads: [],
      providers: [],
      models: [],
    };
    if (path === "/api/auth/status")
      body = { configured: true, authenticated: true };
    if (path === "/api/automation/restore")
      body = { held: true, invalid: false };
    if (path === "/api/automation/restore/preview")
      body = {
        previewToken: "a".repeat(64),
        cutoff: 1800000000,
        pendingPushes: 1,
        pastReminders: 0,
        overdueRoutines: 0,
      };
    if (path === "/api/automation/restore/resume") {
      writes++;
      return route.abort();
    }
    return route.fulfill({ json: body });
  });
  await page.goto("/?view=settings");
  await page
    .locator("summary")
    .filter({ hasText: /^Backups/ })
    .click();
  const panel = page.locator('[aria-label="Restored automations"]');
  await panel.getByRole("button", { name: "Review future schedules" }).click();
  await panel.getByRole("checkbox").check();
  await panel
    .getByRole("button", { name: "Confirm future-only resume" })
    .click();
  await expect(panel.getByText(/request was not repeated/)).toBeVisible();
  await panel
    .getByRole("button", { name: "Refresh automation status" })
    .click();
  expect(writes).toBe(1);
  await expect(
    panel.getByRole("button", { name: "Confirm future-only resume" }),
  ).toHaveCount(0);
});
test("mobile worker status reports paused despite a fresh heartbeat", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: any = {
      items: [],
      data: [],
      threads: [],
      providers: [],
      models: [],
    };
    if (path === "/api/auth/status")
      data = { configured: true, authenticated: true };
    if (
      [
        "/api/notifications/status",
        "/api/routines/status",
        "/api/push/status",
      ].includes(path)
    )
      data = {
        healthy: true,
        lastCheck: Date.now() / 1000,
        automationHeld: true,
        automationInvalid: false,
        devices: [{ id: "fixture", name: "QA phone", state: "active" }],
        deliveries: [],
        contact: "mailto:qa@example.com",
      };
    if (path === "/api/routines")
      data = {
        items: [
          {
            id: "routine-fixture",
            revision: 1,
            title: "Daily check",
            message: "Check in",
            time: "09:00",
            timezone: "UTC",
            days: [0, 1, 2, 3, 4, 5, 6],
            enabled: true,
          },
        ],
      };
    return route.fulfill({ json: data });
  });
  await page.goto("/?view=settings");
  await page
    .locator("summary")
    .filter({ hasText: /^Reminders$/ })
    .click();
  await expect(
    page.getByText(
      "Reminders paused after restore; review in Settings → Backups",
    ),
  ).toBeVisible();
  await page
    .locator("summary")
    .filter({ hasText: /^Phone notifications$/ })
    .click();
  await expect(
    page.getByText(
      "Push delivery paused after restore; review in Settings → Backups",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send test to QA phone" }),
  ).toBeDisabled();
  await page.goto("/?view=routines");
  await expect(
    page.getByText(
      "Routines paused after restore; review in Settings → Backups",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run now", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText("Scheduler running", { exact: true }),
  ).toHaveCount(0);
});
