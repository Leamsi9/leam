import {test, expect, Page} from "@playwright/test";

async function fixture(page: Page, options: {missing?: boolean; legacy?: boolean} = {}) {
  const data: any = {job: null, writes: [], missing: options.missing, lose: false, holdPost: false, release: null, delayRead: false, releaseRead: null};
  if (options.legacy) data.job = {requestId: "11111111-1111-4111-8111-111111111111", state: "in_progress", detail: "Legacy request accepted; completion has not been confirmed.", mainThreadId: "main-1", created: 10};
  await page.addInitScript(({legacy, job}) => {
    (window as any).EventSource = class extends EventTarget {close() {}};
    if (legacy) sessionStorage.setItem("leam-backlog-triage", JSON.stringify({body: {requestId: job.requestId, sourceTicketId: "feature:backlog-triage", mainThreadId: "main-1"}, result: {state: "accepted"}}));
  }, {legacy: !!options.legacy, job: data.job});
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname, method = route.request().method();
    let body: any = {items: [], data: [], threads: []};
    if (path === "/api/auth/status") body = {authenticated: true};
    if (path === "/api/backlog") body = {items: [], ordering: {revision: 1}, review: {current: true}};
    if (path === "/api/coding/main/triage" && method === "GET") {
      if (data.delayRead) {
        data.delayRead = false;
        await new Promise<void>(resolve => {data.releaseRead = resolve;});
        return route.fulfill({status: 503, json: {detail: "Obsolete read failure"}});
      }
      body = {job: data.job};
    }
    if (path === "/api/coding/main/triage" && method === "POST") {
      const input = route.request().postDataJSON(); data.writes.push(input);
      if (data.missing) return route.fulfill({status: 409, headers: {"X-Leam-Action-Reserved": "false"}, json: {detail: "Select an available Main coordinator in Coding"}});
      data.job = {...input, state: data.lose ? "uncertain" : "in_progress", mainThreadId: "main-1", created: Date.now() / 1000, revision: 1, detail: data.lose ? "Delivery is unconfirmed; inspect the original request." : "Main accepted the request; review completion has not been confirmed."};
      if (data.holdPost) await new Promise<void>(resolve => {data.release = resolve;});
      if (data.lose) {data.lose = false; return route.abort();}
      body = data.job;
    }
    if (path.startsWith("/api/coding/main/triage/")) {
      if (path.endsWith("/reconcile")) data.job = {...data.job, state: "in_progress", detail: "Main accepted; review completion has not been confirmed."};
      body = data.job;
    }
    await route.fulfill({json: body});
  });
  await page.goto("/?view=backlog");
  const panel = page.getByRole("region", {name: "Backlog triage", exact: true});
  return {data, panel};
}

for (const width of [390, 1440]) test(`durable triage sending acceptance completion and custom priorities ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  const {data, panel} = await fixture(page);
  data.holdPost = true;
  await panel.getByRole("button", {name: "Triage backlog", exact: true}).click();
  await expect(panel.getByRole("status").getByText("Sending", {exact: true})).toBeVisible();
  await expect.poll(() => !!data.release).toBe(true);
  data.release(); data.holdPost = false;
  await expect(panel.getByRole("status").getByText("In progress", {exact: true})).toBeVisible();
  await expect(panel).toContainText("Acceptance is not completion");
  expect(data.writes).toHaveLength(1);
  expect(data.writes[0].priorities).toBe("");
  await page.reload();
  await expect(panel.getByRole("status").getByText("In progress", {exact: true})).toBeVisible();
  await expect(panel.getByRole("button", {name: "Triage backlog", exact: true})).toBeDisabled();
  expect(data.writes).toHaveLength(1);
  data.job = {...data.job, state: "completed", detail: "Reviewed and reordered with fresh canonical evidence", revision: 2, updated: 1790071200};
  await panel.getByRole("button", {name: "Refresh backlog", exact: true}).click();
  await expect(panel.getByRole("status").getByText("Completed", {exact: true})).toBeVisible();
  await expect(panel).toContainText("Completion recorded at");
  await expect(panel.locator("time")).toHaveAttribute("datetime", new Date(1790071200 * 1000).toISOString());
  await expect(panel.locator("time")).not.toBeEmpty();
  await page.reload();
  await expect(panel.getByRole("status").getByText("Completed", {exact: true})).toBeVisible();
  await expect(panel).toContainText("Completion recorded at");
  await expect(panel.locator("time")).toHaveAttribute("datetime", new Date(1790071200 * 1000).toISOString());
  expect(data.writes).toHaveLength(1);
  await panel.getByText("Alternative triage priorities", {exact: true}).click();
  await panel.getByRole("textbox").fill("Reliability and voice before new features");
  await panel.getByRole("button", {name: "Triage backlog", exact: true}).click();
  await expect.poll(() => data.writes.length).toBe(2);
  expect(data.writes[1].priorities).toBe("Reliability and voice before new features");
  expect(data.writes[1].requestId).not.toBe(data.writes[0].requestId);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("preflight failure survives reload then uncertain delivery never resends", async ({page}) => {
  const {data, panel} = await fixture(page, {missing: true});
  await panel.getByRole("button", {name: "Triage backlog", exact: true}).click();
  await expect(panel.getByRole("status").getByText("Failed", {exact: true})).toBeVisible();
  await page.reload();
  await expect(panel.getByRole("status").getByText("Failed", {exact: true})).toBeVisible();
  await expect(panel).toContainText("Select an available Main");
  data.missing = false; data.lose = true;
  await panel.getByRole("button", {name: "Triage backlog", exact: true}).click();
  await expect(panel.getByRole("status").getByText("Uncertain", {exact: true})).toBeVisible();
  expect(data.writes).toHaveLength(2);
  await page.reload();
  await expect(panel.getByRole("status").getByText("Uncertain", {exact: true})).toBeVisible();
  await expect(panel.getByRole("button", {name: "Triage backlog", exact: true})).toBeDisabled();
  await panel.getByRole("button", {name: "Check triage delivery"}).click();
  await expect(panel.getByRole("status").getByText("In progress", {exact: true})).toBeVisible();
  expect(data.writes).toHaveLength(2);
});

test("canonical legacy accepted request remains pending without automatic resend", async ({page}) => {
  const {data, panel} = await fixture(page, {legacy: true});
  await expect(panel.getByRole("status").getByText("In progress", {exact: true})).toBeVisible();
  await expect(panel.getByRole("button", {name: "Triage backlog", exact: true})).toBeDisabled();
  expect(data.writes).toHaveLength(0);
});

test("obsolete refresh failure cannot replace a newer triage outcome", async ({page}) => {
  const {data, panel} = await fixture(page);
  await panel.getByRole("button", {name: "Triage backlog", exact: true}).click();
  await expect(panel.getByRole("status").getByText("In progress", {exact: true})).toBeVisible();
  data.job = {...data.job, state: "completed", detail: "First review complete"};
  await panel.getByRole("button", {name: "Refresh backlog", exact: true}).click();
  await expect(panel.getByRole("status").getByText("Completed", {exact: true})).toBeVisible();
  data.delayRead = true;
  await panel.getByRole("button", {name: "Refresh backlog", exact: true}).click();
  await expect.poll(() => !!data.releaseRead).toBe(true);
  await panel.getByRole("button", {name: "Triage backlog", exact: true}).click();
  await expect(panel.getByRole("status").getByText("In progress", {exact: true})).toBeVisible();
  data.releaseRead();
  await page.waitForLoadState("networkidle");
  await expect(panel.getByRole("alert")).toHaveCount(0);
  expect(data.writes).toHaveLength(2);
});
