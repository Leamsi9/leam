import { test, expect } from "@playwright/test";
async function setup(page: any, connected = true) {
  await page.addInitScript(() => {
    const w = window as any;
    localStorage.setItem(
      "leam.voice.output.v1",
      JSON.stringify({ version: 1, voice: null, rate: 1.1 }),
    );
    w.probe = { starts: 0, spoken: [], aborts: 0 };
    w.SpeechRecognition = class {
      onstart: any;
      onresult: any;
      onend: any;
      onerror: any;
      constructor() {
        w.capture = this;
      }
      start() {
        w.probe.starts++;
        this.onstart?.();
      }
      stop() {
        if (!w.deferEnd) this.onend?.();
      }
      abort() {
        w.probe.aborts++;
      }
    };
    w.transcript = (text: string, final = true) =>
      w.capture.onresult?.({
        results: [{ isFinal: final, 0: { transcript: text } }],
      });
    w.SpeechSynthesisUtterance = class {
      text: string;
      constructor(text: string) {
        this.text = text;
      }
    };
    Object.defineProperty(w, "speechSynthesis", {
      configurable: true,
      value: {
        getVoices: () => [],
        cancel: () => {},
        speak: (utterance: any) => {
          w.probe.spoken.push(utterance.text);
          w.utterance = utterance;
          utterance.onstart?.();
        },
      },
    });
    w.EventSource = class extends EventTarget {
      onopen: any;
      onerror: any;
      constructor(url: string) {
        super();
        if (url === "/api/events") w.stream = this;
      }
      close() {}
    };
  });

  const sends: any[] = [];
  let turns: any[] = [
    {
      id: "old",
      status: "completed",
      items: [
        { id: "old-answer", type: "agentMessage", text: "Old private reply" },
      ],
    },
  ];
  let active = "";
  let unknown = false;
  let operation = "start";
  await page.route("**/api/**", async (route: any) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/threads")
      body = {
        data: [
          {
            id: "coding-a",
            name: "Shared fixture",
            transport: "ide-owner",
            generation: "owner-generation",
          },
        ],
      };
    if (path === "/api/codex/threads/coding-a")
      body = {
        connected,
        thread: { id: "coding-a", transport: "ide-owner" },
        generation: "owner-generation",
        activeTurnId: active,
      };
    if (path === "/api/codex/threads/coding-a/turns") {
      if (route.request().method() === "POST") {
        sends.push(route.request().postDataJSON());
        active = "turn-matching";
        body = unknown
          ? {}
          : {
              turn: { id: "turn-matching", status: "inProgress" },
              transport: "ide-owner",
              operation,
            };
      } else body = { data: [...turns].reverse() };
    }
    if (path.startsWith("/api/codex/submissions/"))
      body = { state: "notSubmitted" };
    if (path === "/api/codex/requests") body = { items: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: /^Open Shared fixture/ }).click();
  await expect(page.getByLabel("Message Codex")).toBeVisible();
  await page.clock.install();
  return {
    sends,
    steer: () => {
      operation = "steer";
    },
    unknown: () => {
      unknown = true;
    },
    async update(items: any[], status = "inProgress", id = "turn-matching") {
      turns = [{ id, status, items }];
      active = status === "inProgress" ? id : "";
      await page.evaluate(() => {
        (window as any).stream.onmessage({
          data: JSON.stringify({
            topic: "codex.shared",
            payload: { threadId: "coding-a", connected: true },
          }),
        });
      });
      await page.clock.fastForward(200);
    },
  };
}

test("Coding dictation remains usable while disconnected and never auto-sends", async ({
  page,
}) => {
  const f = await setup(page, false);
  await expect(
    page.getByRole("button", { name: "Conversation", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Dictate", exact: true }).click();
  await page.evaluate(() =>
    (window as any).transcript("Inspect this exact draft"),
  );
  await page
    .getByRole("button", { name: "Finish dictation", exact: true })
    .click();
  await expect(page.getByLabel("Message Codex")).toHaveValue(
    "Inspect this exact draft",
  );
  expect(f.sends).toHaveLength(0);
});
test("Coding conversation dispatches exact text and owner generation, reads only matching completed final answer", async ({
  page,
}) => {
  const f = await setup(page);
  await page.getByRole("button", { name: "Conversation", exact: true }).click();
  await page.evaluate(() => (window as any).transcript("Run the caller tests"));
  await page.clock.fastForward(4100);
  await expect.poll(() => f.sends.length).toBe(1);
  expect(f.sends[0]).toMatchObject({
    text: "Run the caller tests",
    generation: "owner-generation",
  });
  expect(f.sends[0].requestId).toMatch(/^[a-f0-9-]{36}$/);
  await f.update(
    [{ id: "other", type: "agentMessage", text: "Unrelated" }],
    "completed",
    "wrong-turn",
  );
  expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
  await f.update([
    {
      id: "dev",
      type: "developerMessage",
      text: "Private developer instructions",
    },
    { id: "commentary", type: "agentMessage", text: "Checking workspace" },
    { id: "final", type: "agentMessage", text: "The tests passed: 2 * 3." },
  ]);
  expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
  await f.update(
    [
      {
        id: "dev",
        type: "developerMessage",
        text: "Private developer instructions",
      },
      { id: "commentary", type: "agentMessage", text: "Checking workspace" },
      { id: "final", type: "agentMessage", text: "The tests passed: 2 * 3." },
    ],
    "completed",
  );
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.spoken))
    .toEqual(["The tests passed: 2 * 3."]);
  expect(await page.evaluate(() => (window as any).utterance.rate)).toBe(1.1);
  await page.evaluate(() => (window as any).utterance.onend());
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.starts))
    .toBe(2);
  await page.getByLabel("Message Codex").fill("Typed words");
  await page.evaluate(() => (window as any).transcript("stale speech"));
  await page.clock.fastForward(3000);
  await expect(page.getByLabel("Message Codex")).toHaveValue("Typed words");
  expect(f.sends).toHaveLength(1);
});
test("Coding unknown receipt preserves draft with no automatic resend", async ({
  page,
}) => {
  const f = await setup(page);
  f.unknown();
  await page.getByRole("button", { name: "Conversation", exact: true }).click();
  await page.evaluate(() => (window as any).transcript("Keep this request"));
  await page.clock.fastForward(4100);
  await expect.poll(() => f.sends.length).toBe(1);
  await expect(page.getByLabel("Message Codex")).toHaveValue(
    "Keep this request",
  );
  await page.clock.fastForward(30000);
  expect(f.sends).toHaveLength(1);
  expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
});

test("Coding follow-up acceptance does not speak an existing active turn", async ({
  page,
}) => {
  const f = await setup(page);
  f.steer();
  await page.getByRole("button", { name: "Conversation", exact: true }).click();
  await page.evaluate(() => (window as any).transcript("Follow up safely"));
  await page.clock.fastForward(4100);
  await expect.poll(() => f.sends.length).toBe(1);
  await expect(page.getByText(/delivered as a follow-up/)).toBeVisible();
  await f.update(
    [
      {
        id: "existing-answer",
        type: "agentMessage",
        text: "Earlier run answer",
      },
    ],
    "completed",
  );
  expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
  await expect(page.getByLabel("Message Codex")).toHaveValue("");
});

test("Coding honors an existing explicit two-second browser pause", async ({
  page,
}) => {
  const f = await setup(page);
  await page.evaluate(() =>
    localStorage.setItem(
      "leam.voice.input.v1",
      JSON.stringify({ language: "de-DE", pauseSeconds: 2 }),
    ),
  );
  await page.getByRole("button", { name: "Conversation", exact: true }).click();
  expect(await page.evaluate(() => (window as any).capture.lang)).toBe("de-DE");
  await page.evaluate(() => (window as any).transcript("Configured pause"));
  await page.clock.fastForward(1100);
  expect(f.sends).toHaveLength(0);
  await page.clock.fastForward(1000);
  await expect.poll(() => f.sends.length).toBe(1);
});
