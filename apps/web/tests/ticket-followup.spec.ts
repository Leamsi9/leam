import { test, expect, type Page } from "@playwright/test";
import { navigate } from "./navigation";

async function fixture(page: Page, mode: "success" | "uncertain" | "rejected") {
  const sends: any[] = [],
    reconciles: any[] = [];
  let active = true,
    pending = false;
  let release: () => void = () => {};
  const wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.addInitScript(() => {
    (window as any).EventSource = class {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    let body: any = { items: [], data: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/updates")
      body = {
        items: [
          {
            id: "ticket",
            title: "Active ticket",
            summary: "Fixture",
            stage: "UAT",
            revision: 1,
            sequence: 1,
            deploymentId: "fixture",
            deployedAt: "2026-09-21T00:00:00Z",
            qa: { state: "passed" },
            uat: { state: "pending" },
          },
        ],
        unreadCount: 0,
        nextCursor: null,
      };
    if (path === "/api/updates/ticket/chat")
      body = { state: "ready", threadId: "fixture-ticket", connected: true };
    if (path.startsWith("/api/codex/submissions/"))
      body = { state: pending ? "pending" : "notSubmitted" };
    if (path.endsWith("/reconcile")) {
      reconciles.push(route.request().postDataJSON());
      body = { state: "pending" };
    }
    if (path === "/api/codex/threads/fixture-ticket/turns") {
      if (method === "POST") {
        const request = route.request().postDataJSON();
        sends.push(request);
        await wait;
        if (mode === "uncertain") {
          pending = true;
          await route.abort();
          return;
        }
        if (mode === "rejected" && sends.length === 1) {
          active = false;
          await route.fulfill({
            status: 409,
            headers: { "X-Leam-Action-Reserved": "no" },
            json: {
              detail:
                "Follow-up was not accepted. Send your saved draft again.",
            },
          });
          return;
        }
        body = {
          turn: {
            id: request.expectedTurnId || "next-turn",
            status: "inProgress",
          },
          operation: request.expectedTurnId ? "steer" : "start",
        };
      } else
        body = {
          data: [
            {
              id: "active-turn",
              status: active ? "inProgress" : "completed",
              items: [
                {
                  id: "original",
                  type: "userMessage",
                  content: [{ type: "text", text: "Original question" }],
                },
                {
                  id: "answer",
                  type: "agentMessage",
                  text: "Working on the original question",
                },
              ],
            },
          ],
        };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await navigate(page, "Updates");
  const card = page.getByRole("article", {
    name: "Active ticket",
    exact: true,
  });
  const open = async () => {
    await card.getByText("Chat about this update", { exact: true }).click();
  };
  await open();
  await expect(
    card.getByText("Working on the original question", { exact: true }),
  ).toBeVisible();
  return { card, sends, reconciles, release, open };
}

test("active ticket accepts a targeted follow-up and locks only its delivery", async ({
  page,
}) => {
  const f = await fixture(page, "success");
  const text = "  Correct this — please.\n";
  await f.card.getByLabel("Message about this update").fill(text);
  const send = f.card.getByRole("button", { name: "Send to Codex" });
  await expect(send).toBeEnabled();
  await send.click();
  await expect.poll(() => f.sends.length).toBe(1);
  await expect(f.card.getByRole("button", { name: "Sending…" })).toBeDisabled();
  expect(f.sends[0].text).toBe(text);
  expect(f.sends[0].expectedTurnId).toBe("active-turn");
  f.release();
  await expect(f.card.getByLabel("Message about this update")).toHaveValue("");
  await f.card
    .getByLabel("Message about this update")
    .fill("Another correction");
  await expect(send).toBeEnabled();
  await expect(
    f.card.getByText("Original question", { exact: true }),
  ).toBeVisible();
});

test("lost follow-up receipt retains its target across panel reopen without redispatch", async ({
  page,
}) => {
  const f = await fixture(page, "uncertain");
  f.release();
  await f.card
    .getByLabel("Message about this update")
    .fill("Keep my correction");
  await f.card.getByRole("button", { name: "Send to Codex" }).click();
  await expect(f.card.getByRole("alert")).toBeVisible();
  await f.open();
  await f.open();
  await f.card.getByRole("button", { name: "Retry saved message" }).click();
  await expect.poll(() => f.reconciles.length).toBe(1);
  expect(f.reconciles[0].expectedTurnId).toBe("active-turn");
  expect(f.sends).toHaveLength(1);
  await expect(f.card.getByRole("alert")).toContainText("has not been resent");
});

test("definitive stale-turn rejection keeps draft and requires a new explicit send", async ({
  page,
}) => {
  const f = await fixture(page, "rejected");
  f.release();
  await f.card
    .getByLabel("Message about this update")
    .fill("Preserve this correction");
  const send = f.card.getByRole("button", { name: "Send to Codex" });
  await send.click();
  await expect(f.card.getByRole("alert")).toContainText("not accepted");
  await expect(f.card.getByLabel("Message about this update")).toHaveValue(
    "Preserve this correction",
  );
  await expect(
    f.card.getByText("Codex is working…", { exact: false }),
  ).not.toBeVisible();
  expect(f.sends).toHaveLength(1);
  await send.click();
  await expect.poll(() => f.sends.length).toBe(2);
  expect(f.sends[1]).not.toHaveProperty("expectedTurnId");
  expect(f.sends[1].requestId).not.toBe(f.sends[0].requestId);
});
