import { test, expect } from "@playwright/test";

test("push contact settings persist through the real mobile UI without claiming device delivery", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page
    .locator("input[name=password]")
    .fill("local-browser-acceptance-only");
  await page.getByRole("button", { name: /Enter Leam/ }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page
    .getByLabel("Push contact email")
    .fill("leam-acceptance@example.com");
  await page.getByRole("button", { name: "Save push contact" }).click();
  await expect(page.getByText("Push contact saved.")).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("Push contact email")).toHaveValue(
    "leam-acceptance@example.com",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page
    .getByRole("heading", { name: "Phone notifications" })
    .scrollIntoViewIfNeeded();
  await page.screenshot({ path: "../../.artifacts/push-settings-390.png" });
});
