import { chooseConversation } from "./navigation";
import { test, expect } from "@playwright/test";
import { navigate } from "./navigation";

test("older companion pages survive polling and cannot cross conversations", async ({
  page,
}) => {
  let release!: () => void;
  const wait = new Promise<void>((r) => (release = r));
  let began!: () => void;
  const started = new Promise<void>((r) => (began = r));
  let delay = false;
  await page.route("**/api/**", async (route) => {
    const u = new URL(route.request().url());
    let body: any = {items:[],data:[],threads:[],providers:[]};
    if (u.pathname === "/api/auth/status") body = { authenticated: true };
    if (u.pathname === "/api/proposals") body = { items: [] };
    if (u.pathname === "/api/codex/threads") body = { data: [] };
    if (u.pathname === "/api/companion/threads")
      body = {
        threads: [
          { thread_id: "a", title: "A" },
          { thread_id: "b", title: "B" },
        ],
      };
    if (u.pathname === "/api/companion/threads/a") {
      if (u.searchParams.has("cursor")) {
        if (delay) {
          expect(u.searchParams.get("cursor")).toBe("older-2");
          began();
          await wait;
        }
        body = {
          messages: delay
            ? [
                {
                  message_id: "old2",
                  kind: "assistant",
                  sequence: -100,
                  content: "Late A",
                },
              ]
            : Array.from({ length: 60 }, (_, i) => ({
                message_id: "old-" + i,
                kind: "assistant",
                sequence: i - 60,
                content: i === 0 ? "Earlier A" : "Earlier " + i,
              })),
          next_cursor: delay ? null : "older-2",
        };
      } else
        body = {
          messages: [
            {
              message_id: "new1",
              kind: "assistant",
              sequence: 2,
              content: "Newest A",
            },
          ],
          next_cursor: "older-1",
        };
    }
    if (u.pathname === "/api/companion/threads/b") body = { messages: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Companion", exact: true }).click();
  await chooseConversation(page, "a");
  await page.getByRole("button", { name: "Earlier messages" }).click();
  await expect(page.getByText("Earlier A", { exact: true })).toBeVisible();
  await page.waitForTimeout(2200);
  await expect(page.getByText("Earlier A", { exact: true })).toBeVisible();
  await navigate(page, "Settings");
  await navigate(page, "Companion");
  await expect(page.getByText("Earlier A", { exact: true })).toBeVisible();
  delay = true;
  await page.getByRole("button", { name: "Earlier messages" }).click();
  await started;
  await chooseConversation(page, "b");
  release();
  await page.waitForTimeout(100);
  await expect(page.getByText("Late A", { exact: true })).toHaveCount(0);
});
