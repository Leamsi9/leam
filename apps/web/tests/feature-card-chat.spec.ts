import { test, expect } from "@playwright/test";
import { navigate } from "./navigation";

for (const width of [390, 1440])
  test(`feature conversation and draft continue from Backlog to Updates ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    let published = false;
    const opened: string[] = [];
    const feature = {
      feature: "coherent-feature",
      title: "Coherent cards",
      rationale: "Keep product discussions together",
      scope: "Shared canonical conversations",
      revision: 1,
      currentStep: "Implementing",
      percent: 50,
      deliveryState: "in_progress",
      worker: "card-worker",
      assessedAt: new Date().toISOString(),
      blockers: [],
      subtasks: [],
      priority: "high",
      rank: 0,
    };
    await page.addInitScript(() => {
      (window as any).EventSource = class {
        close() {}
      };
    });
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let data: any = { items: [], data: [] };
      if (path === "/api/auth/status") data = { authenticated: true };
      if (path === "/api/backlog")
        data = {
          items: published ? [] : [feature],
          ordering: { revision: 1 },
          review: { current: true },
        };
      if (path === "/api/updates") {
        published = true;
        data = {
          items: [
            {
              ...feature,
              id: "deployment-second",
              summary: "Delivered",
              deploymentId: "release-two",
              deployedAt: new Date().toISOString(),
              stage: "UAT",
              qa: { state: "passed" },
              uat: { state: "pending" },
            },
          ],
          sequence: 1,
          unreadCount: 0,
        };
      }
      if (path === "/api/features/coherent-feature/chat") {
        opened.push(path);
        data = {
          state: "ready",
          connected: true,
          threadId: "same-feature-session",
          olderConversations: [
            {
              updateId: "older-deployment",
              deploymentId: "release-one",
              threadId: "old-feature-session",
            },
          ],
        };
      }
      if (path === "/api/updates/older-deployment/chat") {
        opened.push(path);
        data = {
          state: "ready",
          connected: true,
          threadId: "old-feature-session",
        };
      }
      if (path === "/api/codex/threads/same-feature-session/turns")
        data = {
          data: [
            {
              id: "answer-turn",
              status: "completed",
              items: [
                {
                  id: "answer",
                  type: "agentMessage",
                  text: "The existing feature conversation",
                },
              ],
            },
          ],
        };
      if (path === "/api/codex/threads/old-feature-session/turns")
        data = {
          data: [
            {
              id: "old-turn",
              status: "completed",
              items: [
                {
                  id: "old-answer",
                  type: "agentMessage",
                  text: "Preserved older conversation",
                },
              ],
            },
          ],
        };
      await route.fulfill({ json: data });
    });
    await page.goto("/?view=backlog");
    let card = page.getByRole("article", {
      name: "Coherent cards",
      exact: true,
    });
    await card.locator(":scope > details > summary").click();
    expect(opened).toEqual([]);
    await card.getByText("Context and links", { exact: true }).click();
    await expect(
      card.getByText("Keep product discussions together", { exact: false }),
    ).toBeVisible();
    await card.getByText("Chat about this ticket", { exact: true }).click();
    await expect(
      card.getByText("The existing feature conversation"),
    ).toBeVisible();
    await card
      .getByLabel("Message about this ticket")
      .fill("Retain this feature draft");
    await navigate(page, "Updates");
    card = page.getByRole("article", { name: "Coherent cards", exact: true });
    await card.getByText("Chat about this update", { exact: true }).click();
    await expect(
      card.getByText("The existing feature conversation"),
    ).toBeVisible();
    await expect(card.getByLabel("Message about this update")).toHaveValue(
      "Retain this feature draft",
    );
    await expect(
      card.getByRole("button", { name: "Dictate", exact: true }),
    ).toBeVisible();
    await card
      .getByRole("combobox", { name: "Ticket conversation", exact: true })
      .selectOption("older-deployment");
    await expect(card.getByText("Preserved older conversation")).toBeVisible();
    await card
      .getByRole("combobox", { name: "Ticket conversation", exact: true })
      .selectOption("feature:coherent-feature");
    await expect(card.getByLabel("Message about this update")).toHaveValue(
      "Retain this feature draft",
    );
    expect(
      opened.filter((path) => path === "/api/features/coherent-feature/chat")
        .length,
    ).toBeGreaterThanOrEqual(2);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });

test("an uncertain legacy message keeps its original deployment binding", async ({
  page,
}) => {
  const calls: string[] = [];
  await page.addInitScript(() => {
    sessionStorage.setItem(
      "leam-ticket-pending:old-update",
      JSON.stringify({
        id: "unchanged-request",
        text: "My uncertain old message",
      }),
    );
    (window as any).EventSource = class {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: any = { items: [], data: [] };
    if (path === "/api/auth/status") data = { authenticated: true };
    if (path === "/api/updates")
      data = {
        items: [
          {
            id: "old-update",
            feature: "stable-feature",
            title: "Saved message",
            summary: "Published",
            stage: "UAT",
            qa: { state: "passed" },
            uat: { state: "pending" },
            deployedAt: new Date().toISOString(),
          },
        ],
      };
    if (path.endsWith("/chat")) {
      calls.push(path);
      data = { state: "ready", threadId: "legacy-thread", connected: true };
    }
    await route.fulfill({ json: data });
  });
  await page.goto("/?view=updates");
  const card = page.getByRole("article", {
    name: "Saved message",
    exact: true,
  });
  await card.getByText("Chat about this update", { exact: true }).click();
  await expect(card.getByLabel("Message about this update")).toHaveValue(
    "My uncertain old message",
  );
  await expect(
    card.getByRole("combobox", { name: "Ticket conversation", exact: true }),
  ).toHaveValue("old-update");
  expect(calls).toEqual(["/api/updates/old-update/chat"]);
  expect(
    await page.evaluate(
      () =>
        JSON.parse(sessionStorage.getItem("leam-ticket-pending:old-update")!)
          .id,
    ),
  ).toBe("unchanged-request");
});
