import { test, expect, type Page } from "@playwright/test";
async function fixture(page: Page, stale = false) {
  const writes: any[] = [];
  let removed = false;
  let reviewCount = 0;
  const c = {
    id: "walk",
    title: "Walk",
    kind: "habit",
    revision: 2,
    status: "active",
    capacityId: "health",
    measure: "boolean",
    target: 1,
    date: "2026-09-20",
    log: { value: 0, revision: 0, done: false },
  };
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [] };
    if (p === "/api/auth/status") body = { authenticated: true };
    if (p === "/api/capacities")
      body = {
        items: removed
          ? [{ id: "growth", name: "Growth", revision: 3 }]
          : [
              { id: "health", name: "Health", revision: 1 },
              { id: "growth", name: "Growth", revision: 3 },
            ],
      };
    if (p === "/api/commitments" || p === "/api/today")
      body = { items: removed ? [] : [c] };
    if (p.endsWith("/removal-preview")) {
      reviewCount++;
      body = {
        id: p.includes("capacities") ? "health" : "walk",
        kind: p.includes("capacities") ? "capacity" : "commitment",
        title: p.includes("capacities") ? "Health" : "Walk",
        revision: 1,
        commitments: [{ id: "walk", title: "Walk", revision: 2 }],
        progressEntries: 7,
        reminders: 1,
        previewToken: "a".repeat(64),
      };
    }
    if (p.endsWith("/remove")) {
      writes.push(route.request().postDataJSON());
      if (stale) {
        await route.fulfill({
          status: 409,
          json: { detail: "Progress changed. Review again." },
        });
        return;
      }
      removed = true;
      body = { removed: true };
    }
    await route.fulfill({ json: body });
  });
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto("/");
  await page.getByRole("button", { name: "Goals", exact: true }).click();
  return {
    writes,
    get reviewCount() {
      return reviewCount;
    },
  };
}

test("commitment removal previews impact, cancel is inert and final confirmation is required", async ({
  page,
}) => {
  const f = await fixture(page);
  await page.getByText("All commitments · 1", { exact: true }).click();
  await page.getByRole("button", { name: "Remove Walk", exact: true }).click();
  let dialog = page.getByRole("dialog", {
    name: "Remove commitment",
    exact: true,
  });
  await expect(
    dialog.getByText("7 progress entries and 1 active reminder."),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Remove commitment", exact: true }),
  ).toBeDisabled();
  await dialog.getByRole("button", { name: "Cancel removal" }).click();
  expect(f.writes).toHaveLength(0);
  await page.getByRole("button", { name: "Remove Walk", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "Remove commitment", exact: true });
  await dialog.getByRole("checkbox").check();
  await dialog
    .getByRole("button", { name: "Remove commitment", exact: true })
    .click();
  await expect(dialog).toHaveCount(0);
  expect(f.writes).toEqual([{ previewToken: "a".repeat(64), confirmed: true }]);
});

test("capacity move shows destination and sends its reviewed revision on narrow mobile", async ({
  page,
}) => {
  const f = await fixture(page);
  await page.getByRole("button", { name: "Manage capacities" }).click();
  await page.getByRole("button", { name: "Remove Health" }).click();
  const dialog = page.getByRole("dialog", {
    name: "Remove capacity",
    exact: true,
  });
  await dialog.getByLabel("Destination capacity").selectOption("growth");
  await dialog.getByRole("checkbox").check();
  const box = await dialog.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(360);
  await dialog
    .getByRole("button", { name: "Move commitments and remove capacity" })
    .click();
  await expect(dialog).toHaveCount(0);
  expect(f.writes).toEqual([
    {
      previewToken: "a".repeat(64),
      confirmed: true,
      operation: "move",
      targetCapacityId: "growth",
      targetRevision: 3,
    },
  ]);
});

test("cascade needs separate explicit confirmation and stale impact remains visible", async ({
  page,
}) => {
  const f = await fixture(page, true);
  await page.getByRole("button", { name: "Manage capacities" }).click();
  await page.getByRole("button", { name: "Remove Health" }).click();
  const dialog = page.getByRole("dialog", {
    name: "Remove capacity",
    exact: true,
  });
  await dialog
    .getByLabel("What should happen to these commitments?")
    .selectOption("cascade");
  const remove = dialog.getByRole("button", {
    name: "Remove capacity and all commitments",
  });
  await expect(remove).toBeDisabled();
  await dialog.getByRole("checkbox").check();
  await remove.click();
  await expect(dialog.getByRole("alert")).toContainText("Progress changed");
  expect(f.writes).toHaveLength(1);
  expect(f.writes[0].operation).toBe("cascade");
  await dialog.getByRole("button", { name: "Review current impact" }).click();
  await expect.poll(() => f.reviewCount).toBe(2);
  await expect(dialog.getByRole("checkbox")).not.toBeChecked();
  expect(f.writes).toHaveLength(1);
});
