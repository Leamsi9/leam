import { test, expect, type Page } from "@playwright/test";

// Shapes follow commitments.py Commitment/Capacity/SubtaskChange and the
// canonical GET/POST/PATCH routes; fixtures capture every mutation argument.
const boardId = "10000000-0000-4000-8000-000000000001";
const cardId = "20000000-0000-4000-8000-000000000001";
const subId = "30000000-0000-4000-8000-000000000001";
async function fixture(page: Page, coherent = false) {
  const state = {
    loseCreateResponse: false,
    reads: [] as string[],
    logs: {} as Record<string, any>,
    holdAgenda: null as Promise<void> | null,
    heldAgendaCount: 0,
    createGate: null as Promise<void> | null,
    conflict: false,
    readsFail: false,
    writes: [] as { path: string; method: string; body: any }[],
    capacities: [
      {
        id: boardId,
        revision: 2,
        name: "EarthShift Global",
        note: "Shared capacity",
        record: "",
      },
    ],
    cards: [
      {
        id: cardId,
        revision: 7,
        title: "Prepare invoice",
        kind: "task",
        status: "active",
        stage: "todo",
        owner: "user",
        priority: "high",
        capacityId: boardId,
        notes: "Keep this note",
        startDate: "2026-09-21",
        endDate: "2026-09-23",
        dueDate: "2026-09-25",
        measure: "boolean",
        target: 1,
        timezone: "Europe/London",
        reminderTime: null,
        reward: "",
        subtasks: [
          {
            id: subId,
            title: "Check details",
            owner: "user",
            status: "todo",
            notes: "",
            startDate: null,
            endDate: null,
            dueDate: null,
            children: [],
          },
        ],
      },
      {
        id: "20000000-0000-4000-8000-000000000002",
        revision: 3,
        title: "Subscription billing",
        kind: "task",
        status: "paused",
        stage: "blocked",
        owner: "leam",
        priority: "normal",
        capacityId: null,
        notes: "",
        startDate: null,
        endDate: null,
        dueDate: null,
        subtasks: [],
      },
      {
        id: "20000000-0000-4000-8000-000000000003",
        revision: 1,
        title: "Deadline only",
        kind: "task",
        status: "active",
        stage: "in_progress",
        owner: "user",
        priority: "low",
        capacityId: boardId,
        notes: "",
        startDate: null,
        endDate: null,
        dueDate: "2026-09-27",
        subtasks: [],
      },
    ] as any[],
  };
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname,
      method = request.method();
    if (method === "GET") state.reads.push(path);
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
        commitments: coherent
          ? state.cards
              .filter(
                (card) =>
                  card.status === "active" ||
                  (card.kind === "task" && state.logs[card.id]),
              )
              .map((card) => ({
                ...card,
                date: new URL(request.url()).searchParams.get("date"),
                key: `commitment:${card.id}`,
                log: state.logs[card.id] || {
                  value: 0,
                  done: false,
                  revision: 0,
                },
                triage: { disposition: "focus", revision: 1 },
              }))
          : [],
        events: [],
        emails: [],
        nextOffset: null,
        partial: false,
        total: {},
        sources: {
          calendar: { state: "not_connected", accounts: [], snapshots: [] },
          email: {
            state: "not_connected",
            accounts: [],
            classification: { state: "ready", counts: {} },
          },
        },
      };
    if (path === "/api/agenda") {
      body.date = new URL(request.url()).searchParams.get("date");
      if (state.holdAgenda) {
        const gate = state.holdAgenda;
        state.holdAgenda = null;
        state.heldAgendaCount++;
        body = structuredClone(body);
        await gate;
      }
    }
    if (path === "/api/capacities") body = { items: state.capacities };
    if (path === "/api/commitments") body = { items: state.cards };
    if (state.readsFail && path === "/api/commitments" && method === "GET")
      return route.fulfill({
        status: 503,
        json: { detail: "Cards temporarily unavailable" },
      });
    if (
      method !== "GET" &&
      (path.startsWith("/api/commitments") ||
        path.startsWith("/api/capacities"))
    ) {
      if (
        state.createGate &&
        method === "POST" &&
        ["/api/capacities", "/api/commitments"].includes(path)
      )
        await state.createGate;
      const input = request.postDataJSON();
      state.writes.push({ path, method, body: input });
      if (state.conflict)
        return route.fulfill({
          status: 409,
          json: { detail: "Changed on another device. Reload before saving." },
        });
      if (path === "/api/capacities") {
        body = { ...input, id: "new-board", revision: 1 };
        state.capacities.push(body);
      } else if (path === "/api/commitments") {
        body = {
          ...input,
          id: "new-card",
          revision: 1,
          kind: "task",
          subtasks: [],
        };
        state.cards.push(body);
      } else {
        const id = path.split("/")[3],
          index = state.cards.findIndex((card) => card.id === id),
          previous = state.cards[index];
        if (path.includes("/progress/")) {
          const log = state.logs[id] || { value: 0, done: false, revision: 0 };
          if (
            input.revision !== log.revision ||
            input.commitmentRevision !== previous.revision
          )
            return route.fulfill({
              status: 409,
              json: { detail: "Revision mismatch" },
            });
          state.logs[id] = {
            value: 1,
            done: !log.done,
            revision: log.revision + 1,
          };
          if (previous.kind === "task") {
            previous.status = state.logs[id].done ? "completed" : "active";
            previous.revision++;
          }
          return route.fulfill({
            json: { commitment: previous, log: state.logs[id] },
          });
        }
        if (input.revision !== previous.revision)
          return route.fulfill({
            status: 409,
            json: { detail: "Revision mismatch" },
          });
        body = structuredClone(previous);
        if (path.endsWith("/subtasks")) {
          const find = (nodes: any[]): any =>
            nodes.find((n) => n.id === input.parentId) ||
            nodes.map((n) => find(n.children)).find(Boolean);
          if (input.action === "add") {
            const { action, revision, parentId, subtaskId, ...fields } = input;
            (parentId ? find(body.subtasks).children : body.subtasks).push({
              ...fields,
              id: subtaskId,
              children: [],
            });
          } else {
            const change = (nodes: any[]): any[] =>
              nodes.flatMap((n) =>
                n.id === input.subtaskId
                  ? input.action === "remove"
                    ? []
                    : [
                        {
                          ...n,
                          ...Object.fromEntries(
                            Object.entries(input).filter(
                              ([k]) =>
                                !["action", "revision", "subtaskId"].includes(
                                  k,
                                ),
                            ),
                          ),
                        },
                      ]
                  : [{ ...n, children: change(n.children) }],
              );
            body.subtasks = change(body.subtasks);
          }
        } else Object.assign(body, input);
        body.revision = previous.revision + 1;
        state.cards[index] = body;
      }
    }
    if (
      state.loseCreateResponse &&
      method === "POST" &&
      ["/api/capacities", "/api/commitments"].includes(path)
    )
      return route.abort("failed");
    return route.fulfill({ json: body });
  });
  await page.goto("/?view=today#today/boards/2026-09-21");
  await expect(
    page.getByRole("heading", { name: "Your boards" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Edit Prepare invoice", exact: true }),
  ).toBeVisible();
  return state;
}

test("canonical board filters and Kanban distinguish paused from blocked without writes", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.getByRole("button", { name: "Kanban", exact: true }).click();
  await expect(
    page
      .getByRole("group", { name: "Unassigned capacity", exact: true })
      .getByRole("region", { name: "Paused", exact: true })
      .getByText("Subscription billing", { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("group", { name: "Unassigned capacity", exact: true })
      .getByRole("region", { name: "Blocked", exact: true }),
  ).not.toContainText("Subscription billing");
  await page
    .getByRole("combobox", { name: "Board", exact: true })
    .selectOption(boardId);
  await expect(
    page.getByText("Subscription billing", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Prepare invoice", { exact: true }),
  ).toBeVisible();
  expect(state.writes).toEqual([]);
});

test("card metadata uses current revision and changed fields; ownership never dispatches", async ({
  page,
}) => {
  const state = await fixture(page);
  await page
    .getByRole("button", { name: "Edit Prepare invoice", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Card details" });
  await dialog
    .getByRole("combobox", { name: "Owner", exact: true })
    .selectOption("leam");
  await dialog
    .getByRole("combobox", { name: "Card status", exact: true })
    .selectOption("blocked");
  await dialog.getByRole("button", { name: "Save card", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(state.writes).toEqual([
    {
      path: `/api/commitments/${cardId}`,
      method: "PATCH",
      body: { revision: 7, status: "active", stage: "blocked" },
    },
    {
      path: `/api/commitments/${cardId}`,
      method: "PATCH",
      body: { revision: 8, owner: "leam" },
    },
  ]);
  await page
    .getByRole("button", { name: "Edit Prepare invoice", exact: true })
    .click();
  await dialog
    .getByRole("combobox", { name: "Card status", exact: true })
    .selectOption("completed");
  await dialog.getByRole("button", { name: "Save card", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(state.writes[2].body).toEqual({ revision: 9, status: "completed" });
  expect(state.cards[0].notes).toBe("Keep this note");
});

test("subtask add child, edit, and confirmed removal reuse exact card revisions", async ({
  page,
}) => {
  const state = await fixture(page);
  await page
    .getByRole("button", { name: "Edit Prepare invoice", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Card details" });
  await dialog.getByRole("button", { name: /^Subtasks/ }).click();
  await dialog
    .getByRole("button", { name: "Add child to Check details", exact: true })
    .click();
  const form = dialog.getByRole("form", { name: "Subtask editor" });
  await form.getByLabel("Subtask title").fill("Check VAT");
  await form
    .getByRole("combobox", { name: "Subtask owner", exact: true })
    .selectOption("leam");
  await form.getByRole("button", { name: "Save subtask", exact: true }).click();
  await expect(form).toHaveCount(0);
  const created = state.writes[0].body;
  expect(created).toMatchObject({
    revision: 7,
    action: "add",
    parentId: subId,
    title: "Check VAT",
    owner: "leam",
    status: "todo",
    startDate: null,
    endDate: null,
    dueDate: null,
  });
  expect(created.subtaskId).toMatch(/^[0-9a-f-]{36}$/);
  await dialog
    .getByRole("button", { name: "Edit subtask Check VAT", exact: true })
    .click();
  await form
    .getByRole("combobox", { name: "Subtask status", exact: true })
    .selectOption("completed");
  await form.getByRole("button", { name: "Save subtask", exact: true }).click();
  await expect(form).toHaveCount(0);
  expect(state.writes[1].body).toMatchObject({
    revision: 8,
    action: "edit",
    subtaskId: created.subtaskId,
    status: "completed",
  });
  expect(state.writes[1].body).not.toHaveProperty("parentId");
  await dialog
    .getByRole("button", { name: "Remove subtask Check details", exact: true })
    .click();
  expect(state.writes).toHaveLength(2);
  await dialog.getByRole("button", { name: "Confirm remove subtask" }).click();
  await expect(dialog.getByText("Check VAT", { exact: true })).toHaveCount(0);
  expect(state.writes[2].body).toEqual({
    revision: 9,
    action: "remove",
    subtaskId: subId,
  });
  expect(state.cards[0].status).toBe("active");
});

test("revision conflicts retain draft until explicit reload and never silently retry", async ({
  page,
}) => {
  const state = await fixture(page);
  await page
    .getByRole("button", { name: "Edit Prepare invoice", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Card details" });
  await dialog.getByLabel("Card title").fill("My revised invoice");
  state.cards[0] = {
    ...state.cards[0],
    revision: 9,
    title: "Other device invoice",
  };
  state.conflict = true;
  await dialog.getByRole("button", { name: "Save card", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText(
    "Changed on another device",
  );
  await expect(dialog.getByLabel("Card title")).toHaveValue(
    "My revised invoice",
  );
  await expect(
    dialog.getByRole("button", { name: "Save card", exact: true }),
  ).toBeDisabled();
  expect(state.writes).toHaveLength(1);
  state.conflict = false;
  await dialog
    .getByRole("button", { name: "Load latest and replace draft" })
    .click();
  await expect(dialog.getByLabel("Card title")).toHaveValue(
    "Other device invoice",
  );
  await dialog
    .getByRole("combobox", { name: "Card status", exact: true })
    .selectOption("in_progress");
  await dialog.getByRole("button", { name: "Save card", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(state.writes[1].body).toEqual({
    revision: 9,
    status: "active",
    stage: "in_progress",
  });
});

test("Gantt shows only explicit spans and deadlines; undated cards remain unscheduled", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.getByRole("button", { name: "Gantt", exact: true }).click();
  for (const name of ["EarthShift Global capacity", "Unassigned capacity"])
    await expect(
      page
        .getByRole("group", { name, exact: true })
        .getByRole("region", { name: "Gantt date timeline" }),
    ).toBeVisible();
  await expect(page.locator(".board-timeline-bar")).toHaveCount(1);
  const deadline = page
    .locator(".board-timeline-row")
    .filter({ hasText: "Deadline only" });
  await expect(deadline.locator(".board-timeline-bar")).toHaveCount(0);
  await expect(deadline).toContainText("Due date: 2026-09-27");
  await expect(
    page.getByRole("heading", { name: "Unscheduled (1)" }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("group", { name: "Unassigned capacity", exact: true })
      .locator(".board-timeline .board-list"),
  ).toContainText("Subscription billing");
  expect(state.writes).toEqual([]);
});

for (const width of [360, 1280])
  test(`boards and card details fit ${width}px with touch controls and retained drafts`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 800 });
    await fixture(page);
    await page.getByRole("button", { name: "Kanban", exact: true }).click();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await page.screenshot({
      path: info.outputPath(`kanban-${width}.png`),
      animations: "disabled",
    });
    await page
      .getByRole("button", { name: "Edit Prepare invoice", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "Card details" });
    await dialog.getByLabel("Card title").fill("Retained card draft");
    const rect = await dialog.boundingBox();
    expect(rect!.x).toBeGreaterThanOrEqual(0);
    expect(rect!.x + rect!.width).toBeLessThanOrEqual(width);
    expect(
      await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth),
    ).toBeTruthy();
    await page.screenshot({
      path: info.outputPath(`card-dialog-${width}.png`),
      animations: "disabled",
    });
    await dialog.getByRole("button", { name: "Close Card details" }).click();
    await page
      .getByRole("button", { name: "Edit Prepare invoice", exact: true })
      .click();
    await expect(dialog.getByLabel("Card title")).toHaveValue(
      "Retained card draft",
    );
  });

test("create board and card through canonical operations; failed refresh keeps saved cards", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.getByRole("button", { name: "New board", exact: true }).click();
  await page.getByLabel("Board name").fill("Personal projects");
  await page.getByRole("button", { name: "Create board", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.getByRole("combobox", { name: "Board", exact: true }),
  ).toHaveValue("new-board");
  await expect(page.getByText("No cards in this board yet.")).toBeVisible();
  await page.getByRole("button", { name: "New card", exact: true }).click();
  await page.getByLabel("Card title").fill("Make a sketch");
  await page.getByRole("button", { name: "Save card", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(state.writes[1]).toMatchObject({
    path: "/api/commitments",
    method: "POST",
    body: {
      title: "Make a sketch",
      capacityId: "new-board",
      owner: "user",
      status: "active",
      stage: "todo",
      startDate: null,
      endDate: null,
      dueDate: null,
    },
  });
  state.readsFail = true;
  await page.getByRole("button", { name: "Refresh boards" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Cards temporarily unavailable",
  );
  await expect(
    page.getByRole("button", { name: "Edit Make a sketch" }),
  ).toBeVisible();
});

for (const kind of ["board", "card"] as const)
  test(`lost ${kind} creation response blocks duplicate submission and preserves unknown draft`, async ({
    page,
  }) => {
    const state = await fixture(page);
    state.loseCreateResponse = true;
    await page
      .getByRole("button", {
        name: kind === "board" ? "New board" : "New card",
        exact: true,
      })
      .click();
    const dialog = page.getByRole("dialog", {
      name: kind === "board" ? "New board" : "New card",
    });
    const label = kind === "board" ? "Board name" : "Card title";
    const save = kind === "board" ? "Create board" : "Save card";
    await dialog.getByLabel(label).fill("Saved despite lost response");
    await dialog.getByRole("button", { name: save, exact: true }).click();
    await expect(dialog.getByRole("status")).toContainText(
      "Creation outcome unknown",
    );
    await expect(
      dialog.getByRole("button", { name: save, exact: true }),
    ).toBeDisabled();
    await expect(dialog.getByLabel(label)).toHaveValue(
      "Saved despite lost response",
    );
    expect(state.writes).toHaveLength(1);
    await dialog
      .getByRole("button", { name: "Inspect saved boards and cards" })
      .click();
    await expect(dialog).toHaveCount(0);
    if (kind === "board")
      await expect(
        page
          .getByRole("combobox", { name: "Board", exact: true })
          .locator("option")
          .filter({ hasText: "Saved despite lost response" }),
      ).toHaveCount(1);
    else
      await expect(
        page.getByRole("button", {
          name: "Edit Saved despite lost response",
          exact: true,
        }),
      ).toBeVisible();
    await page
      .getByRole("button", {
        name: kind === "board" ? "New board" : "New card",
        exact: true,
      })
      .click();
    await expect(dialog.getByRole("status")).toContainText(
      "Creation outcome unknown",
    );
    await expect(
      dialog.getByRole("button", { name: save, exact: true }),
    ).toBeDisabled();
    expect(state.writes).toHaveLength(1);
  });

test("pending board creation freezes the submitted draft", async ({ page }) => {
  const state = await fixture(page);
  let release!: () => void;
  state.createGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.getByRole("button", { name: "New board", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "New board" });
  await dialog.getByLabel("Board name").fill("Submitted board");
  await dialog.getByLabel("Board note").fill("Submitted note");
  await dialog
    .getByRole("button", { name: "Create board", exact: true })
    .click();
  await expect(dialog.getByLabel("Board name")).toBeDisabled();
  await expect(dialog.getByLabel("Board note")).toBeDisabled();
  await expect(
    dialog.getByRole("button", { name: "Close New board" }),
  ).toBeDisabled();
  release();
  await expect(dialog).toHaveCount(0);
  expect(state.writes[0].body).toEqual({
    name: "Submitted board",
    note: "Submitted note",
    record: "",
  });
});

for (const kind of ["board", "card", "card-subtasks"] as const)
  test(`late ${kind} creation receipt cannot close or overwrite a replacement draft`, async ({
    page,
  }) => {
    const state = await fixture(page);
    let release!: () => void;
    state.createGate = new Promise<void>((resolve) => {
      release = resolve;
    });
    // Create a same-document history entry so Back exercises the actual Today
    // page lifecycle while the HTTP response remains in flight.
    await page.getByRole("link", { name: "Overview", exact: true }).click();
    await page.getByRole("link", { name: "Boards", exact: true }).click();
    const open = kind === "board" ? "New board" : "New card";
    const label = kind === "board" ? "Board name" : "Card title";
    await page.getByRole("button", { name: open, exact: true }).click();
    let dialog = page.getByRole("dialog", { name: open });
    await dialog.getByLabel(label).fill("Earlier request");
    await dialog
      .getByRole("button", {
        name:
          kind === "board"
            ? "Create board"
            : kind === "card-subtasks"
              ? "Save and add subtasks"
              : "Save card",
        exact: true,
      })
      .click();
    await expect(dialog.getByLabel(label)).toBeDisabled();
    await page.goBack();
    await expect(dialog).toHaveCount(0);
    await page.getByRole("link", { name: "Boards", exact: true }).click();
    await expect(dialog).toBeVisible();
    await dialog
      .getByRole("button", { name: "Discard creation draft" })
      .click();
    await page.getByRole("button", { name: open, exact: true }).click();
    await dialog.getByLabel(label).fill("Replacement draft");
    const completed = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response
          .url()
          .endsWith(kind === "board" ? "/api/capacities" : "/api/commitments"),
    );
    release();
    await completed;
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel(label)).toHaveValue("Replacement draft");
    await dialog.getByRole("button", { name: `Close ${open}` }).click();
    await page.getByRole("button", { name: open, exact: true }).click();
    await expect(dialog.getByLabel(label)).toHaveValue("Replacement draft");
    expect(state.writes).toHaveLength(1);
  });

test("semantic summary counts filtered card lifecycle without treating habit logs as completion", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const state = await fixture(page);
  state.cards[2].kind = "habit";
  state.cards[2].log = { done: true, value: 1, revision: 3 };
  await page.getByRole("button", { name: "Refresh boards" }).click();
  await page.getByText("Board summary · 3 cards", { exact: true }).click();
  const table = page.getByRole("table", {
    name: "Card counts by status and owner",
  });
  await expect(
    table.getByRole("row", { name: "Total cards 2 1 3", exact: true }),
  ).toBeVisible();
  await expect(
    table.getByRole("row", { name: "Completed 0 0 0", exact: true }),
  ).toBeVisible();
  await expect(
    table.getByRole("row", { name: "In progress 1 0 1", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Board", exact: true })
    .selectOption(boardId);
  await expect(
    table.getByRole("row", { name: "Total cards 2 0 2", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".board-summary-chart > span")).toHaveCount(2);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await table.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("summary-360.png"),
    animations: "disabled",
  });
  expect(state.writes).toEqual([]);
});

test("semantic board badges, columns and date bars have labelled consistent tones and readable contrast", async ({
  page,
}, info) => {
  const state = await fixture(page);
  state.cards.push({
    ...state.cards[0],
    id: "blocked-card",
    title: "Blocked example",
    stage: "blocked",
    owner: "leam",
    subtasks: [],
  });
  state.cards.push({
    ...state.cards[0],
    id: "complete-card",
    title: "Completed example",
    status: "completed",
    subtasks: [],
  });
  await page.getByRole("button", { name: "Refresh boards" }).click();
  const blocked = page.locator(".board-card").filter({
    has: page.getByRole("button", {
      name: "Edit Blocked example",
      exact: true,
    }),
  });
  await blocked
    .getByRole("button", {
      name: "Expand details for Blocked example",
      exact: true,
    })
    .click();
  await expect(
    blocked.locator(".semantic-badge").filter({ hasText: /^Blocked$/ }),
  ).toHaveAttribute("data-tone", "danger");
  await expect(
    blocked.locator(".semantic-badge").filter({ hasText: /^Leam$/ }),
  ).toHaveAttribute("data-tone", "violet");
  await expect(
    blocked.locator(".semantic-badge").filter({ hasText: /^High priority$/ }),
  ).toHaveAttribute("data-tone", "warning");
  const expanders = page.getByRole("button", { name: /^Expand details for / });
  while (await expanders.count()) await expanders.first().click();
  const contrast = await page
    .locator(".semantic-badge:visible")
    .evaluateAll((elements) => {
      const lum = (color: string) => {
        const rgb = color
          .match(/[\d.]+/g)!
          .slice(0, 3)
          .map(Number)
          .map((v) => {
            v /= 255;
            return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
          });
        return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
      };
      return elements.map((el) => {
        const css = getComputedStyle(el),
          a = lum(css.color),
          b = lum(css.backgroundColor);
        return {
          text: el.textContent,
          tone: el.getAttribute("data-tone"),
          ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
        };
      });
    });
  for (const row of contrast)
    expect(row.ratio, row.text || row.tone || "badge").toBeGreaterThanOrEqual(
      4.5,
    );
  expect(new Set(contrast.map((row) => row.tone)).size).toBe(7);
  await info.attach("semantic-text-contrast", {
    body: JSON.stringify(contrast),
    contentType: "application/json",
  });
  await page.getByRole("button", { name: "Kanban", exact: true }).click();
  for (const [name, tone] of [
    ["To do", "neutral"],
    ["In progress", "info"],
    ["Blocked", "danger"],
    ["Completed", "success"],
    ["Paused", "warning"],
  ])
    await expect(
      page
        .getByRole("group", { name: "EarthShift Global capacity", exact: true })
        .getByRole("region", { name, exact: true }),
    ).toHaveAttribute("data-tone", tone);
  const graphics = await page
    .locator(".board-column")
    .evaluateAll((elements) => {
      const lum = (color: string) => {
        const rgb = color
          .match(/[\d.]+/g)!
          .slice(0, 3)
          .map(Number)
          .map((v) => {
            v /= 255;
            return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
          });
        return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
      };
      return elements.map((el) => {
        const css = getComputedStyle(el),
          a = lum(css.borderTopColor),
          b = lum(css.backgroundColor);
        return {
          label: el.getAttribute("aria-label"),
          ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
        };
      });
    });
  for (const row of graphics)
    expect(row.ratio, row.label || "column").toBeGreaterThanOrEqual(3);
  await info.attach("semantic-graphic-contrast", {
    body: JSON.stringify(graphics),
    contentType: "application/json",
  });
  await page.screenshot({
    path: info.outputPath("semantic-kanban-desktop.png"),
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Gantt", exact: true }).click();
  await expect(
    page.locator(".board-timeline-row").filter({ hasText: "Blocked example" }),
  ).toHaveAttribute("data-tone", "danger");
  await expect(
    page
      .locator(".board-timeline-row")
      .filter({ hasText: "Completed example" }),
  ).toHaveAttribute("data-tone", "success");
  const barContrast = await page
    .locator(".board-timeline-bar")
    .evaluateAll((elements) => {
      const lum = (color: string) => {
        const rgb = color
          .match(/[\d.]+/g)!
          .slice(0, 3)
          .map(Number)
          .map((v) => {
            v /= 255;
            return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
          });
        return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
      };
      return elements.map((el) => {
        let parent = el.parentElement!;
        while (
          parent.parentElement &&
          getComputedStyle(parent).backgroundColor === "rgba(0, 0, 0, 0)"
        )
          parent = parent.parentElement;
        const a = lum(getComputedStyle(el).backgroundColor),
          b = lum(getComputedStyle(parent).backgroundColor);
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      });
    });
  for (const ratio of barContrast) expect(ratio).toBeGreaterThanOrEqual(3);
  await info.attach("semantic-timeline-contrast", {
    body: JSON.stringify(barContrast),
    contentType: "application/json",
  });
  await page.screenshot({
    path: info.outputPath("semantic-gantt-desktop.png"),
    animations: "disabled",
  });
  expect(state.writes).toEqual([]);
});

test("shared approval badges distinguish pending, execution and receipts; unknown remains neutral", async ({
  page,
}) => {
  await fixture(page);
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET") writes.push(request.url());
  });
  await page.route("**/api/proposals?**", (route) =>
    route.fulfill({
      json: {
        items: ["pending", "executing", "complete", "future_state"].map(
          (state, i) => ({
            id: `p-${i}`,
            state,
            fingerprint: `fp-${i}`,
            unread: i === 0,
            operation: "commitment.create",
            input: { title: `State ${state}` },
            review: { after: { title: `State ${state}` } },
            reason: "Explicit user request",
          }),
        ),
        nextOffset: null,
      },
    }),
  );
  await page.goto("/?view=approvals");
  const pending = page.getByRole("article", {
    name: "Approval: State pending",
  });
  await expect(pending.locator(".semantic-badge")).toHaveAttribute(
    "data-tone",
    "warning",
  );
  await expect(pending.getByLabel("Unread approval")).toHaveClass(
    "approvals-unread-dot",
  );
  await expect(
    page
      .getByRole("article", { name: "Approval: State executing" })
      .locator(".semantic-badge"),
  ).toHaveAttribute("data-tone", "info");
  await page.getByText("Previous decisions · 2", { exact: true }).click();
  await expect(
    page
      .getByRole("article", { name: "Approval: State complete" })
      .locator(".semantic-badge"),
  ).toHaveText("Applied");
  await expect(
    page
      .getByRole("article", { name: "Approval: State complete" })
      .locator(".semantic-badge"),
  ).toHaveAttribute("data-tone", "success");
  await expect(
    page
      .getByRole("article", { name: "Approval: State future_state" })
      .locator(".semantic-badge"),
  ).toHaveAttribute("data-tone", "neutral");
  expect(writes).toEqual([]);
});

const todayPage = (page: Page, name: string) =>
  page
    .getByRole("navigation", { name: "Today pages" })
    .getByRole("link", { name, exact: true })
    .click();

test("board receipts invalidate Plan, while clean page changes keep canonical reads cached", async ({
  page,
}) => {
  const state = await fixture(page, true);
  await todayPage(page, "Overview");
  await expect(
    page.getByRole("button", { name: "Complete Prepare invoice", exact: true }),
  ).toBeVisible();
  const agendaReads = state.reads.filter((p) => p === "/api/agenda").length;
  const cardReads = state.reads.filter((p) => p === "/api/commitments").length;
  await todayPage(page, "Boards");
  await todayPage(page, "Overview");
  expect(state.reads.filter((p) => p === "/api/agenda")).toHaveLength(
    agendaReads,
  );
  expect(state.reads.filter((p) => p === "/api/commitments")).toHaveLength(
    cardReads,
  );
  await todayPage(page, "Boards");
  await page
    .getByRole("button", { name: "Edit Prepare invoice", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Card details" });
  await dialog.getByLabel("Card title").fill("Reviewed invoice");
  await dialog.getByRole("button", { name: "Save card", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await todayPage(page, "Overview");
  await expect(
    page.getByRole("button", {
      name: "Complete Reviewed invoice",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Complete Prepare invoice", exact: true }),
  ).toHaveCount(0);
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0].body).toEqual({
    revision: 7,
    title: "Reviewed invoice",
  });
});

test("new board receipt refreshes Plan capacity labels for moved cards", async ({
  page,
}) => {
  const state = await fixture(page, true);
  await todayPage(page, "Overview");
  await expect(
    page.getByRole("button", { name: "Complete Prepare invoice", exact: true }),
  ).toBeVisible();
  await todayPage(page, "Boards");
  await page.getByRole("button", { name: "New board", exact: true }).click();
  await page.getByLabel("Board name").fill("Health projects");
  await page.getByRole("button", { name: "Create board", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page
    .getByRole("combobox", { name: "Board", exact: true })
    .selectOption("all");
  await page
    .getByRole("button", { name: "Edit Prepare invoice", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Card details" });
  await dialog
    .getByRole("combobox", { name: "Capacity board", exact: true })
    .selectOption("new-board");
  await dialog.getByRole("button", { name: "Save card", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await todayPage(page, "Overview");
  const card = page.locator(".commitment-card").filter({
    has: page.getByRole("heading", { name: "Prepare invoice", exact: true }),
  });
  await expect(card).toContainText("Health projects");
  expect(state.writes).toHaveLength(2);
});

test("late pre-mutation agenda read cannot undo a newer board receipt", async ({
  page,
}) => {
  const state = await fixture(page, true);
  await todayPage(page, "Overview");
  await expect(
    page.getByRole("button", { name: "Complete Prepare invoice", exact: true }),
  ).toBeVisible();
  let release!: () => void;
  state.holdAgenda = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.getByRole("button", { name: "Day options", exact: true }).click();
  await page
    .getByRole("button", { name: "Refresh saved view", exact: true })
    .click();
  await expect.poll(() => state.heldAgendaCount).toBe(1);
  await page.keyboard.press("Escape");
  await todayPage(page, "Boards");
  await page
    .getByRole("button", { name: "Edit Prepare invoice", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Card details" });
  await dialog.getByLabel("Card title").fill("New canonical title");
  await dialog.getByRole("button", { name: "Save card", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await todayPage(page, "Overview");
  await expect(
    page.getByRole("button", {
      name: "Complete New canonical title",
      exact: true,
    }),
  ).toBeVisible();
  release();
  await page.waitForTimeout(100);
  await expect(
    page.getByRole("button", {
      name: "Complete New canonical title",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Complete Prepare invoice", exact: true }),
  ).toHaveCount(0);
});

for (const kind of ["task", "habit"])
  test(`Plan ${kind} progress invalidates Boards without conflating daily completion and lifecycle`, async ({
    page,
  }) => {
    const state = await fixture(page, true);
    state.cards[0].kind = kind;
    await todayPage(page, "Overview");
    // Explicit refresh admits this synthetic canonical kind before exercising the caller.
    await page
      .getByRole("button", { name: "Day options", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Refresh saved view", exact: true })
      .click();
    await page.keyboard.press("Escape");
    await page
      .getByRole("button", { name: "Complete Prepare invoice", exact: true })
      .click();
    await expect(
      page.getByRole("button", {
        name: "Complete Prepare invoice",
        exact: true,
      }),
    ).toHaveCount(0); // Completed work leaves Overview; canonical state remains in Boards/Goals.
    await todayPage(page, "Boards");
    await page
      .getByRole("button", { name: "Edit Prepare invoice", exact: true })
      .click();
    await expect(
      page
        .getByRole("dialog")
        .getByRole("combobox", { name: "Card status", exact: true }),
    ).toHaveValue(kind === "task" ? "completed" : "todo");
    expect(state.cards[0].status).toBe(
      kind === "task" ? "completed" : "active",
    );
    expect(state.writes).toHaveLength(1);
    expect(state.writes[0].body).toEqual({
      operation: "toggle",
      revision: 0,
      commitmentRevision: 7,
    });
  });

test("confirmed Today reconciliation refreshes capacity labels and cached Boards together", async ({
  page,
}) => {
  const state = await fixture(page, true);
  let confirmed = false;
  await page.route("**/api/agenda/chat?**", (route) =>
    route.fulfill({ json: { threadId: "coherence-day-thread" } }),
  );
  await page.route("**/api/agenda/reconciliation?**", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: "coherence-check",
            turnId: "turn",
            revision: confirmed ? 2 : 1,
            state: "done",
            outcome: confirmed ? "changes_confirmed_complete" : "no_action",
            proposalIds: [],
            clarification: null,
            error: null,
            attempts: 1,
            nextRetryAt: null,
          },
        ],
        coverage: {
          enabledAt: 1,
          scope: "New Today exchanges since enabledAt",
          state: "idle",
        },
      },
    }),
  );
  await todayPage(page, "Overview");
  await expect(
    page.getByRole("button", { name: "Complete Prepare invoice", exact: true }),
  ).toBeVisible();
  await todayPage(page, "Boards");
  state.capacities[0] = {
    ...state.capacities[0],
    revision: 3,
    name: "Renamed through chat",
  };
  state.cards[0] = {
    ...state.cards[0],
    revision: 8,
    title: "Chat confirmed title",
  };
  confirmed = true;
  await todayPage(page, "Overview");
  const card = page.locator(".commitment-card").filter({
    has: page.getByRole("heading", {
      name: "Chat confirmed title",
      exact: true,
    }),
  });
  await expect(card).toContainText("Renamed through chat");
  await todayPage(page, "Boards");
  await expect(
    page.getByRole("button", {
      name: "Edit Chat confirmed title",
      exact: true,
    }),
  ).toBeVisible();
  expect(state.writes).toHaveLength(0);
});

test("Board lifecycle completion updates Plan eligibility without inventing daily progress", async ({
  page,
}) => {
  const state = await fixture(page, true);
  await todayPage(page, "Overview");
  await expect(
    page.getByRole("button", { name: "Complete Prepare invoice", exact: true }),
  ).toBeVisible();
  await todayPage(page, "Boards");
  await page
    .getByRole("button", { name: "Edit Prepare invoice", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Card details" });
  await dialog
    .getByRole("combobox", { name: "Card status", exact: true })
    .selectOption("completed");
  await dialog.getByRole("button", { name: "Save card", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await todayPage(page, "Overview");
  await expect(
    page.getByRole("button", { name: "Complete Prepare invoice", exact: true }),
  ).toHaveCount(0);
  expect(state.logs).toEqual({});
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0].body).toEqual({ revision: 7, status: "completed" });
});

test("capacity groups collapse without writes, keep stable accents and obey the global board filter", async ({
  page,
}) => {
  const state = await fixture(page, true);
  const group = page.getByRole("group", {
    name: "EarthShift Global capacity",
    exact: true,
  });
  await expect(group.locator(":scope > summary")).toContainText("2 cards");
  const accent = await group.getAttribute("data-capacity-accent");
  const semantic = await group
    .locator(`[data-order-card="${cardId}"]`)
    .getAttribute("data-tone");
  await group.locator(":scope > summary").click();
  await expect(
    page.getByRole("button", { name: "Edit Prepare invoice", exact: true }),
  ).not.toBeVisible();
  await group.locator(":scope > summary").click();
  await expect(
    page.getByRole("button", { name: "Edit Prepare invoice", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Kanban", exact: true }).click();
  await expect(group).toHaveAttribute("data-capacity-accent", accent!);
  await expect(group.locator(`[data-order-card="${cardId}"]`)).toHaveAttribute(
    "data-tone",
    semantic!,
  );
  await page
    .getByRole("combobox", { name: "Board", exact: true })
    .selectOption("unassigned");
  await expect(group).toHaveCount(0);
  await expect(
    page.getByRole("group", { name: "Unassigned capacity", exact: true }),
  ).toHaveAttribute("open", "");
  await expect(
    page.getByRole("button", {
      name: "Edit Subscription billing",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Board", exact: true })
    .selectOption("all");
  await expect(group).toHaveAttribute("data-capacity-accent", accent!);
  expect(state.writes).toHaveLength(0);
});

test("expanded card opens a direct subtask draft, preserves it on reopen and counts nested completion", async ({
  page,
}) => {
  const state = await fixture(page);
  const card = page.locator(`[data-order-card="${cardId}"]`);
  await expect(
    card.getByRole("button", { name: "Add subtask", exact: true }),
  ).toHaveCount(0);
  await card
    .getByRole("button", { name: "Expand details for Prepare invoice" })
    .click();
  await card.getByRole("button", { name: "Add subtask", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Card details" });
  await dialog
    .getByLabel("Subtask title", { exact: true })
    .fill("Review line items");
  await dialog
    .getByRole("button", { name: "Task details", exact: true })
    .click();
  await expect(
    dialog.getByRole("button", { name: "Save card", exact: true }),
  ).toBeEnabled();
  await dialog.getByRole("button", { name: "Close Card details" }).click();
  await card.getByRole("button", { name: "Add subtask", exact: true }).click();
  await expect(dialog.getByLabel("Subtask title", { exact: true })).toHaveValue(
    "Review line items",
  );
  await dialog
    .getByRole("button", { name: "Save subtask", exact: true })
    .click();
  await expect(
    dialog.getByRole("form", { name: "Subtask editor" }),
  ).toHaveCount(0);
  expect(state.writes[0]).toMatchObject({
    path: `/api/commitments/${cardId}/subtasks`,
    method: "POST",
    body: {
      action: "add",
      revision: 7,
      parentId: null,
      title: "Review line items",
    },
  });
  await dialog
    .getByRole("button", { name: "Add child to Review line items" })
    .click();
  await dialog
    .getByLabel("Subtask title", { exact: true })
    .fill("Confirm amount");
  await dialog
    .getByRole("combobox", { name: "Subtask status", exact: true })
    .selectOption("completed");
  await dialog
    .getByRole("button", { name: "Save subtask", exact: true })
    .click();
  await expect(
    dialog.getByRole("heading", { name: "Subtasks · 1/3 complete" }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Close Card details" }).click();
  await expect(card).toContainText("1/3 subtasks");
  await card
    .getByRole("button", { name: "Edit subtasks · 1/3 complete" })
    .click();
  await expect(
    dialog.getByRole("heading", { name: "Subtasks · 1/3 complete" }),
  ).toBeInViewport();
  expect(state.cards[0].status).toBe("active");
});

test("card details edit canonical measurement timezone reward and reminder with revision", async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const state = await fixture(page);
  await page
    .getByRole("button", { name: "Edit Prepare invoice", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Card details" });
  await dialog
    .getByText("Type, progress and reminders", { exact: true })
    .click();
  await dialog
    .getByRole("combobox", { name: "Card type", exact: true })
    .selectOption("habit");
  await dialog
    .getByRole("combobox", { name: "Progress measure", exact: true })
    .selectOption("minutes");
  await dialog.getByLabel("Daily target", { exact: true }).fill("20.5");
  await dialog.getByLabel("Timezone", { exact: true }).fill("America/New_York");
  await dialog.getByLabel("Daily reminder time", { exact: true }).fill("09:30");
  await dialog.getByLabel("Reward", { exact: true }).fill("Tea break");
  const bounds = await dialog.boundingBox();
  expect(bounds!.width).toBeLessThanOrEqual(360);
  expect(
    await dialog.evaluate(
      (element) => element.scrollWidth <= element.clientWidth + 1,
    ),
  ).toBe(true);
  await page.screenshot({ path: info.outputPath("metadata-360.png") });
  await dialog.getByRole("button", { name: "Save card", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(state.writes).toEqual([
    {
      path: `/api/commitments/${cardId}`,
      method: "PATCH",
      body: {
        revision: 7,
        kind: "habit",
        measure: "minutes",
        target: 20.5,
        timezone: "America/New_York",
        reminderTime: "09:30",
        reward: "Tea break",
      },
    },
  ]);
  await page
    .getByRole("button", { name: "Edit Prepare invoice", exact: true })
    .click();
  await dialog
    .getByText("Type, progress and reminders", { exact: true })
    .click();
  await expect(dialog.getByLabel("Daily target", { exact: true })).toHaveValue(
    "20.5",
  );
  await dialog.getByLabel("Daily reminder time", { exact: true }).fill("");
  await dialog.getByRole("button", { name: "Save card", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(state.writes[1].body).toEqual({ revision: 8, reminderTime: null });
});

test("new card can continue into subtasks only after confirmed creation", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.getByRole("button", { name: "New card", exact: true }).click();
  await page.getByLabel("Card title", { exact: true }).fill("New planned task");
  await page
    .getByRole("button", { name: "Save and add subtasks", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Card details" });
  await expect(
    dialog.getByLabel("Subtask title", { exact: true }),
  ).toBeVisible();
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0].path).toBe("/api/commitments");
  await dialog.getByLabel("Subtask title", { exact: true }).fill("First step");
  await dialog
    .getByRole("button", { name: "Save subtask", exact: true })
    .click();
  await expect(
    dialog.getByRole("form", { name: "Subtask editor" }),
  ).toHaveCount(0);
  expect(state.writes[1]).toMatchObject({
    path: "/api/commitments/new-card/subtasks",
    method: "POST",
    body: { revision: 1, action: "add", title: "First step" },
  });
});

test("uncertain save and add subtasks never invents a new record or dispatches subtasks", async ({
  page,
}) => {
  const state = await fixture(page);
  state.loseCreateResponse = true;
  await page.getByRole("button", { name: "New card", exact: true }).click();
  await page
    .getByLabel("Card title", { exact: true })
    .fill("Unconfirmed creation");
  await page
    .getByRole("button", { name: "Save and add subtasks", exact: true })
    .click();
  await expect(page.getByText(/Creation outcome unknown/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Save and add subtasks", exact: true }),
  ).toBeDisabled();
  await expect(page.getByLabel("Subtask title", { exact: true })).toHaveCount(
    0,
  );
  expect(state.writes).toHaveLength(1);
});

test("expanded canonical board card loads linked Resources only on demand", async ({
  page,
}) => {
  await fixture(page);
  const reads: string[] = [];
  await page.route(
    (url) => url.pathname === "/api/resource-links",
    async (route) => {
      reads.push(route.request().url());
      await route.fulfill({
        json: {
          items: [
            { id: "invoice-guide", title: "Invoice guide", kind: "markdown" },
          ],
        },
      });
    },
  );
  await page.goto("/?view=today#today/boards/2026-09-21");
  await page
    .getByRole("button", {
      name: "Expand details for Prepare invoice",
      exact: true,
    })
    .click();
  expect(reads).toEqual([]);
  const card = page.locator(".board-card").filter({
    has: page.getByRole("button", {
      name: "Collapse details for Prepare invoice",
      exact: true,
    }),
  });
  await card
    .locator("summary")
    .filter({ hasText: /^Resources$/ })
    .click();
  await expect(
    card.getByRole("link", { name: /Invoice guide/ }),
  ).toHaveAttribute("href", "/?artifact=invoice-guide");
  expect(reads).toHaveLength(1);
  const query = new URL(reads[0]).searchParams;
  expect(Object.fromEntries(query)).toEqual({
    targetType: "commitment",
    targetId: cardId,
  });
});

for (const view of ["List", "Kanban", "Gantt"]) {
  test(`board ${view} card and capacity chats reuse canonical item bindings lazily`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await fixture(page);
    const opened: string[] = [];
    await page.route(
      /\/api\/(commitments|capacities)\/[^/]+\/chat$/,
      async (route) => {
        opened.push(new URL(route.request().url()).pathname);
        await route.fulfill({ json: { threadId: "canonical-item-thread" } });
      },
    );
    await page.route("**/api/companion/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      await route.fulfill({
        json: path.endsWith("/system")
          ? {
              available: true,
              activeModel: { model: "test-model", reasoning_effort: "medium" },
            }
          : {
              messages: [],
              items: [],
              runs: [],
              thread_id: "canonical-item-thread",
            },
      });
    });
    await page.getByRole("button", { name: view, exact: true }).click();
    if (view !== "Gantt")
      await page
        .getByRole("button", { name: "Expand details for Prepare invoice" })
        .click();
    expect(opened).toEqual([]);
    await page.getByText("Chat about Prepare invoice", { exact: true }).click();
    const panel = page.getByRole("region", {
      name: "Chat about Prepare invoice",
    });
    await expect(panel.getByLabel("Message Leam")).toBeVisible();
    await expect(
      panel.getByRole("button", { name: "Dictate", exact: true }),
    ).toBeVisible();
    await expect(panel.getByLabel("Attach files")).toBeVisible();
    expect(opened).toEqual([`/api/commitments/${cardId}/chat`]);
    await panel
      .getByLabel("Message Leam")
      .fill("Keep this card discussion draft");
    await page.getByText("Chat about Prepare invoice", { exact: true }).click();
    await page.getByText("Chat about Prepare invoice", { exact: true }).click();
    await expect(panel.getByLabel("Message Leam")).toHaveValue(
      "Keep this card discussion draft",
    );
    await page.getByText("Chat about Prepare invoice", { exact: true }).click();
    await page
      .getByText("Chat about EarthShift Global", { exact: true })
      .click();
    await expect(
      page
        .getByRole("region", { name: "Chat about EarthShift Global" })
        .getByLabel("Message Leam"),
    ).toBeVisible();
    expect(opened.at(-1)).toBe(`/api/capacities/${boardId}/chat`);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}

for (const width of [390, 1440])
  test(`task metadata is independent of retained subtask drafts ${width}`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await fixture(page);
    const card = page.locator(`[data-order-card="${cardId}"]`);
    await card
      .getByRole("button", { name: "Expand details for Prepare invoice" })
      .click();
    await card
      .getByRole("button", { name: "Edit task details", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "Card details" });
    await expect(
      dialog.getByRole("form", { name: "Subtask editor" }),
    ).toHaveCount(0);
    await dialog
      .getByRole("textbox", { name: "Notes", exact: true })
      .fill("Metadata edited directly");
    expect(state.writes).toEqual([]);
    await dialog
      .getByRole("button", { name: "Save card", exact: true })
      .click();
    await expect(dialog).toHaveCount(0);
    expect(state.writes[0]).toMatchObject({
      method: "PATCH",
      path: `/api/commitments/${cardId}`,
      body: { revision: 7, notes: "Metadata edited directly" },
    });
    await card
      .getByRole("button", { name: "Add subtask", exact: true })
      .click();
    await dialog
      .getByLabel("Subtask title", { exact: true })
      .fill("Retain this child draft");
    await dialog
      .getByRole("button", { name: "Task details", exact: true })
      .click();
    await expect(
      dialog.getByRole("form", { name: "Subtask editor" }),
    ).not.toBeVisible();
    await dialog
      .getByRole("textbox", { name: "Notes", exact: true })
      .fill("Independent metadata revision");
    await dialog
      .getByRole("button", { name: "Save card", exact: true })
      .click();
    await expect(
      dialog.getByText(
        "Task details saved. Your subtask draft is retained separately.",
      ),
    ).toBeVisible();
    await dialog.screenshot({
      path: info.outputPath(`task-details-${width}.png`),
    });
    expect(state.writes).toHaveLength(2);
    expect(state.writes[1]).toMatchObject({
      method: "PATCH",
      body: { revision: 8, notes: "Independent metadata revision" },
    });
    await dialog
      .getByRole("button", { name: "Close Card details", exact: true })
      .click();
    await card
      .getByRole("button", { name: "Edit task details", exact: true })
      .click();
    await expect(dialog.getByRole("textbox", { name: "Notes", exact: true })).toHaveValue(
      "Independent metadata revision",
    );
    await expect(
      dialog.getByRole("form", { name: "Subtask editor" }),
    ).not.toBeVisible();
    await dialog.getByRole("button", { name: /^Subtasks/ }).click();
    await expect(
      dialog.getByLabel("Subtask title", { exact: true }),
    ).toHaveValue("Retain this child draft");
    await dialog.screenshot({
      path: info.outputPath(`subtask-draft-${width}.png`),
    });
    expect(state.writes).toHaveLength(2);
    await dialog
      .getByRole("button", { name: "Save subtask", exact: true })
      .click();
    await expect(
      dialog.getByRole("form", { name: "Subtask editor" }),
    ).toHaveCount(0);
    expect(state.writes[2]).toMatchObject({
      method: "POST",
      path: `/api/commitments/${cardId}/subtasks`,
      body: { revision: 9, action: "add", title: "Retain this child draft" },
    });
    expect(state.cards[0].notes).toBe("Independent metadata revision");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });

test("task revision conflict retains separate metadata and subtask drafts", async ({
  page,
}) => {
  const state = await fixture(page);
  const card = page.locator(`[data-order-card="${cardId}"]`);
  await card
    .getByRole("button", { name: "Expand details for Prepare invoice" })
    .click();
  await card.getByRole("button", { name: "Add subtask", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Card details" });
  await dialog
    .getByLabel("Subtask title", { exact: true })
    .fill("Do not lose this child");
  await dialog
    .getByRole("button", { name: "Task details", exact: true })
    .click();
  await dialog
    .getByRole("textbox", { name: "Notes", exact: true })
    .fill("Do not lose these task details");
  state.conflict = true;
  await dialog.getByRole("button", { name: "Save card", exact: true }).click();
  await expect(dialog.getByText(/Your draft is retained/)).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "Notes", exact: true })).toHaveValue(
    "Do not lose these task details",
  );
  await dialog.getByRole("button", { name: /^Subtasks/ }).click();
  await expect(dialog.getByLabel("Subtask title", { exact: true })).toHaveValue(
    "Do not lose this child",
  );
  await expect(
    dialog.getByRole("button", { name: "Save subtask", exact: true }),
  ).toBeDisabled();
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0].method).toBe("PATCH");
});
