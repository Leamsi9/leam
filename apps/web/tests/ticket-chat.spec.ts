import { test, expect, type Page } from "@playwright/test";
import { navigate } from "./navigation";

async function fixture(page: Page, uncertain = false) {
  let created = false;
  const sends: any[] = [];
  const calls: string[] = [];
  let turns: any[] = [];
  await page.addInitScript(() => {
    const w = window as any;
    w.probe = { starts: 0, spoken: [] };
    w.SpeechRecognition = class {
      onstart: any;
      onresult: any;
      onend: any;
      constructor() {
        w.capture = this;
      }
      start() {
        w.probe.starts++;
        this.onstart?.();
      }
      stop() {
        this.onend?.();
      }
      abort() {}
    };
    w.SpeechSynthesisUtterance = class {
      constructor(public text: string) {}
    };
    Object.defineProperty(w, "speechSynthesis", {
      configurable: true,
      value: {
        getVoices: () => [],
        cancel: () => {},
        speak: (utterance: any) => {
          w.probe.spoken.push(utterance.text);
          utterance.onstart?.();
        },
      },
    });
    w.EventSource = class {
      onmessage: any;
      onopen: any;
      constructor() {
        w.ticketStream = this;
      }
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    let body: any = {};
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/threads") body = { data: [] };
    if (path === "/api/updates")
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
    if (path === "/api/updates/ticket/chat") {
      calls.push(method);
      if (method === "POST") created = true;
      body = {
        state: created ? "ready" : "notCreated",
        threadId: created ? "dedicated-ticket" : null,
        connected: created,
      };
    }
    if (path.startsWith("/api/codex/submissions/"))
      body = { state: sends.length && uncertain ? "pending" : "notSubmitted" };
    if (path.endsWith("/reconcile")) body = { state: "pending" };
    if (path === "/api/codex/threads/dedicated-ticket/turns") {
      if (method === "POST") {
        sends.push(route.request().postDataJSON());
        if (uncertain) {
          await route.abort();
          return;
        }
        turns = [{ id: "matching-turn", status: "inProgress", items: [] }];
        body = { turn: { id: "matching-turn", status: "inProgress" } };
      } else body = { data: [...turns].reverse() };
    }
    if (path === "/api/codex/requests") body = { items: [] };
    await route.fulfill({ json: body });
    if (
      path === "/api/codex/threads/dedicated-ticket/turns" &&
      method === "POST" &&
      !uncertain
    ) {
      await page.evaluate(() =>
        (window as any).ticketStream?.onmessage({
          data: JSON.stringify({
            topic: "codex",
            payload: {
              method: "turn/started",
              params: {
                threadId: "dedicated-ticket",
                turn: { id: "matching-turn", status: "inProgress", items: [] },
              },
            },
          }),
        }),
      );
    }
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await navigate(page, "Updates");
  const card = page.getByRole("article", { name: "Continuation", exact: true });
  return {
    card,
    sends,
    calls,
    async open() {
      await card.getByText("Chat about this update", { exact: true }).click();
      await expect(card.getByLabel("Message about this update")).toBeVisible();
      await expect(card.getByText("Loading ticket conversation…")).toHaveCount(
        0,
      );
    },
    async reply(status: string, text: string) {
      turns = [
        {
          id: "matching-turn",
          status,
          items: [{ id: "answer", type: "agentMessage", text }],
        },
      ];
      await page.evaluate(
        ({ status, text }) =>
          (window as any).ticketStream.onmessage({
            data: JSON.stringify({
              topic: "codex",
              payload:
                status === "inProgress"
                  ? {
                      method: "item/completed",
                      params: {
                        threadId: "dedicated-ticket",
                        turnId: "matching-turn",
                        item: { id: "answer", type: "agentMessage", text },
                      },
                    }
                  : {
                      method: "turn/completed",
                      params: {
                        threadId: "dedicated-ticket",
                        turn: {
                          id: "matching-turn",
                          status,
                          items: [{ id: "answer", type: "agentMessage", text }],
                        },
                      },
                    },
            }),
          }),
        { status, text },
      );
    },
  };
}

test("ticket chat stays lazy, sends unchanged text to a dedicated thread and reopens history", async ({
  page,
}) => {
  const f = await fixture(page);
  expect(f.calls).toEqual([]);
  await f.open();
  expect(f.calls).toEqual(["GET"]);
  const raw = "  What changed?\nKeep this exact.  ";
  await f.card.getByLabel("Message about this update").fill(raw);
  await f.card.getByRole("button", { name: "Send to Codex" }).click();
  await expect.poll(() => f.sends.length).toBe(1);
  expect(f.sends[0].text).toBe(raw);
  expect(f.sends[0]).not.toHaveProperty("additionalContext");
  expect(f.calls.filter((method) => method === "POST")).toHaveLength(1);
  await f.reply("completed", "A dedicated ticket reply.");
  await expect(f.card.getByText("A dedicated ticket reply.")).toBeVisible();
  await f.card.getByText("Chat about this update", { exact: true }).click();
  await f.open();
  await expect(f.card.getByText("A dedicated ticket reply.")).toBeVisible();
  expect(f.sends).toHaveLength(1);
  await expect(f.card.getByText("Your UAT: pending")).toBeVisible();
});

test("uncertain ticket sends keep their receipt and are not resent", async ({
  page,
}) => {
  const f = await fixture(page, true);
  await f.open();
  await f.card
    .getByLabel("Message about this update")
    .fill("Question with uncertain delivery");
  await f.card.getByRole("button", { name: "Send to Codex" }).click();
  await expect(f.card.getByRole("alert")).toBeVisible();
  await f.card.getByRole("button", { name: "Send to Codex" }).click();
  await expect(f.card.getByRole("alert")).toContainText("has not been resent");
  expect(f.sends).toHaveLength(1);
  await page.reload();
  await navigate(page, "Updates");
  await f.open();
  await expect(f.card.getByLabel("Message about this update")).toHaveValue(
    "Question with uncertain delivery",
  );
  expect(f.sends).toHaveLength(1);
});

test("ticket voice uses the shared composer and speaks only its finalized matching reply", async ({
  page,
}) => {
  const f = await fixture(page);
  await f.open();
  await page.clock.install();
  await f.card
    .getByRole("button", { name: "Conversation", exact: true })
    .click();
  await page.evaluate(() =>
    (window as any).capture.onresult({
      results: [{ isFinal: true, 0: { transcript: "Explain this ticket" } }],
    }),
  );
  await page.clock.fastForward(4200);
  await expect.poll(() => f.sends.length).toBe(1);
  await f.reply("inProgress", "Do not speak this partial");
  await page.clock.fastForward(500);
  expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
  await f.reply("completed", "This is the final ticket answer.");
  await page.clock.fastForward(500);
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.spoken))
    .toEqual(["This is the final ticket answer."]);
  await f.card.getByText("Chat about this update", { exact: true }).click();
  expect(f.sends).toHaveLength(1);
});

test("unsent ticket draft survives collapse and reload before a thread exists", async ({
  page,
}) => {
  const f = await fixture(page);
  await f.open();
  await f.card
    .getByLabel("Message about this update")
    .fill("Draft before any Codex session");
  await f.card.getByText("Chat about this update", { exact: true }).click();
  await f.open();
  await expect(f.card.getByLabel("Message about this update")).toHaveValue(
    "Draft before any Codex session",
  );
  await page.reload();
  await navigate(page, "Updates");
  await f.open();
  await expect(f.card.getByLabel("Message about this update")).toHaveValue(
    "Draft before any Codex session",
  );
  expect(f.calls.filter((method) => method === "POST")).toHaveLength(0);
  expect(f.sends).toHaveLength(0);
});

test("live ticket deltas arrive before final and survive stale history and replay", async ({
  page,
}) => {
  const f = await fixture(page);
  await f.open();
  await f.card.getByLabel("Message about this update").fill("Stream a reply");
  await f.card.getByRole("button", { name: "Send to Codex" }).click();
  await expect.poll(() => f.sends.length).toBe(1);
  let release: (() => void) | undefined;
  let entered = false;
  await page.route(
    "**/api/codex/threads/dedicated-ticket/turns",
    async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback();
        return;
      }
      entered = true;
      await new Promise<void>((resolve) => {
        release = resolve;
      });
      await route.fulfill({
        json: {
          data: [{ id: "matching-turn", status: "inProgress", items: [] }],
        },
      });
    },
  );
  await f.card
    .getByRole("button", { name: "Refresh ticket conversation" })
    .click();
  await expect.poll(() => entered).toBe(true);
  const delta = {
    id: 100,
    topic: "codex",
    payload: {
      method: "item/agentMessage/delta",
      params: {
        threadId: "dedicated-ticket",
        turnId: "matching-turn",
        itemId: "answer",
        delta: "Visible partial",
      },
    },
  };
  await page.evaluate(
    (packet) =>
      (window as any).ticketStream.onmessage({ data: JSON.stringify(packet) }),
    delta,
  );
  await expect(
    f.card.getByText("Visible partial", { exact: true }),
  ).toBeVisible();
  release!();
  await page.unroute("**/api/codex/threads/dedicated-ticket/turns");
  await page.evaluate(
    (packet) =>
      (window as any).ticketStream.onmessage({ data: JSON.stringify(packet) }),
    delta,
  );
  await expect(
    f.card.getByText("Visible partial", { exact: true }),
  ).toBeVisible();
  await f.card
    .getByRole("button", { name: "Refresh ticket conversation" })
    .click();
  await expect(
    f.card.getByText("Visible partial", { exact: true }),
  ).toBeVisible();
  await f.reply("completed", "The authoritative final answer.");
  await expect(
    f.card.getByText("The authoritative final answer.", { exact: true }),
  ).toBeVisible();
  await expect(
    f.card.getByText("Visible partial", { exact: true }),
  ).toHaveCount(0);
});

test("accepted dictation remains an unsent ticket draft across collapse", async ({
  page,
}) => {
  const f = await fixture(page);
  await f.open();
  await f.card.getByRole("button", { name: "Dictate", exact: true }).click();
  await page.evaluate(() =>
    (window as any).capture.onresult({
      results: [{ isFinal: true, 0: { transcript: "Dictated ticket draft" } }],
    }),
  );
  await f.card.getByRole("button", { name: "Finish dictation" }).click();
  await expect(f.card.getByLabel("Message about this update")).toHaveValue(
    "Dictated ticket draft",
  );
  await f.card.getByText("Chat about this update", { exact: true }).click();
  await f.open();
  await expect(f.card.getByLabel("Message about this update")).toHaveValue(
    "Dictated ticket draft",
  );
  expect(f.sends).toHaveLength(0);
});

test("accepting a ticket message does not erase a newer draft", async ({
  page,
}) => {
  const f = await fixture(page);
  await f.open();
  let release: (() => void) | undefined;
  let entered = false;
  await page.route(
    "**/api/codex/threads/dedicated-ticket/turns",
    async (route) => {
      if (route.request().method() === "POST") {
        entered = true;
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      }
      await route.fallback();
    },
  );
  await f.card.getByLabel("Message about this update").fill("First message");
  await f.card.getByRole("button", { name: "Send to Codex" }).click();
  await expect.poll(() => entered).toBe(true);
  await f.card.getByLabel("Message about this update").fill("My newer draft");
  release!();
  await expect.poll(() => f.sends.length).toBe(1);
  await expect(f.card.getByRole("button", { name: "Sending…" })).toHaveCount(0);
  await expect(f.card.getByLabel("Message about this update")).toHaveValue(
    "My newer draft",
  );
  await f.card.getByText("Chat about this update", { exact: true }).click();
  await f.open();
  await expect(f.card.getByLabel("Message about this update")).toHaveValue(
    "My newer draft",
  );
});

test("ticket conversation honors the shared browser pause preference", async ({
  page,
}) => {
  const f = await fixture(page);
  await f.open();
  await page.clock.install();
  await page.evaluate(() =>
    localStorage.setItem(
      "leam.voice.input.v1",
      JSON.stringify({ language: "es-ES", pauseSeconds: 7 }),
    ),
  );
  await f.card
    .getByRole("button", { name: "Conversation", exact: true })
    .click();
  expect(await page.evaluate(() => (window as any).capture.lang)).toBe("es-ES");
  await page.evaluate(() =>
    (window as any).capture.onresult({
      results: [{ isFinal: true, 0: { transcript: "Explain the ticket" } }],
    }),
  );
  await page.clock.fastForward(6100);
  expect(f.sends).toHaveLength(0);
  await page.clock.fastForward(1000);
  await expect.poll(() => f.sends.length).toBe(1);
});
