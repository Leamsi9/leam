import { test, expect } from "@playwright/test";

test("reviewed calendar creation keeps one retry identity across a reload", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const calendar = {
    id: "c",
    name: "Personal",
    identity: "test@example.com",
    provider: "google",
    canWrite: true,
  };
  const commitment = {
    id: "task",
    revision: 1,
    title: "Work on Leam",
    status: "active",
  };
  const posts: any[] = [];
  let history: any[] = [];
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/api/calendar/actions/preview") {
      const schedule = route.request().postDataJSON();
      await route.fulfill({
        json: {
          schedule,
          title: commitment.title,
          calendar,
          digest: "a".repeat(64),
          start: "2026-09-20T09:00:00+00:00",
          end: "2026-09-20T09:30:00+00:00",
          localStart: "2026-09-20T10:00:00+01:00",
          localEnd: "2026-09-20T10:30:00+01:00",
        },
      });
      return;
    }
    if (p === "/api/calendar/actions" && route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      posts.push(body);
      if (posts.length === 1) {
        history = [
          {
            requestId: body.requestId,
            state: "unconfirmed",
            request: body,
            review: {
              title: commitment.title,
              calendar,
              localStart: "2026-09-20T10:00:00+01:00",
              localEnd: "2026-09-20T10:30:00+01:00",
            },
            error: "Response lost",
            result: null,
          },
        ];
        await route.fulfill({ status: 502, json: { detail: "Response lost" } });
        return;
      }
      const result = {
        requestId: body.requestId,
        calendarId: "c",
        commitmentId: "task",
        event: {
          id: "external",
          title: commitment.title,
          start: "2026-09-20T09:00:00Z",
          end: "2026-09-20T09:30:00Z",
        },
      };
      history = [{ ...history[0], state: "complete", error: null, result }];
      await route.fulfill({ json: result });
      return;
    }
    const body =
      p === "/api/auth/status"
        ? { authenticated: true }
        : p === "/api/calendar"
          ? { accounts: [], items: [calendar] }
          : p === "/api/calendar/c"
            ? { ...calendar, events: [] }
            : p === "/api/commitments"
              ? { items: [commitment] }
              : p === "/api/calendar/actions"
                ? { items: history }
                : { items: [], data: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Calendar", exact: true }).click();
  await page.getByLabel("Commitment to schedule").selectOption("task");
  await page.getByLabel("Event date", { exact: true }).fill("2026-09-20");
  await page.getByLabel("Start time", { exact: true }).fill("10:00");
  await page
    .getByRole("button", { name: "Preview calendar event", exact: true })
    .click();
  await expect(page.getByText("No invitations will be sent.")).toBeVisible();
  expect(posts).toHaveLength(0);
  await page
    .getByRole("button", { name: "Create this event", exact: true })
    .click();
  await expect.poll(() => posts.length).toBe(1);
  await expect(
    page.getByRole("button", { name: "Create this event", exact: true }),
  ).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", { name: "Calendar", exact: true }).click();
  await page
    .getByRole("button", { name: "Recover this calendar action", exact: true })
    .click();
  await expect(
    page.getByText("Event creation confirmed", { exact: true }),
  ).toBeVisible();
  expect(posts).toHaveLength(2);
  expect(posts[1]).toEqual(posts[0]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("a rejected stale preview releases only the unreserved attempt", async ({
  page,
}) => {
  const item = { id: "task", revision: 1, title: "Focus", status: "active" };
  const calendar = {
    id: "c",
    name: "Personal",
    identity: "test@example.com",
    provider: "google",
    canWrite: true,
  };
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/api/calendar/actions/preview") {
      const schedule = route.request().postDataJSON();
      await route.fulfill({
        json: {
          schedule,
          title: item.title,
          calendar,
          digest: "b".repeat(64),
          localStart: "2026-09-20T10:00:00+01:00",
          localEnd: "2026-09-20T10:30:00+01:00",
        },
      });
      return;
    }
    if (p === "/api/calendar/actions" && route.request().method() === "POST") {
      await route.fulfill({
        status: 409,
        headers: { "X-Leam-Action-Reserved": "no" },
        json: { detail: "Commitment changed; review its current version" },
      });
      return;
    }
    await route.fulfill({
      json:
        p === "/api/auth/status"
          ? { authenticated: true }
          : p === "/api/calendar"
            ? { accounts: [], items: [calendar] }
            : p === "/api/calendar/c"
              ? { ...calendar, events: [] }
              : p === "/api/commitments"
                ? { items: [item] }
                : { items: [], data: [] },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Calendar", exact: true }).click();
  await page.getByLabel("Commitment to schedule").selectOption("task");
  await page
    .getByRole("button", { name: "Preview calendar event", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Create this event", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Preview calendar event", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", {
      name: "Recover this calendar action",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(() =>
      JSON.parse(sessionStorage.getItem("leam-calendar-actions") || "[]"),
    ),
  ).toEqual([]);
});
