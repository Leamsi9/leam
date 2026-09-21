import { test, expect } from "@playwright/test";
import { navigate, settingsSection } from "./navigation";

test("new coding defaults expose supported efforts, save separately and survive reload on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let selected = { model: "gpt-5.6-sol", reasoningEffort: "medium" };
  const writes: any[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [] };
    if (path === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (path === "/api/settings/providers") body = { providers: [] };
    if (path === "/api/settings/models")
      body = {
        models: [
          {
            model: "gpt-5.6-sol",
            displayName: "Sol",
            supportedReasoningEfforts: [
              { reasoningEffort: "medium" },
              { reasoningEffort: "high" },
            ],
          },
          {
            model: "gpt-5.6-luna",
            displayName: "Luna",
            defaultReasoningEffort: "low",
            supportedReasoningEfforts: [{ reasoningEffort: "low" }],
          },
        ],
      };
    if (path === "/api/settings/coding-model") {
      if (route.request().method() === "POST") {
        selected = route.request().postDataJSON();
        writes.push(selected);
      }
      body = selected;
    } else if (route.request().method() === "POST")
      writes.push({ unexpected: path });
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await navigate(page, "Settings");
  await settingsSection(page, "Model and reasoning");
  const section = page.getByRole("region", {
    name: "New coding session defaults",
  });
  await expect(section.getByLabel("Coding model", { exact: true })).toHaveValue(
    "gpt-5.6-sol",
  );
  await expect(
    section.getByLabel("Coding reasoning effort", { exact: true }),
  ).toHaveValue("medium");
  await expect(section).toContainText("ongoing shared session");
  await section
    .getByLabel("Coding model", { exact: true })
    .selectOption("gpt-5.6-luna");
  await expect(
    section.getByLabel("Coding reasoning effort", { exact: true }),
  ).toHaveValue("low");
  await section.getByRole("button", { name: "Save coding defaults" }).click();
  await expect(section.getByRole("status")).toContainText("Defaults saved");
  expect(writes).toEqual([{ model: "gpt-5.6-luna", reasoningEffort: "low" }]);
  await page.reload();
  await navigate(page, "Settings");
  await settingsSection(page, "Model and reasoning");
  await expect(section.getByLabel("Coding model", { exact: true })).toHaveValue(
    "gpt-5.6-luna",
  );
  await expect(
    section.getByLabel("Coding reasoning effort", { exact: true }),
  ).toHaveValue("low");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(
    section.getByRole("button", { name: "Save coding defaults" }),
  ).toBeVisible();
});
