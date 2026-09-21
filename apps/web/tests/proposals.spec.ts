import { chooseConversation } from "./navigation";
import { test, expect } from "@playwright/test";

test("companion suggestion creates nothing before explicit mobile approval", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let proposal: any = {
    id: "p",
    operation: "commitment.create",
    state: "pending",
    reason: "You asked to make time for French",
    review: {
      before: null,
      after: {
        title: "Practice French",
        kind: "habit",
        measure: "minutes",
        target: 20,
        timezone: "Europe/London",
      },
    },
    input: { title: "Practice French" },
    result: null,
  };
  let approvals = 0;
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/api/proposals/p/approve") {
      approvals++;
      proposal = {
        ...proposal,
        state: "complete",
        result: { id: "task", title: "Practice French" },
      };
      await route.fulfill({ json: proposal });
      return;
    }
    const body =
      p === "/api/auth/status"
        ? { authenticated: true }
        : p === "/api/companion/threads"
          ? { threads: [{ thread_id: "t", title: "Planning" }] }
          : p === "/api/companion/threads/t"
            ? { messages: [] }
            : p === "/api/proposals"
              ? { items: [proposal] }
              : { items: [], data: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Companion", exact: true }).click();
  await chooseConversation(page, "t");
  await expect(
    page.getByRole("heading", { name: "Suggested: new commitment" }),
  ).toBeVisible();
  await expect(
    page.getByText("Practice French", { exact: true }),
  ).toBeVisible();
  expect(approvals).toBe(0);
  await page
    .getByRole("button", { name: "Approve this change", exact: true })
    .click();
  await expect(
    page.getByText("Change completed", { exact: true }),
  ).toBeVisible();
  expect(approvals).toBe(1);
  await page.reload();
  await page.getByRole("button", { name: "Companion", exact: true }).click();
  await chooseConversation(page, "t");
  await expect(
    page.getByText("Change completed", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve this change", exact: true }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("progress and partial edit approvals identify the record and day independently of the reason", async ({
  page,
}) => {
  const items = [
    {
      id: "progress",
      operation: "commitment.progress",
      state: "pending",
      reason: "Requested change",
      review: {
        before: {
          title: "Practice piano",
          date: "2026-09-19",
          value: 7,
          completed: false,
        },
        after: { date: "2026-09-19", action: "Mark complete" },
      },
    },
    {
      id: "edit",
      operation: "commitment.edit",
      state: "pending",
      reason: "Requested change",
      review: {
        before: { title: "Call plumber", notes: "Old notes" },
        after: { notes: "Discuss kitchen sink" },
      },
    },
  ];
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    await route.fulfill({
      json:
        p === "/api/auth/status"
          ? { authenticated: true }
          : p === "/api/companion/threads"
            ? { threads: [{ thread_id: "t", title: "Planning" }] }
            : p === "/api/companion/threads/t"
              ? { messages: [] }
              : p === "/api/proposals"
                ? { items }
                : { items: [], data: [] },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Companion", exact: true }).click();
  await chooseConversation(page, "t");
  const progress = page
    .getByRole("article")
    .filter({
      has: page.getByRole("heading", { name: "Suggested: progress update" }),
    });
  await expect(
    progress.getByRole("heading", { name: "Practice piano", exact: true }),
  ).toBeVisible();
  await expect(
    progress.getByText("Progress for 2026-09-19", { exact: true }),
  ).toBeVisible();
  await expect(
    progress.getByText("Mark complete", { exact: true }),
  ).toBeVisible();
  const edit = page
    .getByRole("article")
    .filter({
      has: page.getByRole("heading", { name: "Suggested: commitment edit" }),
    });
  await expect(
    edit.getByRole("heading", { name: "Call plumber", exact: true }),
  ).toBeVisible();
  await expect(
    edit.getByText("Discuss kitchen sink", { exact: true }),
  ).toBeVisible();
});
