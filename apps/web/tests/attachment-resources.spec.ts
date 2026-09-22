import { test, expect, type Page } from "@playwright/test";
import { chooseConversation } from "./navigation";

const item = {
  id: "attachment-11111111-1111-4111-8111-111111111111",
  title: "Planning notes.txt",
  filename: "Planning notes.txt",
  kind: "text",
  category: "attachment",
  tags: ["Attachment"],
  content: "Private fixture notes",
  sha256: "a".repeat(64),
  bytes: 21,
  publishedAt: 1790000000,
  unread: true,
  sources: [
    { surface: "companion", threadId: "fixture", turnId: "source-message" },
  ],
};
async function fixture(page: Page) {
  const state = { exists: true, deleted: [] as any[], kinds: [] as string[], groups: [] as string[] };
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname;
    let body: any = {
      items: [],
      data: [],
      threads: [],
      messages: [],
      models: [],
      providers: [],
    };
    if (path === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (path === "/api/artifacts/_status")
      body = {
        total: state.exists ? 1 : 0,
        unreadCount: state.exists ? 1 : 0,
        unreadIds: state.exists ? [item.id] : [],
      };
    if (path === "/api/artifacts") {
      state.kinds.push(url.searchParams.get("kind") || "");
      state.groups.push(url.searchParams.get("group") || "");
      body = {
        items: state.exists && url.searchParams.get("group") !== "generated" ? [item] : [],
        total: state.exists ? 1 : 0,
        nextCursor: null,
      };
    }
    if (path === "/api/artifacts/" + item.id) {
      if (route.request().method() === "DELETE") {
        state.deleted.push(route.request().postDataJSON());
        state.exists = false;
        body = { id: item.id, state: "deleted" };
      } else body = item;
    }
    if (path.endsWith("/links"))
      body = { resourceId: item.id, revision: 0, items: [] };
    if (path === "/api/companion/threads")
      body = {
        threads: [
          {
            thread_id: "fixture",
            title: "Attachment history",
            created_at: "2026-09-22T01:00:00Z",
          },
        ],
      };
    if (path === "/api/companion/threads/fixture")
      body = {
        messages: [
          {
            message_id: "source-message",
            kind: "user",
            content: "Original readable text",
            leamAttachments: [
              {
                id: "11111111-1111-4111-8111-111111111111",
                filename: item.filename,
                mimeType: "text/plain",
                sizeBytes: 0,
                sha256: item.sha256,
                state: "deleted",
              },
            ],
          },
        ],
      };
    await route.fulfill({ json: body });
  });
  return state;
}
for (const width of [390, 844])
  test(`Attachment catalogue filter and explicit removal at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 390 });
    const state = await fixture(page);
    await page.goto("/?view=resources");
    await page.getByRole("button", { name: "Uploads", exact: true }).click();
    await expect(page.locator(".resource-attachment-tag")).toHaveText(
      "Upload · ",
    );
    await page
      .getByRole("combobox", { name: "File type" })
      .selectOption("text");
    await expect.poll(() => state.kinds).toContain("text");
    await expect.poll(() => state.groups).toContain("uploads");
    await page
      .getByRole("button", { name: "Delete Planning notes.txt", exact: true })
      .click();
    await expect(
      page.getByText("Chat history keeps a deleted-file placeholder.", {
        exact: false,
      }),
    ).toBeVisible();
    expect(state.deleted).toHaveLength(0);
    await page
      .getByRole("button", { name: "Delete resource", exact: true })
      .click();
    await expect.poll(() => state.deleted.length).toBe(1);
    expect(state.deleted[0]).toMatchObject({
      expectedSha256: item.sha256,
      confirmed: true,
    });
    await expect(
      page.getByRole("link", { name: "Open Planning notes.txt" }),
    ).toHaveCount(0);
  });
test("Attachment viewer hides source metadata until expanded", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/?artifact=" + item.id);
  await expect(
    page.getByText("Private fixture notes", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("source-message", { exact: true }),
  ).not.toBeVisible();
  await page.getByText("Conversation references", { exact: true }).click();
  await expect(page.getByText("source-message", { exact: true })).toBeVisible();
});
test("Deleted attachment keeps readable conversation without broken file requests", async ({
  page,
}) => {
  await fixture(page);
  let downloads = 0;
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/attachments/")) downloads++;
  });
  await page.goto("/?view=companion");
  await chooseConversation(page, "fixture", "companion");
  await expect(
    page.getByText("Original readable text", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Planning notes.txt · Attachment deleted", { exact: true }),
  ).toBeVisible();
  expect(downloads).toBe(0);
});
