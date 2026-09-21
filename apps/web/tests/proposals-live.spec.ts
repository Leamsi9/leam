import { chooseConversation } from "./navigation";
import { test, expect } from "@playwright/test";

test("a synthetic suggestion approved in the live mobile companion becomes a persisted Today habit", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page
    .locator("input[name=password]")
    .fill("local-browser-acceptance-only");
  await page.getByRole("button", { name: /Enter Leam/ }).click();
  await expect(
    page.getByRole("button", { name: "Companion", exact: true }),
  ).toBeVisible();
  const headers = { origin: "http://127.0.0.1:46400" };
  const threadResponse = await page.request.post("/api/companion/threads", {
    headers,
    data: { requestId: crypto.randomUUID() },
  });
  expect(threadResponse.ok(), await threadResponse.text()).toBeTruthy();
  const thread = (await threadResponse.json()).thread.thread_id;
  const title = "Approved practice " + Date.now();
  const proposal = await page.request.post("/api/proposals", {
    headers,
    data: {
      requestId: crypto.randomUUID(),
      threadId: thread,
      operation: "commitment.create",
      input: {
        title,
        kind: "habit",
        measure: "minutes",
        target: 20,
        timezone: "Europe/London",
      },
      reason: "Synthetic acceptance suggestion; no model call",
    },
  });
  expect(proposal.ok()).toBeTruthy();
  const before = await (await page.request.get("/api/commitments")).json();
  expect(before.items.some((c: any) => c.title === title)).toBeFalsy();
  await page.getByRole("button", { name: "Companion", exact: true }).click();
  await chooseConversation(page, thread);
  await expect(page.getByText(title, { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Approve this change", exact: true })
    .click();
  await expect(
    page.getByText("Change completed", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Companion", exact: true }).click();
  await chooseConversation(page, thread);
  await expect(
    page.getByText("Change completed", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Today", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: title, exact: true }),
  ).toBeVisible();
  const after = await (await page.request.get("/api/commitments")).json();
  expect(after.items.filter((c: any) => c.title === title)).toHaveLength(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
