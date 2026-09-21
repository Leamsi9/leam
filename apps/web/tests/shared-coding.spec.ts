import { test, expect } from "@playwright/test";

for (const width of [390, 1440]) {
  test(`shared owner follow-up and reconnect at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => {
      class Events {
        onmessage: any;
        onopen: any;
        constructor() {
          (window as any).events = this;
        }
        close() {}
      }
      (window as any).EventSource = Events;
    });
    const id = "00000000-0000-0000-0000-000000000001";
    const thread = {
      id,
      name: "Shared build fixture",
      transport: "ide-owner",
      model: "gpt-6-astra",
      reasoningEffort: "high",
      cwd: "/fixture",
    };
    const sent: any[] = [];
    let connected = true;
    let generation = "first-owner-binding";
    let message = "Owner message from Codex";
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: any = {};
      if (path === "/api/auth/status")
        body = { authenticated: true, configured: true };
      if (path === "/api/codex/threads") body = { data: [thread] };
      if (path === `/api/codex/threads/${id}` || path.endsWith("/connect"))
        body = {
          thread,
          connected,
          generation,
          activeTurnId: "active-turn",
          truncated: true,
        };
      if (path.endsWith("/goal"))
        body = { goal: { objective: "Build fixture", status: "active" } };
      if (path.endsWith("/turns")) {
        if (route.request().method() === "POST") {
          sent.push(route.request().postDataJSON());
          body = {
            turn: { id: "active-turn", status: "inProgress" },
            operation: "steer",
          };
        } else
          body = {
            data: [
              {
                id: "active-turn",
                status: "inProgress",
                items: [
                  { id: "assistant", type: "agentMessage", text: message },
                ],
              },
            ],
            nextCursor: null,
          };
      }
      if (path === "/api/codex/requests")
        body = {
          items: [
            {
              id: "ide:request",
              unsupported: true,
              method: "item/tool/requestUserInput",
              params: { threadId: id },
              detail: "Answer this request in the original Codex window.",
            },
          ],
        };
      if (path === "/api/imports") body = { items: [] };
      if (path.includes("/submissions/")) body = { state: "notSubmitted" };
      await route.fulfill({ json: body });
    });
    await page.goto("/");
    await page.getByRole("button", { name: /^Open Shared build fixture/ }).click();
    const input = page.getByRole("textbox", { name: "Message Codex" });
    await expect(input).toBeEnabled();
    await expect(
      page.getByText(/Shared with Codex · gpt-6-astra · high/),
    ).toBeVisible();
    await expect(page.getByText("Owner message from Codex")).toBeVisible();
    await expect(page.getByRole("button", { name: "Stop turn" })).toHaveCount(
      0,
    );
    await page.evaluate((id) => {
      (window as any).events.onmessage({
        data: JSON.stringify({
          topic: "codex",
          payload: {
            method: "item/completed",
            params: {
              threadId: id,
              turnId: "active-turn",
              item: {
                id: "wrong",
                type: "agentMessage",
                text: "Wrong subprocess event",
              },
            },
          },
        }),
      });
    }, id);
    await expect(page.getByText("Wrong subprocess event")).toHaveCount(0);
    await input.fill("  untouched follow-up\n");
    await page.getByRole("button", { name: "Send message" }).click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0].text).toBe("  untouched follow-up\n");
    expect(sent[0].generation).toBe("first-owner-binding");
    connected = false;
    generation = "disconnected-binding";
    const event = async () =>
      page.evaluate(
        ({ id, connected }) => {
          (window as any).events.onmessage({
            data: JSON.stringify({
              topic: "codex.shared",
              payload: { threadId: id, connected },
            }),
          });
        },
        { id, connected },
      );
    await event();
    await expect(input).toBeEnabled();
    await expect(
      page.getByRole("button", { name: "Send message", exact: true }),
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Reconnect shared session" }),
    ).toBeVisible();
    await expect(
      page.getByText("Answer this request in the original Codex window."),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Reply", exact: true }),
    ).toHaveCount(0);
    connected = true;
    generation = "new-owner-binding";
    message = "A later owner update";
    await event();
    await expect(page.getByText(message)).toBeVisible();
    await expect(input).toBeEnabled();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  });
}
