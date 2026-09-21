import { test, expect } from "@playwright/test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("live Codex reads a workspace through the authenticated Leam UI", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const workspace = mkdtempSync(join(tmpdir(), "leam-codex-acceptance-"));
  const marker = "LEAM_LIVE_" + Date.now();
  writeFileSync(join(workspace, "acceptance-input.txt"), marker + "\n");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page
    .locator("input[name=password]")
    .fill("local-browser-acceptance-only");
  await page.getByRole("button", { name: /Enter Leam/ }).click();
  await page.getByText("Start a new coding session", { exact: true }).click();
  await page
    .getByRole("textbox", { name: "Workspace directory" })
    .fill(workspace);
  const created = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/codex/threads") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Create session" }).click();
  const result = await (await created).json();
  expect(result.thread?.id).toBeTruthy();
  await expect(
    page.getByRole("textbox", { name: "Message Codex" }),
  ).toBeEnabled();
  await page
    .getByRole("textbox", { name: "Message Codex" })
    .fill(
      "Integration acceptance: use a tool to read acceptance-input.txt in this workspace, then reply with its exact contents. Also inspect the engineering policy supplied by this application: use a tool to read its minor-work-protocol.md, and report the package version and the protocol heading. Do not edit any files.",
    );
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.locator(".message.assistant").filter({ hasText: marker }),
  ).toBeVisible({ timeout: 90_000 });
  await expect(
    page.locator(".message.assistant").filter({ hasText: "0.0.19" }),
  ).toBeVisible({ timeout: 90_000 });
  await expect(
    page
      .locator(".message.assistant")
      .filter({ hasText: "Minor Work Protocol" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop turn" })).toHaveCount(0, {
    timeout: 30_000,
  });
  const history = await page.request
    .get(`/api/codex/threads/${result.thread.id}/turns`)
    .then((r) => r.json());
  expect(
    history.data
      .flatMap((t: any) => t.items)
      .some((i: any) => i.type === "commandExecution"),
  ).toBeTruthy();
  const commands = history.data
    .flatMap((t: any) => t.items)
    .filter((i: any) => i.type === "commandExecution");
  expect(
    commands.some((i: any) =>
      JSON.stringify(i).includes("agent-protocols/minor-work-protocol.md"),
    ),
  ).toBeTruthy();
  writeFileSync(
    "../../.artifacts/codex-live.json",
    JSON.stringify({
      threadId: result.thread.id,
      workspace,
      verifiedAt: new Date().toISOString(),
      marker,
      readViaTool: true,
      protocolReadViaTool: true,
      protocolVersion: "0.0.19",
    }),
  );
  await page.screenshot({
    path: "../../.artifacts/codex-live-390.png",
    fullPage: true,
  });
});
