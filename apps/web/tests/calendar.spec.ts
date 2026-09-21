import { test, expect } from "@playwright/test";

test("calendar keeps the saved range and freshness after a failed sync on mobile", async ({
  page,
}) => {
  const saved = {
    id: "c",
    name: "Personal",
    identity: "test@example.com",
    provider: "google",
    syncedAt: 1700000000,
    start: "2026-09-20T00:00:00Z",
    end: "2026-09-27T00:00:00Z",
    events: [
      {
        id: "e",
        title: "Saved event",
        start: "2026-09-20T10:00:00Z",
        end: "2026-09-20T11:00:00Z",
        allDay: false,
      },
    ],
  };
  let error: string | null = null;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/api/calendar/c/sync") {
      error = "Provider unavailable; previous data retained";
      await route.fulfill({ status: 502, json: { detail: error } });
      return;
    }
    const body =
      p === "/api/auth/status"
        ? { authenticated: true }
        : p === "/api/calendar"
          ? {
              accounts: [
                {
                  id: "a",
                  identity: "test@example.com",
                  provider: "google",
                  state: "connected",
                },
              ],
              items: [
                { id: "c", name: "Personal", identity: "test@example.com" },
              ],
            }
          : p === "/api/calendar/c"
            ? { ...saved, error }
            : { data: [], items: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Calendar", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Saved event" }),
  ).toBeVisible();
  const oldStatus = await page.getByText(/Last successful sync:/).innerText();
  await page.getByLabel("First day").fill("2026-10-01");
  await page.getByLabel("Last day").fill("2026-10-07");
  await page.getByRole("button", { name: "Sync this date range" }).click();
  await expect(
    page.getByRole("heading", { name: "Saved event" }),
  ).toBeVisible();
  await expect(page.getByText(/Last successful sync:/)).toHaveText(oldStatus);
  await expect(
    page.getByRole("button", { name: "Sync this date range" }),
  ).toBeEnabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("a delayed cached response cannot overwrite a successful calendar sync", async ({
  page,
}) => {
  let release!: () => void;
  const wait = new Promise<void>((r) => (release = r));
  let started!: () => void;
  const entered = new Promise<void>((r) => (started = r));
  function snapshot(title: string, time: number) {
    return {
      id: "c",
      name: "Personal",
      identity: "test@example.com",
      provider: "google",
      syncedAt: time,
      start: "2026-09-20T00:00:00Z",
      end: "2026-09-27T00:00:00Z",
      events: [
        {
          id: "e",
          title,
          start: "2026-09-20T10:00:00Z",
          end: "2026-09-20T11:00:00Z",
          allDay: false,
        },
      ],
    };
  }
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/api/calendar/c") {
      started();
      await wait;
      await route.fulfill({ json: snapshot("Old cache", 1700000000) });
      return;
    }
    const body =
      p === "/api/auth/status"
        ? { authenticated: true }
        : p === "/api/calendar"
          ? {
              accounts: [],
              items: [
                { id: "c", name: "Personal", identity: "test@example.com" },
              ],
            }
          : p === "/api/calendar/c/sync"
            ? snapshot("Fresh sync", 1800000000)
            : { data: [], items: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Calendar", exact: true }).click();
  await entered;
  await page.getByRole("button", { name: "Sync this date range" }).click();
  await expect(page.getByRole("heading", { name: "Fresh sync" })).toBeVisible();
  release();
  await page.waitForTimeout(100);
  await expect(page.getByRole("heading", { name: "Fresh sync" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Old cache" })).toHaveCount(0);
});
