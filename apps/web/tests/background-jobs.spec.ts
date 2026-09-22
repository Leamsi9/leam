import { test, expect, type Page } from "@playwright/test";
import { chooseConversation } from "./navigation";

async function fixture(page: Page) {
  const state = { jobs: [] as any[], writes: [] as any[], lose: false };
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname, method = route.request().method();
    const body = method === "GET" ? null : route.request().postDataJSON();
    let data: any = { items: [], data: [], threads: [], messages: [] };
    if (path === "/api/auth/status") data = { authenticated: true };
    if (path === "/api/companion/threads") data = { threads: [{ thread_id: "a", title: "My conversation" }] };
    if (path === "/api/companion/threads/a") data = { messages: [{ message_id: "m", kind: "assistant", content: "How can I help?", status: "finalized", sequence: 1 }] };
    if (path === "/api/companion/jobs" && method === "GET") data = { items: state.jobs };
    if (path === "/api/companion/jobs" && method === "POST") {
      state.writes.push(body);
      if (state.lose) { state.lose = false; return route.abort(); }
      data = { id: body.requestId, title: body.title, state: "queued", revision: 1, proposalIds: [], status: "Queued separately" };
      state.jobs = [data];
    }
    if (path.startsWith("/api/companion/jobs/")) {
      const job = state.jobs.find(item => item.id === path.split("/").at(-1));
      if (method === "POST") { state.writes.push({ path, ...body }); job.state = "cancelled"; job.revision++; }
      data = { ...job, result: job.state === "completed" ? "Saved report" : null };
    }
    if (path.endsWith("/messages") && method === "POST") { state.writes.push({ path, ...body }); data = { outcome: "submitted", thread_id: "a", run_id: "7461168f-d5b4-40c5-9fd7-4bd0c277dafb" }; }
    await route.fulfill({ json: data });
  });
  await page.goto("/?view=companion");
  await chooseConversation(page, "a");
  return state;
}

test("background request has its own receipt and keeps foreground composer available", async ({ page }) => {
  const state = await fixture(page);
  await page.getByRole("button", { name: "Background work", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Background work" });
  await dialog.getByText("Start a background task", { exact: true }).click();
  await dialog.getByLabel("Explicit task").fill("Create my report while we keep chatting");
  await dialog.getByRole("button", { name: "Run in background" }).click();
  await expect(dialog.getByText(/Create my report while we keep chatting · queued/)).toBeVisible();
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0].threadId).toBe("a");
  await dialog.getByRole("button", { name: "Close Background work" }).click();
  await expect(page.getByText("How can I help?", { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message Leam", exact: true })).toBeEnabled();
  await page.getByRole("textbox", { name: "Message Leam", exact: true }).fill("What else is on today?");
  await expect(page.getByRole("button", { name: "Send to Leam", exact: true })).toBeEnabled();
});

test("uncertain job start retries exact UUID without losing its task", async ({ page }) => {
  const state = await fixture(page); state.lose = true;
  await page.getByRole("button", { name: "Background work", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Background work" });
  await dialog.getByText("Start a background task", { exact: true }).click();
  await dialog.getByLabel("Explicit task").fill("Prepare a report");
  await dialog.getByRole("button", { name: "Run in background" }).click();
  await expect(dialog.getByRole("alert")).toContainText("exact request is kept");
  await dialog.getByRole("button", { name: "Retry exact task" }).click();
  expect(state.writes).toHaveLength(2);
  expect(state.writes[0]).toEqual(state.writes[1]);
});

test("job cancellation uses job revision and never foreground Stop", async ({ page }) => {
  const state = await fixture(page);
  state.jobs = [{ id: "job-a", title: "Report", state: "running", revision: 4, proposalIds: [], status: "Working separately" }];
  await page.getByRole("button", { name: "Background work", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Background work" });
  await dialog.getByRole("button", { name: "Refresh background status" }).click();
  await dialog.getByText("Report · running", { exact: true }).click();
  await dialog.getByRole("button", { name: "Cancel background job" }).click();
  expect(state.writes).toEqual([{ path: "/api/companion/jobs/job-a", revision: 4, action: "cancel" }]);
  await expect(dialog.getByText("Report · cancelled", { exact: true })).toBeVisible();
});
