import { test, expect, type Page } from "@playwright/test";
async function fixture(page: Page, unavailable = false, completeEvidence = false, accomplishments?: any) {
  const writes: string[] = [];
  const make = (id: string, extra: any = {}) => ({
    id,
    key: `commitment:${id}`,
    title: id,
    kind: "task",
    owner: "user",
    status: "active",
    stage: "todo",
    revision: 2,
    capacityId: "work",
    measure: "boolean",
    target: 1,
    log: { done: false, value: 0, revision: 0 },
    triage: { disposition: "none", revision: 0 },
    ...extra,
  });
  const cards = [
    make("Invoice sent", { status: "completed", stage: "todo" }),
    make("Plan draft", { stage: "in_progress" }),
    make("Next task"),
    make("Waiting", { stage: "blocked" }),
    make("Paused task", { status: "paused", stage: "in_progress" }),
    make("Walk", { kind: "habit", capacityId: "health" }),
  ];
  await page.addInitScript(() => {
    (window as any).__activityStreams = [];
    (window as any).EventSource = class extends EventTarget {
      url: string; closed = false;
      constructor(url: string) { super(); this.url = url; (window as any).__activityStreams.push(this); }
      close() { this.closed = true; }
    };
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url()),
      p = url.pathname;
    if (route.request().method() !== "GET") writes.push(p);
    let value: any = {
      items: [],
      data: [],
      providers: [],
      threads: [],
      messages: [],
    };
    if (p === "/api/auth/status") value = { authenticated: true };
    if (p === "/api/capacities")
      value = {
        items: [
          { id: "work", name: "Work", revision: 1 },
          { id: "health", name: "Health", revision: 1 },
        ],
      };
    if (p === "/api/commitments") {
      if (unavailable)
        return route.fulfill({
          status: 503,
          json: { detail: "Task snapshot unavailable" },
        });
      value = { items: cards, boardOrders: {} };
    }
    if (p === "/api/agenda") {
      const date = url.searchParams.get("date") || "2026-09-22";
      value = {
        date,
        timezone: "Europe/London",
        partial: true,
        nextOffset: null,
        commitments: [
          make("Invoice sent", {
            status: "completed",
            log: { done: true, value: 1, revision: 2, date: "2026-09-22" },
          }),
          make("Walk", {
            kind: "habit",
            log: { done: true, value: 1, revision: 1, date: "2026-09-22" },
          }),
          make("Undone", { log: { done: false, revision: 3, date } }),
          make("Undated complete", { status: "completed" }),
          make("No saved revision", { log: { done: true, date } }),
          make("Yesterday", {
            status: "completed",
            log: { done: true, revision: 1, date: "2026-09-21" },
          }),
        ],
        events: [],
        emails: [],
        total: { commitments: 6, events: 0, emails: 0 },
        sources: {
          calendar: { state: "not_connected", accounts: [] },
          email: { state: "not_connected", accounts: [] },
        },
      };
    }
    if (p === "/api/agenda" && completeEvidence) value.accomplishments = {
      date: value.date, undatedCompleted: 3,
      activity: [{id:"step",title:"Small step",parentTitle:"Active parent",action:"subtask_completed",owner:"user"},{id:"start",title:"A start",action:"started",owner:"user"}],
      items: Array.from({length: 10}, (_, n) => make(`Completed ${n + 1}`, {completionEvidence: "status_transition"})),
    };
    if (p === "/api/agenda" && accomplishments) value.accomplishments = {date: value.date, ...accomplishments};
    await route.fulfill({ json: value });
  });
  await page.goto("/?view=today#today/plan/2026-09-22");
  return { writes };
}
for (const size of [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
])
  test(`Overview records completion evidence and collapses each card at ${size.width}`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    const f = await fixture(page);
    const celebration = page.locator(".overview-celebration");
    await expect(
      celebration.getByText("Invoice sent", { exact: true }),
    ).toBeVisible();
    await expect(celebration.getByText("Walk", { exact: true })).toBeVisible();
    await expect(celebration).toContainText(
      "2 commitments recorded complete for 2026-09-22",
    );
    for (const text of [
      "Undone",
      "Undated complete",
      "No saved revision",
      "Yesterday",
    ])
      await expect(celebration.getByText(text, { exact: true })).toHaveCount(0);
    await expect(celebration).toContainText("loaded part of this day");
    await expect(page.locator(".overview-card")).toHaveCount(9);
    const checks = page.locator(".overview-conversation-checks");
    await expect(checks).not.toHaveAttribute("open", "");
    await checks.locator(":scope > summary").click();
    await expect(checks).toHaveAttribute("open", "");
    await checks.locator(":scope > summary").click();
    const panels = page.locator(".overview-card:not(.overview-conversation-checks)");
    await expect(panels).toHaveCount(8);
    for (const panel of await panels.all()) {
      const summary = panel.locator(":scope > summary");
      await summary.click();
      await expect(panel).not.toHaveAttribute("open", "");
      await expect(
        panel.locator(":scope > .overview-card-body"),
      ).not.toBeVisible();
      await summary.focus();
      await page.keyboard.press("Enter");
      await expect(panel).toHaveAttribute("open", "");
    }
    const tasks = page
      .locator(".overview-destination")
      .filter({
        has: page.getByRole("heading", { name: "Tasks", exact: true }),
      });
    const work = tasks
      .locator("li")
      .filter({ has: page.getByText("Work", { exact: true }) });
    await expect(work).toContainText("1 to do");
    await expect(work).toContainText("1 in progress");
    await expect(work).toContainText("1 done");
    await expect(work).toContainText("1 blocked");
    await expect(work).toContainText("1 paused");
    await expect(tasks).toContainText("across all dates");
    await tasks
      .getByRole("button", { name: "Open Tasks", exact: true })
      .click();
    await expect(page).toHaveURL(/#today\/boards\/2026-09-22/);
    expect(f.writes).toEqual([]);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
test("Overview date selection does not reuse another day's accomplishments", async ({
  page,
}) => {
  await fixture(page);
  await expect(page.locator(".overview-celebration")).toContainText(
    "2 commitments",
  );
  await page.goto("/?view=today#today/plan/2026-09-21");
  const panel = page.locator(".overview-celebration");
  await expect(panel).toContainText(
    "1 commitment recorded complete for 2026-09-21",
  );
  await expect(panel.getByText("Yesterday", { exact: true })).toBeVisible();
  await expect(panel.getByText("Invoice sent", { exact: true })).toHaveCount(0);
});
test("Unavailable canonical task counts are not shown as zero", async ({
  page,
}) => {
  await fixture(page, true);
  await expect(
    page.getByText(
      "Task counts are unavailable until the canonical task list loads.",
    ),
  ).toBeVisible();
  await expect(page.locator(".overview-capacity-counts")).toHaveCount(0);
  await expect(page.locator(".overview-celebration")).toContainText(
    "2 commitments",
  );
});

test("Celebration uses the full dated completion projection, not the agenda page", async ({ page }) => {
  await fixture(page, false, true);
  const panel = page.locator(".overview-celebration");
  await expect(panel).toContainText("10 commitments recorded complete");
  await expect(panel.getByRole("listitem")).toHaveCount(12);
  await expect(panel.getByText("Completed 10", {exact:true})).toBeVisible();
  await expect(panel).toContainText("3 other completed tasks have no recorded completion date");
  await expect(panel).not.toContainText("loaded part of this day");
  await expect(panel.getByRole("region", {name:"Completed subtasks"})).toContainText("Small step");
  await page.goto("/?view=today#today/wellbeing/2026-09-22");
  const context = page.locator("details").filter({has:page.locator("summary",{hasText:"Activity is context"})});
  await context.locator("summary").click();
  await expect(context.getByRole("region", {name:"Completed commitments"})).toContainText("10 completed commitments");
  await expect(context.getByRole("region", {name:"Completed subtasks"})).toContainText("Small step");
  await expect(context.getByRole("region", {name:"Tasks moved into progress",exact:true})).toContainText("A start");
});

for (const width of [390, 844]) test(`Personal celebration names the achievement without inventing its owner ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: width === 390 ? 844 : 390});
  await fixture(page, false, false, {items: [
    {id: "mine", title: "Send the project invoice", owner: "user"},
    {id: "delegated", title: "Prepare the summary", owner: "leam"},
    {id: "unknown", title: "Review the draft"},
  ], activity: []});
  const panel = page.locator(".overview-celebration");
  await expect(panel).toContainText("You completed “Send the project invoice”. Nicely done.");
  await expect(panel).toContainText("3 commitments recorded complete for 2026-09-22");
  await expect(panel.getByRole("listitem").filter({hasText: "Prepare the summary"})).toContainText("Leam");
  await expect(panel.getByRole("listitem").filter({hasText: "Review the draft"})).toContainText("Owner unspecified");
  await expect(panel.locator('summary [aria-hidden="true"]').filter({hasText: "🎉"})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("Personal celebration recognises the subtask without completing its parent", async ({page}) => {
  await fixture(page, false, false, {items: [], activity: [
    {id:"step", title:"Collect receipts", parentTitle:"Prepare tax return", action:"subtask_completed", owner:"user"},
  ]});
  const panel = page.locator(".overview-celebration");
  await expect(panel).toContainText("You finished “Collect receipts” towards “Prepare tax return”.");
  await expect(panel).not.toContainText("commitment recorded complete");
  await expect(panel.getByRole("region", {name:"Completed subtasks"})).toContainText("Collect receipts");
});

test("Leam completion is not credited to the user and empty days imply no mood", async ({page}) => {
  await fixture(page, false, false, {items: [{id:"leam", title:"Gather meeting notes", owner:"leam"}], activity: []});
  const panel = page.locator(".overview-celebration");
  await expect(panel).toContainText("Leam completed “Gather meeting notes” for you.");
  await expect(panel).not.toContainText("You completed");
  await page.route("**/api/agenda?**", route => route.fulfill({json: {date:"2026-09-23", timezone:"Europe/London", commitments:[], events:[], emails:[], total:{commitments:0, events:0, emails:0}, sources:{calendar:{state:"not_connected",accounts:[]},email:{state:"not_connected",accounts:[]}}, accomplishments: {date:"2026-09-23", items:[], activity:[]}}}));
  await page.goto("/?view=today#today/plan/2026-09-23");
  await expect(panel).toContainText("Nothing recorded here for this day. You can take it at your own pace.");
  await expect(panel.locator('summary [aria-hidden="true"]').filter({hasText: "🌱"})).toBeVisible();
  await expect(panel).not.toContainText("You completed");
});

test("Wellbeing shows only explicitly user-owned activity, including independent subtask owners", async ({page}) => {
  await fixture(page, false, false, {items: [
    {id:"mine", title:"My finished task", owner:"user"},
    {id:"leam", title:"Delegated finished task", owner:"leam"},
    {id:"unknown", title:"Unassigned finished task"},
  ], activity: [
    {id:"my-step", title:"My completed step", parentTitle:"Leam parent task", owner:"user", action:"subtask_completed"},
    {id:"leam-step", title:"Delegated completed step", parentTitle:"My parent task", owner:"leam", action:"subtask_completed"},
    {id:"unknown-step", title:"Unassigned completed step", action:"subtask_completed"},
    {id:"my-start", title:"My started task", owner:"user", action:"started"},
    {id:"my-started-step",title:"My started step",parentTitle:"Delegated parent",owner:"user",action:"subtask_started"},
    {id:"leam-start", title:"Delegated started task", owner:"leam", action:"started"},
  ]});
  // Celebration remains a shared view and labels the actual step owner.
  await expect(page.locator(".overview-celebration").getByRole("listitem").filter({hasText:"Delegated completed step"})).toContainText("Leam");
  await page.goto("/?view=today#today/wellbeing/2026-09-22");
  const panel = page.locator("details").filter({has:page.locator("summary", {hasText:"Activity is context"})});
  await panel.locator("summary").click();
  for (const title of ["My finished task", "My completed step", "My started task", "My started step"])
    await expect(panel.getByText(title, {exact:true})).toBeVisible();
  for (const title of ["Delegated finished task", "Unassigned finished task", "Delegated completed step", "Unassigned completed step", "Delegated started task"])
    await expect(panel.getByText(title, {exact:true})).toHaveCount(0);
  await expect(panel.getByRole("region", {name:"Completed subtasks"})).toContainText("1 completed subtasks");
});

function activitySnapshot(items: any[], activity: any[] = []) {
  return {date:"2026-09-22", timezone:"Europe/London", commitments:[], events:[], emails:[],
    total:{commitments:0,events:0,emails:0}, sources:{calendar:{state:"not_connected",accounts:[]},email:{state:"not_connected",accounts:[]}},
    accomplishments:{date:"2026-09-22",items,activity}};
}

test("Both achievement surfaces refresh canonical activity without writes", async ({page}) => {
  const f = await fixture(page);
  let reads = 0;
  await page.route("**/api/agenda?**", async route => {
    reads++;
    await route.fulfill({json:activitySnapshot([{id:"fresh",title:`Saved achievement ${reads}`,owner:"user"}])});
  });
  await page.getByRole("button",{name:"Refresh celebrations"}).click();
  await expect(page.locator(".overview-celebration")).toContainText("Saved achievement 1");
  await page.goto("/?view=today#today/wellbeing/2026-09-22");
  const panel=page.locator("details").filter({has:page.locator("summary",{hasText:"Activity is context"})});
  await panel.locator("summary").click();
  const previous=reads;
  await panel.getByRole("button",{name:"Refresh my activity"}).click();
  await expect(panel).toContainText(`Saved achievement ${previous+1}`);
  expect(f.writes).toEqual([]);
});

test("Canonical progress events refresh visible wellbeing and keep unknown owners out", async ({page}) => {
  await fixture(page);
  await page.goto("/?view=today#today/wellbeing/2026-09-22");
  const panel=page.locator("details").filter({has:page.locator("summary",{hasText:"Activity is context"})});
  await panel.locator("summary").click();
  let reads=0;
  await page.route("**/api/agenda?**", async route => {reads++;await route.fulfill({json:activitySnapshot([], [
    {id:"step",title:"Freshly completed step",parentTitle:"My project",owner:"user",action:"subtask_completed"},
    {id:"advance",title:"Freshly started step",owner:"user",action:"subtask_started"},
    {id:"unknown",title:"Unknown owner step",action:"subtask_completed"},
  ])});});
  await page.evaluate(() => {
    const source=(window as any).__activityStreams.findLast((s:any)=>s.url==="/api/events"&&!s.closed);
    for(let i=0;i<5;i++) source.onmessage({data:JSON.stringify({topic:"commitment.subtask_status_changed",payload:{id:"card",revision:4}})});
  });
  await expect(panel).toContainText("Freshly completed step");
  await expect(panel.getByRole("region",{name:"Subtasks moved into progress"})).toContainText("Freshly started step");
  await expect(panel).not.toContainText("Unknown owner step");
  expect(reads).toBe(1);
});

test("Activity stream closes while hidden and reconciles on reconnect without polling", async ({page}) => {
  await fixture(page);
  await expect(page.locator(".overview-celebration")).toContainText("Invoice sent");
  await expect.poll(() => page.evaluate(() => (window as any).__activityStreams.filter((s:any)=>s.url==="/api/events"&&!s.closed).length)).toBe(1);
  let reads=0;
  await page.route("**/api/agenda?**", async route => {reads++;await route.fulfill({json:activitySnapshot([{id:"later",title:"Completed while away",owner:"user"}])});});
  await page.evaluate(() => {
    Object.defineProperty(document,"hidden",{configurable:true,get:()=>true});
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(await page.evaluate(() => (window as any).__activityStreams.filter((s:any)=>s.url==="/api/events").every((s:any)=>s.closed))).toBe(true);
  expect(reads).toBe(0);
  await page.evaluate(() => {
    Object.defineProperty(document,"hidden",{configurable:true,get:()=>false});
    document.dispatchEvent(new Event("visibilitychange"));
    const source=(window as any).__activityStreams.findLast((s:any)=>s.url==="/api/events"&&!s.closed);
    source.onopen();
  });
  await expect(page.locator(".overview-celebration")).toContainText("Completed while away");
  expect(reads).toBe(1);
  await page.goto("/?view=settings");
  expect(await page.evaluate(() => (window as any).__activityStreams.filter((s:any)=>s.url==="/api/events").every((s:any)=>s.closed))).toBe(true);
});

test("User-assigned activity dates remain labelled in Celebration and Wellbeing", async ({page}) => {
  await fixture(page, false, false, {items:[{id:"assigned",title:"Assigned-date task",owner:"user",completionEvidence:"user_requested_backfill"}],activity:[{id:"step",title:"Assigned-date step",owner:"user",action:"subtask_started",source:"user_requested_backfill"}]});
  const labels=page.locator(".overview-celebration small").filter({hasText:"Date assigned at your request"});
  await expect(labels).toHaveCount(2);
  await page.goto("/?view=today#today/wellbeing/2026-09-22");
  const panel=page.locator("details").filter({has:page.locator("summary",{hasText:"Activity is context"})});
  await panel.locator("summary").click();
  await expect(panel.locator("small").filter({hasText:"Date assigned at your request"})).toHaveCount(2);
});
