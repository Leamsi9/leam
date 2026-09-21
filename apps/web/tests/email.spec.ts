import { test, expect } from "@playwright/test";
import { settingsSection } from "./navigation";
for (const width of [390, 1440]) {
  test(`email requires a separate explicit opt-in and sync at ${width}px`, async ({
    page,
  }) => {
    const writes: string[] = [];
    let granted = false,
      synced = false;
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      const method = route.request().method();
      let body: any = { items: [], data: [], providers: [] };
      if (method !== "GET") writes.push(method + " " + path);
      if (path === "/api/auth/status")
        body = { authenticated: true, configured: true };
      if (path === "/api/accounts/mail-account/email/authorize") {
        granted = true;
        body = { url: "/?view=settings&account=email-connected" };
      }
      if (path === "/api/email/accounts/mail-account/sync") {
        synced = true;
        body = { state: "ready", truncated: true };
      }
      if (path === "/api/email/accounts/mail-account" && method === "DELETE")
        granted = false;
      if (path === "/api/accounts")
        body = {
          providers: [],
          items: [
            {
              id: "mail-account",
              provider: "google",
              identity: "fixture@example.test",
              state: "connected",
              email: {
                granted,
                state: granted ? "connected" : "not_connected",
              },
            },
          ],
        };
      if (path === "/api/email")
        body = {
          accounts: [
            {
              accountId: "mail-account",
              syncedAt: synced ? 1790000000 : null,
              stale: false,
            },
          ],
        };
      await route.fulfill({ json: body });
    });
    await page.goto("/?view=settings");
    await settingsSection(page, "Connected accounts");
    await expect(
      page.getByText(
        "Email is not connected. Calendar access does not include your inbox.",
      ),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Sync recent inbox" }),
    ).toHaveCount(0);
    expect(writes).toEqual([]);
    await page.getByRole("button", { name: "Enable read-only email" }).click();
    await page.waitForURL("**/\?view=settings&account=email-connected");
    await settingsSection(page, "Connected accounts");
    await expect(page.getByText("Mailbox reading granted.")).toBeVisible();
    expect(writes).toEqual(["POST /api/accounts/mail-account/email/authorize"]);
    await page.getByRole("button", { name: "Sync recent inbox" }).click();
    await expect(
      page.getByText(
        "Recent inbox synced. More messages are available in Gmail.",
      ),
    ).toBeVisible();
    await page.getByRole("button", { name: "Remove email from Leam" }).click();
    await expect(
      page.getByRole("button", { name: "Enable read-only email" }),
    ).toBeVisible();
    expect(writes).toEqual([
      "POST /api/accounts/mail-account/email/authorize",
      "POST /api/email/accounts/mail-account/sync",
      "DELETE /api/email/accounts/mail-account",
    ]);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  });
}
