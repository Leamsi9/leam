import { test, expect, type Page } from "@playwright/test";

// GET/PUT shapes mirror board_order.MoveCard/projection and commitments' router.
// All API traffic is isolated; no owner data or coding/model calls are performed.
const capacity = "10000000-0000-4000-8000-000000000001";
const token = "a".repeat(64);
async function fixture(page: Page) {
  const state = {
    ids: ["a", "b", "c"],
    revision: 0,
    conflict: false,
    loseResponse: false,
    gate: null as Promise<void> | null,
    writes: [] as any[],
    cardWrites: [] as any[],
    statusConflict: false,
    receipts: new Map<string, any>(),
  };
  const cards = [
    { id: "a", title: "Alpha card", stage: "todo" },
    { id: "b", title: "Beta card", stage: "blocked" },
    { id: "c", title: "Gamma card", stage: "todo" },
  ].map((card) => ({
    ...card,
    capacityId: capacity,
    revision: 1,
    kind: "task",
    status: "active",
    owner: "user",
    priority: "normal",
    notes: "Expanded private note",
    subtasks: [],
    startDate: null,
    endDate: null,
    dueDate: null,
    measure: "boolean",
    target: 1,
    timezone: "Europe/London",
    reward: "",
    reminderTime: null,
  }));
  const current = () => ({
    capacityId: capacity,
    revision: state.revision,
    membershipToken: token,
    ids: [...state.ids],
    canReorder: true,
  });
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    let body: any = {
      items: [],
      data: [],
      threads: [],
      messages: [],
      models: [],
      providers: [],
    };
    if (path === "/api/auth/status")
      body = { configured: true, authenticated: true };
    if (path === "/api/agenda")
      body = {
        date: "2026-09-21",
        timezone: "Europe/London",
        commitments: [],
        events: [],
        emails: [],
        nextOffset: null,
        total: {},
        partial: false,
        sources: {
          calendar: { state: "not_connected", accounts: [], snapshots: [] },
          email: {
            state: "not_connected",
            accounts: [],
            classification: { state: "ready", counts: {} },
          },
        },
      };
    if (path === "/api/capacities")
      body = {
        items: [
          { id: capacity, revision: 1, name: "Work", note: "", record: "" },
          {
            id: "empty",
            revision: 1,
            name: "Empty board",
            note: "",
            record: "",
          },
        ],
      };
    if (path === "/api/commitments")
      body = { items: cards, boardOrders: { [capacity]: current() } };
    if (path === "/api/commitments/order") {
      expect(request.method()).toBe("PUT");
      const input = request.postDataJSON();
      state.writes.push(input);
      expect(Object.keys(input).sort()).toEqual([
        "beforeId",
        "capacityId",
        "cardId",
        "lane",
        "membershipToken",
        "requestId",
        "revision",
      ]);
      expect(input.capacityId).toBe(capacity);
      expect(input.membershipToken).toBe(token);
      expect(input.requestId).toMatch(/^[a-f0-9-]{36}$/);
      if (state.gate) {
        const gate = state.gate;
        state.gate = null;
        await gate;
      }
      if (state.receipts.has(input.requestId))
        return route.fulfill({ json: state.receipts.get(input.requestId) });
      if (state.conflict || input.revision !== state.revision)
        return route.fulfill({
          status: 409,
          json: { detail: "Cards or order changed" },
        });
      const next = state.ids.filter((id) => id !== input.cardId);
      let index = next.length;
      if (input.beforeId) index = next.indexOf(input.beforeId);
      else if (input.lane)
        index = next.reduce(
          (last, id, i) =>
            cards.find((c) => c.id === id)!.stage === input.lane ? i + 1 : last,
          0,
        );
      next.splice(index, 0, input.cardId);
      state.ids = next;
      state.revision++;
      body = current();
      state.receipts.set(input.requestId, body);
      if (state.loseResponse) {
        state.loseResponse = false;
        return route.abort("failed");
      }
    } else if (
      path.startsWith("/api/commitments/") &&
      request.method() === "PATCH"
    ) {
      const input = request.postDataJSON(),
        card = cards.find((item) => item.id === path.split("/").at(-1));
      state.cardWrites.push({ path, body: input });
      if (!card || state.statusConflict || input.revision !== card.revision)
        return route.fulfill({
          status: 409,
          json: { detail: "Card changed; reload" },
        });
      Object.assign(card, input, { revision: card.revision + 1 });
      body = card;
    } else if (!["GET", "HEAD"].includes(request.method())) {
      throw new Error(`Unexpected mutation ${request.method()} ${path}`);
    }
    return route.fulfill({ json: body });
  });
  await page.goto("/?view=today#today/boards/2026-09-21");
  await expect(
    page.getByRole("button", { name: "Edit Alpha card", exact: true }),
  ).toBeVisible();
  return state;
}
const cards = (page: Page) =>
  page.locator(".board-card[data-order-card]:visible");
const handle = (page: Page, name: string) =>
  page.getByRole("button", {
    name: `Move ${name} card; use arrow keys up or down`,
    exact: true,
  });
async function ids(page: Page) {
  return cards(page).evaluateAll((nodes) =>
    nodes.map((node) => (node as HTMLElement).dataset.orderCard),
  );
}

for (const [width, height] of [
  [360, 800],
  [844, 390],
  [1440, 1000],
]) {
  test(`compact cards retain 44px targets and one-click editing at ${width}x${height}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height });
    const state = await fixture(page);
    const card = page.locator('[data-order-card="a"]');
    await expect(card.getByText("Expanded private note")).toHaveCount(0);
    const geometry = await card.evaluate((node) => ({
      height: node.getBoundingClientRect().height,
      targets: [...node.querySelectorAll("button")].map((b) => ({
        width: b.getBoundingClientRect().width,
        height: b.getBoundingClientRect().height,
      })),
    }));
    expect(geometry.height).toBeLessThanOrEqual(100);
    for (const target of geometry.targets) {
      expect(target.height).toBeGreaterThanOrEqual(44);
      expect(target.width).toBeGreaterThanOrEqual(44);
    }
    expect(
      await page.evaluate(() => document.body.scrollWidth),
    ).toBeLessThanOrEqual(await page.evaluate(() => window.innerWidth));
    await testInfo.attach("card-geometry", {
      body: JSON.stringify({ width, height, ...geometry }),
      contentType: "application/json",
    });
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: testInfo.outputPath(`compact-${width}.png`),
      fullPage: true,
    });
    await page
      .getByRole("button", {
        name: "Expand details for Alpha card",
        exact: true,
      })
      .click();
    await expect(card.getByText("Expanded private note")).toBeVisible();
    await page
      .getByRole("button", {
        name: "Collapse details for Alpha card",
        exact: true,
      })
      .click();
    await expect(card.getByText("Expanded private note")).toHaveCount(0);
    await page
      .getByRole("button", { name: "Edit Alpha card", exact: true })
      .click();
    await expect(page.getByRole("dialog")).toBeVisible();
    expect(state.writes).toEqual([]);
  });
}

test("keyboard order survives filters, views, collapse and reload without lifecycle mutation", async ({
  page,
}) => {
  const state = await fixture(page);
  await handle(page, "Gamma").press("ArrowUp");
  await expect.poll(() => ids(page)).toEqual(["a", "c", "b"]);
  expect(state.writes[0]).toMatchObject({
    cardId: "c",
    beforeId: "b",
    lane: null,
    revision: 0,
  });
  await page
    .getByRole("button", { name: "Refresh boards", exact: true })
    .click();
  await expect(handle(page, "Gamma")).toBeEnabled();
  await expect.poll(() => ids(page)).toEqual(["a", "c", "b"]);
  await page.getByRole("button", { name: "Kanban", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "To do", exact: true })
      .locator(".board-card"),
  ).toHaveCount(2);
  await page.getByRole("button", { name: "Gantt", exact: true }).click();
  await expect.poll(() => ids(page)).toEqual(["a", "c", "b"]);
  await page.getByRole("button", { name: "List", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Board", exact: true })
    .selectOption("empty");
  await expect(cards(page)).toHaveCount(0);
  await page
    .getByRole("combobox", { name: "Board", exact: true })
    .selectOption(capacity);
  await expect.poll(() => ids(page)).toEqual(["a", "c", "b"]);
  await page.reload();
  await expect.poll(() => ids(page)).toEqual(["a", "c", "b"]);
  expect(state.writes).toHaveLength(1);
});

test("Kanban move stays within lane and retains hidden-card relative order", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.getByRole("button", { name: "Kanban", exact: true }).click();
  await handle(page, "Gamma").press("ArrowUp");
  await expect.poll(() => state.ids).toEqual(["c", "a", "b"]);
  expect(state.writes[0]).toMatchObject({
    cardId: "c",
    beforeId: "a",
    lane: "todo",
  });
  await expect(handle(page, "Beta")).toBeDisabled();
  await page
    .getByRole("button", { name: "Expand details for Gamma card", exact: true })
    .click();
  await page
    .locator('[data-order-card="c"]')
    .getByRole("button", { name: "Move later", exact: true })
    .click();
  await expect.poll(() => state.ids).toEqual(["a", "c", "b"]);
});

test("mouse drag commits once at the target and never expands cards", async ({
  page,
}) => {
  const state = await fixture(page);
  await handle(page, "Gamma").scrollIntoViewIfNeeded();
  const from = (await handle(page, "Gamma").boundingBox())!,
    to = (await page.locator('[data-order-card="a"]').boundingBox())!;
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x + 20, to.y + 8, { steps: 5 });
  expect(state.writes).toHaveLength(0);
  await page.mouse.up();
  await expect.poll(() => ids(page)).toEqual(["c", "a", "b"]);
  expect(state.writes).toHaveLength(1);
  await expect(page.getByText("Expanded private note")).toHaveCount(0);
});

test("real touch drag on phone orders cards", async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    baseURL: test.info().project.use.baseURL as string,
    hasTouch: true,
    isMobile: true,
    serviceWorkers: "block",
  });
  const page = await context.newPage();
  const state = await fixture(page);
  await handle(page, "Gamma").scrollIntoViewIfNeeded();
  const from = (await handle(page, "Gamma").boundingBox())!,
    to = (await page.locator('[data-order-card="a"]').boundingBox())!;
  const session = await context.newCDPSession(page);
  await session.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [{ x: from.x + 22, y: from.y + 22 }],
  });
  await session.send("Input.dispatchTouchEvent", {
    type: "touchMove",
    touchPoints: [{ x: to.x + 20, y: to.y + 8 }],
  });
  await session.send("Input.dispatchTouchEvent", {
    type: "touchEnd",
    touchPoints: [],
  });
  await expect.poll(() => state.ids).toEqual(["c", "a", "b"]);
  expect(state.writes).toHaveLength(1);
  await context.close();
});

test("conflict requires refresh and does not silently retry", async ({
  page,
}) => {
  const state = await fixture(page);
  state.conflict = true;
  await handle(page, "Gamma").press("ArrowUp");
  await expect(
    page.getByText(
      "Cards or order changed. Refresh boards before moving again.",
    ),
  ).toBeVisible();
  await expect(handle(page, "Gamma")).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Retry same move", exact: true }),
  ).toHaveCount(0);
  expect(state.writes).toHaveLength(1);
  state.conflict = false;
  await page
    .getByRole("button", { name: "Refresh boards", exact: true })
    .click();
  await expect(handle(page, "Gamma")).toBeEnabled();
  expect(state.writes).toHaveLength(1);
  await handle(page, "Gamma").press("ArrowUp");
  await expect.poll(() => ids(page)).toEqual(["a", "c", "b"]);
});

test("lost committed response offers explicit exact-ID retry without duplicate mutation", async ({
  page,
}) => {
  const state = await fixture(page);
  state.loseResponse = true;
  await handle(page, "Gamma").press("ArrowUp");
  const retry = page.getByRole("button", {
    name: "Retry same move",
    exact: true,
  });
  await expect(retry).toBeVisible();
  expect(state.writes).toHaveLength(1);
  await expect(handle(page, "Alpha")).toBeDisabled();
  await retry.click();
  await expect.poll(() => ids(page)).toEqual(["a", "c", "b"]);
  expect(state.writes).toHaveLength(2);
  expect(state.writes[1]).toEqual(state.writes[0]);
  expect(state.revision).toBe(1);
});

test("pending move disables duplicate controls and refresh", async ({
  page,
}) => {
  const state = await fixture(page);
  let release!: () => void;
  state.gate = new Promise<void>((resolve) => (release = resolve));
  await handle(page, "Gamma").press("ArrowUp");
  await expect.poll(() => state.writes.length).toBe(1);
  await expect(handle(page, "Alpha")).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Refresh boards", exact: true }),
  ).toBeDisabled();
  release();
  await expect.poll(() => ids(page)).toEqual(["a", "c", "b"]);
  expect(state.writes).toHaveLength(1);
});

test("a manual move preserves collapsed groups and detail state", async ({
  page,
}) => {
  const state = await fixture(page);
  const work = page.getByRole("group", { name: "Work capacity", exact: true });
  const empty = page.getByRole("group", {
    name: "Empty board capacity",
    exact: true,
  });
  await expect(empty).not.toHaveAttribute("open", "");
  await page
    .getByRole("button", { name: "Expand details for Alpha card", exact: true })
    .click();
  await handle(page, "Gamma").press("ArrowUp");
  await expect.poll(() => state.ids).toEqual(["a", "c", "b"]);
  await expect(empty).not.toHaveAttribute("open", "");
  await expect(
    page.getByRole("button", {
      name: "Collapse details for Alpha card",
      exact: true,
    }),
  ).toBeVisible();
  await work.locator(":scope > summary").click();
  await expect(cards(page)).toHaveCount(0);
  await work.locator(":scope > summary").click();
  await expect.poll(() => ids(page)).toEqual(["a", "c", "b"]);
  expect(state.writes).toHaveLength(1);
});

test("status selection persists immediately and Kanban/list share the receipt", async ({
  page,
}) => {
  const state = await fixture(page);
  await page
    .getByRole("button", { name: "Edit Alpha card", exact: true })
    .click();
  const editor = page.getByRole("dialog", { name: "Card details" });
  await editor
    .getByLabel("Card title", { exact: true })
    .fill("Unsaved title draft");
  await editor
    .getByRole("combobox", { name: "Card status", exact: true })
    .selectOption("completed");
  await expect(editor.getByText(/Status saved: Completed/)).toBeVisible();
  await expect(editor.getByLabel("Card title", { exact: true })).toHaveValue(
    "Unsaved title draft",
  );
  expect(state.cardWrites).toEqual([
    { path: "/api/commitments/a", body: { revision: 1, status: "completed" } },
  ]);
  await editor.getByRole("button", { name: /Close/ }).click();
  await page.getByRole("button", { name: "Kanban", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "Completed", exact: true })
      .getByRole("button", { name: "Edit Alpha card", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "List", exact: true }).click();
  await expect(page.locator('[data-order-card="a"]')).toContainText(
    "Completed",
  );
  await page
    .getByRole("button", { name: "Edit Alpha card", exact: true })
    .click();
  await expect(editor.getByLabel("Card title", { exact: true })).toHaveValue(
    "Unsaved title draft",
  );
});

test("a rejected immediate status does not display unsaved completion", async ({
  page,
}) => {
  const state = await fixture(page);
  state.statusConflict = true;
  await page
    .getByRole("button", { name: "Edit Alpha card", exact: true })
    .click();
  const editor = page.getByRole("dialog", { name: "Card details" });
  await editor
    .getByRole("combobox", { name: "Card status", exact: true })
    .selectOption("completed");
  await expect(editor.getByRole("alert")).toContainText("Card changed");
  await expect(
    editor.getByRole("combobox", { name: "Card status", exact: true }),
  ).toHaveValue("todo");
  expect(state.cardWrites).toHaveLength(1);
  expect(state.writes).toHaveLength(0);
});

test("desktop card drag moves into an empty column and saves canonical status before ordering", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const state = await fixture(page);
  await page.getByRole("button", { name: "Kanban", exact: true }).click();
  const source = page.locator('[data-order-card="b"]'),
    destination = page.locator('[data-board-lane="in_progress"]');
  await expect(handle(page, "Beta")).toBeEnabled();
  await source.dragTo(destination);
  await expect(destination.locator('[data-order-card="b"]')).toBeVisible();
  expect(state.cardWrites).toEqual([
    {
      path: "/api/commitments/b",
      body: { revision: 1, status: "active", stage: "in_progress" },
    },
  ]);
  await expect
    .poll(() => state.writes[0])
    .toMatchObject({
      cardId: "b",
      lane: "in_progress",
      beforeId: null,
    });
});

test("touch grip moves the only card out of its column", async ({
  browser,
}) => {
  const context = await browser.newContext({
    viewport: { width: 844, height: 390 },
    hasTouch: true,
    isMobile: true,
  });
  const page = await context.newPage();
  try {
    const state = await fixture(page);
    await page.getByRole("button", { name: "Kanban", exact: true }).click();
    const grip = handle(page, "Beta"),
      target = page.locator('[data-board-lane="in_progress"]');
    await grip.scrollIntoViewIfNeeded();
    const from = await grip.boundingBox(),
      box = await target.boundingBox();
    expect(from).not.toBeNull();
    expect(box).not.toBeNull();
    const cdp = await context.newCDPSession(page);
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x: from!.x + 22, y: from!.y + 22 }],
    });
    const board = await page.locator(".board-kanban").boundingBox();
    expect(board).not.toBeNull();
    // On a narrow screen the previous lane starts offscreen. Exercise the real
    // edge auto-scroll instead of sending an impossible negative touch coordinate.
    let drop = box!;
    for (let step = 0; drop.x + 30 < board!.x && step < 40; step++) {
      await cdp.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [{ x: board!.x + 8 + (step % 2), y: from!.y + 22 }],
      });
      drop = (await target.boundingBox())!;
    }
    expect(drop.x + 30).toBeGreaterThanOrEqual(board!.x);
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [{ x: drop.x + drop.width / 2, y: drop.y + 80 }],
    });
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchEnd",
      touchPoints: [],
    });
    await expect(target.locator('[data-order-card="b"]')).toBeVisible();
    await expect(page.locator(".board-kanban")).not.toHaveCSS(
      "scroll-snap-type",
      "none",
    );
    expect(state.cardWrites[0].body).toEqual({
      revision: 1,
      status: "active",
      stage: "in_progress",
    });
  } finally {
    await context.close();
  }
});

test("cancelling a touch drag restores normal board scrolling without a write", async ({
  browser,
}) => {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
  });
  const page = await context.newPage();
  try {
    const state = await fixture(page);
    await page.getByRole("button", { name: "Kanban", exact: true }).click();
    const grip = handle(page, "Beta");
    await grip.scrollIntoViewIfNeeded();
    const from = (await grip.boundingBox())!;
    const board = page.locator(".board-kanban");
    const snap = await board.evaluate(
      (node) => getComputedStyle(node).scrollSnapType,
    );
    const cdp = await context.newCDPSession(page);
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x: from.x + 22, y: from.y + 22 }],
    });
    await expect(board).toHaveCSS("scroll-snap-type", "none");
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchCancel",
      touchPoints: [],
    });
    await expect(board).toHaveCSS("scroll-snap-type", snap);
    expect(state.cardWrites).toHaveLength(0);
    expect(state.writes).toHaveLength(0);
  } finally {
    await context.close();
  }
});

test("failed ordering after a successful status move reports both facts", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const state = await fixture(page);
  state.conflict = true;
  await page.getByRole("button", { name: "Kanban", exact: true }).click();
  await page
    .locator('[data-order-card="b"]')
    .dragTo(page.locator('[data-board-lane="in_progress"]'));
  await expect(
    page.getByText(/Status saved: In progress\. Cards or order changed/),
  ).toBeVisible();
  await expect(
    page.locator('[data-board-lane="in_progress"] [data-order-card="b"]'),
  ).toBeVisible();
  expect(state.cardWrites).toHaveLength(1);
  expect(state.writes).toHaveLength(1);
});
