import {test, expect, Page} from "@playwright/test";
import {chooseConversation} from "./navigation";
const old1 = "11111111-1111-4111-8111-111111111111", old2 = "22222222-2222-4222-8222-222222222222", current = "33333333-3333-4333-8333-333333333333";
const user = (id: string, sequence: number) => ({message_id: "message-" + id, turn_run_id: id, kind: "user", status: "submitted", sequence, content: "Request " + sequence});
async function setup(page: Page, messages: any[]) {
  const cancels: any[] = [];
  let release: (() => void) | null = null;
  await page.addInitScript(() => {
    const w = window as any;
    w.sources = {};
    w.EventSource = class extends EventTarget {
      constructor(url: string) { super(); if (url.includes("/companion/")) w.sources[url.includes("/threads/a/") ? "a" : "b"] = this; }
      close() {}
    };
  });
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {items: [], models: [], providers: []};
    if (path === "/api/auth/status") body = {authenticated: true};
    if (path === "/api/companion/threads") body = {threads: [{thread_id: "a", title: "Current"}, {thread_id: "b", title: "Other"}]};
    if (path === "/api/companion/threads/a") body = {messages};
    if (path === "/api/companion/threads/b") body = {messages: []};
    if (path.endsWith("/cancel")) {
      cancels.push({path, ...route.request().postDataJSON()});
      await new Promise<void>(resolve => {release = resolve;});
      body = {run_id: current, status: "CancelRequested"};
    }
    await route.fulfill({json: body});
  });
  await page.goto("/?view=companion");
  await chooseConversation(page, "a");
  await page.waitForFunction(() => !!(window as any).sources.a);
  return {cancels, release: () => release?.()};
}
async function project(page: Page, ids: string[], status = "running", thread = "a") {
  await page.evaluate(({ids, status, thread}) => (window as any).sources[thread].dispatchEvent(new MessageEvent("projection_update", {data: JSON.stringify({type: "projection_update", state: {thread_id: thread, items: ids.map(id => ({run_status: {run_id: id, status}}))}})})), {ids, status, thread});
}

for (const width of [390, 844]) test(`one exact current stop despite historical missing terminals at ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: width === 390 ? 844 : 390});
  const fixture = await setup(page, [user(old1, 1), user(old2, 2), user(current, 3)]);
  // Deliberately newest first: arrival/insertion order cannot identify current run.
  await project(page, [current, old2, old1]);
  const control = page.locator(".composer .companion-stop");
  await expect(control).toHaveCount(1);
  await expect(control).toHaveText("Stop response");
  await expect(control).toBeInViewport();
  await control.click();
  await expect.poll(() => fixture.cancels.length).toBe(1);
  expect(fixture.cancels[0].path).toBe(`/api/companion/threads/a/runs/${current}/cancel`);
  await expect(control).toHaveText("Requesting stop…");
  await expect(control).toBeDisabled();
  fixture.release();
  await expect(control).toHaveText("Stop requested…");
  await expect(control).toBeDisabled();
  await project(page, [current], "cancelled");
  await expect(control).toHaveCount(0); // Never fall back to an old unfinished projection.
});

test("finalized latest answer and ambiguous unanchored snapshots do not expose stale stop", async ({page}) => {
  await setup(page, [user(old1, 1), user(current, 3), {message_id: "answer", turn_run_id: current, kind: "assistant", status: "finalized", sequence: 4, content: "Done"}]);
  await project(page, [old1, old2, current]);
  await expect(page.locator(".companion-stop")).toHaveCount(0);
  await chooseConversation(page, "b");
  await page.waitForFunction(() => !!(window as any).sources.b);
  await project(page, [old1, old2], "running", "b");
  await expect(page.locator(".companion-stop")).toHaveCount(0);
});

test("thread change hides previous stop even while cancellation is pending", async ({page}) => {
  const fixture = await setup(page, [user(current, 1)]);
  await project(page, [current]);
  await page.getByRole("button", {name: "Stop response", exact: true}).click();
  await expect.poll(() => fixture.cancels.length).toBe(1);
  await chooseConversation(page, "b");
  await expect(page.locator(".companion-stop")).toHaveCount(0);
  fixture.release();
  await expect(page.locator(".companion-stop")).toHaveCount(0);
  expect(fixture.cancels).toHaveLength(1);
});


test("a rejected followup cannot hide the actual active response stop", async ({page}) => {
  await setup(page, [user(old1, 1), user(current, 2), {message_id: "rejected", kind: "user", status: "rejected_busy", sequence: 3, content: "Not admitted", turn_run_id: null}]);
  await project(page, [current, old1]);
  await expect(page.locator(".composer .companion-stop")).toHaveCount(1);
  await expect(page.getByRole("button", {name: "Stop response", exact: true})).toBeEnabled();
});
