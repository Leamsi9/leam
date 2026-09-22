import { test, expect } from "@playwright/test";
import { chooseConversation } from "./navigation";

test("child records load on demand and explicit handover remains distinct from completion", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let reads = 0,
    sends = 0;
  const main = {
    threadId: "main-session",
    name: "Coordinator",
    revision: 1,
    transport: "native",
  };
  const row: any = {
    id: "job-1",
    feature: "bounded-feature",
    worker: "worker-one",
    childThreadId: "child-session",
    childTurnId: "child-turn",
    revision: 1,
    main,
    observation: { status: "completed", observedAt: 1790065138 },
    handover: null,
  };
  await page.addInitScript(() => {
    (window as any).EventSource = class {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { data: [], items: [] };
    if (path === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (path === "/api/codex/shared-thread")
      body = { configured: false, thread: null };
    if (path === "/api/codex/threads")
      body = {
        data: [{ id: "main-session", name: "Coordinator", leamMain: true }],
      };
    if (path === "/api/codex/threads/main-session") body = { connected: true };
    if (path === "/api/coding/main") body = { main, bindingValid: true };
    if (path === "/api/coding/main/children") {
      reads++;
      body = { items: [row], nextOffset: null };
    }
    if (path === "/api/coding/main/children/job-1/handover") {
      sends++;
      const input = route.request().postDataJSON();
      expect(input).toMatchObject({
        expectedRevision: 1,
        summary: "Review commit; not deployed",
        evidence: "commit abc123",
      });
      row.handover = { delivery: { state: "accepted" } };
      body = row;
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await chooseConversation(page, "main-session", "coding");
  await page
    .getByRole("button", { name: "Main coding coordinator", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Main coding coordinator" });
  expect(reads).toBe(0);
  await dialog.getByText("Child handovers", { exact: true }).click();
  await dialog
    .getByText("bounded-feature · completed", { exact: true })
    .click();
  await expect(dialog).toContainText("Turn status is not task completion");
  expect(sends).toBe(0);
  await dialog
    .getByLabel("Handover for Main")
    .fill("Review commit; not deployed");
  await dialog
    .getByLabel("Evidence references (optional)")
    .fill("commit abc123");
  await dialog
    .getByRole("button", { name: "Send handover to original Main" })
    .click();
  await expect(dialog).toContainText("handover accepted");
  await expect(dialog).toContainText("not reviewed, deployed or complete");
  expect(sends).toBe(1);
  await dialog.getByRole("button", { name: "Refresh records" }).click();
  expect(sends).toBe(1);
});
