import { test, expect } from "@playwright/test";
for (const width of [390, 1440])
  test(`Across Leam overview stays collapsed and reports sourced/stale build state at ${width}`, async ({
    page,
  }) => {
    page.on("pageerror", error => console.error(error));
    await page.setViewportSize({ width, height: 900 });
    let calls = 0,
      fail = false;
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let data: any = { items: [], data: [] };
      if (path === "/api/auth/status")
        data = { authenticated: true, configured: true };
      if (path === "/api/companion/overview") {
        calls++;
        if (fail) {
          await route.fulfill({
            status: 503,
            json: { detail: "Overview temporarily unavailable" },
          });
          return;
        }
        data = {
          observedAt: 1800000000,
          modules: [
            {
              kind: "routine",
              source: "leam:routine",
              observedAt: 1800000000,
              dataAsOf: 1799999000,
              availability: "available",
              count: 2,
              truncated: false,
            },
            {
              kind: "notification",
              source: "leam:notification",
              observedAt: 1800000000,
              dataAsOf: null,
              availability: "unavailable",
              count: null,
            },
          ],
          project: {
            name: "Leam",
            deployedFeatures: 3,
            userAcceptedFeatures: 0,
            unfinishedFeatures: 2,
            awaitingUatFeatures: 3,
            failedQaFeatures: 0,
            failedUatFeatures: 0,
            staleAssessments: 1,
            workItems: [
              {
                feature: "fixture",
                title: "Finish fixture",
                percent: 64,
                stale: true,
                currentStep: "Waiting for user testing",
              },
            ],
          },
        };
      }
      await route.fulfill({ json: data });
    });
    await page.goto("/?view=overview");
    const disclosure = page
      .locator("details")
      .filter({ has: page.locator("summary", { hasText: "Across Leam" }) });
    await expect(disclosure).not.toHaveAttribute("open", "");
    expect(calls).toBe(0);
    await disclosure.locator(":scope > summary").click();
    await expect(
      page.getByRole("heading", { name: "Leam build", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("3 deployed · 0 passed your acceptance · 2 unfinished"),
    ).toBeVisible();
    await expect(
      page.getByText(/estimated 64% toward deployment/),
    ).toBeVisible();
    await expect(
      page.getByText(/progress assessments are stale/),
    ).toBeVisible();
    await expect(page.getByText(/Source: leam:routine/)).toBeVisible();
    fail = true;
    await page.getByRole("button", { name: "Refresh overview" }).click();
    await expect(page.getByRole("alert")).toContainText(
      "Previous snapshot remains below",
    );
    await expect(
      page.getByRole("heading", { name: "Leam build", exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  });
