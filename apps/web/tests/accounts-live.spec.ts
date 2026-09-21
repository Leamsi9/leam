import { test, expect } from "@playwright/test";

test("real account application settings persist and remove on mobile without contacting a provider", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=settings");
  await page
    .locator("input[name=password]")
    .fill("local-browser-acceptance-only");
  await page.getByRole("button", { name: /Enter Leam/ }).click();
  const setup = page.getByText("Google application setup", { exact: true });
  await expect(setup).toBeVisible();
  if (!(await page.getByLabel("Google client ID").isVisible()))
    await setup.click();
  await page.getByLabel("Google client ID").fill("synthetic-acceptance-client");
  await page
    .getByLabel("Google client secret")
    .fill("synthetic-acceptance-secret");
  await page
    .getByRole("button", { name: "Save Google application", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Connect Google", exact: true }),
  ).toBeEnabled();
  await page.reload();
  await setup.click();
  await expect(page.getByLabel("Google client ID")).toHaveValue(
    "synthetic-acceptance-client",
  );
  await expect(page.getByLabel("Google client secret")).toHaveValue("");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page
    .getByRole("button", {
      name: "Remove Google application and disconnect its accounts",
    })
    .click();
  await expect(
    page.getByRole("button", { name: "Connect Google", exact: true }),
  ).toBeDisabled();
});
