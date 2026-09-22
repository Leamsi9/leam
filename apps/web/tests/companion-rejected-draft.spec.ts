import { test, expect, type Page } from "@playwright/test";
import { chooseConversation } from "./navigation";
async function setup(page: Page, status = "rejected_busy", attachment = false) {
  const writes: any[] = [];
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (method !== "GET")
      writes.push({ path, body: route.request().postData() });
    let data: any = { items: [], data: [], threads: [], messages: [] };
    if (path === "/api/auth/status") data = { authenticated: true };
    if (path === "/api/companion/threads")
      data = {
        threads: [
          { thread_id: "a", title: "A" },
          { thread_id: "b", title: "B" },
        ],
      };
    if (path === "/api/companion/threads/a")
      data = {
        messages: [
          {
            message_id: "queued-original",
            kind: "user",
            status,
            content: "Preserved queued follow-up",
            turn_run_id: "cancelled-run",
            sequence: 4,
            ...(attachment
              ? {
                  leamAttachments: [{ id: "file-a", filename: "original.txt" }],
                }
              : {}),
          },
        ],
      };
    if (path === "/api/attachments")
      data = {
        id: "file-new",
        filename: "draft.txt",
        mimeType: "text/plain",
        sizeBytes: 4,
        state: "uploaded",
        sha256: "fixture",
      };
    await route.fulfill({ json: data });
  });
  await page.goto("/?view=companion");
  await chooseConversation(page, "a");
  await expect(
    page.getByText("Preserved queued follow-up", { exact: true }),
  ).toBeVisible();
  return writes;
}
test("canonical rejected queued text returns to draft only; files require explicit reattachment", async ({
  page,
}) => {
  const writes = await setup(page, "rejected_busy", true);
  await expect(page.getByLabel("Unprocessed message")).toContainText(
    "was not processed",
  );
  await page.getByRole("button", { name: "Use as draft" }).click();
  await expect(page.getByRole("textbox", { name: "Message Leam" })).toHaveValue(
    "Preserved queued follow-up",
  );
  await expect(page.getByLabel("Unprocessed message")).toContainText(
    "Original files are not restored",
  );
  expect(writes).toEqual([]);
});
test("existing draft and attachment survive preparation and thread navigation", async ({
  page,
}) => {
  const writes = await setup(page);
  const draft = page.getByRole("textbox", { name: "Message Leam" });
  await draft.fill("My newer draft");
  await page
    .locator('input[type="file"]')
    .setInputFiles({
      name: "draft.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("keep"),
    });
  await expect(page.getByText("draft.txt", { exact: true })).toBeVisible();
  const count = writes.length;
  await page.getByRole("button", { name: "Use as draft" }).click();
  await expect(draft).toHaveValue("My newer draft");
  await expect(page.getByLabel("Unprocessed message")).toContainText(
    "existing draft and attachments are preserved",
  );
  await expect(page.getByText("draft.txt", { exact: true })).toBeVisible();
  expect(writes).toHaveLength(count);
  await chooseConversation(page, "b");
  await expect(page.getByRole("button", { name: "Use as draft" })).toHaveCount(
    0,
  );
  await expect(draft).toHaveValue("");
  await chooseConversation(page, "a");
  await expect(draft).toHaveValue("My newer draft");
  expect(writes.filter((item) => item.path.endsWith("/messages"))).toEqual([]);
});
test("queued but not rejected canonical input cannot be prepared for duplicate delivery", async ({
  page,
}) => {
  const writes = await setup(page, "queued");
  await expect(page.getByRole("button", { name: "Use as draft" })).toHaveCount(
    0,
  );
  expect(writes).toEqual([]);
});
