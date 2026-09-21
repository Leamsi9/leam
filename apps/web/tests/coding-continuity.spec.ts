import { test, expect, type Page } from "@playwright/test";
import { navigate } from "./navigation";

const id = "00000000-0000-0000-0000-000000000011";
const thread = { id, name: "Continuity fixture", transport: "ide-owner" };
async function fixture(page: Page) {
  await page.addInitScript(() => {
    const streams: any[] = [];
    class Events {
      onmessage: any;
      onopen: any;
      constructor() {
        streams.push(this);
      }
      close() {}
    }
    (window as any).EventSource = Events;
    (window as any).streams = streams;
  });
  const state = {
    holdSend: false,
    holdHistory: false,
    holdCatalog: false,
    connected: true,
    failSend: false,
    authenticated: true,
    emptyHistory: false,
    holdReceipt: false,
    revokeReads: false,
    configured: true,
    holdShared: false,
    native: false,
    nextCursor: null as string | null,
    sent: [] as any[],
    confirmed: [] as any[],
    releases: [] as (() => void)[],
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {
      items: [],
      data: [],
      models: [],
      threads: [],
      providers: [],
    };
    if (path === "/api/auth/status")
      body = { authenticated: state.authenticated, configured: true };
    if (path === "/api/codex/shared-thread") {
      if (state.holdShared)
        await new Promise<void>((r) => state.releases.push(r));
      body = {
        configured: state.configured,
        thread: state.configured ? thread : null,
      };
    }
    if (path === "/api/codex/threads") {
      if (state.holdCatalog)
        await new Promise<void>((r) => state.releases.push(r));
      body = {
        data: [{ ...thread, transport: state.native ? "native" : "ide-owner" }],
        nextCursor: state.nextCursor,
      };
    }
    if (path === `/api/codex/threads/${id}`)
      body = {
        thread: { ...thread, transport: state.native ? "native" : "ide-owner" },
        connected: state.connected,
        generation: "fixture-binding",
        activeTurnId: state.native ? "" : "active-turn",
      };
    if (path.endsWith("/goal")) body = { goal: null };
    if (path.includes("/submissions/")) {
      if (state.holdReceipt)
        await new Promise<void>((r) => state.releases.push(r));
      body = { state: "notSubmitted" };
    }
    if (path === `/api/codex/threads/${id}/turns`) {
      if (route.request().method() === "POST") {
        state.sent.push(route.request().postDataJSON());
        if (state.holdSend)
          await new Promise<void>((r) => state.releases.push(r));
        if (state.failSend) return route.abort("failed");
        body = {
          turn: { id: "active-turn", status: "inProgress" },
          ...(state.native ? {} : { operation: "steer" }),
        };
      } else {
        if (state.revokeReads)
          return route.fulfill({
            status: 401,
            json: { detail: "Sign in to Leam" },
          });
        if (state.holdHistory)
          await new Promise<void>((r) => state.releases.push(r));
        body = {
          data: state.emptyHistory
            ? []
            : [
                {
                  id: "active-turn",
                  status: state.native ? "completed" : "inProgress",
                  items: [
                    {
                      id: "old",
                      type: "agentMessage",
                      text: "Last loaded owner answer",
                    },
                    ...state.confirmed,
                  ],
                },
              ],
        };
      }
    }
    await route.fulfill({ json: body });
  });
  return state;
}
async function open(page: Page) {
  await page.goto("/?view=coding");
  await page.getByRole("button", { name: /^Open Continuity fixture/ }).click();
  await expect(
    page.getByText("Last loaded owner answer", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeDisabled();
}
async function refresh(page: Page, connected = true) {
  await page.evaluate(
    ({ id, connected }) => {
      const stream = (window as any).streams.at(-1);
      stream.onmessage({
        data: JSON.stringify({
          topic: "codex.shared",
          payload: { threadId: id, connected },
        }),
      });
    },
    { id, connected },
  );
}

test("own message appears while receipt is held and retires only by request identity", async ({
  page,
}) => {
  const state = await fixture(page);
  await open(page);
  state.holdSend = true;
  await page
    .getByLabel("Message Codex", { exact: true })
    .fill("Same follow-up text");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() => state.sent.length).toBe(1);
  await expect(page.locator("[data-local-submission] .prose")).toHaveText(
    "Same follow-up text",
    { timeout: 700 },
  );
  await expect(page.locator("[data-local-submission]")).toContainText(
    "Sending",
  );
  state.releases.splice(0).forEach((r) => r());
  await expect(page.locator("[data-local-submission]")).toContainText(
    "Accepted",
  );
  state.confirmed = [
    {
      id: "other",
      clientId: "another-request",
      type: "userMessage",
      content: [{ type: "text", text: "Same follow-up text" }],
    },
  ];
  await refresh(page);
  await expect(page.locator("[data-local-submission]")).toHaveCount(1);
  state.confirmed = [
    {
      id: "confirmed",
      clientId: state.sent[0].requestId,
      type: "userMessage",
      content: [{ type: "text", text: "Same follow-up text" }],
    },
  ];
  await refresh(page);
  await expect(page.locator("[data-local-submission]")).toHaveCount(0);
  await expect(page.locator(".message.user .prose")).toHaveText(
    "Same follow-up text",
  );
  expect(state.sent).toHaveLength(1);
});

test("deliberate reload retains authenticated last-good shared display during held reads", async ({
  page,
}) => {
  const state = await fixture(page);
  await open(page);
  state.holdHistory = true;
  state.holdCatalog = true;
  await page.reload();
  await expect(
    page.getByText("Last loaded owner answer", { exact: true }),
  ).toBeVisible({ timeout: 1000 });
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeDisabled();
  expect(state.sent).toHaveLength(0);
  state.releases.splice(0).forEach((r) => r());
});

test("local feedback precedes a held receipt lookup and never claims acceptance", async ({
  page,
}) => {
  const state = await fixture(page);
  await open(page);
  state.holdReceipt = true;
  await page
    .getByLabel("Message Codex", { exact: true })
    .fill("Visible before lookup finishes");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.locator("[data-local-submission]")).toContainText(
    "Sending",
    { timeout: 700 },
  );
  expect(state.sent).toHaveLength(0);
  await expect(page.locator("[data-local-submission]")).not.toContainText(
    "Accepted",
  );
  state.releases.splice(0).forEach((r) => r());
});

test("uncertain send survives reload without resending", async ({ page }) => {
  const state = await fixture(page);
  await open(page);
  state.failSend = true;
  await page
    .getByLabel("Message Codex", { exact: true })
    .fill("Uncertain fixture message");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.locator("[data-local-submission]")).toContainText(
    "Delivery uncertain",
  );
  expect(state.sent).toHaveLength(1);
  await page.reload();
  await expect(page.locator("[data-local-submission]")).toContainText(
    "Delivery uncertain",
  );
  await expect(page.locator("[data-local-submission] .prose")).toHaveText(
    "Uncertain fixture message",
  );
  expect(state.sent).toHaveLength(1);
});

test("disconnected empty refresh and navigation preserve last-good shared history", async ({
  page,
}) => {
  const state = await fixture(page);
  await open(page);
  state.connected = false;
  state.emptyHistory = true;
  await refresh(page, false);
  await expect(
    page.getByText("Last loaded owner answer", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeDisabled();
  await navigate(page, "Settings");
  await navigate(page, "Coding");
  await expect(
    page.getByText("Last loaded owner answer", { exact: true }),
  ).toBeVisible();
  expect(state.sent).toHaveLength(0);
});

test("local shared catalog appears before native catalog completes", async ({
  page,
}) => {
  const state = await fixture(page);
  state.holdCatalog = true;
  await page.goto("/?view=coding");
  await expect(
    page.getByRole("button", { name: /^Open Continuity fixture/ }),
  ).toBeVisible({ timeout: 1000 });
  expect(state.sent).toHaveLength(0);
  state.releases.splice(0).forEach((r) => r());
});

test("auth revocation clears persisted private display and local echoes", async ({
  page,
}) => {
  const state = await fixture(page);
  await open(page);
  await expect
    .poll(() =>
      page.evaluate(() => sessionStorage.getItem("leam-chat-display-v1")),
    )
    .not.toBeNull();
  state.revokeReads = true;
  await refresh(page);
  await expect(
    page.getByText("Last loaded owner answer", { exact: true }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(() => sessionStorage.getItem("leam-chat-display-v1")),
  ).toBeNull();
  expect(state.sent).toHaveLength(0);
});

test("expired display is not reused after a deliberate reload", async ({
  page,
}) => {
  const state = await fixture(page);
  await open(page);
  await expect
    .poll(() =>
      page.evaluate(() => sessionStorage.getItem("leam-chat-display-v1")),
    )
    .not.toBeNull();
  await page.evaluate(() => {
    const cached = JSON.parse(sessionStorage.getItem("leam-chat-display-v1")!);
    cached.savedAt = Date.now() - 11 * 60 * 1000;
    sessionStorage.setItem("leam-chat-display-v1", JSON.stringify(cached));
  });
  // A fresh page, not pagehide on the old document, models an expired tab cache.
  await page.evaluate(() => {
    const cached = sessionStorage.getItem("leam-chat-display-v1")!;
    (window as any).expiredCache = cached;
  });
  const expired = await page.evaluate(() => (window as any).expiredCache);
  await page.addInitScript(
    (raw) => sessionStorage.setItem("leam-chat-display-v1", raw),
    expired,
  );
  state.holdHistory = true;
  await page.reload();
  await expect(page.getByLabel("Message Codex", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Last loaded owner answer", { exact: true }),
  ).toHaveCount(0);
  state.releases.splice(0).forEach((r) => r());
});

test("native receipt returning an existing turn never retires a different user's message", async ({
  page,
}) => {
  const state = await fixture(page);
  state.native = true;
  state.configured = false;
  state.confirmed = [
    {
      id: "old-user",
      type: "userMessage",
      content: [{ type: "text", text: "Earlier user message" }],
    },
  ];
  await open(page);
  await page
    .getByLabel("Message Codex", { exact: true })
    .fill("New native follow-up");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.locator("[data-local-submission]")).toContainText(
    "Accepted",
  );
  await expect(
    page.getByText("Earlier user message", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("[data-local-submission] .prose")).toHaveText(
    "New native follow-up",
  );
  expect(state.sent).toHaveLength(1);
});

test("late shared lookup preserves the native pagination cursor in reload storage", async ({
  page,
}) => {
  const state = await fixture(page);
  state.holdShared = true;
  state.nextCursor = "next-native-page";
  await page.goto("/?view=coding");
  await expect(
    page.getByRole("button", { name: /^Open Continuity fixture/ }),
  ).toBeVisible();
  await expect.poll(() => state.releases.length).toBe(1);
  state.releases.splice(0).forEach((r) => r());
  await expect
    .poll(() =>
      page.evaluate(() => {
        const saved = JSON.parse(
          sessionStorage.getItem("leam-chat-display-v1") || "null",
        );
        return saved?.entries.find(
          ([key]: [string]) => key === "coding:threads",
        )?.[1]?.nextCursor;
      }),
    )
    .toBe("next-native-page");
});

test("removed shared configuration keeps saved history read-only without reconnect authority", async ({
  page,
}) => {
  const state = await fixture(page);
  await open(page);
  state.configured = false;
  await page.reload();
  await expect(
    page.getByText("Last loaded owner answer", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Reconnect shared session" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText(
      "This shared session is no longer configured. Its saved conversation remains read-only.",
      { exact: true },
    ),
  ).toBeVisible();
  expect(state.sent).toHaveLength(0);
});
