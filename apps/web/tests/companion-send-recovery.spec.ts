import { test, expect, type Page } from "@playwright/test";
import { chooseConversation } from "./navigation";
const run = "11111111-1111-4111-8111-111111111111";
const fileId = "11111111-1111-4111-8111-111111111122";
async function setup(page: Page, today = false, restored = false) {
  const state = {
    posts: [] as any[],
    reads: [] as string[],
    provenNo: false,
    accept: false,
    receipt: "pending",
    lookupStatus: 200,
  };
  await page.addInitScript(
    ({ restored }) => {
      (window as any).EventSource = class extends EventTarget {
        close() {}
      };
      if (restored)
        sessionStorage.setItem(
          "leam-companion-submission:a",
          JSON.stringify({
            thread: "a",
            id: "old-request-id",
            text: "An old uncertain message",
            attachmentIds: [],
          }),
        );
    },
    { restored },
  );
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname;
    let body: any = {
      items: [],
      data: [],
      threads: [],
      messages: [],
      providers: [],
      models: [],
    };
    if (path === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (path === "/api/companion/threads")
      body = { threads: [{ thread_id: "a", title: "Recovery chat" }] };
    if (path === "/api/agenda/chat") body = { threadId: "a" };
    if (path === "/api/agenda") {
      const date = url.searchParams.get("date");
      body = {
        date,
        timezone: "Europe/London",
        observedAt: Date.now() / 1000,
        window: { start: `${date}T00:00:00Z`, end: `${date}T23:59:59Z` },
        commitments: [],
        events: [],
        emails: [],
        total: { commitments: 0, events: 0, emails: 0 },
        nextOffset: null,
        partial: false,
        sources: {
          calendar: { state: "not_connected", accounts: [], snapshots: [] },
          email: { state: "not_connected", accounts: [] },
        },
      };
    }
    if (path === "/api/agenda/reconciliation")
      body = {
        items: [],
        coverage: {
          enabledAt: 1,
          scope: "New Today exchanges since enabledAt",
          state: "idle",
        },
      };
    if (path === "/api/attachments")
      body = {
        id: fileId,
        filename: "kept.txt",
        mimeType: "text/plain",
        sizeBytes: 4,
        state: "uploaded",
        sha256: "fixture",
      };
    if (path === "/api/proposals/status")
      body = { pendingCount: 0, unreadCount: 0 };
    if (path === "/api/companion/threads/a/messages") {
      state.posts.push(route.request().postDataJSON());
      if (!state.accept)
        return route.fulfill({
          status: state.provenNo ? 400 : 503,
          headers: state.provenNo ? { "X-Leam-Action-Reserved": "no" } : {},
          json: { detail: "Provider could not receive this request" },
        });
      body = { outcome: "submitted", run_id: run, thread_id: "a" };
    }
    if (path.includes("/submissions/")) {
      state.reads.push(path);
      if (state.lookupStatus !== 200)
        return route.fulfill({
          status: state.lookupStatus,
          json: { detail: "Receipt unavailable" },
        });
      body =
        state.receipt === "recorded"
          ? {
              state: "recorded",
              receipt: { outcome: "submitted", run_id: run, thread_id: "a" },
            }
          : { state: state.receipt };
    }
    await route.fulfill({ json: body });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/?view=${today ? "today" : "companion"}`);
  if (today)
    await page.getByRole("navigation", { name: "Today pages" }).getByRole("link", { name: "Chat", exact: true }).click();
  else await chooseConversation(page, "a");
  await expect(page.getByLabel("Message Leam")).toBeVisible();
  return state;
}
async function attach(page: Page) {
  await page.getByLabel("Attach files", { exact: true }).evaluate(input => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["keep"], "kept.txt", { type: "text/plain" }));
    (input as HTMLInputElement).files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await expect(
    page.getByRole("list", { name: "Attached files" }),
  ).toContainText("kept.txt");
}

test("Today proven predispatch rejection unlocks the unchanged draft and attachments", async ({
  page,
}) => {
  const state = await setup(page, true);
  state.provenNo = true;
  const draft = page.getByLabel("Message Leam");
  await draft.fill("Continue planning my day");
  await attach(page);
  await page.getByRole("button", { name: "Send to Leam", exact: true }).click();
  const recovery = page.getByRole("complementary", {
    name: "Message delivery recovery",
  });
  await expect(recovery).toContainText("Not sent.");
  await expect(draft).toBeEditable();
  await expect(draft).toHaveValue("Continue planning my day");
  await expect(
    page.getByRole("button", { name: "Remove kept.txt" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Send to Leam", exact: true }),
  ).toBeEnabled();
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("leam-companion-submission:a"),
    ),
  ).toBeNull();
  expect(state.posts).toHaveLength(1);
  expect(state.reads).toHaveLength(0);
  const bounds = await recovery.boundingBox();
  expect(bounds!.y + bounds!.height).toBeLessThan(844);
});

test("unknown404 keeps identity without replay and explicit retry reuses exact body", async ({
  page,
}) => {
  const state = await setup(page);
  state.lookupStatus = 404;
  await page.getByLabel("Message Leam").fill("Keep this request");
  await attach(page);
  await page.getByRole("button", { name: "Send to Leam", exact: true }).click();
  const recovery = page.getByRole("complementary", {
    name: "Message delivery recovery",
  });
  await expect(recovery).toContainText("Receipt not found");
  await expect(page.getByLabel("Message Leam")).not.toBeEditable();
  await expect(
    page.getByRole("button", { name: "Send to Leam", exact: true }),
  ).toBeDisabled();
  await recovery.getByRole("button", { name: "Check saved receipt" }).click();
  await expect(recovery).toContainText("does not prove");
  expect(state.posts).toHaveLength(1);
  const stored = await page.evaluate(() =>
    JSON.parse(sessionStorage.getItem("leam-companion-submission:a")!),
  );
  expect(stored.id).toBe(state.posts[0].requestId);
  state.accept = true;
  await recovery.getByRole("button", { name: "Retry saved message" }).click();
  await expect(page.getByLabel("Message Leam")).toHaveValue("");
  expect(state.posts).toHaveLength(2);
  expect(state.posts[1]).toEqual(state.posts[0]);
});

test("Keep editing preserves attachments and archives identity; old receipt cannot clear new draft", async ({
  page,
}) => {
  const state = await setup(page);
  await page.getByLabel("Message Leam").fill("Original uncertain draft");
  await attach(page);
  await page.getByRole("button", { name: "Send to Leam", exact: true }).click();
  const recovery = page.getByRole("complementary", {
    name: "Message delivery recovery",
  });
  await expect(recovery).toContainText("Receipt pending");
  page.once("dialog", (dialog) => dialog.accept());
  await recovery.getByRole("button", { name: "Keep editing" }).click();
  await expect(page.getByLabel("Message Leam")).toHaveValue(
    "Original uncertain draft",
  );
  await expect(
    page.getByRole("button", { name: "Remove kept.txt" }),
  ).toBeEnabled();
  await page.getByLabel("Message Leam").fill("Edited draft, not sent");
  const archived = await page.evaluate(() =>
    JSON.parse(sessionStorage.getItem("leam-companion-receipt-history:a")!),
  );
  expect(archived[0].id).toBe(state.posts[0].requestId);
  state.receipt = "recorded";
  await page
    .getByRole("button", { name: "Companion chat options", exact: true })
    .click();
  const options = page.getByRole("dialog", { name: "Companion chat options" });
  await options
    .getByText("Earlier message receipts (1/8)", { exact: true })
    .click();
  await options.getByRole("button", { name: "Check earlier receipt" }).click();
  await expect(options).toContainText("Delivery confirmed");
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("Message Leam")).toHaveValue(
    "Edited draft, not sent",
  );
  await expect(
    page.getByRole("list", { name: "Attached files" }),
  ).toContainText("kept.txt");
  expect(state.posts).toHaveLength(1);
});

test("old Today attempt restores pending receipt visibly and retries only on explicit action", async ({
  page,
}) => {
  const state = await setup(page, true, true);
  const recovery = page.getByRole("complementary", {
    name: "Message delivery recovery",
  });
  await expect(recovery).toContainText("Receipt pending");
  await expect(page.getByLabel("Message Leam")).toHaveValue(
    "An old uncertain message",
  );
  expect(state.posts).toHaveLength(0);
  state.lookupStatus = 503;
  await recovery.getByRole("button", { name: "Check saved receipt" }).click();
  await expect(recovery).toContainText("Receipt unavailable");
  expect(state.posts).toHaveLength(0);
  state.accept = true;
  await recovery.getByRole("button", { name: "Retry saved message" }).click();
  await expect(page.getByLabel("Message Leam")).toHaveValue("");
  expect(state.posts[0]).toMatchObject({
    requestId: "old-request-id",
    text: "An old uncertain message",
    attachmentIds: [],
  });
});
