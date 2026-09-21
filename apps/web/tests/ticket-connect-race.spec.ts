import { test, expect } from "@playwright/test";
import { navigate } from "./navigation";

for (const newerDraft of [false, true])
  test(`first connection remount uses one intent; newer draft=${newerDraft}`, async ({
    page,
  }) => {
    let releaseFirst!: () => void;
    const firstResponse = new Promise<void>((r) => (releaseFirst = r));
    let firstStarted!: () => void;
    const firstRequest = new Promise<void>((r) => (firstStarted = r));
    let created = false,
      connections = 0;
    const sends: any[] = [];
    await page.addInitScript(() => {
      (window as any).EventSource = class {
        close() {}
      };
    });
    await page.route("**/api/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      const method = route.request().method();
      let body: any = { items: [], data: [] };
      if (p === "/api/auth/status") body = { authenticated: true };
      if (p === "/api/updates")
        body = {
          items: [
            {
              id: "ticket",
              title: "Continuation",
              summary: "Same-session messages",
              stage: "UAT",
              revision: 1,
              sequence: 1,
              deploymentId: "fixture",
              deployedAt: "2026-09-20T20:00:00Z",
              qa: { state: "passed" },
              uat: { state: "pending" },
            },
          ],
          unreadCount: 0,
          nextCursor: null,
        };
      if (p === "/api/updates/ticket/chat") {
        if (method === "POST") {
          created = true;
          connections++;
          if (connections === 1) {
            firstStarted();
            await firstResponse;
          }
        }
        body = {
          state: created ? "ready" : "notCreated",
          threadId: created ? "dedicated-ticket" : null,
          connected: created,
        };
      }
      if (p.startsWith("/api/codex/submissions/"))
        body = { state: "notSubmitted" };
      if (
        p === "/api/codex/threads/dedicated-ticket/turns" &&
        method === "POST"
      ) {
        sends.push(route.request().postDataJSON());
        body = { turn: { id: "turn-" + sends.length, status: "inProgress" } };
      }
      await route.fulfill({ json: body });
    });
    await page.goto("/");
    await navigate(page, "Updates");
    const card = page.getByRole("article", {
      name: "Continuation",
      exact: true,
    });
    const toggle = card.getByText("Chat about this update", { exact: true });
    await toggle.click();
    await expect(card.getByText("Loading ticket conversation…")).toHaveCount(0);
    await card
      .getByLabel("Message about this update")
      .fill("One explicit request");
    await card.getByRole("button", { name: "Send to Codex" }).click();
    await firstRequest;
    if (newerDraft)
      await card
        .getByLabel("Message about this update")
        .fill("Newer unsent draft");
    await toggle.click();
    await toggle.click();
    await expect(card.getByText("Loading ticket conversation…")).toHaveCount(0);
    await expect(card.getByLabel("Message about this update")).toHaveValue(
      newerDraft ? "Newer unsent draft" : "One explicit request",
    );
    await card.getByRole("button", { name: "Send to Codex" }).click();
    if (newerDraft) {
      await expect(card.getByRole("alert")).toContainText(
        "Check the saved message",
      );
      expect(sends).toHaveLength(0);
      await card.getByRole("button", { name: "Retry saved message" }).click();
    }
    await expect.poll(() => sends.length).toBe(1);
    releaseFirst();
    await page.waitForTimeout(500);
    await test.info().attach("submissions", {
      body: JSON.stringify(sends, null, 2),
      contentType: "application/json",
    });
    expect(sends).toHaveLength(1);
    if (newerDraft)
      await expect(card.getByLabel("Message about this update")).toHaveValue(
        "Newer unsent draft",
      );
  });
