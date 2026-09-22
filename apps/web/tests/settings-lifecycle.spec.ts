import { test, expect, type Page } from "@playwright/test";
import { navigate, settingsSection } from "./navigation";

async function fixture(page: Page) {
  const state = {
    reads: [] as string[],
    writes: [] as string[],
    backupWait: null as Promise<void> | null,
  };
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    (method === "GET" ? state.reads : state.writes).push(path);
    let body: any = {
      items: [],
      data: [],
      threads: [],
      accounts: [],
      providers: [],
      models: [],
    };
    if (path === "/api/auth/status" || path === "/api/auth/login")
      body = { authenticated: true, configured: true };
    if (path === "/api/settings/providers")
      body = {
        providers: [
          {
            id: "openai",
            description: "OpenAI API",
            adapter: "open_ai_completions",
            default_model: "fixture-model",
            accepts_api_key: true,
            active: false,
          },
        ],
      };
    if (path === "/api/push/status")
      body = {
        contact: "mailto:owner@example.test",
        devices: [],
        available: true,
      };
    if (path === "/api/automation/restore") body = { held: false };
    if (path === "/api/backups" && method === "POST" && state.backupWait)
      await state.backupWait;
    if (path === "/api/usage")
      body = {
        totals: {
          requests: 0,
          input: "0",
          output: "0",
          total: "0",
          cached: "0",
          reasoning: "0",
          nonCached: "0",
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
        warning: { exceeded: false, observed: "0", limit: 0 },
        generatedAt: 1790000000,
      };
    await route.fulfill({ json: body });
  });
  await page.goto("/?view=settings");
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
  return state;
}
const section = (page: Page, title: string) =>
  page.locator(".settings-section").filter({
    has: page.locator(":scope > summary", {
      hasText: new RegExp(`^${title}$`),
    }),
  });
const close = async (page: Page, title: string) => {
  await section(page, title).locator(":scope > summary").click();
};

test("unopened Settings panels make no domain reads or periodic checks", async ({
  page,
}) => {
  await page.clock.install();
  const state = await fixture(page);
  await page.clock.fastForward(35000);
  const forbidden = [
    "/api/settings/providers",
    "/api/settings/models",
    "/api/accounts",
    "/api/email",
    "/api/memory",
    "/api/backups",
    "/api/automation/restore",
    "/api/push/status",
    "/api/notifications/status",
    "/api/voice/status",
    "/api/companion/system",
    "/api/companion/tools",
    "/api/procedures",
    "/api/usage",
  ];
  expect(state.reads.filter((path) => forbidden.includes(path))).toEqual([]);
  expect(state.writes).toEqual([]);
  await settingsSection(page, "Model and reasoning");
  await expect(page.getByLabel("Model provider")).toBeVisible();
  await expect
    .poll(() => state.reads.filter((p) => p === "/api/settings/models").length)
    .toBe(2); // Companion and new-Coding defaults each load their catalog once.
  expect(state.reads.filter((path) => forbidden.includes(path)).sort()).toEqual([
    "/api/settings/models",
    "/api/settings/models",
    "/api/settings/providers",
  ]);
  await page.locator("summary", { hasText: /^Model and reasoning$/ }).click();
  await page.clock.fastForward(35000);
  expect(state.reads.filter((p) => p === "/api/settings/models")).toHaveLength(2);
});

test("provider draft survives collapse and navigation without catalog reload or storage persistence", async ({
  page,
}) => {
  const state = await fixture(page);
  await settingsSection(page, "Model and reasoning");
  await page.getByLabel("Model provider").selectOption("openai");
  await page
    .getByLabel("API key", { exact: true })
    .fill("unsaved-private-fixture");
  await close(page, "Model and reasoning");
  await navigate(page, "Routines");
  await expect(
    page.getByRole("region", { name: "Settings", exact: true }),
  ).toBeHidden();
  await navigate(page, "Settings");
  await settingsSection(page, "Model and reasoning");
  await expect(page.getByLabel("API key", { exact: true })).toHaveValue(
    "unsaved-private-fixture",
  );
  expect(
    state.reads.filter((p) => p === "/api/settings/providers"),
  ).toHaveLength(1);
  expect(state.writes).toEqual([]);
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...sessionStorage, ...localStorage }),
    ),
  ).not.toContain("unsaved-private-fixture");
  await page.evaluate(() => window.dispatchEvent(new Event("leam:auth-lost")));
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await page.getByLabel("Password", { exact: true }).fill("fixture-password");
  await page.getByRole("button", { name: "Enter Leam" }).click();
  await settingsSection(page, "Model and reasoning");
  await page.getByLabel("Model provider").selectOption("openai");
  await expect(page.getByLabel("API key", { exact: true })).toHaveValue("");
});

test("Push polling pauses on collapse navigation and hidden document while drafts survive", async ({
  page,
}) => {
  await page.clock.install();
  const state = await fixture(page);
  await settingsSection(page, "Phone notifications");
  await expect(page.getByLabel("Push contact email")).toHaveValue(
    "owner@example.test",
  );
  await page.getByLabel("Push contact email").fill("draft@example.test");
  await page.clock.fastForward(11000);
  await expect
    .poll(() => state.reads.filter((p) => p === "/api/push/status").length)
    .toBe(2);
  await close(page, "Phone notifications");
  await page.clock.fastForward(30000);
  expect(state.reads.filter((p) => p === "/api/push/status")).toHaveLength(2);
  await settingsSection(page, "Phone notifications");
  await expect
    .poll(() => state.reads.filter((p) => p === "/api/push/status").length)
    .toBe(3);
  await expect(page.getByLabel("Push contact email")).toHaveValue(
    "draft@example.test",
  );
  await navigate(page, "Routines");
  await page.clock.fastForward(30000);
  expect(state.reads.filter((p) => p === "/api/push/status")).toHaveLength(3);
  await navigate(page, "Settings");
  await expect
    .poll(() => state.reads.filter((p) => p === "/api/push/status").length)
    .toBe(4);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.fastForward(30000);
  expect(state.reads.filter((p) => p === "/api/push/status")).toHaveLength(4);
});

test("accepted backup operation retains its receipt across navigation without duplicate submission", async ({
  page,
}) => {
  const state = await fixture(page);
  let release!: () => void;
  state.backupWait = new Promise<void>((resolve) => {
    release = resolve;
  });
  await settingsSection(page, "Backups and recovery");
  await page
    .getByRole("button", { name: "Create product backup", exact: true })
    .click();
  await expect
    .poll(() => state.writes.filter((p) => p === "/api/backups").length)
    .toBe(1);
  await navigate(page, "Routines");
  release();
  await navigate(page, "Settings");
  await expect(
    page.getByText(
      "Backup saved privately on this Leam host. Download a separate copy below.",
    ),
  ).toBeVisible();
  expect(state.writes.filter((p) => p === "/api/backups")).toHaveLength(1);
});

test("memory activity polling pauses with its enclosing section", async ({
  page,
}) => {
  await page.clock.install();
  const state = await fixture(page);
  await settingsSection(page, "Memory");
  await settingsSection(page, "Memory suggestions and activity");
  await expect
    .poll(() => state.reads.filter((p) => p === "/api/proposals/memory").length)
    .toBe(1);
  await close(page, "Memory");
  await page.clock.fastForward(20000);
  expect(state.reads.filter((p) => p === "/api/proposals/memory")).toHaveLength(
    1,
  );
  await settingsSection(page, "Memory");
  await expect
    .poll(() => state.reads.filter((p) => p === "/api/proposals/memory").length)
    .toBe(2);
});

for (const viewport of [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
])
  test(`Settings disclosures remain reachable at ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await fixture(page);
    const summary = section(page, "Model and reasoning").locator(
      ":scope > summary",
    );
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("Model provider")).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
  });

test("Usage app retains goal draft and selected page after app navigation", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.getByRole("button", { name: "Open Usage", exact: true }).click();
  await page
    .getByRole("group", { name: "Usage views" })
    .getByRole("button", { name: "Goals", exact: true })
    .click();
  await page
    .getByLabel("New goal name", { exact: true })
    .fill("A considered week");
  await page
    .getByLabel("Coding thread ID", { exact: true })
    .fill("fixture-thread");
  await navigate(page, "Routines");
  await navigate(page, "Settings");
  await page.getByRole("button", { name: "Open Usage", exact: true }).click();
  await expect(page.getByLabel("New goal name", { exact: true })).toHaveValue(
    "A considered week",
  );
  await expect(
    page.getByLabel("Coding thread ID", { exact: true }),
  ).toHaveValue("fixture-thread");
  expect(state.reads.filter((p) => p === "/api/usage")).toHaveLength(1);
  expect(state.writes).toEqual([]);
});

test("OAuth return opens only Connected accounts automatically", async ({
  page,
}) => {
  const state = await fixture(page);
  state.reads.length = 0;
  await page.goto("/?view=settings&account=email-connected");
  await expect(
    page.getByText(
      "Read-only email connected. Sync the inbox below to show recent email in Today.",
    ),
  ).toBeVisible();
  await expect
    .poll(() => state.reads.filter((p) => p === "/api/accounts").length)
    .toBe(1);
  expect(state.reads).not.toContain("/api/settings/providers");
});

test("a pending memory approval completes while Settings is hidden and keeps its receipt", async ({
  page,
}) => {
  const state = await fixture(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let approved = false;
  let posts = 0;
  const proposal = () => ({
    id: "fixture-memory",
    operation: "memory.create",
    state: approved ? "complete" : "pending",
    reason: "Explicit fixture preference",
    review: { after: { text: "Prefer quiet mornings", source: "User" } },
  });
  await page.route("**/api/proposals/memory?*", (route) =>
    route.fulfill({ json: { items: [proposal()], nextOffset: null } }),
  );
  await page.route("**/api/proposals/fixture-memory/approve", async (route) => {
    posts++;
    await gate;
    approved = true;
    await route.fulfill({ json: proposal() });
  });
  await settingsSection(page, "Memory");
  await settingsSection(page, "Memory suggestions and activity");
  await page
    .getByRole("button", { name: "Approve this change", exact: true })
    .click();
  await expect.poll(() => posts).toBe(1);
  await navigate(page, "Routines");
  release();
  await expect.poll(() => approved).toBe(true);
  await navigate(page, "Settings");
  await expect(
    page.getByText("Change completed", { exact: true }),
  ).toBeVisible();
  expect(posts).toBe(1);
  expect(state.writes).toEqual([]);
});

test("late first Push status cannot replace a draft typed before its response", async ({
  page,
}) => {
  await fixture(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/push/status", async (route) => {
    await gate;
    await route.fulfill({
      json: { contact: "mailto:older@example.test", devices: [] },
    });
  });
  await settingsSection(page, "Phone notifications");
  await page.getByLabel("Push contact email").fill("kept@example.test");
  await close(page, "Phone notifications");
  release();
  await settingsSection(page, "Phone notifications");
  await expect(page.getByLabel("Push contact email")).toHaveValue(
    "kept@example.test",
  );
});
