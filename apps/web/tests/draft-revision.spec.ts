import { test, expect } from "@playwright/test";
test("reopened commitment draft must keep its original revision when another writer changed the item", async ({
  page,
}) => {
  let item: any = {
    id: "walk",
    revision: 1,
    title: "Walk",
    kind: "habit",
    measure: "boolean",
    target: 1,
    status: "active",
    notes: "Original note",
    reward: "",
    timezone: "Europe/London",
    capacityId: null,
    startDate: null,
    endDate: null,
    reminderTime: null,
  };
  const writes: any[] = [];
  await page.route("**/api/**", async (route) => {
    const u = new URL(route.request().url());
    let body: any = { items: [] };
    if (u.pathname === "/api/auth/status") body = { authenticated: true };
    if (u.pathname === "/api/codex/threads") body = { data: [] };
    if (u.pathname === "/api/commitments") body = { items: [item] };
    if (u.pathname === "/api/today")
      body = {
        items: [
          {
            ...item,
            date: u.searchParams.get("date") || "2026-09-20",
            log: { value: 0, done: false, revision: 1 },
          },
        ],
      };
    if (
      u.pathname === "/api/commitments/walk" &&
      route.request().method() === "PATCH"
    ) {
      const value = route.request().postDataJSON();
      writes.push(value);
      if (value.revision !== item.revision) {
        await route.fulfill({
          status: 409,
          json: { detail: "Commitment changed; reload before editing" },
        });
        return;
      }
      item = { ...item, ...value, revision: item.revision + 1 };
      body = item;
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/?view=goals");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page
    .getByLabel("Commitment title", { exact: true })
    .fill("My resumed draft");
  await page.getByRole("button", { name: "Close commitment editor" }).click();
  item = { ...item, revision: 2, notes: "New note saved from another client" };
  await page.getByText("Progress date and filters", { exact: true }).click();
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await expect(
    page.getByText("New note saved from another client", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(
    page.getByLabel("Commitment title", { exact: true }),
  ).toHaveValue("My resumed draft");
  await page.getByRole("button", { name: "Save commitment" }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].revision).toBe(1);
  expect(item.notes).toBe("New note saved from another client");
});
