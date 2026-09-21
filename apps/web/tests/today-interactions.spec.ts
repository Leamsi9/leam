import { test, expect } from "@playwright/test";

test("a delayed progress save refreshes the currently selected day", async ({
  page,
}) => {
  let release!: () => void;
  const wait = new Promise<void>((r) => (release = r));
  let begin!: () => void;
  const started = new Promise<void>((r) => (begin = r));
  const dates: string[] = [];
  await page.route("**/api/**", async (route) => {
    const u = new URL(route.request().url());
    let body: any = { items: [] };
    if (u.pathname === "/api/auth/status") body = { authenticated: true };
    if (u.pathname === "/api/codex/threads") body = { data: [] };
    if (u.pathname === "/api/capacities" || u.pathname === "/api/commitments")
      body = { items: [] };
    if (u.pathname === "/api/today") {
      const date = u.searchParams.get("date") || "2026-09-20";
      dates.push(date);
      body = {
        items: [
          {
            id: "walk",
            revision: 1,
            title: "Walk",
            kind: "habit",
            measure: "minutes",
            target: 30,
            status: "active",
            date,
            log: {
              value: date.endsWith("21") ? 21 : 7,
              revision: 1,
              done: false,
            },
          },
        ],
      };
    }
    if (u.pathname.includes("/progress/")) {
      begin();
      await wait;
      body = { saved: true };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Today", exact: true }).click();
  await page.getByRole("spinbutton", { name: "Progress for Walk" }).fill("8");
  await page.getByRole("button", { name: "Save progress" }).click();
  await started;
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await expect(
    page.getByRole("spinbutton", { name: "Progress for Walk" }),
  ).toHaveValue("21");
  const before = dates.length;
  release();
  await expect.poll(() => dates.length).toBeGreaterThan(before);
  await expect(
    page.getByRole("spinbutton", { name: "Progress for Walk" }),
  ).toHaveValue("21");
  expect(dates.at(-1)).toBe("2026-09-21");
});

test("quick-add completion keeps the currently selected date", async ({
  page,
}) => {
  let release!: () => void;
  const wait = new Promise<void>((r) => (release = r));
  let begin!: () => void;
  const started = new Promise<void>((r) => (begin = r));
  const dates: string[] = [];
  await page.route("**/api/**", async (route) => {
    const u = new URL(route.request().url());
    let body: any = { items: [] };
    if (u.pathname === "/api/auth/status") body = { authenticated: true };
    if (u.pathname === "/api/codex/threads") body = { data: [] };
    if (u.pathname === "/api/capacities" || u.pathname === "/api/commitments")
      body = { items: [] };
    if (
      u.pathname === "/api/commitments" &&
      route.request().method() === "POST"
    ) {
      begin();
      await wait;
      body = { id: "new" };
    }
    if (u.pathname === "/api/today") {
      const day = u.searchParams.get("date") || "2026-09-20";
      dates.push(day);
      body = { items: [] };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Today", exact: true }).click();
  await page.getByRole("textbox", { name: "New commitment" }).fill("New task");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await started;
  await page.getByLabel("Viewing date").fill("2026-09-21");
  await expect.poll(() => dates.at(-1)).toBe("2026-09-21");
  const before = dates.length;
  release();
  await expect.poll(() => dates.length).toBeGreaterThan(before);
  expect(dates.at(-1)).toBe("2026-09-21");
});
