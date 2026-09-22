import { test, expect, type Page } from "@playwright/test";
import { navigate, settingsSection } from "./navigation";
async function fixture(page: Page) {
  const state = {
    unread: ["report-one", "report-two"],
    calls: [] as string[],
    writes: [] as any[],
    failRead: false,
    publishOnRead: false,
  };
  const status = () => ({
    total: 2 + (state.unread.includes("new-report") ? 1 : 0),
    unreadCount: state.unread.length,
    unreadIds: [...state.unread],
  });
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname;
    state.calls.push(path + url.search);
    if (request.method() !== "GET")
      state.writes.push({
        path,
        method: request.method(),
        body: request.postDataJSON(),
      });
    let body: any = {
      items: [],
      data: [],
      threads: [],
      models: [],
      providers: [],
      messages: [],
    };
    if (path === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (path === "/api/artifacts/_status") body = status();
    if (path === "/api/artifacts/_read") {
      if (state.failRead)
        return route.fulfill({
          status: 503,
          json: { detail: "Read state unavailable" },
        });
      if (state.publishOnRead) {
        state.unread.push("new-report");
        state.publishOnRead = false;
      }
      const ids = request.postDataJSON().ids;
      state.unread = state.unread.filter((id) => !ids.includes(id));
      body = status();
    }
    const rows = ["report-one", "report-two"].map((id, index) => ({
      id,
      title: index ? "Beta report" : "Alpha report",
      kind: "markdown",
      content: "# A report",
      filename: id + ".md",
      publishedAt: 1790000000,
      modifiedAt: 1790000100 + index,
      sha256: "a".repeat(64),
      unread: state.unread.includes(id),
      sortParent: index
        ? null
        : {
            title: "A parent ticket",
            targetType: "feature",
            targetId: "a-feature",
          },
    }));
    if (path === "/api/artifacts")
      body = { items: rows, total: 2, nextCursor: null };
    if (path === "/api/artifacts/report-one") body = rows[0];
    if (path === "/api/usage")
      body = {
        totals: {
          requests: 0,
          input: "0",
          output: "0",
          total: "0",
          cached: null,
          reasoning: null,
          nonCached: null,
          cacheKnownRequests: 0,
        },
        daily: [],
        groups: [],
        coverage: { sources: [], conflicts: 0, unassignedRequests: 0 },
        opportunities: [],
        filters: { models: [], operations: [], goals: [] },
        settings: {
          enabled: true,
          dailyWarningTokens: 0,
          largeCallTokens: 100000,
        },
        warning: { exceeded: false },
        generatedAt: 1790000000,
      };
    if (path === "/api/usage/exhaustions")
      body = { events: [], periods: [], nextOffset: null };
    if (path === "/api/companion/overview")
      body = {
        capacities: [],
        commitments: [],
        calendar: [],
        priorities: [],
        modules: [],
      };
    await route.fulfill({ json: body });
  });
  return state;
}
for (const viewport of [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
]) {
  test(`explicit read controls and More dot preserve later publications at ${viewport.width}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const state = await fixture(page);
    await page.goto("/?view=resources");
    const library = page.getByRole("region", {
      name: "Resources",
      exact: true,
    });
    await expect(library.getByText("2 unread", { exact: true })).toBeVisible();
    await expect(
      page.locator('nav [aria-label="2 unread resources"]:visible'),
    ).toBeVisible();
    expect(state.writes).toEqual([]);
    await library
      .getByRole("button", { name: "Mark Alpha report as read", exact: true })
      .click();
    await expect(library.getByText("1 unread", { exact: true })).toBeVisible();
    expect(state.writes[0]).toEqual({
      path: "/api/artifacts/_read",
      method: "POST",
      body: { ids: ["report-one"] },
    });
    state.publishOnRead = true;
    await library
      .getByRole("button", { name: "Mark all resources read", exact: true })
      .click();
    await expect.poll(() => state.writes.length).toBe(2);
    expect(state.writes[1].body).toEqual({ ids: ["report-two"] });
    await expect(library.getByText("1 unread", { exact: true })).toBeVisible();
    expect(state.unread).toEqual(["new-report"]);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
  });
}
test("opening a viewer never marks it read; failed explicit acknowledgment stays visible and retryable", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/?artifact=report-one");
  await expect(
    page.getByRole("button", { name: "Mark as read", exact: true }),
  ).toBeVisible();
  expect(state.writes).toEqual([]);
  state.failRead = true;
  await page.getByRole("button", { name: "Mark as read", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Read state unavailable");
  expect(state.unread).toContain("report-one");
  state.failRead = false;
  await page.getByRole("button", { name: "Mark as read", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Mark as read", exact: true }),
  ).toHaveCount(0);
});
test("resource ordering is server-backed, defaults newest and persists all selection controls", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/?view=resources");
  await expect(
    page.getByRole("combobox", { name: "Sort resources", exact: true }),
  ).toHaveValue("modified");
  await expect
    .poll(() =>
      state.calls.some(
        (path) =>
          path.includes("/api/artifacts?") &&
          path.includes("sort=modified&order=desc"),
      ),
    )
    .toBe(true);
  await page
    .getByRole("combobox", { name: "Sort resources", exact: true })
    .selectOption("parent");
  await page
    .getByRole("combobox", { name: "Order", exact: true })
    .selectOption("asc");
  await expect(
    page.getByText("A parent ticket", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("No available parent ticket", { exact: true }),
  ).toBeVisible();
  await expect
    .poll(() =>
      state.calls.some(
        (path) =>
          path.includes("sort=parent&order=asc") && !path.includes("cursor="),
      ),
    )
    .toBe(true);
  await page.reload();
  await expect(
    page.getByRole("combobox", { name: "Sort resources", exact: true }),
  ).toHaveValue("parent");
  await expect(
    page.getByRole("combobox", { name: "Order", exact: true }),
  ).toHaveValue("asc");
  await page
    .getByRole("combobox", { name: "Sort resources", exact: true })
    .selectOption("title");
  await expect
    .poll(() =>
      state.calls.some((path) => path.includes("sort=title&order=asc")),
    )
    .toBe(true);
  expect(state.writes).toEqual([]);
});
test("More hosts one retained Usage app and Across Leam opens inside Settings", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await fixture(page);
  await page.goto("/?view=settings");
  await expect(
    page.getByRole("button", { name: "Open Usage", exact: true }),
  ).toBeVisible();
  expect(state.calls.filter((path) => path.startsWith("/api/usage"))).toEqual(
    [],
  );
  await navigate(page, "Usage");
  await expect(
    page.getByRole("region", { name: "Usage app", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("group", { name: "Usage views" })
    .getByRole("button", { name: "Goals", exact: true })
    .click();
  await page.getByLabel("New goal name", { exact: true }).fill("A useful goal");
  await navigate(page, "Settings");
  await expect(
    page.getByRole("region", { name: "Usage app", exact: true }),
  ).not.toBeVisible();
  await settingsSection(page, "Across Leam");
  await expect
    .poll(
      () =>
        state.calls.filter((path) => path === "/api/companion/overview").length,
    )
    .toBe(1);
  await page.getByRole("button", { name: "Open Usage", exact: true }).click();
  await expect(page.getByLabel("New goal name", { exact: true })).toHaveValue(
    "A useful goal",
  );
  expect(
    state.calls.filter((path) => path.startsWith("/api/usage?")),
  ).toHaveLength(1);
  expect(state.writes).toEqual([]);
});
