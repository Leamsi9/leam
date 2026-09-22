import { test, expect, type Page } from "@playwright/test";
import { navigate, settingsSection, chooseConversation } from "./navigation";

const capacityId = "11111111-1111-4111-8111-111111111111";
function proposal(id: string, title: string, operation = "commitment.create") {
  return {
    id,
    fingerprint: `fingerprint-${id}`,
    thread_id: "chat",
    operation,
    state: "pending",
    unread: true,
    readAt: null,
    input:
      operation === "coding.handoff"
        ? { title, instructions: "Review a bounded code change", context: "" }
        : { title, kind: "task", capacityId },
    review: {
      after: { title, kind: "task", capacityId },
      approval: { mode: "manual" },
    },
    reason: "Requested change",
    result: null,
    error: null,
  };
}
async function fixture(page: Page) {
  const state = {
    items: [
      proposal("one", "First task"),
      proposal("two", "Second task"),
      proposal("code", "Coding request", "coding.handoff"),
    ] as any[],
    writes: [] as { path: string; body: any; method: string }[],
    policy: {
      revision: 0,
      todayRequiresApproval: false,
      goalsRequiresApproval: false,
    },
    staleEdit: false,
    delayedBulk: false,
  };
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname,
      method = route.request().method();
    const data = method === "GET" ? null : route.request().postDataJSON();
    if (method !== "GET") state.writes.push({ path, body: data, method });
    let body: any = {
      items: [],
      data: [],
      messages: [],
      threads: [],
      models: [],
      providers: [],
    };
    if (path === "/api/auth/status")
      body = { configured: true, authenticated: true };
    if (path === "/api/capacities")
      body = { items: [{ id: capacityId, name: "Health" }] };
    if (path === "/api/proposals")
      body = {
        items: state.items,
        nextOffset: null,
        unreadCount: state.items.filter((item) => item.unread).length,
      };
    if (path === "/api/proposals/status")
      body = {
        unreadCount: state.items.filter((item) => item.unread).length,
        pendingCount: state.items.filter((item) => item.state === "pending")
          .length,
      };
    if (path === "/api/proposals/policy") {
      if (method === "PUT")
        state.policy = { ...data, revision: data.revision + 1 };
      body = state.policy;
    }
    if (path === "/api/proposals/approve-all") {
      const ids = new Set(data.items.map((row: any) => row.id));
      const changed = state.items
        .filter(
          (item) => ids.has(item.id) && item.operation !== "coding.handoff",
        )
        .map((item) => ({ ...item, state: "complete" }));
      state.items = state.items.map(
        (item) => changed.find((value) => value.id === item.id) || item,
      );
      body = { items: changed, failed: [], skipped: [] };
    }
    if (path === "/api/proposals/read-all") {
      state.items = state.items.map((item) => ({
        ...item,
        unread: false,
        readAt: 1,
      }));
      body = { readCount: state.items.length };
    }
    const action = path.match(
      /^\/api\/proposals\/([^/]+)\/(read|approve|decline)$/,
    );
    if (action) {
      const item = state.items.find((item) => item.id === action[1]);
      if (action[2] === "read") {
        item.unread = false;
        item.readAt = 1;
        body = { id: item.id, unread: false };
      }
      if (action[2] === "approve") {
        item.state = "complete";
        body = item;
      }
      if (action[2] === "decline") {
        state.items = state.items.filter((row) => row.id !== item.id);
        body = { id: item.id, state: "declined", deleted: true };
      }
    }
    if (method === "PATCH" && /^\/api\/proposals\/[^/]+$/.test(path)) {
      if (state.staleEdit)
        return route.fulfill({
          status: 409,
          json: { detail: "Proposal changed. Refresh before editing." },
        });
      const id = path.split("/").at(-1),
        before = state.items.find((item) => item.id === id);
      body = {
        ...before,
        id: `${id}-edited`,
        fingerprint: `edited-${before.fingerprint}`,
        input: data.input,
        review: { ...before.review, after: data.input },
      };
      state.items = state.items.map((item) => (item.id === id ? body : item));
    }
    if (path.startsWith("/api/coding/handoffs/"))
      body = { state: "not_reviewed", taskStatus: "not_started" };
    if (path === "/api/companion/threads")
      body = { threads: [{ thread_id: "chat", title: "Conversation" }] };
    if (path === "/api/companion/threads/chat") body = { messages: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  return state;
}

for (const width of [390, 1440])
  test(`Approvals navigation, unread distinction and exact bulk at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await fixture(page);
    await navigate(page, "Approvals");
    await expect(
      page.getByRole("heading", { name: "Approvals", exact: true }),
    ).toBeVisible();
    const first = page.getByRole("article", { name: "Approval: First task" });
    await expect(first.getByLabel("Unread approval")).toBeVisible();
    const colour = await first
      .getByLabel("Unread approval")
      .evaluate((node) => getComputedStyle(node).backgroundColor);
    expect(colour).toBe("rgb(124, 58, 237)");
    await first.getByRole("button", { name: "Mark read", exact: true }).click();
    await expect(first.getByLabel("Unread approval")).toHaveCount(0);
    expect(state.items.find((item) => item.id === "one").state).toBe("pending");
    await page
      .getByRole("button", { name: "Approve all (2)", exact: true })
      .click();
    await expect(
      page.getByRole("status").filter({ hasText: "2 changes applied" }),
    ).toBeVisible();
    const bulk = state.writes.find(
      (write) => write.path === "/api/proposals/approve-all",
    )!;
    expect(bulk.body).toEqual({
      items: [
        { id: "one", fingerprint: "fingerprint-one" },
        { id: "two", fingerprint: "fingerprint-two" },
      ],
    });
    expect(state.items.find((item) => item.id === "code").state).toBe(
      "pending",
    );
    await expect(
      page.getByRole("article", { name: "Approval: Coding request" }),
    ).toContainText("separate review required");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });

test("edit uses capacity names, creates a new review and never applies it implicitly", async ({
  page,
}) => {
  const state = await fixture(page);
  await navigate(page, "Approvals");
  const first = page.getByRole("article", { name: "Approval: First task" });
  await first.getByText("Review change", { exact: true }).click();
  await expect(first).toContainText("Health");
  await expect(first).not.toContainText(capacityId);
  await first.getByRole("button", { name: "Edit", exact: true }).click();
  const editor = first.getByRole("form", { name: "Edit approval" });
  await expect(editor.getByLabel("Capacity", { exact: true })).toHaveValue(
    capacityId,
  );
  await expect(
    editor.getByRole("option", { name: "Health", exact: true }),
  ).toHaveCount(1);
  await editor.getByLabel("Title", { exact: true }).fill("Revised task");
  await editor.getByRole("button", { name: "Save revised proposal" }).click();
  await expect(
    page.getByRole("article", { name: "Approval: Revised task" }),
  ).toBeVisible();
  const edit = state.writes.find((write) => write.method === "PATCH")!;
  expect(edit.body.fingerprint).toBe("fingerprint-one");
  expect(edit.body.input).toMatchObject({ title: "Revised task", capacityId });
  expect(state.writes.some((write) => write.path.endsWith("/approve"))).toBe(
    false,
  );
  await page
    .getByRole("article", { name: "Approval: Revised task" })
    .getByRole("button", { name: "Approve", exact: true })
    .click();
  expect(
    state.writes.find((write) => write.path.endsWith("/approve"))?.body,
  ).toEqual({ fingerprint: "edited-fingerprint-one" });
});

test("decline removes the card and stale edit preserves the user's text", async ({
  page,
}) => {
  const state = await fixture(page);
  await navigate(page, "Approvals");
  const second = page.getByRole("article", { name: "Approval: Second task" });
  await second.getByRole("button", { name: "Decline", exact: true }).click();
  await expect(second).toHaveCount(0);
  const first = page.getByRole("article", { name: "Approval: First task" });
  await first.getByRole("button", { name: "Edit", exact: true }).click();
  await first.getByLabel("Title", { exact: true }).fill("Keep this draft");
  state.staleEdit = true;
  await first.getByRole("button", { name: "Save revised proposal" }).click();
  await expect(page.getByRole("alert")).toContainText("Proposal changed");
  await expect(first.getByLabel("Title", { exact: true })).toHaveValue(
    "Keep this draft",
  );
});

test("ordinary chat has no approval footer, global navigation and policy revisions remain", async ({
  page,
}) => {
  const state = await fixture(page);
  await navigate(page, "Companion");
  await chooseConversation(page, "chat");
  await expect(
    page.getByRole("button", {
      name: "Approvals · 3 awaiting review",
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Approve this change" }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Suggested changes", { exact: true }),
  ).toHaveCount(0);
  await navigate(page, "Approvals");
  await expect(
    page.getByRole("heading", { name: "Approvals", exact: true }),
  ).toBeVisible();
  await navigate(page, "Settings");
  await settingsSection(page, "Approval preferences");
  const policy = page.getByRole("region", { name: "Approval preferences" });
  await expect(
    policy.getByLabel("Automatically approve Today changes"),
  ).toBeChecked();
  await expect(
    policy.getByLabel("Automatically approve Goals changes"),
  ).toBeChecked();
  await policy.getByLabel("Automatically approve Today changes").uncheck();
  await policy
    .getByRole("button", { name: "Save approval preferences" })
    .click();
  await expect(policy.getByRole("status")).toContainText("saved");
  expect(
    state.writes.find((write) => write.path === "/api/proposals/policy")?.body,
  ).toEqual({
    revision: 0,
    todayRequiresApproval: true,
    goalsRequiresApproval: false,
  });
  await expect(policy).toContainText("Coding: review required");
});
