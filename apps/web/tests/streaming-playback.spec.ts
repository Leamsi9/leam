import { test, expect, type Page } from "@playwright/test";
import { navigate, chooseConversation } from "./navigation";
const run = "19e9c46c-a47b-4e01-8cb4-f4c5c4c26c63";
async function setup(page: Page, module: "companion" | "coding" = "companion") {
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
        w.probe.echo ||= w.probe.audio;
        this.onstart?.();
      }
      stop() {
        this.onend?.();
      }
      abort() {}
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
      body = { threads: [{ thread_id: "a", title: "A" }] };
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
  await page.setViewportSize({ width: 390, height: 844 });
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
