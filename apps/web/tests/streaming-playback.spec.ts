import { test, expect, type Page } from "@playwright/test";
import { navigate, chooseConversation } from "./navigation";
const run = "19e9c46c-a47b-4e01-8cb4-f4c5c4c26c63";
async function setup(page: Page, module: "companion" | "coding" = "companion", width = 390) {
  await page.addInitScript(() => {
    const w = window as any;
    w.probe = { starts: 0, spoken: [], audio: false, pauses: 0 };
    w.streams = [];
    w.EventSource = class extends EventTarget {
      onmessage: any;
      onopen: any;
      onerror: any;
      closed = false;
      constructor(public url: string) {
        super();
        w.streams.push(this);
      }
      close() {
        this.closed = true;
      }
    };
    w.emit = (
      module: string,
      frame: any,
      name = "projection_update",
      id = 0,
    ) => {
      for (const source of w.streams.filter(
        (s: any) =>
          !s.closed &&
          (module === "companion"
            ? s.url.includes("/companion/")
            : s.url.startsWith("/api/events")),
      )) {
        const event = new MessageEvent(
          module === "companion" ? name : "message",
          { data: JSON.stringify(frame), lastEventId: String(id) },
        );
        if (module === "coding") source.onmessage?.(event);
        else source.dispatchEvent(event);
      }
    };
    w.SpeechRecognition = class {
      onstart: any;
      onend: any;
      onresult: any;
      constructor() {
        w.capture = this;
      }
      start() {
        w.probe.starts++;
        w.probe.captureActive = true;
        w.probe.echo ||= w.probe.audio;
        this.onstart?.();
      }
      stop() {
        w.probe.captureActive = false;
        this.onend?.();
      }
      abort() { w.probe.captureActive = false; }
    };
    w.transcript = (text: string) =>
      w.capture.onresult?.({
        results: [{ isFinal: true, 0: { transcript: text } }],
      });
    w.SpeechSynthesisUtterance = class {
      constructor(public text: string) {}
    };
    Object.defineProperty(w, "speechSynthesis", {
      configurable: true,
      value: {
        getVoices: () => [],
        cancel: () => {
          w.probe.audio = false;
        },
        pause: () => {
          w.probe.pauses++;
        },
        resume: () => {},
        speak: (u: any) => {
          w.probe.micDuringOutput ||= !!w.probe.captureActive;
          w.utterance = u;
          w.probe.audio = true;
          w.probe.spoken.push(u.text);
          if (!w.deferStart) u.onstart?.();
        },
      },
    });
    w.finishAudio = () => {
      w.probe.audio = false;
      w.utterance?.onend?.();
    };
  });
  let messages: any[] = [];
  let turns: any[] = [];
  let active = "";
  const writes: any[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {
      items: [],
      data: [],
      threads: [],
      accounts: [],
      providers: [],
      models: [],
    };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/companion/threads")
      body = { threads: [{ thread_id: "a", title: "A" }, { thread_id: "b", title: "B" }] };
    if (path === "/api/companion/threads/a") body = { messages };
    if (path === "/api/companion/threads/a/messages") {
      writes.push(route.request().postDataJSON());
      body = { outcome: "submitted", run_id: run };
    }
    if (path === "/api/codex/threads")
      body = { data: [{ id: "a", name: "A" }] };
    if (path === "/api/codex/threads/a")
      body = { connected: true, thread: { id: "a" }, activeTurnId: active };
    if (path === "/api/codex/threads/a/turns") {
      if (route.request().method() === "POST") {
        writes.push(route.request().postDataJSON());
        active = run;
        body = { turn: { id: run, status: "inProgress" } };
      } else body = { data: turns };
    }
    if (path.startsWith("/api/codex/submissions/"))
      body = { state: "notSubmitted" };
    await route.fulfill({ json: body });
  });
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/");
  if (module === "companion") await navigate(page, "Companion");
  await chooseConversation(page, "a", module);
  await page.clock.install();
  return {
    writes,
    setMessages: (v: any[]) => (messages = v),
    setTurns: (v: any[]) => {
      turns = v;
      active = v.some((t) => t.status === "inProgress") ? run : "";
    },
  };
}
async function say(page: Page) {
  await page.getByRole("button", { name: "Conversation", exact: true }).click();
  await page.evaluate(() => (window as any).transcript("Exact spoken request"));
  await page.clock.fastForward(4100);
  await expect(
    page.getByRole("complementary", { name: "Speech playback" }),
  ).toBeVisible();
}
async function companion(
  page: Page,
  text?: string,
  status?: string,
  final = false,
  id = run,
) {
  await page.evaluate(
    ({ text, status, final, id }) =>
      (window as any).emit("companion", {
        type: "projection_update",
        state: {
          thread_id: "a",
          items: [
            ...(text === undefined
              ? []
              : [{ text: { run_id: id, body: text, finalized: final } }]),
            ...(status ? [{ run_status: { run_id: id, status } }] : []),
          ],
        },
      }),
    { text, status, final, id },
  );
}
async function codex(page: Page, method: string, params: any, id: number) {
  await page.evaluate(
    ({ method, params, id, run }) =>
      (window as any).emit(
        "coding",
        {
          id,
          topic: "codex",
          payload: {
            method,
            params: { threadId: "a", turnId: run, ...params },
          },
        },
        "message",
        id,
      ),
    { method, params, id, run },
  );
}
const spoken = (page: Page) =>
  page.evaluate(() => (window as any).probe.spoken as string[]);

for (const width of [390, 1440]) test(`popup Speak interrupts its conversation, ignores late audio and retains the silence timeout at ${width}px`, async ({ page }) => {
  const fixture = await setup(page, "companion", width);
  await say(page);
  await companion(page, "A complete reply.", "completed", true);
  await expect.poll(() => spoken(page)).toHaveLength(1);
  const panel = page.getByRole("complementary", { name: "Speech playback" });
  const speak = panel.getByRole("button", { name: "Speak", exact: true });
  const box = await panel.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(width);
  const buttonBox = await speak.boundingBox();
  expect(buttonBox!.width).toBeGreaterThanOrEqual(44);
  expect(buttonBox!.height).toBeGreaterThanOrEqual(44);
  await speak.focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => page.evaluate(() => (window as any).probe.starts)).toBe(2);
  await page.evaluate(() => (window as any).finishAudio());
  expect(await page.evaluate(() => (window as any).probe.echo)).toBeFalsy();
  await page.clock.fastForward(10100);
  await expect(page.getByText("Conversation ended after 10 seconds without speech.")).toBeVisible();
  expect(fixture.writes).toHaveLength(1);
});

test("popup returns to its original chat with microphone off before explicit Speak", async ({ page }) => {
  const fixture = await setup(page);
  await say(page);
  fixture.setMessages([{ message_id: "reply", kind: "assistant", content: "A complete reply.", turn_run_id: run, status: "finalized" }]);
  await companion(page, "A complete reply.", "completed", true);
  await expect.poll(() => spoken(page)).toHaveLength(1);
  await chooseConversation(page, "b", "companion");
  const panel = page.getByRole("complementary", { name: "Speech playback" });
  await expect(panel.getByRole("button", { name: "Speak", exact: true })).toHaveCount(0);
  await panel.getByRole("button", { name: "Return to chat to speak" }).click();
  const speak = panel.getByRole("button", { name: "Speak", exact: true });
  await expect(speak).toBeVisible();
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(1);
  await speak.click();
  await expect.poll(() => page.evaluate(() => (window as any).probe.starts)).toBe(2);
  await page.evaluate(() => (window as any).transcript("Continue in the original chat"));
  await page.clock.fastForward(4100);
  await expect.poll(() => fixture.writes.length).toBe(2);
  expect(await page.evaluate(() => (window as any).probe.echo)).toBeFalsy();
});

test("manual playback Speak preserves an existing draft without capturing or sending", async ({ page }) => {
  const fixture = await setup(page);
  await companion(page, "Read this reply.", "completed", true);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  const draft = page.locator("textarea").last();
  await draft.fill("Keep my typed words");
  await page.getByRole("complementary", { name: "Speech playback" }).getByRole("button", { name: "Speak", exact: true }).click();
  await expect(draft).toHaveValue("Keep my typed words");
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(0);
  expect(await page.evaluate(() => (window as any).probe.audio)).toBeFalsy();
  expect(fixture.writes).toHaveLength(0);
});

test("manual Coding playback returns from Settings and Speak sends only to its thread", async ({ page }) => {
  const fixture = await setup(page, "coding");
  const turns = [{ id: run, status: "completed", items: [{ id: "answer", type: "agentMessage", text: "Coding reply.", phase: "final_answer" }] }];
  fixture.setTurns(turns);
  await codex(page, "turn/started", { turn: { id: run, status: "inProgress", items: [] } }, 1);
  await codex(page, "item/completed", { item: turns[0].items[0] }, 2);
  await codex(page, "turn/completed", { turn: turns[0] }, 3);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect.poll(() => spoken(page)).toHaveLength(1);
  await navigate(page, "Settings");
  const panel = page.getByRole("complementary", { name: "Speech playback" });
  await panel.getByRole("button", { name: "Return to chat to speak" }).click();
  const speak = panel.getByRole("button", { name: "Speak", exact: true });
  await expect(speak).toBeVisible();
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(0);
  await expect(page.getByRole("button", { name: "Conversation", exact: true })).toBeEnabled();
  await speak.click();
  await expect.poll(() => page.evaluate(() => (window as any).probe.starts)).toBe(1);
  await page.evaluate(() => (window as any).transcript("Coding follow-up"));
  await page.clock.fastForward(4100);
  await expect.poll(() => fixture.writes.length).toBe(1);
  expect(await page.evaluate(() => (window as any).probe.echo)).toBeFalsy();
});

for (const width of [390, 1440]) test(`Replay after Speak cancels capture, rereads the same reply and never auto-rearms at ${width}px`, async ({ page }) => {
  const f = await setup(page, "companion", width);
  await say(page);
  f.setMessages([{ message_id: "reply", kind: "assistant", content: "Saved reply for replay.", turn_run_id: run, status: "finalized" }]);
  await companion(page, "Saved reply for replay.", "completed", true);
  await expect.poll(() => spoken(page)).toEqual(["Saved reply for replay."]);
  const panel = page.getByRole("complementary", { name: "Speech playback" });
  await panel.getByRole("button", { name: "Speak", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).probe.starts)).toBe(2);
  const replay = panel.getByRole("button", { name: "Replay last output", exact: true });
  await expect(replay).toBeVisible();
  const box = await replay.boundingBox();
  expect(box!.width).toBeGreaterThanOrEqual(44);
  expect(box!.x + box!.width).toBeLessThanOrEqual(width);
  await replay.focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => spoken(page)).toEqual(["Saved reply for replay.", "Saved reply for replay."]);
  await page.evaluate(() => (window as any).finishAudio());
  await page.clock.fastForward(12000);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(2);
  expect(f.writes).toHaveLength(1);
  expect(await page.evaluate(() => (window as any).probe.captureActive)).toBeFalsy();
  expect(await page.evaluate(() => (window as any).probe.micDuringOutput)).toBeFalsy();
});

test("Replay survives Stop and completion, follows current exact text across navigation, and clears on logout", async ({ page }) => {
  const f = await setup(page);
  await say(page);
  await companion(page, "Original answer.", "completed", true);
  await expect.poll(() => spoken(page)).toHaveLength(1);
  await page.getByRole("button", { name: "Stop playback" }).click();
  await navigate(page, "Settings");
  f.setMessages([{ message_id: "reply", kind: "assistant", content: "Current saved answer.", turn_run_id: run, status: "finalized" }]);
  const replay = page.getByRole("button", { name: "Replay last output", exact: true });
  await replay.click();
  await expect.poll(() => spoken(page)).toEqual(["Original answer.", "Current saved answer."]);
  await page.evaluate(() => (window as any).finishAudio());
  await expect(page.getByText("Playback finished.", { exact: true })).toBeVisible();
  await replay.click();
  await expect.poll(() => spoken(page)).toEqual(["Original answer.", "Current saved answer.", "Current saved answer."]);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(1);
  await page.evaluate(() => window.dispatchEvent(new Event("leam:auth-lost")));
  await expect(replay).toHaveCount(0);
  await expect(page.getByRole("complementary", { name: "Speech playback" })).toHaveCount(0);
});

test("automatic partials speak before completion; terminal status waits for authoritative text and natural speech completion", async ({
  page,
}) => {
  await setup(page);
  await say(page);
  await companion(page, "Wrong response. More", undefined, false, "other");
  expect(await spoken(page)).toEqual([]);
  await companion(page, "First sentence. The rest");
  await expect.poll(() => spoken(page)).toEqual(["First sentence. "]);
  await companion(page, undefined, "completed");
  await page.evaluate(() => (window as any).finishAudio());
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(1);
  await expect(page.getByLabel("Playback elapsed")).toContainText("Live");
  await companion(page, "First sentence. The rest is final.", undefined, true);
  await expect
    .poll(() => spoken(page))
    .toEqual(["First sentence. ", "The rest is final."]);
  await page.evaluate(() => (window as any).finishAudio());
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.starts))
    .toBe(2);
  expect(await page.evaluate(() => (window as any).probe.echo)).toBeFalsy();
});

test("app playback follows the same run across navigation, pauses elapsed time, stops on auth loss and never rearms hidden microphone", async ({
  page,
}) => {
  await setup(page);
  await say(page);
  await companion(page, "First sentence. More");
  await expect.poll(() => spoken(page)).toHaveLength(1);
  await page.clock.fastForward(2100);
  await page.getByRole("button", { name: "Pause playback" }).click();
  const elapsed = await page.getByLabel("Playback elapsed").textContent();
  await page.clock.fastForward(6100);
  await expect(page.getByLabel("Playback elapsed")).toHaveText(elapsed!);
  await navigate(page, "Settings");
  await expect(
    page.getByRole("button", { name: "Resume playback" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Resume playback" }).click();
  await page.evaluate(() => (window as any).finishAudio());
  await companion(
    page,
    "First sentence. More after navigation.",
    undefined,
    true,
  );
  await expect
    .poll(() => spoken(page))
    .toEqual(["First sentence. ", "More after navigation."]);
  await page.evaluate(() => (window as any).finishAudio());
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(1);
  await page.evaluate(() => window.dispatchEvent(new Event("leam:auth-lost")));
  await expect(
    page.getByRole("complementary", { name: "Speech playback" }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(
      () => (window as any).streams.filter((s: any) => !s.closed).length,
    ),
  ).toBe(0);
});

test("panel Stop pauses the attached conversation, ignores late speech callbacks and does not rearm", async ({
  page,
}) => {
  await setup(page);
  await say(page);
  await companion(page, "First sentence. More");
  await expect.poll(() => spoken(page)).toHaveLength(1);
  await page.getByRole("button", { name: "Stop playback" }).click();
  await expect(
    page.getByRole("button", { name: "Resume conversation" }),
  ).toBeVisible();
  await page.evaluate(() => (window as any).finishAudio());
  await companion(page, "First sentence. More final.", undefined, true);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(1);
  expect(await spoken(page)).toHaveLength(1);
});

test("manual Coding commentary follows canonical unnamed events after navigation and deduplicates replay", async ({
  page,
}) => {
  const f = await setup(page, "coding");
  await codex(
    page,
    "turn/started",
    { turn: { id: run, status: "inProgress", items: [] } },
    1,
  );
  await codex(
    page,
    "item/started",
    {
      item: {
        id: "comment",
        type: "agentMessage",
        text: "",
        phase: "commentary",
      },
    },
    2,
  );
  await codex(
    page,
    "item/agentMessage/delta",
    { itemId: "comment", delta: "Manual commentary. More" },
    3,
  );
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect.poll(() => spoken(page)).toEqual(["Manual commentary. "]);
  await navigate(page, "Settings");
  // The new follower replays from a retained turn start, never appending to an unwatermarked snapshot.
  await codex(
    page,
    "turn/started",
    { turn: { id: run, status: "inProgress", items: [] } },
    1,
  );
  await codex(
    page,
    "item/started",
    {
      item: {
        id: "comment",
        type: "agentMessage",
        text: "",
        phase: "commentary",
      },
    },
    2,
  );
  await codex(
    page,
    "item/agentMessage/delta",
    { itemId: "comment", delta: "Manual commentary. More" },
    3,
  );
  await codex(
    page,
    "item/agentMessage/delta",
    { itemId: "comment", delta: "Manual commentary. More" },
    3,
  );
  await codex(
    page,
    "item/agentMessage/delta",
    { itemId: "comment", delta: " text. Last" },
    4,
  );
  await page.evaluate(() => (window as any).finishAudio());
  await expect
    .poll(() => spoken(page))
    .toEqual(["Manual commentary. ", "More text. "]);
  f.setTurns([
    {
      id: run,
      status: "completed",
      items: [
        {
          id: "comment",
          type: "agentMessage",
          text: "Manual commentary. More text. Last.",
          phase: "commentary",
        },
      ],
    },
  ]);
  await codex(
    page,
    "turn/completed",
    { turn: { id: run, status: "completed", items: [] } },
    5,
  );
  await expect(page.getByLabel("Playback elapsed")).toContainText(
    "Duration unavailable",
  );
  await page.evaluate(() => (window as any).finishAudio());
  await expect
    .poll(() => spoken(page))
    .toEqual(["Manual commentary. ", "More text. ", "Last."]);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(0);
});

test("authoritative correction of already spoken text stops visibly without replay", async ({
  page,
}) => {
  await setup(page);
  await say(page);
  await companion(page, "Original sentence. More");
  await expect.poll(() => spoken(page)).toHaveLength(1);
  await companion(page, "Corrected complete answer.", undefined, true);
  await expect(
    page.getByRole("complementary", { name: "Speech playback" }),
  ).toContainText("reply changed after speech began");
  await expect(
    page.getByRole("button", { name: "Resume conversation" }),
  ).toBeVisible();
  expect(await spoken(page)).toHaveLength(1);
});

test("pause before native onstart remains paused and Stop cancels the pending source", async ({
  page,
}) => {
  await setup(page);
  await say(page);
  await page.evaluate(() => ((window as any).deferStart = true));
  await companion(page, "First sentence. More");
  await expect.poll(() => spoken(page)).toHaveLength(1);
  await page.getByRole("button", { name: "Pause playback" }).click();
  await page.evaluate(() => (window as any).utterance.onstart?.());
  await page.clock.fastForward(6000);
  await expect(
    page.getByRole("button", { name: "Resume playback" }),
  ).toBeVisible();
  await expect(page.getByLabel("Playback elapsed")).toContainText("0:00");
  await page.getByRole("button", { name: "Resume playback" }).click();
  await page.clock.fastForward(1100);
  await expect(page.getByLabel("Playback elapsed")).toContainText("0:01");
  await page.getByRole("button", { name: "Stop playback" }).click();
  expect(
    await page.evaluate(
      () => (window as any).streams.filter((s: any) => !s.closed).length,
    ),
  ).toBe(1);
});

test("new explicit playback replaces the old target and late callbacks cannot stop its output", async ({
  page,
}) => {
  await setup(page);
  await companion(page, "First message. Tail");
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect.poll(() => spoken(page)).toHaveLength(1);
  await page.evaluate(
    () => ((window as any).oldUtterance = (window as any).utterance),
  );
  await companion(page, "Second message. Tail", undefined, false, "other");
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect
    .poll(() => spoken(page))
    .toEqual(["First message. ", "Second message. "]);
  await page.evaluate(() => (window as any).oldUtterance.onend?.());
  await companion(page, "First message. Wrong tail.", undefined, true);
  await companion(
    page,
    "Second message. Tail complete.",
    undefined,
    true,
    "other",
  );
  await page.evaluate(() => (window as any).finishAudio());
  await expect
    .poll(() => spoken(page))
    .toEqual(["First message. ", "Second message. ", "Tail complete."]);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(0);
});

test('dismiss clears the retained playback panel and ignores late output', async ({ page }) => {
  await setup(page);
  await say(page);
  await companion(page, 'First sentence. More');
  await expect.poll(() => spoken(page)).toHaveLength(1);
  await page.getByRole('button', { name: 'Stop playback', exact: true }).click();
  const dismiss = page.getByRole('button', { name: 'Dismiss playback', exact: true });
  await expect(dismiss).toBeVisible();
  const box = await dismiss.boundingBox();
  expect(box!.width).toBeGreaterThanOrEqual(44);
  expect(box!.height).toBeGreaterThanOrEqual(44);
  await dismiss.click();
  await expect(page.getByRole('complementary', { name: 'Speech playback' })).toHaveCount(0);
  await companion(page, 'First sentence. More final.', undefined, true);
  await page.evaluate(() => (window as any).finishAudio());
  await expect(page.getByRole('complementary', { name: 'Speech playback' })).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(1);
});

test("dismiss during active audio cancels output and fences late native callbacks", async ({ page }) => {
  await setup(page);
  await say(page);
  await companion(page, "First sentence. More");
  await expect.poll(() => spoken(page)).toHaveLength(1);
  expect(await page.evaluate(() => (window as any).probe.audio)).toBe(true);
  await page.getByRole("button", { name: "Dismiss playback", exact: true }).click();
  expect(await page.evaluate(() => (window as any).probe.audio)).toBe(false);
  await expect(page.getByRole("complementary", { name: "Speech playback" })).toHaveCount(0);
  await companion(page, "First sentence. More final.", undefined, true);
  await page.evaluate(() => {
    (window as any).utterance.onend?.();
    (window as any).utterance.onerror?.({ error: "interrupted" });
  });
  await expect(page.getByRole("complementary", { name: "Speech playback" })).toHaveCount(0);
  expect(await spoken(page)).toHaveLength(1);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(1);
});
