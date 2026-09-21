import { test, expect } from "@playwright/test";
import { navigate } from "./navigation";
test("shared Stop keeps exact turn and generation, excludes double click and leaves steering available", async ({
  page,
}) => {
  let release!: () => void;
  const wait = new Promise<void>((r) => (release = r));
  const controls: any[] = [];
  await page.addInitScript(() => {
    (window as any).EventSource = class {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [] };
    const thread = {
      id: "shared-fixture",
      name: "Shared fixture",
      transport: "ide-owner",
      generation: "binding-1",
    };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/threads") body = { data: [thread] };
    if (path === "/api/codex/threads/shared-fixture")
      body = {
        thread,
        connected: true,
        generation: "binding-1",
        activeTurnId: "turn-1",
      };
    if (path === "/api/codex/threads/shared-fixture/turns")
      body = { data: [{ id: "turn-1", status: "inProgress", items: [] }] };
    if (path === "/api/codex/shared/interrupt") {
      controls.push(route.request().postDataJSON());
      await wait;
      body = {
        state: "requested",
        detail:
          "Stop requested for this turn and its child agents. The goal remains active.",
      };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await navigate(page, "Coding");
  await page.getByRole("button", { name: /^Open Shared fixture/ }).click();
  const stop = page.getByRole("button", { name: "Stop current shared turn" });
  await stop.click();
  await expect(stop).toBeDisabled();
  expect(controls).toEqual([{ turnId: "turn-1", generation: "binding-1" }]);
  release();
  await expect(page.getByRole("status")).toContainText("goal remains active");
  await expect(
    page.getByRole("button", { name: "Send message" }),
  ).toBeVisible();
});

test("a stale stop reply cannot disable or annotate a newer turn", async ({
  page,
}) => {
  let active = "turn-a",
    release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.addInitScript(() => {
    (window as any).EventSource = class {
      onmessage: any;
      constructor() {
        (window as any).testStream = this;
      }
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const thread = {
      id: "shared-fixture",
      name: "Shared fixture",
      transport: "ide-owner",
    };
    let body: any = { items: [], data: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/threads") body = { data: [thread] };
    if (path === "/api/codex/threads/shared-fixture")
      body = {
        thread,
        connected: true,
        generation: "binding",
        activeTurnId: active,
      };
    if (path === "/api/codex/threads/shared-fixture/turns")
      body = { data: [{ id: active, status: "inProgress", items: [] }] };
    if (path === "/api/codex/shared/interrupt") {
      await held;
      body = { state: "requested", detail: "Old-turn receipt" };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await navigate(page, "Coding");
  await page.getByRole("button", { name: /^Open Shared fixture/ }).click();
  const stop = page.getByRole("button", { name: "Stop current shared turn" });
  await stop.click();
  await expect(stop).toBeDisabled();
  active = "turn-b";
  await page.evaluate(() =>
    (window as any).testStream.onmessage({
      data: JSON.stringify({
        topic: "codex.shared",
        payload: { threadId: "shared-fixture", connected: true },
      }),
    }),
  );
  await expect(stop).toBeEnabled();
  release();
  await expect(page.getByText("Old-turn receipt", { exact: true })).toHaveCount(
    0,
  );
  await expect(stop).toBeEnabled();
});
