import { test, expect, type Page } from "@playwright/test";

const commitmentId = "20000000-0000-4000-8000-000000000001";
const proposalId = "30000000-0000-4000-8000-000000000001";
const artifact = {
  id: "fixture-report",
  title: "Project report",
  kind: "markdown",
  filename: "report.md",
  content: "# A saved report",
  publishedAt: 1790000000,
  sha256: "a".repeat(64),
  bytes: 32,
};
async function fixture(page: Page) {
  const targets = [
    {
      targetType: "commitment",
      targetId: commitmentId,
      title: "Prepare invoice",
      state: "active",
      available: true,
      location: "goals",
    },
    {
      targetType: "feature",
      targetId: "fixture-feature",
      title: "Useful feature",
      state: "Complete",
      available: true,
      location: "updates",
      updateId: "fixture-update",
    },
    {
      targetType: "proposal",
      targetId: proposalId,
      title: "Coding request",
      state: "pending",
      available: true,
      location: "approvals",
    },
  ];
  const state = {
    targets,
    links: [] as typeof targets,
    revision: 0,
    reads: [] as string[],
    writes: [] as any[],
    receipts: new Map<string, any>(),
    lose: false,
    conflict: false,
    searchGate: null as Promise<void> | null,
  };
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname;
    const method = request.method();
    if (method === "GET") state.reads.push(path + url.search);
    if (method !== "GET")
      state.writes.push({ path, method, body: request.postDataJSON() });
    let body: any = {
      items: [],
      data: [],
      models: [],
      providers: [],
      threads: [],
      messages: [],
    };
    if (path === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (path === "/api/artifacts")
      body = { items: [artifact], nextCursor: null, total: 1 };
    if (path === "/api/artifacts/fixture-report") body = artifact;
    if (path === "/api/artifacts/fixture-report/links") {
      if (method === "POST") {
        const input = request.postDataJSON();
        if (state.conflict) {
          state.conflict = false;
          state.revision++;
          return route.fulfill({
            status: 409,
            json: { detail: "Stale revision" },
          });
        }
        if (!state.receipts.has(input.requestId)) {
          if (input.revision !== state.revision)
            return route.fulfill({
              status: 409,
              json: { detail: "Stale revision" },
            });
          const target = targets.find(
            (item) =>
              item.targetType === input.targetType &&
              item.targetId === input.targetId,
          )!;
          state.links = state.links.filter(
            (item) => item.targetId !== input.targetId,
          );
          if (input.operation === "link") state.links.push(target);
          state.revision++;
          state.receipts.set(input.requestId, {
            ...input,
            revision: state.revision,
            resourceId: artifact.id,
          });
        }
        if (state.lose) {
          state.lose = false;
          return route.abort("failed");
        }
        body = state.receipts.get(input.requestId);
      } else
        body = {
          resourceId: artifact.id,
          revision: state.revision,
          items: state.links,
        };
    }
    if (path === "/api/resource-links/targets") {
      const selected = targets.filter(
        (item) => item.targetType === url.searchParams.get("type"),
      );
      if (state.searchGate) await state.searchGate;
      body = { items: selected, nextCursor: null };
    }
    if (path === "/api/resource-links")
      body = { items: [artifact], target: targets[0] };
    if (path === "/api/commitments")
      body = {
        items: [
          {
            id: commitmentId,
            title: "Prepare invoice",
            status: "active",
            revision: 7,
            kind: "task",
            capacityId: null,
          },
        ],
      };
    if (path === "/api/today") body = { items: [] };
    if (path === "/api/proposals")
      body = {
        items: [
          {
            id: proposalId,
            fingerprint: "fixture",
            thread_id: "fixture-thread",
            operation: "commitment.create",
            state: "pending",
            unread: false,
            readAt: 1,
            input: { title: "Coding request", kind: "task" },
            review: {
              after: { title: "Coding request" },
              approval: { mode: "manual" },
            },
            reason: "Requested",
            result: null,
          },
        ],
        nextOffset: null,
        unreadCount: 0,
      };
    if (path === "/api/updates")
      body = {
        items: [
          {
            id: "fixture-update",
            feature: "fixture-feature",
            title: "Useful feature",
            revision: 1,
            stage: "Complete",
            summary: "Deployed",
            deployedAt: new Date().toISOString(),
            deploymentId: "fixture-release",
            qa: { state: "passed", details: "Caller QA" },
            uat: { state: "passed", details: "User accepted" },
            unread: false,
            superseded: false,
          },
        ],
        sequence: 1,
        unreadCount: 0,
        nextCursor: null,
      };
    await route.fulfill({ json: body });
  });
  return state;
}
async function open(page: Page) {
  await page.goto("/?artifact=fixture-report");
  await page
    .locator("summary")
    .filter({ hasText: /^Linked work$/ })
    .click();
  await expect(page.getByText("No linked work yet.")).toBeVisible();
}
for (const width of [390, 844]) {
  test(`canonical search link/unlink and compact library at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width === 844 ? 390 : 844 });
    const state = await fixture(page);
    await page.goto("/?view=resources");
    await expect(
      page.getByRole("link", { name: "Open Project report", exact: true }),
    ).toBeVisible();
    expect(
      state.reads.filter(
        (path) => path.includes("/links") || path.includes("resource-links"),
      ),
    ).toEqual([]);
    await page
      .locator("summary")
      .filter({ hasText: /^Linked work$/ })
      .click();
    await page.getByLabel("Find work by name").fill("Prepare invoice");
    await page.getByRole("button", { name: "Find work", exact: true }).click();
    await page
      .getByRole("button", { name: "Link Prepare invoice", exact: true })
      .click();
    await expect(
      page.getByRole("link", { name: "Prepare invoice", exact: true }),
    ).toHaveAttribute("href", `/?view=goals&commitment=${commitmentId}`);
    expect(state.writes[0]).toMatchObject({
      path: "/api/artifacts/fixture-report/links",
      method: "POST",
      body: {
        revision: 0,
        operation: "link",
        targetType: "commitment",
        targetId: commitmentId,
      },
    });
    expect(state.writes[0].body.requestId).toMatch(/^[0-9a-f-]{36}$/);
    expect(state.reads).toContain(
      "/api/resource-links/targets?type=commitment&q=Prepare+invoice&limit=30",
    );
    state.targets[0].title = "Prepare corrected invoice";
    await page.getByRole("button", { name: "Refresh linked work" }).click();
    await expect(
      page.getByRole("link", { name: "Prepare corrected invoice" }),
    ).toBeVisible();
    await page
      .getByRole("button", {
        name: "Unlink Prepare corrected invoice",
        exact: true,
      })
      .click();
    await expect(page.getByText("No linked work yet.")).toBeVisible();
    expect(state.writes[1].body).toMatchObject({
      revision: 1,
      operation: "unlink",
      targetType: "commitment",
      targetId: commitmentId,
    });
    expect(state.writes).toHaveLength(2);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
  });
}
test("uncertain mutation retries the same receipt after reload, without duplicating", async ({
  page,
}) => {
  const state = await fixture(page);
  state.lose = true;
  await open(page);
  await page.getByRole("button", { name: "Find work", exact: true }).click();
  await page
    .getByRole("button", { name: "Link Prepare invoice", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText(
    "Confirmation unavailable",
  );
  const original = state.writes[0].body;
  await page.reload();
  await page
    .locator("summary")
    .filter({ hasText: /^Linked work$/ })
    .click();
  await expect(
    page.getByRole("button", { name: "Retry saved change" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Retry saved change" }).click();
  await expect(
    page.getByText("Resource linked. Original records are unchanged."),
  ).toBeVisible();
  expect(state.writes[1].body).toEqual(original);
  expect(state.links).toHaveLength(1);
  expect(state.revision).toBe(1);
});
test("revision conflict refreshes before a new explicit action; unavailable links remain removable", async ({
  page,
}) => {
  const state = await fixture(page);
  state.conflict = true;
  await open(page);
  await page.getByRole("button", { name: "Find work", exact: true }).click();
  await page
    .getByRole("button", { name: "Link Prepare invoice", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("Linked work changed");
  expect(state.writes).toHaveLength(1);
  await page
    .getByRole("button", { name: "Link Prepare invoice", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: "Prepare invoice", exact: true }),
  ).toBeVisible();
  expect(state.writes[1].body.revision).toBe(1);
  expect(state.writes[1].body.requestId).not.toBe(
    state.writes[0].body.requestId,
  );
  state.targets[0].available = false;
  state.targets[0].title = "";
  await page.getByRole("button", { name: "Refresh linked work" }).click();
  await expect(
    page.getByText("Record unavailable", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Unlink unavailable record" }).click();
  await expect(page.getByText("No linked work yet.")).toBeVisible();
});
test("changing target type fences a late name-search response", async ({
  page,
}) => {
  const state = await fixture(page);
  let release!: () => void;
  state.searchGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await open(page);
  await page.getByRole("button", { name: "Find work", exact: true }).click();
  await expect
    .poll(() =>
      state.reads.some((path) => path.includes("targets?type=commitment")),
    )
    .toBe(true);
  await page
    .getByRole("combobox", { name: "Link to", exact: true })
    .selectOption("feature");
  state.searchGate = null;
  release();
  await page.getByRole("button", { name: "Find work", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Link Useful feature" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Link Prepare invoice" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Link Useful feature" }).click();
  await expect(
    page.getByRole("link", { name: "Useful feature", exact: true }),
  ).toHaveAttribute(
    "href",
    "/?view=updates&feature=fixture-feature&update=fixture-update",
  );
});
for (const [view, type, id] of [
  ["goals", "commitment", commitmentId],
  ["updates", "feature", "fixture-feature"],
  ["approvals", "proposal", proposalId],
]) {
  test(`linked ${view} target focuses canonical card and lazy reverse resources`, async ({
    page,
  }) => {
    const state = await fixture(page);
    await page.goto(`/?view=${view}&${type}=${id}`);
    const selected = page.locator(".resource-target-selected");
    await expect(selected).toBeVisible();
    await expect(selected).toBeFocused();
    expect(
      state.reads.filter((path) => path.startsWith("/api/resource-links?")),
    ).toEqual([]);
    if (view === "updates")
      await selected.getByText("Context and links", { exact: true }).click();
    await selected
      .locator("summary")
      .filter({ hasText: /^Resources$/ })
      .click();
    await expect(
      selected.getByRole("link", { name: /Project report/ }),
    ).toHaveAttribute("href", "/?artifact=fixture-report");
    expect(state.reads).toContain(
      `/api/resource-links?targetType=${type}&targetId=${id}`,
    );
    expect(state.writes).toEqual([]);
  });
}

test("an older linked approval loads its exact canonical record beyond the first page", async ({
  page,
}) => {
  const state = await fixture(page);
  const exactReads: string[] = [];
  await page.route(
    (url) => url.pathname === "/api/proposals",
    (route) => route.fulfill({ json: { items: [], nextOffset: 50 } }),
  );
  await page.route(
    (url) => url.pathname === `/api/proposals/${proposalId}`,
    (route) => {
      exactReads.push(route.request().url());
      return route.fulfill({
        json: {
          id: proposalId,
          fingerprint: "older",
          operation: "commitment.create",
          state: "complete",
          unread: false,
          readAt: 1,
          input: { title: "Older applied change" },
          review: {
            after: { title: "Older applied change" },
            approval: { mode: "manual" },
          },
          result: null,
        },
      });
    },
  );
  await page.goto(`/?view=approvals&proposal=${proposalId}`);
  const selected = page.locator(".resource-target-selected");
  await expect(
    selected.getByRole("heading", { name: "Older applied change" }),
  ).toBeVisible();
  await expect(selected).toBeFocused();
  expect(exactReads).toHaveLength(1);
  expect(state.writes).toEqual([]);
});

test("an older completed feature loads its exact publication and retains the stable feature key", async ({
  page,
}) => {
  const state = await fixture(page);
  const id = "40000000-0000-4000-8000-000000000001";
  const exactReads: string[] = [];
  await page.route(
    (url) => url.pathname === "/api/updates",
    (route) =>
      route.fulfill({
        json: { items: [], sequence: 100, unreadCount: 0, nextCursor: 50 },
      }),
  );
  await page.route(
    (url) => url.pathname === `/api/updates/${id}`,
    (route) => {
      exactReads.push(route.request().url());
      return route.fulfill({
        json: {
          id,
          feature: "fixture-feature",
          title: "Older completed feature",
          revision: 1,
          stage: "Complete",
          summary: "Deployed and accepted",
          deployedAt: new Date().toISOString(),
          deploymentId: "older-release",
          qa: { state: "passed" },
          uat: { state: "passed" },
          unread: false,
          superseded: false,
        },
      });
    },
  );
  await page.goto(`/?view=updates&feature=fixture-feature&update=${id}`);
  const selected = page.locator(".resource-target-selected");
  await expect(
    selected.getByRole("heading", { name: "Older completed feature" }),
  ).toBeVisible();
  await selected.getByText("Context and links", { exact: true }).click();
  await selected
    .locator("summary")
    .filter({ hasText: /^Resources$/ })
    .click();
  await expect(
    selected.getByRole("link", { name: /Project report/ }),
  ).toBeVisible();
  expect(state.reads).toContain(
    "/api/resource-links?targetType=feature&targetId=fixture-feature",
  );
  expect(exactReads).toHaveLength(1);
  expect(state.writes).toEqual([]);
});
