import { test, expect } from "@playwright/test";

for (const width of [390, 1440])
  test(`permission preview, apply and restore remain explicit at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    let global = true;
    const writes: any[] = [];
    let history: any[] = [];
    const modes = () => ({
      autoApprove: global,
      items: [
        {
          id: "builtin.time",
          description: "Read current time",
          state: "always_allow",
          locked: false,
          protected: false,
        },
      ],
    });
    const preview = () => ({
      previewToken: "a".repeat(64),
      autoApproveBefore: global,
      autoApproveAfter: false,
      changes: [
        { id: "builtin.shell", before: "always_allow", after: "disabled" },
      ],
      missingRecommendedTools: [],
      history,
      hostCeiling: {
        enforcementStatus: "unknown",
        behaviorAccepted: false,
        recommendedIds: ["builtin.time"],
      },
    });
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let data: any = {
        items: [],
        data: [],
        threads: [],
        providers: [],
        models: [],
      };
      if (path === "/api/auth/status")
        data = { configured: true, authenticated: true };
      if (path === "/api/companion/tools") data = modes();
      if (path === "/api/companion/tools/profile") data = preview();
      if (
        path.includes("/profile/restore/") &&
        route.request().method() === "GET"
      )
        data = {
          ...preview(),
          previewToken: "b".repeat(64),
          autoApproveAfter: true,
          compatible: true,
          warning:
            "Restoring previous permission modes can widen access. The host ceiling is unchanged.",
        };
      if (
        route.request().method() === "POST" &&
        path.includes("/tools/profile/")
      ) {
        const body = route.request().postDataJSON();
        writes.push({ path, body });
        global = path.endsWith("/restore");
        data = {
          requestId: body.requestId,
          operation: global ? "restore" : "apply",
          created: 1789900000,
          state: "complete",
          message:
            "Runtime permission modes verified. Host ceiling was not changed.",
          hasSnapshot: true,
          backupId: body.requestId,
        };
        history = [data];
      }
      await route.fulfill({ json: data });
    });
    await page.goto("/?view=settings");
    await page
      .locator("summary")
      .filter({ hasText: /^Companion permissions$/ })
      .click();
    const panel = page.getByRole("region", {
      name: "Companion tool permissions",
    });
    await expect(
      panel.getByText(/Unknown — source and running configuration/),
    ).toBeVisible();
    expect(writes).toHaveLength(0);
    await panel
      .getByRole("button", { name: "Preview Companion profile", exact: true })
      .click();
    await expect(
      panel.getByRole("button", { name: "Confirm apply", exact: true }),
    ).toBeDisabled();
    expect(writes).toHaveLength(0);
    await panel.getByRole("checkbox").check();
    await panel
      .getByRole("button", { name: "Confirm apply", exact: true })
      .click();
    await expect(
      panel
        .getByText(
          "Runtime permission modes verified. Host ceiling was not changed.",
        )
        .first(),
    ).toBeVisible();
    expect(writes).toHaveLength(1);
    expect(writes[0].body).toMatchObject({
      confirmed: true,
      previewToken: "a".repeat(64),
    });
    await panel
      .locator("summary")
      .filter({ hasText: "Saved snapshots and profile history" })
      .click();
    await panel
      .getByRole("button", { name: "Preview restore to before this change" })
      .click();
    await expect(panel.getByText(/can widen access/)).toBeVisible();
    await expect(
      panel.getByRole("button", { name: "Confirm restore", exact: true }),
    ).toBeDisabled();
    expect(writes).toHaveLength(1);
    await panel.getByRole("checkbox").check();
    await panel
      .getByRole("button", { name: "Confirm restore", exact: true })
      .click();
    await expect.poll(() => writes.length).toBe(2);
    expect(writes[1].body.backupId).toBe(writes[0].body.requestId);
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      )
      .toBe(true);
  });

test("uncertain profile receipt remains visible without automatic mutation retry", async ({
  page,
}) => {
  let writes = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: any = {
      items: [],
      data: [],
      threads: [],
      models: [],
      providers: [],
    };
    if (path === "/api/auth/status")
      data = { authenticated: true, configured: true };
    if (path === "/api/companion/tools/profile")
      data = {
        previewToken: "a".repeat(64),
        changes: [],
        history: [],
        missingRecommendedTools: [],
        hostCeiling: { enforcementStatus: "unknown" },
      };
    if (route.request().method() === "POST") {
      writes++;
      data = {
        state: "needs_review",
        message:
          "The change may be partial. Refresh actual permissions before another action.",
      };
    }
    await route.fulfill({ json: data });
  });
  await page.goto("/?view=settings");
  await page
    .locator("summary")
    .filter({ hasText: /^Companion permissions$/ })
    .click();
  const panel = page.getByRole("region", {
    name: "Companion tool permissions",
  });
  await panel
    .getByRole("button", { name: "Preview Companion profile" })
    .click();
  await panel.getByRole("checkbox").check();
  await panel.getByRole("button", { name: "Confirm apply" }).click();
  await expect(panel.getByRole("alert")).toContainText("may be partial");
  await panel
    .getByRole("button", { name: "Refresh actual permissions" })
    .click();
  expect(writes).toBe(1);
});
