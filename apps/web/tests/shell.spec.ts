import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
const password = "local-browser-acceptance-only";
for (const viewport of [
  { width: 390, height: 844 },
  { width: 1440, height: 1000 },
]) {
  test(`real local Codex history and commitments at ${viewport.width}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await page.goto("/");
    const auth = await page.request
      .get("/api/auth/status")
      .then((r) => r.json());
    if (!auth.configured)
      await page
        .getByLabel("Pairing code")
        .fill(
          readFileSync("/tmp/leam-candidate-ui/bootstrap-token", "utf8").trim(),
        );
    await page.locator("input[name=password]").fill(password);
    await page.getByRole("button", { name: /Enter Leam/ }).click();
    await expect(
      page.getByRole("heading", { name: "Pick up a thread." }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: /Review Leam architecture and gaps/ })
      .click();
    await expect(page.getByText("Read only", { exact: true })).toBeVisible();
    await expect(page.getByText("Build goal · active")).toBeVisible();
    await expect(page.locator(".message").last()).toBeVisible();
    await expect(
      page.getByRole("textbox", { name: "Message Codex" }),
    ).toBeDisabled();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await page.screenshot({
      path: `../../.artifacts/codex-${viewport.width}.png`,
      fullPage: true,
    });
    await page.getByRole("button", { name: "Today", exact: true }).click();
    const title = `Acceptance commitment ${viewport.width} ${Date.now()}`;
    await page.getByRole("textbox", { name: "New commitment" }).fill(title);
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await page.getByRole("button", { name: "Complete " + title }).click();
    await expect(
      page.getByRole("button", { name: "Reopen " + title }),
    ).toBeVisible();
    await page.reload();
    await page.getByRole("button", { name: "Today", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Reopen " + title }),
    ).toBeVisible();
    await page.screenshot({
      path: `../../.artifacts/today-${viewport.width}.png`,
      fullPage: true,
    });
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Welcome back." }),
    ).toBeVisible();
    const denied = await page.request.get("/api/codex/threads");
    expect(denied.status()).toBe(401);
  });
}
