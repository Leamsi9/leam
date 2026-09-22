import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

const totals = {
  requests: 31,
  input: "9007199254740993",
  output: "100",
  total: "9007199254741093",
  cached: null,
  reasoning: null,
  nonCached: null,
  cacheKnownRequests: 0,
};
function overview() {
  return {
    totals,
    daily: [{ day: "2026-09-21", ...totals }],
    groups: [
      { kind: "operation", key: "coding.turn", ...totals },
      { kind: "thread", key: "fixture-thread", ...totals },
      { kind: "goal", key: "unassigned", ...totals },
    ],
    coverage: {
      sources: [
        {
          source: "Codex responses",
          status: "partial",
          lastSuccess: 1790000000,
          details: "Only own-thread response records are counted.",
        },
        {
          source: "Companion / IronClaw",
          status: "gap",
          lastSuccess: null,
          details:
            "Runtime and auxiliary model calls are missing from these totals.",
        },
      ],
      conflicts: 1,
      unassignedRequests: 31,
    },
    opportunities: [
      {
        id: "large-calls",
        title: "Review large responses",
        details:
          "Inspect observed input and cache coverage before changing context.",
        severity: "info",
      },
    ],
    filters: {
      models: ["fixture-model", "second-model"],
      operations: ["coding.turn"],
      goals: [{ id: "goal-one", name: "Ship an accessible app" }],
    },
    settings: { enabled: true, dailyWarningTokens: 0, largeCallTokens: 100000 },
    warning: { exceeded: false, observed: "1000", limit: 0 },
    generatedAt: 1790000000,
  };
}
function record(index: number) {
  return {
    id: `response-${index}`,
    source: "codex.response",
    threadId: "fixture-thread",
    turnId: "fixture-turn",
    model: "fixture-model",
    operation: "coding.turn",
    goalId: null,
    timestamp: 1790000000,
    input: "9007199254740993",
    output: "100",
    total: "9007199254741093",
    cached: null,
    reasoning: null,
  };
}
async function setup(page: Page, loseFirstExhaustionResponse = false) {
  const calls: { path: string; method: string; query: string; body: any }[] =
    [];
  let settings = overview().settings;
  const exhaustionEvents: any[] = [];
  let exhaustionPosts = 0;
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    let body: any = {
      items: [],
      providers: [],
      models: [],
      accounts: [],
      data: [],
    };
    if (url.pathname.startsWith("/api/usage"))
      calls.push({
        path: url.pathname,
        method: request.method(),
        query: url.search,
        body: request.postData() ? request.postDataJSON() : null,
      });
    if (url.pathname === "/api/auth/status") body = { authenticated: true };
    if (url.pathname === "/api/usage") body = { ...overview(), settings };
    if (url.pathname === "/api/usage/exhaustions") {
      if (request.method() === "POST") {
        const input = request.postDataJSON();
        let saved = exhaustionEvents.find(
          (event) => event.id === input.requestId,
        );
        if (!saved) {
          saved = {
            id: input.requestId,
            observedAt: Date.parse(input.observedAt) / 1000,
            timezone: input.timezone,
            source: input.source,
          };
          exhaustionEvents.push(saved);
        }
        exhaustionPosts += 1;
        if (loseFirstExhaustionResponse && exhaustionPosts === 1) {
          await route.fulfill({
            status: 503,
            json: {
              detail: "Response lost after acceptance; retry the same event",
            },
          });
          return;
        }
        body = saved;
      } else {
        const events = [...exhaustionEvents].sort(
          (a, b) => b.observedAt - a.observedAt,
        );
        const periods = events.map((event, index) => ({
          eventId: event.id,
          start: event.observedAt,
          end: index ? events[index - 1].observedAt : null,
          timezone: event.timezone,
          totals,
        }));
        body = {
          events,
          periods,
          current: periods[0] || null,
          total: events.length,
          nextOffset: null,
        };
      }
    }
    if (url.pathname === "/api/usage/records") {
      const offset = Number(url.searchParams.get("offset") || 0);
      body = {
        items: Array.from({ length: offset ? 1 : 30 }, (_, index) =>
          record(offset + index),
        ),
        total: 31,
      };
    }
    if (url.pathname === "/api/usage/settings") {
      settings = request.postDataJSON();
      body = settings;
    }
    if (url.pathname === "/api/usage/goals")
      body = { id: "associated-goal", name: request.postDataJSON().name };
    if (url.pathname === "/api/usage/refresh") body = { scheduled: true };
    if (url.pathname === "/api/usage/export")
      body = {
        schemaVersion: 1,
        metadataOnly: true,
        truncated: true,
        items: [record(0)],
        total: 10001,
      };
    await route.fulfill({ json: body });
  });
  await page.goto("/?view=settings");
  return calls;
}
function dashboard(page: Page) {
  return page.getByRole("region", { name: "Usage and efficiency dashboard" });
}
async function open(page: Page) {
  if (!["usage", "tokenops"].includes(new URL(page.url()).searchParams.get("view") || ""))
    await page.getByRole("button", { name: "Open Usage", exact: true }).click();
  await expect(
    dashboard(page).getByText("Gross tokens", { exact: true }).first(),
  ).toBeVisible();
}

for (const width of [390, 1440]) {
  test(`usage opens on demand with exact counters, source gaps and accessible daily rows at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const calls = await setup(page);
    expect(
      calls.filter((call) => call.path.startsWith("/api/usage")),
    ).toHaveLength(0);
    await open(page);
    await expect(
      dashboard(page)
        .getByText("9,007,199,254,741,093", { exact: true })
        .first(),
    ).toBeVisible();
    await expect(
      dashboard(page).getByText("Unknown", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByRole("table", { name: "Daily token usage" }),
    ).toBeVisible();
    await expect(
      dashboard(page).getByText(
        "Runtime and auxiliary model calls are missing from these totals.",
      ),
    ).toBeVisible();
    await expect(
      dashboard(page).getByText(
        /API prices and subscription allowances are unknown/,
      ),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await dashboard(page)
      .getByRole("button", { name: "Opportunities", exact: true })
      .click();
    await expect(
      dashboard(page).getByRole("heading", { name: "Review large responses" }),
    ).toBeVisible();
    expect(calls.every((call) => call.method === "GET")).toBe(true);
  });
}

test("filters reach the actual caller, persist only selections and reset response pagination", async ({
  page,
}) => {
  const calls = await setup(page);
  await open(page);
  await page
    .getByRole("combobox", { name: "Usage range", exact: true })
    .selectOption("0");
  await page
    .getByRole("combobox", { name: "Usage model", exact: true })
    .selectOption("second-model");
  await page
    .getByRole("combobox", { name: "Usage operation", exact: true })
    .selectOption("coding.turn");
  await page
    .getByRole("combobox", { name: "Usage goal", exact: true })
    .selectOption("goal-one");
  await dashboard(page)
    .getByRole("button", { name: "Calls", exact: true })
    .click();
  await expect(dashboard(page).locator(".usage-record")).toHaveCount(30);
  await dashboard(page).getByRole("button", { name: "Next responses" }).click();
  await expect(dashboard(page).locator(".usage-record")).toHaveCount(1);
  let last = calls.filter((call) => call.path === "/api/usage/records").at(-1)!;
  expect(Object.fromEntries(new URLSearchParams(last.query))).toEqual({
    days: "0",
    model: "second-model",
    operation: "coding.turn",
    goal: "goal-one",
    offset: "30",
    limit: "30",
  });
  await page
    .getByRole("combobox", { name: "Usage range", exact: true })
    .selectOption("30");
  await expect(dashboard(page).locator(".usage-record")).toHaveCount(30);
  last = calls.filter((call) => call.path === "/api/usage/records").at(-1)!;
  expect(new URLSearchParams(last.query).get("offset")).toBe("0");
  await page.reload();
  await open(page);
  await expect(
    page.getByRole("combobox", { name: "Usage range", exact: true }),
  ).toHaveValue("30");
  await expect(
    page.getByRole("combobox", { name: "Usage model", exact: true }),
  ).toHaveValue("second-model");
  expect(
    await page.evaluate(() =>
      JSON.stringify(sessionStorage).includes("response-0"),
    ),
  ).toBe(false);
});

test("response attribution and advisory settings send exact explicit choices", async ({
  page,
}) => {
  const calls = await setup(page);
  await open(page);
  await dashboard(page)
    .getByRole("button", { name: "Calls", exact: true })
    .click();
  await dashboard(page)
    .getByRole("button", { name: "Associate this thread with a goal" })
    .first()
    .click();
  await expect(
    page.getByLabel("Coding thread ID", { exact: true }),
  ).toHaveValue("fixture-thread");
  await page.getByLabel("New goal name", { exact: true }).fill("Review usage");
  await page.getByLabel("Include known and future descendant threads").check();
  await dashboard(page)
    .getByRole("button", { name: "Create goal and associate" })
    .click();
  await expect(
    dashboard(page).getByText(
      "Goal association saved. Recorded tokens are counted once.",
    ),
  ).toBeVisible();
  expect(calls.find((call) => call.path === "/api/usage/goals")?.body).toEqual({
    name: "Review usage",
    threadId: "fixture-thread",
    includeDescendants: true,
  });
  await dashboard(page)
    .getByRole("button", { name: "Controls", exact: true })
    .click();
  await page.getByLabel("Collect usage metadata").uncheck();
  await page
    .getByLabel("Daily warning threshold · gross tokens")
    .fill("250000");
  await page
    .getByLabel("Large response threshold · gross tokens")
    .fill("50000");
  await dashboard(page)
    .getByRole("button", { name: "Save usage controls" })
    .click();
  await expect(
    dashboard(page).getByText(
      "Usage controls saved. Warning thresholds are advisory.",
    ),
  ).toBeVisible();
  expect(
    calls.find((call) => call.path === "/api/usage/settings")?.body,
  ).toEqual({
    enabled: false,
    dailyWarningTokens: 250000,
    largeCallTokens: 50000,
  });
  await dashboard(page)
    .getByRole("button", { name: "Data", exact: true })
    .click();
  await expect(
    dashboard(page).getByRole("button", { name: "Import recent metadata" }),
  ).toBeDisabled();
});

test("metadata export preserves exact numeric strings and reports the export bound", async ({
  page,
}) => {
  const calls = await setup(page);
  await open(page);
  await dashboard(page)
    .getByRole("button", { name: "Data", exact: true })
    .click();
  await dashboard(page)
    .getByRole("button", { name: "Import recent metadata" })
    .click();
  await expect(
    dashboard(page).getByText(/Metadata import scheduled/),
  ).toBeVisible();
  expect(
    calls.filter((call) => call.path === "/api/usage/refresh"),
  ).toHaveLength(1);
  const pending = page.waitForEvent("download");
  await dashboard(page)
    .getByRole("button", { name: "Export usage JSON" })
    .click();
  const download = await pending;
  const contents = JSON.parse(await readFile((await download.path())!, "utf8"));
  expect(contents.metadataOnly).toBe(true);
  expect(contents.items[0].input).toBe("9007199254740993");
  expect(contents.items[0].cached).toBeNull();
  await expect(
    dashboard(page).getByText(/10,000-record limit was reached/),
  ).toBeVisible();
});

test("a delayed old filter response cannot overwrite newer usage", async ({
  page,
}) => {
  await setup(page);
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/usage?**", async (route) => {
    const days = new URL(route.request().url()).searchParams.get("days");
    if (days === "7") await held;
    const value = overview();
    value.totals = { ...totals, total: days === "30" ? "777" : "111" };
    await route.fulfill({ json: value }).catch(() => {});
  });
  if (!["usage", "tokenops"].includes(new URL(page.url()).searchParams.get("view") || ""))
    await page.getByRole("button", { name: "Open Usage", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Usage range", exact: true })
    .selectOption("30");
  await expect(dashboard(page).getByText("777", { exact: true })).toBeVisible();
  release();
  await expect(dashboard(page).getByText("111", { exact: true })).toHaveCount(
    0,
  );
});

test.describe("subscription exhaustion observations", () => {
  test.use({ timezoneId: "Europe/London" });
  for (const width of [390, 1440]) {
    test(`manual report starts exact observed counter and retains source disclosure at ${width}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 950 });
      const calls = await setup(page);
      await open(page);
      const history = page.getByRole("region", {
        name: "ChatGPT budget history",
      });
      await history
        .locator("summary")
        .filter({ hasText: "Record budget exhaustion" })
        .click();
      await history
        .getByLabel("Observed report time")
        .fill("2026-09-21T11:22:39.509");
      await expect(
        history.getByRole("button", { name: "Record exhaustion", exact: true }),
      ).toBeDisabled();
      await history.getByRole("checkbox").check();
      await history
        .getByRole("button", { name: "Record exhaustion", exact: true })
        .click();
      await expect(
        history.getByText("Since latest exhaustion report", { exact: true }),
      ).toBeVisible();
      await expect(
        history.getByText("9,007,199,254,741,093", { exact: true }).first(),
      ).toBeVisible();
      await expect(
        history.getByText(
          /automatic subscription exhaustion detection is not connected/,
        ),
      ).toBeVisible();
      const request = calls.find(
        (call) =>
          call.path === "/api/usage/exhaustions" && call.method === "POST",
      )!;
      expect(request.body.observedAt).toBe("2026-09-21T10:22:39.509Z");
      expect(request.body.provider).toBe("chatgpt");
      expect(request.body.attributeCodexToChatGPT).toBe(true);
      await history
        .locator("summary")
        .filter({ hasText: "Exhaustion history (1)" })
        .click();
      await expect(
        history.getByText(/User-reported observation/),
      ).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
    });
  }
  test("uncertain submission retries the same event identity", async ({
    page,
  }) => {
    const calls = await setup(page, true);
    await open(page);
    const history = page.getByRole("region", {
      name: "ChatGPT budget history",
    });
    await history
      .locator("summary")
      .filter({ hasText: "Record budget exhaustion" })
      .click();
    await history
      .getByLabel("Observed report time")
      .fill("2026-09-21T11:22:39.509");
    await history.getByRole("checkbox").check();
    await history
      .getByRole("button", { name: "Record exhaustion", exact: true })
      .click();
    await expect(history.getByRole("alert")).toBeVisible();
    await history
      .getByRole("button", { name: "Record exhaustion", exact: true })
      .click();
    await expect(
      history.getByText("Since latest exhaustion report", { exact: true }),
    ).toBeVisible();
    const posts = calls.filter(
      (call) =>
        call.path === "/api/usage/exhaustions" && call.method === "POST",
    );
    expect(posts).toHaveLength(2);
    expect(posts[0].body.requestId).toBe(posts[1].body.requestId);
    await expect(
      history.locator("summary").filter({ hasText: "Exhaustion history (1)" }),
    ).toBeVisible();
  });
});

function journey(thread: string, offset = 0) {
  return {
    threadId: thread,
    collection: "enabled",
    total: 101,
    nextOffset: offset + (offset ? 1 : 100),
    hasMore: !offset,
    counts: { model_call: 1 },
    inferredCounts: { tool_call: 100 },
    coverage: {
      importComplete: false,
      unattributedSteps: 2,
      excludedInherited: 3,
      oversizedRecords: 1,
      tokenAllocation:
        "Provider response totals only; per-tool token contribution unavailable",
      limitations: "Native hidden prompt stages are unavailable.",
    },
    items: Array.from({ length: offset ? 1 : 100 }, (_, i) => ({
      id: `${thread}-${offset + i}`,
      kind: i === 0 && !offset ? "model_call" : "tool_call",
      toolName: i === 0 && !offset ? null : "exec_command",
      evidence: i === 0 && !offset ? "exact_response" : "inferred_timestamp",
      timestamp: 1790000000,
      status: "observed",
      turnId: "fixture-turn",
      turnAttribution: "inferred",
      model: "fixture-model",
      callId: `${thread}-call`,
      inputBytes: 15,
      outputBytes: null,
      tokens:
        i === 0 && !offset
          ? {
              input: "9007199254740993",
              output: "100",
              cached: null,
              reasoning: null,
            }
          : null,
    })),
  };
}
for (const viewport of [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
  { width: 1440, height: 900 },
]) {
  test(`native journey preserves accounting boundaries, pagination and lazy reads at ${viewport.width}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const calls = await setup(page);
    const queries: URLSearchParams[] = [];
    await page.route("**/api/usage/journey?**", async (route) => {
      const query = new URL(route.request().url()).searchParams;
      queries.push(query);
      await route.fulfill({
        json: journey(query.get("threadId")!, Number(query.get("offset"))),
      });
    });
    await open(page);
    expect(queries).toHaveLength(0);
    await dashboard(page)
      .getByRole("button", { name: "Calls", exact: true })
      .click();
    await dashboard(page)
      .getByRole("button", { name: "Trace this session" })
      .first()
      .click();
    const region = page.getByRole("region", {
      name: "Native Codex session journey",
    });
    await expect(region.getByRole("status")).toHaveText(
      /Historical scan is incomplete/,
    );
    await expect(
      region.getByText("9,007,199,254,740,993", { exact: true }),
    ).toBeVisible();
    await expect(
      region.getByText("15 input bytes · Unknown output bytes").first(),
    ).toBeVisible();
    await expect(
      region.getByText(/Tool byte sizes are not token charges/),
    ).toBeVisible();
    await region.getByRole("button", { name: "Next steps" }).click();
    await expect(region.getByText("Steps 101–101 of 101")).toBeVisible();
    expect(queries[1].get("offset")).toBe("100");
    expect(
      queries.every((query) => query.get("threadId") === "fixture-thread"),
    ).toBe(true);
    await region.getByLabel("Turn ID (optional)").fill("another-turn");
    await region.getByRole("button", { name: "Apply turn filter" }).click();
    await expect(region.getByText("Steps 1–100 of 101")).toBeVisible();
    expect(queries.at(-1)?.get("turnId")).toBe("another-turn");
    expect(queries.at(-1)?.get("offset")).toBe("0");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBe(true);
    await dashboard(page)
      .getByRole("button", { name: "Overview", exact: true })
      .click();
    const count = queries.length;
    await page.waitForTimeout(600);
    expect(queries).toHaveLength(count);
    expect(calls.filter((call) => call.method !== "GET")).toHaveLength(0);
  });
}
test("native journey discards late responses after session switch and reports failures", async ({
  page,
}) => {
  await setup(page);
  await page.route("**/api/usage?**", (route) =>
    route.fulfill({
      json: {
        ...overview(),
        groups: [
          ...overview().groups,
          { kind: "thread", key: "second-thread", ...totals },
        ],
      },
    }),
  );
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = false;
  await page.route("**/api/usage/journey?**", async (route) => {
    const thread = new URL(route.request().url()).searchParams.get("threadId")!;
    if (thread === "fixture-thread") {
      started = true;
      await held;
      await route.fulfill({ json: journey(thread) }).catch(() => {});
    } else
      await route.fulfill({
        status: 503,
        json: { detail: "Saved trace temporarily unavailable" },
      });
  });
  await open(page);
  await dashboard(page)
    .getByRole("button", { name: "Session journey", exact: true })
    .click();
  await page.getByLabel("Journey session").selectOption("fixture-thread");
  await expect.poll(() => started).toBe(true);
  await page.getByLabel("Journey session").selectOption("second-thread");
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "Saved trace temporarily unavailable" }),
  ).toBeVisible();
  release();
  await page.waitForTimeout(150);
  await expect(page.getByLabel("Journey session")).toHaveValue("second-thread");
  await expect(
    page
      .getByRole("region", { name: "Native Codex session journey" })
      .locator(".usage-record"),
  ).toHaveCount(0);
});

for (const width of [390, 1440]) {
  test(`runtime attempts are lazy, numeric and paginated at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await setup(page);
    let requests = 0;
    await page.route(url => url.pathname === "/api/usage/runtime-attempts", async route => {
      expect(new URL(route.request().url()).pathname).toBe("/api/usage/runtime-attempts");
      requests += 1;
      const offset = Number(new URL(route.request().url()).searchParams.get("offset"));
      await route.fulfill({ json: {
        total: 31,
        coverage: { status: "partial", details: "Retained diagnostics only; evicted history unavailable." },
        items: [{ id: `attempt-${offset}`, threadId: "fixture-thread", runId: "fixture-run", callId: `call-${offset}`, iteration: 2, model: "fixture-runtime-model", status: "failed", startedAt: 1790000000, durationMs: 1234, input: "9007199254740993", output: null, cached: null, cacheWrite: null, reasoning: null, provider: null, source: "ironclaw.model_attempt", conflict: true }],
      } });
    });
    await open(page);
    expect(requests).toBe(0);
    await dashboard(page).getByRole("button", { name: "IronClaw attempts", exact: true }).click();
    const attempts = page.getByRole("region", { name: "IronClaw model attempts" });
    await expect(attempts.getByText("31 observed attempts")).toBeVisible();
    await attempts.locator("summary").click();
    await expect(attempts.getByText("call-0", { exact: true })).toBeVisible();
    await expect(attempts.getByText("Unknown", { exact: true }).first()).toBeVisible();
    await expect(attempts.locator("summary")).toContainText("9,007,199,254,740,993 input");
    await expect(attempts.locator("summary")).toContainText("excluded from totals");
    await attempts.getByRole("button", { name: "Next attempts" }).click();
    await expect(attempts.locator("summary")).toContainText("fixture-runtime-model");
    expect(requests).toBe(2);
    await expect(attempts.getByRole("button", { name: "Next attempts" })).toBeDisabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await dashboard(page).getByRole("button", { name: "Overview", exact: true }).click();
    await page.waitForTimeout(500);
    expect(requests).toBe(2);
  });
}


test("Trace this session passes the canonical runtime thread without the ledger prefix", async ({ page }) => {
  await setup(page);
  await page.route(url => url.pathname === "/api/usage/records", route => route.fulfill({ json: { total: 1, items: [{ ...record(0), source: "ironclaw.model_attempt", threadId: "ironclaw:runtime-thread" }] } }));
  const queries: string[] = [];
  await page.route(url => url.pathname === "/api/usage/runtime-attempts", route => {
    queries.push(new URL(route.request().url()).searchParams.get("threadId") || "");
    return route.fulfill({ json: { total: 0, items: [], coverage: { status: "partial", details: "Fixture measurements" } } });
  });
  await open(page);
  await dashboard(page).getByRole("button", { name: "Calls", exact: true }).click();
  await dashboard(page).getByRole("button", { name: "Trace this session", exact: true }).click();
  await expect(page.getByText("Conversation: runtime-thread", { exact: true })).toBeVisible();
  await expect.poll(() => queries).toEqual(["runtime-thread"]);
  await page.getByRole("button", { name: "Show all runtime conversations", exact: true }).click();
  await expect.poll(() => queries).toEqual(["runtime-thread", ""]);
});
