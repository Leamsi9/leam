import { test, expect } from "@playwright/test";
import { chooseConversation, navigate } from "./navigation";
for (const width of [390, 1440])
  test(`memory review lives in Settings and preferences persist at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    let policy = {
      revision: 0,
      createRequiresApproval: false,
      editRequiresApproval: false,
      removeRequiresApproval: false,
    };
    let memory: any[] = [],
      approvals = 0,
      settingsSaves = 0;
    let proposal: any = {
      id: "p",
      operation: "memory.create",
      state: "pending",
      reason: "Remember this preference",
      review: {
        before: null,
        after: { text: "Enjoys green tea", source: "User" },
        approval: { mode: "manual" },
      },
    };
    await page.route("**/api/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      let data: any = {
        items: [],
        data: [],
        threads: [],
        accounts: [],
        providers: [],
      };
      if (p === "/api/auth/status") data = { authenticated: true };
      if (p === "/api/companion/threads")
        data = { threads: [{ thread_id: "t", title: "Planning" }] };
      if (p === "/api/companion/threads/t") data = { messages: [] };
      if (p === "/api/proposals" || p === "/api/proposals/memory")
        data = { items: [proposal] };
      if (p === "/api/proposals/memory/policy") {
        if (route.request().method() === "PUT") {
          settingsSaves++;
          policy = {
            ...route.request().postDataJSON(),
            revision: policy.revision + 1,
          };
        }
        data = policy;
      }
      if (p === "/api/proposals/p/approve") {
        approvals++;
        memory = [
          { id: "m", revision: 1, text: "Enjoys green tea", source: "User" },
        ];
        proposal = { ...proposal, state: "complete" };
        data = proposal;
      }
      if (p === "/api/memory") data = { items: memory };
      await route.fulfill({ json: data });
    });
    await page.goto("/");
    await navigate(page, "Companion");
    await chooseConversation(page, "t");
    await expect(
      page.getByRole("heading", { name: "Suggested: new memory" }),
    ).toHaveCount(0);
    await navigate(page, "Settings");
    await page
      .locator("summary")
      .filter({ hasText: /^Memory$/ })
      .click();
    await page
      .getByText("Memory approval preferences", { exact: true })
      .click();
    await expect(
      page.getByLabel("Review new memories", { exact: true }),
    ).not.toBeChecked();
    await expect(
      page.getByLabel("Review memory edits", { exact: true }),
    ).not.toBeChecked();
    await expect(
      page.getByLabel("Review forgetting memories", { exact: true }),
    ).not.toBeChecked();
    await page.getByLabel("Review new memories", { exact: true }).check();
    await page.getByRole("button", { name: "Save memory preferences" }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "Memory preferences saved" }),
    ).toBeVisible();
    expect(settingsSaves).toBe(1);
    expect(approvals).toBe(0);
    await page
      .getByText("Memory suggestions and activity", { exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Suggested: new memory" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Approve this change", exact: true })
      .click();
    await expect(page.locator(".memory-entry")).toContainText(
      "Enjoys green tea",
    );
    expect(approvals).toBe(1);
    await page.reload();
    await navigate(page, "Settings");
    await page
      .locator("summary")
      .filter({ hasText: /^Memory$/ })
      .click();
    await page
      .getByText("Memory approval preferences", { exact: true })
      .click();
    await expect(
      page.getByLabel("Review new memories", { exact: true }),
    ).toBeChecked();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  });
