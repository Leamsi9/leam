import { test, expect } from "@playwright/test";

test("mobile event edit reviews the current version and recovers after reload", async ({
  page,
}) => {
  const creation = "86b4940b-b026-4a7d-a85c-ef0360413a28";
  const calendar = {
    id: "c",
    name: "Personal",
    identity: "test@example.com",
    provider: "google",
    canWrite: true,
  };
  const event = {
    id: "e",
    title: "Focus",
    start: "2026-09-20T09:00:00Z",
    end: "2026-09-20T09:30:00Z",
    etag: '"v1"',
  };
  const creationRecord = {
    requestId: creation,
    state: "complete",
    request: { schedule: { calendarId: "c" } },
    review: { title: "Focus", calendar },
    result: { event },
  };
  const posts: any[] = [];
  let changes: any[] = [];
  let removed = false;
  let releaseHistory!: () => void;
  const historyGate = new Promise<void>(
    (resolve) => (releaseHistory = resolve),
  );
  let historyStarted!: () => void;
  const historyEntered = new Promise<void>(
    (resolve) => (historyStarted = resolve),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p.endsWith("/changes") && route.request().method() === "GET") {
      historyStarted();
      await historyGate;
    }
    if (p === "/api/calendar/changes/preview") {
      const request = route.request().postDataJSON();
      await route.fulfill({
        json: {
          request,
          before: event,
          after:
            request.operation === "edit"
              ? {
                  title: request.edit.title,
                  localStart: "2026-09-20T11:00:00+01:00",
                  localEnd: "2026-09-20T11:30:00+01:00",
                  timezone: "Europe/London",
                }
              : null,
        },
      });
      return;
    }
    if (p === "/api/calendar/changes") {
      const body = route.request().postDataJSON();
      posts.push(body);
      if (posts.length === 1) {
        changes = [
          {
            requestId: body.requestId,
            state: "pending",
            request: body,
            error: "Response lost",
          },
        ];
        await route.fulfill({ status: 502, json: { detail: "Response lost" } });
        return;
      }
      if (body.operation === "delete") {
        removed = true;
        changes = [
          ...changes,
          { requestId: body.requestId, request: body, state: "complete" },
        ];
        await route.fulfill({ json: { deleted: true } });
        return;
      }
      changes = [{ ...changes[0], state: "complete" }];
      await route.fulfill({
        json: { event: { ...event, title: "Later focus" } },
      });
      return;
    }
    const body =
      p === "/api/auth/status"
        ? { authenticated: true }
        : p === "/api/calendar"
          ? { accounts: [], items: [calendar] }
          : p === "/api/calendar/c"
            ? { ...calendar, events: [event] }
            : p === "/api/calendar/actions"
              ? { items: [creationRecord] }
              : p.endsWith("/event")
                ? {
                    event,
                    deleted: removed,
                    editBlocked: null,
                    checkedAt: 1800000000,
                    edit: {
                      title: "Focus",
                      date: "2026-09-20",
                      time: "10:00",
                      timezone: "Europe/London",
                      minutes: 30,
                      fold: 0,
                    },
                  }
                : p.endsWith("/changes")
                  ? { items: changes }
                  : { items: [], data: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Calendar", exact: true }).click();
  await historyEntered;
  await expect(
    page.getByRole("button", { name: "Inspect event", exact: true }),
  ).toBeDisabled();
  releaseHistory();
  await page
    .getByRole("button", { name: "Inspect event", exact: true })
    .click();
  await page.getByLabel("Event title", { exact: true }).fill("Later focus");
  await page
    .getByRole("button", { name: "Preview event edit", exact: true })
    .click();
  expect(posts).toHaveLength(0);
  await page
    .getByRole("button", { name: "Confirm event edit", exact: true })
    .click();
  await expect.poll(() => posts.length).toBe(1);
  await page.reload();
  await page.getByRole("button", { name: "Calendar", exact: true }).click();
  await page
    .getByRole("button", { name: "Recover event change", exact: true })
    .click();
  await expect(
    page.getByText("Event change confirmed", { exact: true }),
  ).toBeVisible();
  expect(posts).toHaveLength(2);
  expect(posts[1]).toEqual(posts[0]);
  expect(posts[0].expectedEtag).toBe('"v1"');
  await page
    .getByRole("button", { name: "Inspect event", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Review event removal", exact: true })
    .click();
  expect(posts).toHaveLength(2);
  await page
    .getByRole("button", { name: "Confirm event removal", exact: true })
    .click();
  await expect.poll(() => posts.length).toBe(3);
  await expect(
    page.getByText("Event change confirmed", { exact: true }),
  ).toBeVisible();
  expect(posts[2].operation).toBe("delete");
  await page
    .getByRole("button", { name: "Inspect event", exact: true })
    .click();
  await expect(
    page.getByText("This event has been removed from the provider calendar."),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
