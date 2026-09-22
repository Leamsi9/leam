import { test, expect, type Page } from "@playwright/test";
import { navigate, chooseConversation } from "./navigation";

async function fixture(page: Page) {
  let unavailable = true, error = false, checks = 0;
  let release: (() => void) | undefined;
  let gate: Promise<void> | undefined;
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget { close() {} };
  });
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/shared-thread") body = { configured: false };
    if (path === "/api/codex/threads") {
      checks++;
      if (gate) await gate;
      if (error) return route.fulfill({ status: 503, json: { detail: "Session list temporarily unavailable" } });
      body = { data: [{ id: "a", name: "Thread A", cwd: "/workspace" }], leamHandoffsUnavailable: unavailable, nextCursor: null };
    }
    if (path === "/api/codex/threads/a") body = { connected: true, thread: { id: "a", name: "Thread A", cwd: "/workspace" } };
    if (path === "/api/codex/threads/a/turns") body = { data: [{ id: "old", status: "completed", items: [{ id: "reply", type: "agentMessage", text: "Existing conversation output" }] }] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await navigate(page, "Coding");
  return {
    checks: () => checks,
    success: () => { unavailable = false; error = false; },
    fail: () => { error = true; },
    hold: () => { gate = new Promise<void>(resolve => { release = resolve; }); },
    release: () => { gate = undefined; release?.(); },
  };
}

for (const width of [390, 1440]) test(`catalog warning stays in session list and clears after a successful retry ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 844 });
  const f = await fixture(page);
  const list = page.getByRole("region", { name: "Coding sessions", exact: true });
  const warning = page.getByText("Some handoff sessions could not be checked.", { exact: true });
  await expect(list.getByText("Some handoff sessions could not be checked.", { exact: true })).toBeVisible();
  await expect(list.locator("time")).toHaveAttribute("datetime", /T/);
  await chooseConversation(page, "a", "coding");
  await expect(warning).toHaveCount(0);
  await expect(page.getByText("Existing conversation output", { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Message Codex", exact: true }).fill("Keep my draft");
  await list.getByRole("button", { name: "Thread A", exact: true }).click();
  await expect(warning).toBeVisible();
  f.hold(); f.success();
  await list.getByRole("button", { name: "Refresh session list", exact: true }).click();
  await expect(list.getByRole("button", { name: "Refresh session list", exact: true })).toBeDisabled();
  await expect(list.getByRole("button", { name: "Refresh session list", exact: true })).toHaveText("Checking sessions…");
  f.release();
  await expect(warning).toHaveCount(0);
  await expect(list.getByRole("button", { name: "Refresh session list", exact: true })).toBeEnabled();
  await expect(page.getByRole("textbox", { name: "Message Codex", exact: true })).toHaveValue("Keep my draft");
  expect(f.checks()).toBeGreaterThanOrEqual(2);
});

test("catalog fetch failure is scoped and retriable without replacing current chat", async ({ page }) => {
  const f = await fixture(page);
  await chooseConversation(page, "a", "coding");
  const list = page.getByRole("region", { name: "Coding sessions", exact: true });
  await list.getByRole("button", { name: "Thread A", exact: true }).click();
  f.fail();
  await list.getByRole("button", { name: "Refresh session list", exact: true }).click();
  await expect(list.getByRole("alert")).toHaveText("Session list temporarily unavailable");
  await expect(list.getByText("Last attempt:", { exact: false })).toBeVisible();
  await list.getByRole("button", { name: "Thread A", exact: true }).click();
  await expect(page.getByText("Session list temporarily unavailable", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Existing conversation output", { exact: true })).toBeVisible();
  await list.getByRole("button", { name: "Thread A", exact: true }).click();
  f.success();
  await list.getByRole("button", { name: "Refresh session list", exact: true }).click();
  await expect(list.getByRole("alert")).toHaveCount(0);
  await expect(list.getByText("Last check:", { exact: false })).toBeVisible();
});
