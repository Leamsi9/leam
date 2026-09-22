import { test, expect, type Page } from "@playwright/test";

async function fixture(page: Page) {
  const state = { exists: true, uncertain: false, deletions: [] as any[] };
  const item = {
    id: "report",
    title: "Review report",
    kind: "markdown",
    content: "# Contents",
    publishedAt: 1790000000,
    modifiedAt: 1790000000,
    sha256: "a".repeat(64),
    bytes: 10,
    unread: true,
  };
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {
      items: [],
      threads: [],
      messages: [],
      models: [],
      providers: [],
    };
    if (path === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (path === "/api/artifacts/_status")
      body = {
        total: state.exists ? 1 : 0,
        unreadCount: state.exists ? 1 : 0,
        unreadIds: state.exists ? ["report"] : [],
      };
    if (path === "/api/artifacts")
      body = {
        items: state.exists ? [item] : [],
        total: state.exists ? 1 : 0,
        nextCursor: null,
      };
    if (path === "/api/artifacts/report") {
      if (route.request().method() === "DELETE") {
        state.deletions.push(route.request().postDataJSON());
        state.exists = false;
        if (state.uncertain) {
          state.uncertain = false;
          return route.fulfill({
            status: 503,
            json: { detail: "Deletion receipt unavailable; retry" },
          });
        }
        body = { id: "report", state: "deleted", deletedAt: 1790000200 };
      } else if (state.exists) body = item;
      else
        return route.fulfill({
          status: 404,
          json: { detail: "Resource not found" },
        });
    }
    await route.fulfill({ json: body });
  });
  return state;
}

for (const width of [390, 844])
  test(`library delete requires confirmation and refreshes count at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 390 });
    const state = await fixture(page);
    await page.goto("/?view=resources");
    await page
      .getByRole("button", { name: "Delete Review report", exact: true })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "Delete Review report",
      exact: true,
    });
    await expect(dialog).toBeVisible();
    expect(state.deletions).toEqual([]);
    await dialog
      .getByRole("button", { name: "Close Delete Review report" })
      .click();
    expect(state.exists).toBe(true);
    await page
      .getByRole("button", { name: "Delete Review report", exact: true })
      .click();
    await dialog
      .getByRole("button", { name: "Delete resource", exact: true })
      .click();
    await expect(
      page.getByRole("link", { name: "Open Review report" }),
    ).toHaveCount(0);
    await expect(
      page.getByText("0 resources saved", { exact: true }),
    ).toBeVisible();
    expect(state.deletions).toHaveLength(1);
    expect(state.deletions[0]).toMatchObject({
      expectedSha256: "a".repeat(64),
      confirmed: true,
    });
  });

test("uncertain delete retries its exact UUID instead of submitting a new operation", async ({
  page,
}) => {
  const state = await fixture(page);
  state.uncertain = true;
  await page.goto("/?view=resources");
  await page
    .getByRole("button", { name: "Delete Review report", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Delete Review report",
    exact: true,
  });
  await dialog
    .getByRole("button", { name: "Delete resource", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText("receipt unavailable");
  await dialog
    .getByRole("button", { name: "Retry deletion", exact: true })
    .click();
  await expect(
    page.getByText("0 resources saved", { exact: true }),
  ).toBeVisible();
  expect(state.deletions).toHaveLength(2);
  expect(state.deletions[1]).toEqual(state.deletions[0]);
});

test("viewer deletion closes content and leaves a library return link", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/?artifact=report");
  await expect(
    page.getByRole("heading", { name: "Contents", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Delete Review report", exact: true })
    .click();
  await page
    .getByRole("dialog", { name: "Delete Review report", exact: true })
    .getByRole("button", { name: "Delete resource", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Resource deleted", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Contents", exact: true }),
  ).toHaveCount(0);
  expect(state.deletions).toHaveLength(1);
});
