import { test, expect } from "@playwright/test";

test("live IronClaw configuration and sourced memory are usable on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page
    .locator("input[name=password]")
    .fill("local-browser-acceptance-only");
  await page.getByRole("button", { name: /Enter Leam/ }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByLabel("Model provider").selectOption("openai_codex");
  await expect(page.getByLabel("Model", { exact: true })).not.toHaveValue("");
  const memory = "Acceptance preference " + Date.now();
  await page.getByLabel("Memory", { exact: true }).fill(memory);
  await page
    .getByLabel("Source", { exact: true })
    .fill("Synthetic acceptance record");
  await page.getByRole("button", { name: "Remember this" }).click();
  const card = page.locator(".memory-entry").filter({ hasText: memory });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Memory", { exact: true }).fill(memory + " corrected");
  await page.getByRole("button", { name: "Save correction" }).click();
  await expect(
    card.getByText(memory + " corrected", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Forget" }).click();
  await expect(card).toHaveCount(0);
  await page.getByRole("button", { name: "Companion", exact: true }).click();
  await page.getByRole("button", { name: "New conversation" }).click();
  await expect(
    page.getByRole("textbox", { name: "Message Leam" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../../.artifacts/companion-390.png",
    fullPage: true,
  });
});
