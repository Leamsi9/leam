import { test, expect } from "@playwright/test";

test("a reminder becomes ready with the browser closed and completes from Today", async ({
  page,
  context,
}) => {
  test.setTimeout(60000);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page
    .locator("input[name=password]")
    .fill("local-browser-acceptance-only");
  await page.getByRole("button", { name: /Enter Leam/ }).click();
  await page.getByRole("button", { name: "Today", exact: true }).click();
  await page.getByRole("button", { name: "Plan a commitment" }).click();
  const title = "Durable reminder " + Date.now();
  await page.getByLabel("Commitment title", { exact: true }).fill(title);
  await page.getByLabel("Kind", { exact: true }).selectOption("habit");
  await page
    .getByLabel("Reminder time")
    .fill(new Date().toISOString().slice(11, 16));
  await page.getByLabel("Timezone", { exact: true }).fill("UTC");
  await page
    .getByRole("button", { name: "Save commitment", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: title, exact: true }),
  ).toBeVisible();
  await page.close();
  await new Promise((resolve) => setTimeout(resolve, 17000));
  const next = await context.newPage();
  await next.setViewportSize({ width: 390, height: 844 });
  await next.goto("/");
  await next.getByRole("button", { name: "Today", exact: true }).click();
  const reminder = next
    .locator(".reminder-card")
    .filter({ has: next.getByRole("heading", { name: title, exact: true }) });
  await expect(reminder).toBeVisible({ timeout: 20000 });
  await reminder
    .getByRole("button", { name: "Mark done", exact: true })
    .click();
  await expect(reminder).toHaveCount(0);
  await expect(
    next.getByRole("button", { name: "Reopen " + title, exact: true }),
  ).toBeVisible();
});
