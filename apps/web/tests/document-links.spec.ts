import { test, expect } from "@playwright/test";
import { chooseConversation } from "./navigation";
const first = "11111111-1111-4111-8111-111111111111";
const second = "22222222-2222-4222-8222-222222222222";
const download = (thread: string, path: string) =>
  `/api/companion/threads/${thread}/files/content?path=${encodeURIComponent(path)}`;

test("Companion document links download with the current thread in saved and streamed prose", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    const streams: any[] = [];
    class Source extends EventTarget {
      onopen: any;
      onerror: any;
      constructor(public url: string) {
        super();
        if (url.includes("/companion/")) streams.push(this);
        setTimeout(() => this.onopen?.(), 0);
      }
      close() {}
    }
    (window as any).EventSource = Source;
    (window as any).documentStreams = streams;
  });
  const downloaded: string[] = [];
  await page.route("**/api/**", (route) => {
    const u = new URL(route.request().url());
    let data: any = { items: [], data: [], messages: [], runs: [] };
    if (u.pathname === "/api/auth/status") data = { authenticated: true };
    if (u.pathname === "/api/companion/threads")
      data = {
        threads: [
          { thread_id: first, title: "First" },
          { thread_id: second, title: "Second" },
        ],
      };
    if (u.pathname === `/api/companion/threads/${first}`)
      data = {
        messages: [
          {
            message_id: "saved",
            kind: "assistant",
            status: "finalized",
            sequence: 1,
            content:
              "[Text file](/workspace/report.txt) [PDF](/workspace/report.pdf) [Unicode](/workspace/caf%C3%A9%20notes.txt) [External](https://example.test/docs) [Unsafe](javascript:alert%281%29) [Traversal](/workspace/%2e%2e/secret) [Backslash](/workspace/a%5Cb) [Control](/workspace/a%00b)",
          },
        ],
      };
    if (u.pathname.endsWith("/files/content")) {
      downloaded.push(u.pathname + u.search);
      return route.fulfill({
        body: "synthetic document",
        headers: {
          "content-type": "application/octet-stream",
          "content-disposition": 'attachment; filename="report.txt"',
        },
      });
    }
    return route.fulfill({ json: data });
  });
  await page.goto("/?view=companion");
  await chooseConversation(page, first);
  await expect(
    page.getByRole("link", { name: "Text file", exact: true }),
  ).toHaveAttribute("href", download(first, "/workspace/report.txt"));
  await expect(
    page.getByRole("link", { name: "PDF", exact: true }),
  ).toHaveAttribute("href", download(first, "/workspace/report.pdf"));
  await expect(
    page.getByRole("link", { name: "Unicode", exact: true }),
  ).toHaveAttribute("href", download(first, "/workspace/café notes.txt"));
  await expect(
    page.getByRole("link", { name: "External", exact: true }),
  ).toHaveAttribute("href", "https://example.test/docs");
  for (const label of ["Unsafe", "Traversal", "Backslash", "Control"])
    await expect(
      page.locator("a").filter({ hasText: new RegExp(`^${label}$`) }),
    ).toHaveAttribute("href", "");
  const received = page.waitForEvent("download");
  await page.getByRole("link", { name: "Text file", exact: true }).click();
  await received;
  expect(downloaded).toEqual([download(first, "/workspace/report.txt")]);
  await chooseConversation(page, second);
  await page.waitForFunction(
    () => (window as any).documentStreams.length === 2,
  );
  await page.evaluate((thread) => {
    (window as any).documentStreams[1].dispatchEvent(
      new MessageEvent("projection_update", {
        data: JSON.stringify({
          type: "projection_update",
          state: {
            thread_id: thread,
            items: [
              {
                text: {
                  id: "stream",
                  run_id: "run",
                  body: "[Stream PDF](/workspace/live.pdf)",
                },
              },
            ],
          },
        }),
      }),
    );
  }, second);
  await expect(
    page.getByRole("link", { name: "Stream PDF", exact: true }),
  ).toHaveAttribute("href", download(second, "/workspace/live.pdf"));
});
