import { settingsSection } from "./navigation";
import { test, expect } from "@playwright/test";

test("mobile account application settings clear saved secret and show exact callback", async ({
  page,
}) => {
  let configured = false;
  const saves: any[] = [];
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (
      path === "/api/accounts/providers/google" &&
      route.request().method() === "PUT"
    ) {
      saves.push(route.request().postDataJSON());
      configured = true;
    }
    const body =
      path === "/api/auth/status"
        ? { authenticated: true }
        : path === "/api/accounts"
          ? {
              providers: [
                {
                  id: "google",
                  clientId: configured ? "application-id" : "",
                  revision: configured ? 1 : 0,
                  secretConfigured: configured,
                  callbackPath: "/api/accounts/oauth/google/callback",
                },
              ],
              items: [],
            }
          : path === "/api/settings/providers"
            ? { providers: [] }
            : { items: [], data: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/?view=settings");
  await settingsSection(page, "Connected accounts");
  await expect(
    page.getByRole("heading", { name: "Connected accounts" }),
  ).toBeVisible();
  await expect(
    page.getByText(
      new URL("/api/accounts/oauth/google/callback", page.url()).href,
      { exact: true },
    ),
  ).toBeVisible();
  await page.getByLabel("Google client ID").fill("application-id");
  await page
    .getByLabel("Google client secret")
    .fill("private-secret-for-fixture");
  await page.getByRole("button", { name: "Save Google application" }).click();
  await expect.poll(() => saves.length).toBe(1);
  expect(saves[0].clientSecret).toBe("private-secret-for-fixture");
  await page.getByText("Google application setup", { exact: true }).click();
  await expect(page.getByLabel("Google client secret")).toHaveValue("");
  await expect(
    page.getByRole("button", { name: "Connect Google", exact: true }),
  ).toBeEnabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
