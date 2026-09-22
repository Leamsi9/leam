import { test, expect } from "@playwright/test";
import { chooseConversation } from "./navigation";

// Controlled transport fixtures; real owner behavior is separately covered at HTTP/socket tier.
test("mobile selects exact Main and sends an explicit source task without sending discussion", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let main: any = null;
  const selections: any[] = [];
  const handoffs: any[] = [];
  const ordinary: any[] = [];
  await page.addInitScript(() => {
    (window as any).EventSource = class {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    let body: any = { data: [], items: [] };
    if (path === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (path === "/api/codex/shared-thread")
      body = { configured: false, thread: null };
    if (path === "/api/codex/threads")
      body = {
        data: [
          {
            id: "main-session",
            name: "Build coordinator",
            leamMain: main?.threadId === "main-session",
          },
          { id: "discussion", name: "Ticket discussion" },
        ],
      };
    if (/\/codex\/threads\/(main-session|discussion)$/.test(path))
      body = { connected: true };
    if (path === "/api/coding/main") {
      if (method === "PUT") {
        const input = route.request().postDataJSON();
        selections.push(input);
        main = {
          threadId: input.threadId,
          name: "Build coordinator",
          transport: "native",
          revision: 1,
        };
      }
      body = {
        main,
        bindingValid: !!main,
        permissionEnforcement: "instructions-only",
        limitation: "Coordination instructions are not a permission sandbox.",
      };
    }
    if (path === "/api/coding/main/handoffs") {
      const input = route.request().postDataJSON();
      handoffs.push(input);
      body = {
        ...input,
        state: "accepted",
        receipt: { operation: "steer", turn: { id: "active-main" } },
      };
    }
    if (method === "POST" && /\/codex\/threads\/.*\/turns$/.test(path))
      ordinary.push(route.request().postDataJSON());
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await chooseConversation(page, "main-session", "coding");
  await page
    .getByRole("button", { name: "Main coding coordinator", exact: true })
    .click();
  let control = page.getByRole("dialog", { name: "Main coding coordinator" });
  await control.getByText("Use this session as Main", { exact: true }).click();
  await control.getByRole("checkbox").check();
  await control.getByRole("button", { name: "Make main", exact: true }).click();
  await expect(control).toContainText("Main · coordinator");
  expect(selections).toEqual([
    { threadId: "main-session", expectedRevision: 0, confirmed: true },
  ]);
  await page.keyboard.press("Escape");
  await chooseConversation(page, "discussion", "coding");
  control = page.getByRole("dialog", { name: "Main coding coordinator" });
  await page
    .getByRole("textbox", { name: "Message Codex" })
    .fill("Implement the reviewed fix");
  await page
    .getByRole("button", { name: "Main coding coordinator", exact: true })
    .click();
  await control
    .getByRole("button", { name: "Send to main", exact: true })
    .click();
  await expect(control.getByLabel("Implementation task")).toHaveValue(
    "Implement the reviewed fix",
  );
  await control
    .getByLabel("Concise context (optional)")
    .fill("One bounded requirement");
  expect(handoffs).toHaveLength(0);
  await control.getByRole("button", { name: "Confirm send to main" }).click();
  await expect(control.getByRole("status")).toContainText(
    "Accepted by Main as an active-turn follow-up",
  );
  expect(handoffs).toHaveLength(1);
  expect(handoffs[0]).toMatchObject({
    mainThreadId: "main-session",
    mainRevision: 1,
    sourceThreadId: "discussion",
    text: "Implement the reviewed fix",
    context: "One bounded requirement",
  });
  expect(ordinary).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(control).toBeVisible();
});
