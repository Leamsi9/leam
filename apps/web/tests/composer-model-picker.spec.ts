import { test, expect, type Page } from "@playwright/test";
import { navigate, chooseConversation } from "./navigation";

const catalog = [
  { model: "gpt-5.6-sol", displayName: "Sol", supportedReasoningEfforts: [{ reasoningEffort: "medium" }, { reasoningEffort: "high" }] },
  { model: "gpt-5.6-luna", displayName: "Luna", defaultReasoningEffort: "low", supportedReasoningEfforts: [{ reasoningEffort: "low" }] },
];
async function fixture(page: Page) {
  const writes: any[] = [];
  const reads: string[] = [];
  const selections: Record<string, any> = {};
  let fail = false;
  let active = { provider_id: "openai_codex", model: "gpt-5.6-sol", reasoning_effort: "medium" };
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const post = route.request().method() === "POST";
    if (!post) reads.push(path);
    let body: any = { items: [], data: [], threads: [], messages: [], providers: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/settings/models") body = { models: catalog };
    if (path === "/api/settings/providers") body = { active, providers: [] };
    if (path === "/api/settings/providers/active") {
      const value = route.request().postDataJSON(); writes.push({ path, value });
      active = { provider_id: value.providerId, model: value.model, reasoning_effort: value.reasoningEffort };
      body = { active };
    }
    const session = path.match(/^\/api\/codex\/threads\/([^/]+)\/model$/);
    if (session) {
      const id = session[1];
      selections[id] ||= { threadId: id, generation: "gen-" + id, connected: true, model: "gpt-5.6-sol", reasoningEffort: "medium" };
      if (post) {
        const value = route.request().postDataJSON(); writes.push({ path, value });
        if (fail) { await route.fulfill({ status: 409, json: { detail: "Session model changed. Refresh before applying." } }); return; }
        selections[id] = { ...selections[id], model: value.model, reasoningEffort: value.reasoningEffort };
      }
      body = { ...selections[id], ...(post ? { accepted: true, confirmed: true } : {}) };
    }
    if (path === "/api/codex/threads") body = { data: ["a", "b"].map(id => ({ id, name: "Session " + id })) };
    if (/^\/api\/codex\/threads\/[^/]+$/.test(path)) body = { thread: { id: path.split("/").at(-1), turns: [] }, connected: true };
    if (path === "/api/companion/threads") body = { threads: [{ thread_id: "main", title: "Main" }] };
    if (path === "/api/companion/system") body = { available: true, activeModel: active };
    if (path.includes("/agenda/") && path.endsWith("/chat")) body = { threadId: "today-fixture" };
    if (path === "/api/commitments/c1/chat") body = { threadId: "goal-fixture" };
    if (path === "/api/commitments" || path === "/api/today") body = { items: [{ id: "c1", title: "Walk", kind: "task", status: "active", revision: 1, capacityId: "health", measure: "boolean", target: 1, ...(path === "/api/today" ? { date: "2026-09-22", log: { value: 0, done: false, revision: 0 } } : {}) }] };
    if (path === "/api/capacities") body = { items: [{ id: "health", name: "Health", revision: 1 }] };
    if (path === "/api/updates") body = { items: [{ id: "ticket", title: "Fixture update", summary: "Model controls", stage: "UAT", revision: 1, sequence: 1, deploymentId: "fixture", deployedAt: "2026-09-22T20:00:00Z", qa: { state: "passed" }, uat: { state: "pending" } }], unreadCount: 0, nextCursor: null };
    if (path === "/api/updates/ticket/chat") body = { state: "ready", threadId: "ticket-session", connected: true };
    await route.fulfill({ json: body });
  });
  return { writes, reads, fail: () => { fail = true; } };
}
async function openPicker(page: Page) {
  await page.getByRole("button", { name: "Model and reasoning", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Model and reasoning" });
  await expect(dialog.getByTestId("actual-model")).toContainText("gpt-5.6-sol");
  await expect(dialog.getByLabel("Chat model")).toBeEnabled();
  return dialog;
}

test("Coding picker targets current session, keeps draft and switches identities", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await fixture(page);
  await page.goto("/"); await navigate(page, "Coding");
  await chooseConversation(page, "a", "coding");
  const input = page.locator(".composer textarea");
  await input.fill("Preserved coding draft");
  expect(state.reads.some(path => path.endsWith("/model"))).toBe(false);
  let dialog = await openPicker(page);
  await expect(dialog).toContainText("This coding session");
  await dialog.getByLabel("Chat model").selectOption("gpt-5.6-luna");
  await expect(dialog.getByLabel("Chat reasoning")).toHaveValue("low");
  await dialog.getByRole("button", { name: "Apply to this session" }).click();
  await expect(dialog.getByRole("status")).toContainText("Saved for this session");
  await dialog.getByRole("button", { name: "Close model and reasoning" }).click();
  await expect(input).toHaveValue("Preserved coding draft");
  await expect(page.getByRole("button", { name: "Model and reasoning", exact: true })).toBeFocused();
  await chooseConversation(page, "b", "coding");
  dialog = await openPicker(page);
  await dialog.getByLabel("Chat reasoning").selectOption("high");
  await dialog.getByRole("button", { name: "Apply to this session" }).click();
  await expect(dialog.getByRole("status")).toContainText("Saved for this session");
  expect(state.writes).toEqual([
    { path: "/api/codex/threads/a/model", value: { model: "gpt-5.6-luna", reasoningEffort: "low", generation: "gen-a", expectedModel: "gpt-5.6-sol", expectedReasoningEffort: "medium" } },
    { path: "/api/codex/threads/b/model", value: { model: "gpt-5.6-sol", reasoningEffort: "high", generation: "gen-b", expectedModel: "gpt-5.6-sol", expectedReasoningEffort: "medium" } },
  ]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("ticket chat uses its actual Codex session rather than ticket identity", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/"); await navigate(page, "Updates");
  await page.getByText("Chat about this update", { exact: true }).click();
  await page.getByLabel("Message about this update").fill("Ticket draft");
  const dialog = await openPicker(page);
  await dialog.getByLabel("Chat reasoning").selectOption("high");
  await dialog.getByRole("button", { name: "Apply to this session" }).click();
  await expect(dialog.getByRole("status")).toContainText("Saved for this session");
  expect(state.writes[0].path).toBe("/api/codex/threads/ticket-session/model");
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("Message about this update")).toHaveValue("Ticket draft");
});

for (const surface of ["Companion", "Today", "Goals"]) test(`${surface} picker labels shared defaults and preserves draft`, async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 });
  const state = await fixture(page);
  await page.goto(surface === "Today" ? "/?view=today#today/chat/2026-09-22" : "/");
  if (surface === "Companion") { await navigate(page, "Companion"); await chooseConversation(page, "main"); }
  if (surface === "Goals") { await navigate(page, "Goals"); await page.getByText("Chat about Walk", { exact: true }).click(); }
  await page.getByLabel("Message Leam", { exact: true }).fill("Draft from " + surface);
  const dialog = await openPicker(page);
  await expect(dialog).toContainText("Shared defaults · Companion, Today and Goals");
  await dialog.getByLabel("Chat reasoning").selectOption("high");
  await dialog.getByRole("button", { name: "Apply shared defaults" }).click();
  await expect(dialog.getByRole("status")).toContainText("Saved for new Companion");
  expect(state.writes).toEqual([{ path: "/api/settings/providers/active", value: { providerId: "openai_codex", model: "gpt-5.6-sol", reasoningEffort: "high" } }]);
  await dialog.getByRole("button", { name: "Close model and reasoning" }).click();
  await expect(page.getByLabel("Message Leam", { exact: true })).toHaveValue("Draft from " + surface);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("rejected configuration stays visible and requires refresh without automatic replay", async ({ page }) => {
  const state = await fixture(page); state.fail();
  await page.goto("/"); await navigate(page, "Coding"); await chooseConversation(page, "a", "coding");
  const dialog = await openPicker(page);
  await dialog.getByLabel("Chat reasoning").selectOption("high");
  await dialog.getByRole("button", { name: "Apply to this session" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Session model changed");
  await expect(dialog.getByRole("button", { name: "Apply to this session" })).toBeDisabled();
  await expect(dialog.getByTestId("actual-model")).toContainText("medium reasoning");
  expect(state.writes).toHaveLength(1);
  await dialog.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(dialog.getByLabel("Chat reasoning")).toHaveValue("medium");
  expect(state.writes).toHaveLength(1);
});
