import { test, expect } from "@playwright/test";
import { navigate } from "./navigation";
for (const width of [390, 1440])
  test(`Coding identifies Companion tasks and refreshes an older reopened list ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const first = {
      id: "existing",
      name: "My chosen session name",
      createdAt: 1789955540,
      cwd: "/fixture",
      leamOrigin: "reviewed-companion-handoff",
      leamHandoffTitle: "Reviewed web search task",
      leamSourceThreadId: "source-a",
    };
    await page.addInitScript((first) => {
      sessionStorage.setItem(
        "leam-view:coding:selected",
        JSON.stringify(first),
      );
      (window as any).EventSource = class extends EventTarget {
        close() {}
      };
    }, first);
    let lists = 0,
      posts = 0;
    await page.route("**/api/**", async (route) => {
      const request = route.request(),
        path = new URL(request.url()).pathname;
      if (request.method() !== "GET") posts++;
      let body: any = { items: [], data: [] };
      if (path === "/api/auth/status") body = { authenticated: true };
      if (path === "/api/codex/threads") {
        lists++;
        body = {
          data:
            lists === 1
              ? [first]
              : [
                  first,
                  {
                    id: "new-handoff",
                    name: "Reviewed progress task",
                    createdAt: 1789957822,
                    leamOrigin: "reviewed-companion-handoff",
                    leamHandoffTitle: "Reviewed progress task",
                  },
                ],
          nextCursor: "older",
        };
      }
      if (path === "/api/codex/threads/existing")
        body = { thread: { ...first, status: { type: "idle" } } };
      if (path.endsWith("/goal")) body = { goal: null };
      await route.fulfill({ json: body });
    });
    await page.goto("/");
    await navigate(page, "Coding");
    await expect.poll(() => lists).toBe(1);
    await page.clock.install();
    const list = page.getByRole("region", { name: "Coding sessions" }),
      toggle = list.locator(".conversation-list-toggle");
    await expect(toggle).toContainText("My chosen session name");
    await toggle.click();
    const original = list.locator('[data-thread-id="existing"]');
    await expect(
      original.getByRole("button", {
        name: "Open My chosen session name",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      original.getByText("From Companion · Reviewed web search task", {
        exact: true,
      }),
    ).toBeVisible();
    await toggle.click();
    await toggle.click();
    expect(lists).toBe(1);
    await toggle.click();
    await page.clock.fastForward(16000);
    await toggle.click();
    await expect.poll(() => lists).toBe(2);
    await expect(
      list
        .locator('[data-thread-id="new-handoff"]')
        .getByRole("button", {
          name: "Open Reviewed progress task",
          exact: true,
        }),
    ).toBeVisible();
    await expect(toggle).toContainText("My chosen session name");
    expect(posts).toBe(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
