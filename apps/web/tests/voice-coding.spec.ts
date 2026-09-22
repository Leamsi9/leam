import { test, expect } from "@playwright/test";
import { navigate } from "./navigation";
async function setup(page: any, connected = true, initiallyActive = false, transport = "ide-owner") {
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
        if (url.startsWith("/api/events")) (w.streams ||= []).push(this);
      }
      closed = false;
      close() { this.closed = true; }
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
  let active = initiallyActive ? "turn-existing" : "";
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
            transport,
            generation: "owner-generation",
          },
        ],
      };
    if (path === "/api/codex/threads/coding-a")
      body = {
        connected,
        thread: { id: "coding-a", transport },
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
              transport,
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
    async update(items: any[], status = "inProgress", id = "turn-matching", stillActive = false) {
      turns = [{ id, status, items }];
      active = stillActive ? "turn-other-running" : status === "inProgress" ? id : "";
      await page.evaluate(({ transport, id, status, items }) => {
        const emit = (data: any) => {
          for (const stream of (window as any).streams || [])
            if (!stream.closed) stream.onmessage?.({ data: JSON.stringify(data) });
        };
        if (transport === "ide-owner") emit({ topic: "codex.shared", payload: { threadId: "coding-a", connected: true } });
        else {
          const packet = (method: string, extra: any) => emit({ topic: "codex", payload: { method, params: { threadId: "coding-a", ...extra } } });
          packet("turn/started", { turn: { id, status: "inProgress", items: [] } });
          for (const item of items) packet("item/completed", { turnId: id, item });
          if (status !== "inProgress") packet("turn/completed", { turn: { id, status } });
        }
      }, { transport, id, status, items });
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

test("active shared Coding allows explicit conversation steering without claiming a new reply", async ({ page }) => {
  const f = await setup(page, true, true);
  f.steer();
  const conversation = page.getByRole("button", { name: "Conversation", exact: true });
  await expect(conversation).toBeEnabled();
  await conversation.click();
  await page.evaluate(() => (window as any).transcript("Change the current approach"));
  await page.clock.fastForward(4100);
  await expect.poll(() => f.sends.length).toBe(1);
  expect(f.sends[0]).toMatchObject({ text: "Change the current approach", generation: "owner-generation" });
  await expect(page.getByText(/delivered as a follow-up/)).toBeVisible();
  await f.update([{ id: "prior-answer", type: "agentMessage", text: "Existing run output" }], "completed");
  expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(1);
  await expect(page.getByLabel("Message Codex")).toHaveValue("");
});

test("shared Coding Ear uses the same owner-bound follow-up and review receipt", async ({ page }) => {
  const f = await setup(page, true, true);
  f.steer();
  await page.getByRole("button", { name: "Active listening (5 minutes)", exact: true }).click();
  await page.clock.fastForward(15000);
  expect(f.sends).toHaveLength(0);
  await page.evaluate(() => (window as any).transcript("Owner-bound voice follow-up"));
  await page.clock.fastForward(4100);
  await expect.poll(() => f.sends.length).toBe(1);
  expect(f.sends[0]).toMatchObject({ text: "Owner-bound voice follow-up", generation: "owner-generation" });
  await expect(page.getByText(/delivered as a follow-up/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop active listening", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
});

test("Coding automatic rearm still waits for authoritative active-turn clearance", async ({ page }) => {
  const f = await setup(page);
  await page.getByRole("button", { name: "Conversation", exact: true }).click();
  await page.evaluate(() => (window as any).transcript("Read the matched result"));
  await page.clock.fastForward(4100);
  await expect.poll(() => f.sends.length).toBe(1);
  const answer = [{ id: "answer", type: "agentMessage", text: "Matched reply." }];
  await f.update(answer, "completed", "turn-matching", true);
  await expect.poll(() => page.evaluate(() => (window as any).probe.spoken)).toEqual(["Matched reply."]);
  await page.evaluate(() => (window as any).utterance.onend?.());
  await expect(page.getByText("Checking response completion…", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(1);
  await f.update(answer, "completed");
  await expect.poll(() => page.evaluate(() => (window as any).probe.starts)).toBe(2);
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


for (const transport of ["ide-owner", "app-server"]) {
  test(`${transport} Coding speaker is below output and latest replay works before any playback`, async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 780 });
    const f = await setup(page, true, false, transport);
    const article = page.locator("#coding-message-old-answer");
    const below = await article.evaluate((node) => !!(node.querySelector(".prose")!.compareDocumentPosition(node.querySelector("button")!) & Node.DOCUMENT_POSITION_FOLLOWING));
    expect(below).toBe(true);
    expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
    await page.getByRole("button", { name: "Replay last reply", exact: true }).click();
    await expect.poll(() => page.evaluate(() => (window as any).probe.spoken)).toEqual(["Old private reply"]);
    await page.getByRole("button", { name: "Dismiss playback", exact: true }).click();
    await f.update([{ id: "new-answer", type: "agentMessage", phase: "final_answer", text: "Newest completed reply." }], "completed");
    await page.getByRole("button", { name: "Replay last reply", exact: true }).click();
    await expect.poll(() => page.evaluate(() => (window as any).probe.spoken)).toEqual(["Old private reply", "Newest completed reply."]);
  });

  test(`${transport} conversation reads matching final-answer stream and completion only while enabled`, async ({ page }) => {
    const f = await setup(page, true, false, transport);
    await page.getByRole("button", { name: "Conversation", exact: true }).click();
    await page.evaluate(() => (window as any).transcript("Read the current result"));
    await page.clock.fastForward(4100);
    await expect.poll(() => f.sends.length).toBe(1);
    await f.update([{ id: "spoken", type: "agentMessage", phase: "final_answer", text: "First new sentence. The next sentence" }]);
    await expect.poll(() => page.evaluate(() => (window as any).probe.spoken)).toEqual(["First new sentence. "]);
    await page.setViewportSize({ width: 360, height: 780 });
    const fits = await page.locator(".voice-status-row").evaluate((row) => Array.from(row.querySelectorAll("button")).every((button) => {
      const box = button.getBoundingClientRect(); return box.left >= 0 && box.right <= innerWidth;
    }));
    expect(fits).toBe(true);
    await page.getByRole("button", { name: "End conversation", exact: true }).click();
    await f.update([{ id: "spoken", type: "agentMessage", phase: "final_answer", text: "First new sentence. The next sentence is complete." }], "completed");
    expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual(["First new sentence. "]);
  });
}

test("shared spoken steering reads only appended/new answer text from its baseline", async ({ page }) => {
  const f = await setup(page);
  const old = { id: "answer", type: "agentMessage", phase: "final_answer", text: "Already displayed answer. " };
  await f.update([old]);
  f.steer();
  await page.getByRole("button", { name: "Conversation", exact: true }).click();
  await page.evaluate(() => (window as any).transcript("Continue and read the new result"));
  await page.clock.fastForward(4100);
  await expect.poll(() => f.sends.length).toBe(1);
  await f.update([old]);
  await f.update([{ ...old, text: "Already displayed" }]);
  expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
  await f.update([{ ...old, text: old.text + "New result after your request. More follows" }]);
  await expect.poll(() => page.evaluate(() => (window as any).probe.spoken)).toEqual(["New result after your request. "]);
  await page.evaluate(() => (window as any).utterance.onend?.());
  await f.update([{ ...old, text: old.text + "New result after your request. More follows here." }], "completed");
  await expect.poll(() => page.evaluate(() => (window as any).probe.spoken)).toEqual(["New result after your request. ", "More follows here."]);
});

test("shared steering suppresses historical output and stops on a rewritten baseline", async ({ page }) => {
  const f = await setup(page);
  const old = { id: "answer", type: "agentMessage", phase: "final_answer", text: "Already displayed answer." };
  await f.update([old]); f.steer();
  await page.getByRole("button", { name: "Conversation", exact: true }).click();
  await page.evaluate(() => (window as any).transcript("Continue"));
  await page.clock.fastForward(4100);
  await expect.poll(() => f.sends.length).toBe(1);
  await f.update([{ ...old, text: "A replacement of old content." }], "completed");
  expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
  await expect(page.getByRole("button", { name: "Resume conversation", exact: true })).toBeVisible();
});


test("shared steering reads a new final-answer item without replaying the baseline", async ({ page }) => {
  const f = await setup(page);
  const old = { id: "before", type: "agentMessage", phase: "commentary", text: "Old progress message." };
  await f.update([old]); f.steer();
  await page.getByRole("button", { name: "Conversation", exact: true }).click();
  await page.evaluate(() => (window as any).transcript("Read the next result"));
  await page.clock.fastForward(4100);
  await expect.poll(() => f.sends.length).toBe(1);
  await f.update([old, { id: "after", type: "agentMessage", phase: "final_answer", text: "New final result." }], "completed");
  await expect.poll(() => page.evaluate(() => (window as any).probe.spoken)).toEqual(["New final result."]);
});


for (const transport of ["ide-owner", "app-server"]) {
  test(`${transport} conversation automatically reads its completed answer`, async ({ page }) => {
    const f = await setup(page, true, false, transport);
    await page.getByRole("button", { name: "Conversation", exact: true }).click();
    await page.evaluate(() => (window as any).transcript("Complete this spoken request"));
    await page.clock.fastForward(4100);
    await expect.poll(() => f.sends.length).toBe(1);
    await f.update([{ id: "answer", type: "agentMessage", phase: "final_answer", text: "Your completed result." }], "completed");
    await expect.poll(() => page.evaluate(() => (window as any).probe.spoken)).toEqual(["Your completed result."]);
  });
}

test("navigation preserves exact-source new shared readout without rearming capture", async ({ page }) => {
  const f = await setup(page);
  const old = { id: "before", type: "agentMessage", phase: "final_answer", text: "Old answer. " };
  await f.update([old]); f.steer();
  await page.getByRole("button", { name: "Conversation", exact: true }).click();
  await page.evaluate(() => (window as any).transcript("Continue"));
  await page.clock.fastForward(4100);
  await expect.poll(() => f.sends.length).toBe(1);
  const captureStarts = await page.evaluate(() => (window as any).probe.starts);
  await navigate(page, "Companion");
  await page.evaluate(() => {
    for (const stream of (window as any).streams || []) {
      if (!stream.closed) stream.onmessage?.({ data: JSON.stringify({
        topic: "codex", payload: { method: "item/completed", params: {
          threadId: "destination-thread", turnId: "destination-turn",
          item: { id: "destination-answer", type: "agentMessage", phase: "final_answer", text: "Destination must not be read." },
        } },
      }) });
    }
  });
  expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
  await f.update([{ ...old, text: old.text + "Later output." }], "completed");
  await expect.poll(() => page.evaluate(() => (window as any).probe.spoken)).toEqual(["Later output."]);
  await page.evaluate(() => (window as any).utterance.onend?.());
  await page.clock.fastForward(10000);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(captureStarts);
  expect(f.sends).toHaveLength(1);
});
