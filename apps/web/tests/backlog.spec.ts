import { test, expect } from "@playwright/test";
import { navigate } from "./navigation";

// Canonical Assessment + GET list shape from leam_api/backlog.py. These fixtures
// exercise the shipped consumer, not live inventory or deployment acceptance.
for (const viewport of [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
  { width: 1440, height: 900 },
]) {
  test(`compact delivery list and Kanban ${viewport.width}x${viewport.height}`, async ({
    page,
  }) => {
    const base = {
      currentStep: "Remaining implementation scope",
      percent: 35,
      blockers: [],
      assessedAt: new Date().toISOString(),
      stale: false,
      revision: 1,
    };
    const items = [
      {
        ...base,
        feature: "handover",
        title: "Review delivery",
        deliveryState: "handover",
        deliveryLane: "handover",
        rank: 1,
        priority: "high",
        owner: "main",
        worker: "session-01",
        nextAction: "Integrate candidate",
        dependencies: [],
        subtasks: [
          { id: "build", title: "Build artifact", state: "done" },
          { id: "review", title: "Review source", state: "todo" },
        ],
      },
      {
        ...base,
        feature: "ready",
        title: "Ready scope",
        deliveryState: "ready",
        rank: 2,
        priority: "normal",
        nextAction: "Select worker",
        subtasks: [],
      },
      { ...base, feature: "legacy", title: "Legacy scope", percent: 99, rank: 3 },
      {
        ...base,
        feature: "blocked",
        title: "Blocked scope",
        deliveryState: "blocked",
        rank: 4,
        priority: "high",
        blockers: ["Await provider access"],
        worker: "/home/private/session.json",
        owner: "file:///private/owner",
        dependencies: ["provider-access"],
        subtasks: [
          {
            id: "access",
            title: "Provider consent",
            state: "blocked",
            blocker: "Account participation required",
          },
        ],
      },
    ];
    let reads = 0;
    const writes: string[] = [];
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/backlog" && route.request().method() !== "GET")
        writes.push(route.request().method());
      let body: Record<string, unknown> = {};
      if (path === "/api/auth/status") body = { authenticated: true };
      if (path === "/api/codex/threads") body = { data: [] };
      if (path === "/api/backlog") {
        reads++;
        body = {
          items,
          checkedAt: Date.now() / 1000,
          staleAfterSeconds: 1200,
          estimateBasis: "deployment",
          heartbeatSeconds: 900,
          review: {
            reviewedAt: Date.now() / 1000,
            current: true,
            reviewedCount: items.length,
          },
        };
      }
      await route.fulfill({ json: body });
    });
    await page.setViewportSize(viewport);
    await page.goto("/");
    await navigate(page, "Backlog");
    const panel = page.getByRole("region", { name: "Build backlog" });
    await expect(
      panel.getByRole("button", { name: "List", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(panel.getByRole("article")).toHaveCount(4);
    await expect(panel.getByRole("article").first()).toHaveAccessibleName(
      "Review delivery",
    );
    const legacy = panel.getByRole("article", { name: "Legacy scope" });
    await expect(legacy).toContainText("Stage: Queued");
    await expect(legacy).toContainText("Build priority: 3");
    await expect(legacy).toContainText("Coordinator to select next action");
    const handover = panel.getByRole("article", { name: "Review delivery" });
    await expect(handover).toContainText("Worker");
    await expect(handover).toContainText("session-01");
    await expect(
      handover.getByText("Build artifact", { exact: false }),
    ).not.toBeVisible();
    await handover
      .getByLabel("Details and subtasks for Review delivery (2)", {
        exact: true,
      })
      .click();
    await expect(
      handover.getByRole("list", { name: "Subtasks" }),
    ).toContainText("Build artifact");
    await expect(handover).toContainText("35% estimated to deployment");
    await expect(handover).toContainText("Independent of subtask count");
    await expect(panel).not.toContainText("/home/private");
    await expect(panel).not.toContainText("file:///private");
    await expect(panel).toContainText("activity is not inferred");
    await expect(
      panel.locator("input, select, textarea, [draggable=true]"),
    ).toHaveCount(0);
    await panel.getByRole("button", { name: "Kanban", exact: true }).click();
    await expect(
      panel.getByRole("button", { name: "Kanban", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(
      panel
        .getByRole("region", { name: "Ready to deploy work", exact: true })
        .getByRole("article"),
    ).toHaveCount(1);
    await expect(
      panel.getByRole("region", { name: "Queued work", exact: true }),
    ).toContainText("Legacy scope");
    await expect(
      panel.getByRole("region", { name: "In progress work", exact: true }),
    ).toContainText("No tickets");
    const blocked = panel.getByRole("region", {
      name: "Blocked work",
      exact: true,
    });
    await blocked
      .getByLabel("Details and subtasks for Blocked scope (1)", { exact: true })
      .click();
    await expect(blocked).toContainText("Account participation required");
    await expect(
      panel.getByRole("region", { name: "Complete work", exact: true }),
    ).toHaveCount(0);
    const before = reads;
    await panel
      .getByRole("button", { name: "Reload saved assessments" })
      .click();
    await expect.poll(() => reads).toBeGreaterThan(before);
    await expect(panel.getByRole("article")).toHaveCount(4);
    expect(writes).toEqual([]);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1,
    );
    expect(overflow).toBe(false);
    await panel.getByRole("button", { name: "List", exact: true }).click();
    await expect(panel.getByRole("article").first()).toHaveAccessibleName(
      "Review delivery",
    );
  });
}
