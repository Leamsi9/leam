import { test, expect } from "@playwright/test";

test("measured habits, undo and date navigation work on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page
    .locator("input[name=password]")
    .fill("local-browser-acceptance-only");
  await page.getByRole("button", { name: /Enter Leam/ }).click();
  await page.getByRole("button", { name: "Today", exact: true }).click();
  await page.getByLabel("Viewing date").fill("2026-09-20");
  await page.getByRole("button", { name: "Plan a commitment" }).click();
  const title = "Measured walk " + Date.now();
  await page.getByLabel("Commitment title", { exact: true }).fill(title);
  await page.getByLabel("Kind", { exact: true }).selectOption("habit");
  await page.getByLabel("Measure", { exact: true }).selectOption("minutes");
  await page.getByLabel("Daily target", { exact: true }).fill("20");
  await page
    .getByRole("button", { name: "Save commitment", exact: true })
    .click();
  const card = page
    .locator(".commitment-card")
    .filter({ has: page.getByRole("heading", { name: title, exact: true }) });
  await card
    .getByRole("spinbutton", { name: "Progress for " + title })
    .fill("7");
  await card
    .getByRole("button", { name: "Save progress", exact: true })
    .click();
  await card
    .getByRole("button", { name: "Complete " + title, exact: true })
    .click();
  await card
    .getByRole("button", { name: "Reopen " + title, exact: true })
    .click();
  await expect(
    card.getByRole("spinbutton", { name: "Progress for " + title }),
  ).toHaveValue("7");
  await page.reload();
  await page
    .getByRole("button", { name: "Today", exact: true })
    .first()
    .click();
  await page.getByLabel("Viewing date").fill("2026-09-20");
  await expect(
    card.getByRole("spinbutton", { name: "Progress for " + title }),
  ).toHaveValue("7");
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await expect(
    card.getByRole("spinbutton", { name: "Progress for " + title }),
  ).toHaveValue("0");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../../.artifacts/today-measures-390.png",
    fullPage: true,
  });
});
