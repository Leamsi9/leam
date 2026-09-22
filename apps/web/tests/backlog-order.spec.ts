import { test, expect, type Page } from "@playwright/test";
import { navigate } from "./navigation";

test.use({ hasTouch: true });
async function setup(page: Page) {
  const base = {
    currentStep: "Long implementation details stay collapsed",
    percent: 35,
    blockers: [],
    assessedAt: new Date().toISOString(),
    stale: false,
    revision: 1,
    deliveryState: "ready",
    deliveryLane: "ready",
    worker: "worker-one",
    priority: "normal",
    subtasks: [{ id: "build", title: "Build candidate", state: "done" }],
  };
  const state = {
    revision: 0,
    conflict: false,
    loseReceipt: false,
    writes: [] as any[],
    receipts: new Map<string, any>(),
    items: ["alpha", "beta", "gamma"].map((feature, rank) => ({
      ...base,
      feature,
      title: `${feature} work`,
      rank: rank + 1,
    })) as any[],
  };
  state.items.push({
    ...base,
    feature: "blocked",
    title: "Blocked work",
    deliveryState: "in_progress",
    deliveryLane: "blocked",
    blockers: ["Await access"],
    rank: 4,
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: any = { data: [], items: [] };
    if (path === "/api/auth/status") data = { authenticated: true };
    if (path === "/api/backlog")
      data = {
        items: state.items,
        ordering: { revision: state.revision, positions: {} },
        checkedAt: Date.now() / 1000,
        staleAfterSeconds: 1200,
        review: { current: true, reviewedAt: Date.now() / 1000 },
      };
    if (path === "/api/backlog/order") {
      const body = route.request().postDataJSON();
      state.writes.push(body);
      if (state.conflict) {
        state.conflict = false;
        state.revision++;
        return route.fulfill({
          status: 409,
          json: { detail: "Backlog changed" },
        });
      }
      data = state.receipts.get(body.requestId);
      if (!data) {
        expect(body.expectedRevisions).toEqual(Object.fromEntries(state.items.map(item => [item.feature, item.revision])));
        expect(body.lane).toBe("all");
        expect([...body.features].sort()).toEqual(state.items.map(item => item.feature).sort());
        expect(body.revision).toBe(state.revision);
        state.items = [
          ...body.features.map((id: string) =>
            state.items.find((item) => item.feature === id),
          ),
          ...(body.lane === "all" ? [] : [state.items.find((item) => item.feature === "blocked")]),
        ];
        state.items = state.items.map((item, index) => ({ ...item, rank: index + 1 }));
        data = { requestId: body.requestId, revision: ++state.revision };
        state.receipts.set(body.requestId, data);
      }
      if (state.loseReceipt) return route.abort("failed");
    }
    await route.fulfill({ json: data });
  });
  await page.goto("/");
  await navigate(page, "Backlog");
  await expect(
    page.getByRole("article", { name: "alpha work", exact: true }),
  ).toBeVisible();
  return state;
}
for (const viewport of [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
  { width: 1440, height: 900 },
]) {
  test(`compact cards and pointer priority ${viewport.width}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    const state = await setup(page);
    const first = page.getByRole("article", {
      name: "alpha work",
      exact: true,
    });
    await expect(
      first.getByText("Long implementation details stay collapsed"),
    ).not.toBeVisible();
    const initialCard = (await first.boundingBox())!;
    const grip = (await first
      .getByRole("button", {
        name: "Drag alpha work within Queued",
        exact: true,
      })
      .boundingBox())!;
    const expand = (await first.locator(":scope > details > summary").boundingBox())!;
    expect(initialCard.height).toBeLessThanOrEqual(100);
    expect(grip.width).toBeGreaterThanOrEqual(44);
    expect(grip.height).toBeGreaterThanOrEqual(44);
    expect(expand.height).toBeGreaterThanOrEqual(44);
    await testInfo.attach("compact-geometry", {
      body: JSON.stringify({ viewport, initialCard, grip, expand }),
      contentType: "application/json",
    });
    await page.getByRole("button", { name: "Kanban", exact: true }).click();
    const source = page.getByRole("button", {
      name: "Drag gamma work within Queued",
      exact: true,
    });
    await source.scrollIntoViewIfNeeded();
    const target = page.getByRole("article", {
      name: "beta work",
      exact: true,
    });
    await target.scrollIntoViewIfNeeded();
    // On short landscape, bring both adjacent cards into view before dragging.
    await page.evaluate(() => {
      const cards = [...document.querySelectorAll("[data-backlog-feature]")];
      const beta = cards.find(
        (x) => x.getAttribute("data-backlog-feature") === "beta",
      );
      beta?.scrollIntoView({ block: "center" });
    });
    const origin = (await source.boundingBox())!,
      to = (await target.boundingBox())!;
    await page.mouse.move(
      origin.x + origin.width / 2,
      origin.y + origin.height / 2,
    );
    await page.mouse.down();
    await page.mouse.move(to.x + to.width / 2, to.y + 8, { steps: 8 });
    await page.mouse.up();
    await expect.poll(() => state.writes.length).toBe(1);
    expect(state.writes[0].features).toEqual(["alpha", "gamma", "beta", "blocked"]);
    await expect(
      page.getByRole("status").filter({ hasText: "Order saved" }),
    ).toBeVisible();
    const lane = page.getByRole("region", { name: "Queued work", exact: true });
    await expect(lane.getByRole("article").nth(1)).toHaveAccessibleName(
      "gamma work",
    );
    expect(
      state.items.find((item) => item.feature === "blocked").deliveryState,
    ).toBe("in_progress");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`compact-${viewport.width}.png`),
    });
  });
}
test("real touch grip changes global list priority", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await setup(page);
  const source = page.getByRole("button", {
    name: "Drag beta work within Queued",
    exact: true,
  });
  const a = (await source.boundingBox())!,
    b = (await page
      .getByRole("article", { name: "alpha work", exact: true })
      .boundingBox())!;
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [{ x: a.x + a.width / 2, y: a.y + a.height / 2 }],
  });
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchMove",
    touchPoints: [{ x: b.x + b.width / 2, y: b.y + 8 }],
  });
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0].features).toEqual(["beta", "alpha", "gamma", "blocked"]);
});
test("keyboard order preserves expansion and exact lost-receipt identity", async ({
  page,
}) => {
  const state = await setup(page);
  const card = page.getByRole("article", { name: "beta work", exact: true });
  await card
    .getByLabel("Details and subtasks for beta work (1)", { exact: true })
    .click();
  state.loseReceipt = true;
  await card.getByRole("button", { name: "Move up", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("button", { name: "Retry save", exact: true }),
  ).toBeVisible();
  await expect(card.locator(":scope > details")).toHaveAttribute("open", "");
  await expect(card.locator(":scope > details > summary")).toBeFocused();
  expect(state.writes).toHaveLength(1);
  state.loseReceipt = false;
  await page.getByRole("button", { name: "Retry save", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Order saved" }),
  ).toBeVisible();
  expect(state.writes[1]).toEqual(state.writes[0]);
  await expect(card.locator(":scope > details")).toHaveAttribute("open", "");
  await expect(page.getByRole("article").first()).toHaveAccessibleName(
    "beta work",
  );
});
test("conflict requires a new deliberate move and cross-lane drag does nothing", async ({
  page,
}) => {
  const state = await setup(page);
  const card = page.getByRole("article", { name: "beta work", exact: true });
  await card
    .getByLabel("Details and subtasks for beta work (1)", { exact: true })
    .click();
  state.conflict = true;
  await card.getByRole("button", { name: "Move up", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Backlog changed" }),
  ).toBeVisible();
  expect(state.writes).toHaveLength(1);
  await expect(page.getByRole("article").first()).toHaveAccessibleName(
    "alpha work",
  );
  await card.getByRole("button", { name: "Move up", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(2);
  expect(state.writes[1].requestId).not.toBe(state.writes[0].requestId);
  expect(state.writes[1].revision).toBe(1);
  await expect(
    page.getByRole("status").filter({ hasText: "Order saved" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Kanban", exact: true }).click();
  const source = page.getByRole("button", {
      name: "Drag alpha work within Queued",
      exact: true,
    }),
    target = page.getByRole("article", { name: "Blocked work", exact: true });
  await source.scrollIntoViewIfNeeded();
  const a = (await source.boundingBox())!;
  const b = (await target.boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + 8, { steps: 8 });
  await page.mouse.up();
  expect(state.writes).toHaveLength(2);
});


test("merged Queued lane moves legacy ready and queued work through canonical global rank", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const state = await setup(page);
  const [alpha, beta, gamma, blocked] = state.items;
  beta.deliveryState = beta.deliveryLane = "queued";
  gamma.deliveryState = gamma.deliveryLane = "in_progress";
  const handed = { ...alpha, feature: "handed", title: "Handed over work", deliveryState: "handover", deliveryLane: "handover", revision: 2 };
  state.items = [alpha, gamma, beta, blocked, handed].map((item, index) => ({ ...item, rank: index + 1 }));
  await page.getByRole("button", { name: "Reload saved assessments" }).click();
  await page.getByRole("button", { name: "Kanban", exact: true }).click();
  const queued = page.getByRole("region", { name: "Queued work", exact: true });
  await expect(queued.getByRole("article")).toHaveCount(2);
  await expect(page.getByRole("region", { name: "Ready work", exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Ready to deploy work", exact: true }).getByRole("article")).toHaveCount(1);
  await expect(page.getByRole("region", { name: "In progress work", exact: true }).getByRole("article")).toHaveCount(1);
  const source = queued.getByRole("button", { name: "Drag beta work within Queued" });
  const target = queued.getByRole("article", { name: "alpha work", exact: true });
  const from = (await source.boundingBox())!, to = (await target.boundingBox())!;
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x + to.width / 2, to.y + 8, { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]).toMatchObject({ lane: "all", features: ["beta", "gamma", "alpha", "blocked", "handed"], expectedRevisions: { alpha: 1, beta: 1, gamma: 1, blocked: 1, handed: 2 } });
  await expect(queued.getByRole("article").first()).toHaveAccessibleName("beta work");
  expect(state.items.find(item => item.feature === "alpha").deliveryState).toBe("ready");
  expect(state.items.find(item => item.feature === "beta").deliveryState).toBe("queued");
  expect(state.items.find(item => item.feature === "gamma").rank).toBe(2);
  expect(state.items.find(item => item.feature === "blocked").rank).toBe(4);
  expect(state.items.find(item => item.feature === "handed").rank).toBe(5);
});

test("missing worker and paused queue entries never display as active assignments", async ({ page }) => {
  const state = await setup(page);
  state.items[0] = { ...state.items[0], deliveryState: "in_progress", deliveryLane: "in_progress", worker: null, owner: null };
  state.items[1] = { ...state.items[1], deliveryState: "queued", deliveryLane: "queued", worker: null, owner: null };
  state.items[2] = { ...state.items[2], deliveryState: "in_progress", deliveryLane: "in_progress", worker: "worker-one" };
  await page.getByRole("button", { name: "Reload saved assessments" }).click();
  await page.getByRole("button", { name: "Kanban", exact: true }).click();
  const queued = page.getByRole("region", { name: "Queued work", exact: true });
  await expect(queued.getByRole("article")).toHaveCount(2);
  const unassigned = queued.getByRole("article", { name: "alpha work", exact: true });
  await unassigned.locator(":scope > details > summary").click();
  await expect(unassigned).toContainText("No assigned worker is recorded; this is queued, not active work.");
  const active = page.getByRole("region", { name: "In progress work", exact: true });
  await expect(active.getByRole("article")).toHaveCount(1);
  await active.locator("article > details > summary").click();
  await expect(active).toContainText("live worker activity is not monitored here");
  expect(state.writes).toEqual([]);
  expect(state.items[0].deliveryState).toBe("in_progress");
});
