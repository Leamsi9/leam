import { navigate } from "./navigation";
import { test, expect } from "@playwright/test";

test("UAT completion moves below active updates and regression brings it back", async ({
  page,
}) => {
  const item = (id: string, stage: string) => ({
    id,
    title: id,
    summary: "Deployed fixture",
    stage,
    completed: stage === "Complete",
    deployedAt: "2026-09-20T20:00:00Z",
    deploymentId: id,
    revision: 1,
    qa: { state: "passed" },
    uat: { state: stage === "Complete" ? "passed" : "pending" },
  });
  let items = [
    item("Ready feature", "UAT"),
    item("Finished feature", "Complete"),
    { ...item("Old pending", "UAT"), title: "Ready feature", superseded: true },
    { ...item("Failed QA feature", "QA"), qa: { state: "failed" } },
  ];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/threads") body = { data: [] };
    if (path.endsWith("/uat")) {
      const state = route.request().postDataJSON().state;
      const id = decodeURIComponent(path.split("/")[3]);
      items = items.map((i) =>
        i.id === id
          ? {
              ...i,
              stage: state === "passed" ? "Complete" : "Fail",
              completed: state === "passed",
              uat: { state },
            }
          : i,
      );
    }
    if (path === "/api/updates")
      body = { items, sequence: 2, unreadCount: 0, nextCursor: null };
    await route.fulfill({ json: body });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await navigate(page, "Updates");
  await expect(
    page.getByRole("article", { name: "Ready feature", exact: true }),
  ).toHaveCount(1);
  const qaStage = page
    .getByRole("article", { name: "Failed QA feature" })
    .locator('[aria-current="step"]');
  await expect(qaStage).toHaveText("QA");
  await expect(qaStage).toHaveCSS("background-color", "rgb(127, 29, 29)");
  await expect(
    page
      .getByRole("article", { name: "Ready feature", exact: true })
      .getByRole("button", { name: "I tested this — pass UAT" }),
  ).toBeEnabled();
  const history = page.getByRole("region", { name: "Changelog", exact: true });
  await expect(
    history.getByRole("article", { name: "Finished feature" }),
  ).toBeVisible();
  await expect(
    history.getByRole("article", { name: "Ready feature" }),
  ).toHaveCount(0);
  await page
    .getByRole("article", { name: "Ready feature" })
    .getByRole("button", { name: "I tested this — pass UAT" })
    .click();
  await expect(
    history.getByRole("article", { name: "Ready feature" }),
  ).toBeVisible();
  await history
    .getByRole("article", { name: "Ready feature" })
    .getByRole("button", { name: "Report UAT failure" })
    .click();
  await expect(
    history.getByRole("article", { name: "Ready feature" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("article", { name: "Ready feature" }),
  ).toBeVisible();
});

test("unread menu and ticket highlights clear only on explicit read and return for new QA", async ({
  page,
}) => {
  let item: any = {
    id: "current",
    title: "Session continuation",
    summary: "Keep the same session",
    stage: "UAT",
    completed: false,
    sequence: 2,
    revision: 2,
    unread: true,
    deployedAt: "2026-09-20T20:00:00Z",
    deploymentId: "fixture-current",
    qa: { state: "passed" },
    uat: { state: "pending" },
  };
  let seenCalls = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/threads") body = { data: [] };
    if (path === "/api/updates/status")
      body = { unreadCount: item.unread ? 1 : 0 };
    if (path === "/api/updates/current/seen") {
      expect(route.request().postDataJSON()).toEqual({
        sequence: item.sequence,
      });
      seenCalls++;
      item = { ...item, unread: false };
    }
    if (path === "/api/updates")
      body = {
        items: [
          item,
          {
            ...item,
            id: "old",
            title: "Obsolete deployment",
            superseded: true,
          },
        ],
        unreadCount: item.unread ? 1 : 0,
        sequence: item.sequence,
        nextCursor: null,
      };
    await route.fulfill({ json: body });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const menu = page.getByRole("button", { name: /^More/ });
  await expect(
    menu.getByRole("status", { name: "1 unread updates" }),
  ).toBeVisible();
  await navigate(page, "Updates");
  const card = page.getByRole("article", {
    name: "Session continuation",
    exact: true,
  });
  await expect(card).toHaveAttribute("data-unread", "true");
  await expect(card).toHaveCSS("border-top-color", "rgb(245, 158, 11)");
  await expect(card.locator('[aria-current="step"]')).toHaveText("UAT");
  await expect(
    card.getByRole("button", { name: "I tested this — pass UAT" }),
  ).toBeEnabled();
  await expect(
    page.getByText("Previous deployments", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("article", { name: "Obsolete deployment" }),
  ).toHaveCount(0);
  expect(seenCalls).toBe(0);
  await card.getByRole("button", { name: "Mark as read", exact: true }).click();
  await expect(card).toHaveAttribute("data-unread", "false");
  await expect(menu.getByRole("status")).toHaveCount(0);
  await page.reload();
  await navigate(page, "Updates");
  await expect(card).toHaveAttribute("data-unread", "false");
  item = {
    ...item,
    sequence: 3,
    revision: 3,
    unread: true,
    stage: "QA",
    qa: { state: "failed" },
  };
  await page.getByRole("button", { name: "Refresh updates" }).click();
  await expect(card).toHaveAttribute("data-unread", "true");
  await expect(card.locator('[aria-current="step"]')).toHaveCSS(
    "background-color",
    "rgb(127, 29, 29)",
  );
  expect(seenCalls).toBe(1);
});
