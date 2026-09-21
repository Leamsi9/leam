import { test, expect } from "@playwright/test";
import { navigate } from "./navigation";
async function setup(page: any, request: any) {
  await page.addInitScript(() => {
    (window as any).EventSource = class {
      onmessage: any;
      constructor() {
        (window as any).stream = this;
      }
      close() {}
    };
  });
  const calls: any[] = [];
  let release!: () => void;
  const held = new Promise<void>((r) => (release = r));
  let cards = [request];
  await page.route("**/api/**", async (route: any) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [] };
    const thread = {
      id: "shared",
      name: "Shared fixture",
      transport: "ide-owner",
    };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/threads") body = { data: [thread] };
    if (path === "/api/codex/threads/shared")
      body = {
        thread,
        connected: true,
        generation: "owner-1",
        activeTurnId: "turn-a",
      };
    if (path === "/api/codex/threads/shared/turns")
      body = { data: [{ id: "turn-a", status: "inProgress", items: [] }] };
    if (path === "/api/codex/requests") body = { items: cards };
    if (path.startsWith("/api/codex/shared/requests/")) {
      calls.push({ path, body: route.request().postDataJSON() });
      await held;
      body = {
        state: "submitted",
        detail: "Response submitted to Codex. Waiting for its pending list.",
      };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await navigate(page, "Coding");
  await page.getByRole("button", { name: /^Open Shared fixture/ }).click();
  await page.evaluate(() => (window as any).stream.onopen?.());
  return {
    calls,
    release,
    set: (next: any[]) => (cards = next),
    refresh: () =>
      page.evaluate(() =>
        (window as any).stream.onmessage({
          data: JSON.stringify({
            topic: "codex.shared",
            payload: { threadId: "shared", connected: true },
          }),
        }),
      ),
  };
}
const base = {
  id: "ide:cap:opaque",
  transport: "ide-owner",
  generation: "owner-1",
  connected: true,
  method: "item/commandExecution/requestApproval",
  params: {
    threadId: "shared",
    itemId: "item",
    turnId: "turn-a",
    command: "printf 'review exactly this command'",
    cwd: "/fixture/project",
    reason: "Check fixture",
  },
  decisions: ["accept", "decline"],
};
for (const width of [390, 1440])
  test(`approval shows full review, excludes double send and resolves honestly at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const f = await setup(page, base);
    await expect(
      page.getByText(base.params.command, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("/fixture/project", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Allow once", exact: true }).click();
    await expect(page.getByText("Submitting your response…")).toBeVisible();
    expect(f.calls).toHaveLength(1);
    expect(f.calls[0].body.response).toEqual({ decision: "accept" });
    expect(f.calls[0].body.generation).toBe("owner-1");
    f.release();
    await expect(
      page.getByText(
        "Response submitted to Codex. Waiting for its pending list.",
      ),
    ).toBeVisible();
    f.set([
      {
        ...base,
        submitted: true,
        receipt: {
          state: "resolved",
          detail:
            "This request is no longer pending in Codex; this does not confirm tool execution.",
        },
      },
    ]);
    await f.refresh();
    await expect(
      page.getByText(/does not confirm tool execution/),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Allow once", exact: true }),
    ).toHaveCount(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  });
test("question displays option descriptions and transmits exact answers once; old callback cannot affect replacement", async ({
  page,
}) => {
  const q = {
    ...base,
    method: "item/tool/requestUserInput",
    params: {
      threadId: "shared",
      questions: [
        {
          id: "q",
          header: "Pick one",
          question: "Which choice?",
          options: [
            { label: "First", description: "First explanation" },
            { label: "Second", description: "Second explanation" },
          ],
        },
        {
          id: "note",
          header: "Your note",
          question: "Anything else?",
          options: null,
        },
      ],
    },
  };
  const f = await setup(page, q);
  await expect(page.getByText("First explanation")).toBeVisible();
  await page.getByRole("radio", { name: "Second Second explanation" }).check();
  await page
    .getByRole("textbox", { name: "Your note", exact: true })
    .fill(" exact note ");
  await page.getByRole("button", { name: "Reply to Codex" }).click();
  expect(f.calls).toHaveLength(1);
  expect(f.calls[0].body.response).toEqual({
    answers: {
      q: { answers: ["Second"] },
      note: { answers: [" exact note "] },
    },
  });
  f.set([{ ...base, id: "ide:cap:new" }]);
  await f.refresh();
  await expect(
    page.getByRole("button", { name: "Allow once", exact: true }),
  ).toBeEnabled();
  f.release();
  await expect(
    page.getByText(
      "Response submitted to Codex. Waiting for its pending list.",
    ),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Allow once", exact: true }),
  ).toBeEnabled();
});
