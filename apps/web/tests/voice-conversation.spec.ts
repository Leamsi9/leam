import { chooseConversation } from "./navigation";
import { test, expect } from "@playwright/test";
import { navigate, settingsSection } from "./navigation";
const run = "19e9c46c-a47b-4e01-8cb4-f4c5c4c26c63";
async function setup(page: any) {
  await page.addInitScript(() => {
    const w = window as any;
    localStorage.setItem(
      "leam.voice.output.v1",
      JSON.stringify({ version: 1, voice: null, rate: 1.25 }),
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
        w.probe.micDuringOutput ||= !!w.probe.audioActive;
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
        cancel: () => {
          w.probe.outputCancels = (w.probe.outputCancels || 0) + 1;
          w.probe.audioActive = false;
        },
        speak: (utterance: any) => {
          w.probe.audioActive = true;
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
        if (url.includes("/companion/")) w.stream = this;
      }
      close() {}
    };
  });
  const sends: any[] = [];
  let messages: any[] = [];
  let release: (() => void) | undefined;
  let defer = false;
  let outcome = "submitted";
  await page.route("**/api/**", async (route: any) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], providers: [], models: [], accounts: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/companion/threads")
      body = {
        threads: [
          { thread_id: "a", title: "A" },
          { thread_id: "b", title: "B" },
        ],
      };
    if (path === "/api/companion/threads/a") body = { messages };
    if (path === "/api/companion/threads/b") body = { messages: [] };
    if (path === "/api/proposals") body = { items: [] };
    if (path === "/api/codex/threads") body = { data: [] };
    if (path.endsWith("/messages")) {
      sends.push(route.request().postDataJSON());
      if (defer)
        await new Promise<void>((r) => {
          release = r;
        });
      body =
        outcome === "deferred_busy"
          ? {
              outcome,
              thread_id: "a",
              accepted_message_ref: "msg:voice-fixture",
              active_run_id: run,
              status: "Running",
            }
          : { outcome, run_id: run };
    }
    await route.fulfill({ json: body });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Companion", exact: true }).click();
  await chooseConversation(page, "a");
  await page.clock.install();
  return {
    sends,
    setMessages: (value: any[]) => {
      messages = value;
    },
    queued: () => {
      outcome = "deferred_busy";
    },
    uncertain: () => {
      outcome = "busy";
    },
    defer: () => {
      defer = true;
    },
    release: () => release?.(),
  };
}
async function start(page: any) {
  await page.getByRole("button", { name: "Conversation", exact: true }).click();
  await expect(page.getByText("Listening…", { exact: true })).toBeVisible();
}
async function terminal(page: any, id = run) {
  await page.evaluate(
    (id: string) =>
      (window as any).stream.dispatchEvent(
        new MessageEvent("projection_update", {
          data: JSON.stringify({
            type: "projection_update",
            state: {
              thread_id: "a",
              items: [{ run_status: { run_id: id, status: "completed" } }],
            },
          }),
        }),
      ),
    id,
  );
}

test("conversation combines segments once, waits through model/playback and rearms only after matching natural completion", async ({
  page,
}) => {
  const fixture = await setup(page);
  await start(page);
  await page.evaluate(() => {
    (window as any).transcript("Hello");
    (window as any).transcript("Hello");
    (window as any).capture.onend?.();
  });
  await page.clock.fastForward(200);
  await page.evaluate(() => (window as any).transcript("again"));
  await page.clock.fastForward(4100);
  await expect.poll(() => fixture.sends.length).toBe(1);
  expect(fixture.sends[0].text).toBe("Hello again");
  await expect(
    page.getByText("Waiting for reply…", { exact: true }),
  ).toBeVisible();
  await page.clock.fastForward(11000);
  await expect(
    page.getByText("Waiting for reply…", { exact: true }),
  ).toBeVisible();
  fixture.setMessages([
    {
      message_id: "unrelated",
      turn_run_id: "older",
      kind: "assistant",
      status: "finalized",
      content: "Do not speak",
      sequence: 1,
    },
  ]);
  await terminal(page, "older");
  expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
  fixture.setMessages([
    {
      message_id: "reply",
      turn_run_id: run,
      kind: "assistant",
      status: "finalized",
      content: "Use 2 * 3 and account_id.",
      sequence: 2,
    },
  ]);
  await terminal(page);
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.spoken.length))
    .toBe(1);
  expect(await page.evaluate(() => (window as any).probe.spoken[0])).toBe(
    "Use 2 * 3 and account_id.",
  );
  expect(await page.evaluate(() => (window as any).utterance.rate)).toBe(1.25);
  const starts = await page.evaluate(() => (window as any).probe.starts);
  await page.clock.fastForward(11000);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(starts);
  await page.evaluate(() => (window as any).utterance.onend?.());
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.starts))
    .toBe(starts + 1);
  await page.clock.fastForward(10100);
  await expect(page.getByText(/ended after 10 seconds/)).toBeVisible();
  expect(fixture.sends).toHaveLength(1);
});

test("turning conversation off while delivery is pending prevents playback and listening", async ({
  page,
}) => {
  const fixture = await setup(page);
  fixture.defer();
  await start(page);
  await page.evaluate(() => (window as any).transcript("One message"));
  await page.clock.fastForward(4100);
  await expect.poll(() => fixture.sends.length).toBe(1);
  await page
    .getByRole("button", { name: "End conversation", exact: true })
    .first()
    .click();
  fixture.release();
  await page.clock.fastForward(100);
  fixture.setMessages([
    {
      message_id: "late",
      turn_run_id: run,
      kind: "assistant",
      status: "finalized",
      content: "Late reply",
      sequence: 1,
    },
  ]);
  await terminal(page);
  await page.clock.fastForward(11000);
  expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
  expect(fixture.sends).toHaveLength(1);
});

test("denied microphone pauses without restart, and a draft blocks conversation", async ({
  page,
}) => {
  const fixture = await setup(page);
  await start(page);
  await page.evaluate(() =>
    (window as any).capture.onerror?.({ error: "not-allowed" }),
  );
  await expect(
    page.getByRole("button", { name: "Resume conversation" }),
  ).toBeVisible();
  const starts = await page.evaluate(() => (window as any).probe.starts);
  await page.clock.fastForward(16000);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(starts);
  expect(fixture.sends).toHaveLength(0);
  await page.getByLabel("Message Leam").fill("Keep my draft");
  await expect(
    page.getByRole("button", { name: "Conversation", exact: true }),
  ).toBeDisabled();
});

test("conversation waits for trailing final speech after the pause and ignores late start", async ({
  page,
}) => {
  const fixture = await setup(page);
  await start(page);
  await page.evaluate(() => {
    (window as any).deferEnd = true;
    (window as any).transcript("unfinished", false);
  });
  await page.clock.fastForward(4100);
  await expect(
    page.getByText("Finishing speech…", { exact: true }),
  ).toBeVisible();
  expect(fixture.sends).toHaveLength(0);
  await page.evaluate(() => {
    (window as any).capture.onstart?.();
    (window as any).transcript("Finished words", true);
    (window as any).capture.onend?.();
  });
  await expect.poll(() => fixture.sends.length).toBe(1);
  expect(fixture.sends[0].text).toBe("Finished words");
});

test("speech playback error pauses and a late end cannot restart the microphone", async ({
  page,
}) => {
  const fixture = await setup(page);
  await start(page);
  await page.evaluate(() => (window as any).transcript("Question"));
  await page.clock.fastForward(4100);
  await expect(
    page.getByText("Waiting for reply…", { exact: true }),
  ).toBeVisible();
  fixture.setMessages([
    {
      message_id: "answer",
      turn_run_id: run,
      kind: "assistant",
      status: "finalized",
      content: "Answer",
      sequence: 1,
    },
  ]);
  await terminal(page);
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.spoken.length))
    .toBe(1);
  const starts = await page.evaluate(() => (window as any).probe.starts);
  await page.evaluate(() => {
    const utterance = (window as any).utterance;
    utterance.onerror?.();
    utterance.onend?.();
  });
  await expect(
    page.getByRole("button", { name: "Resume conversation" }),
  ).toBeVisible();
  await page.clock.fastForward(11000);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(starts);
});

test("direct mic and conversation buttons fit mobile and desktop without composer configuration", async ({
  page,
}) => {
  await setup(page);
  for (const width of [360, 390, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(
      page.getByRole("button", { name: "Dictate", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Conversation", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Voice options", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByLabel("Speech language", { exact: true }),
    ).toHaveCount(0);
    for (const name of ["Dictate", "Conversation"]) {
      const box = (await page
        .getByRole("button", { name, exact: true })
        .boundingBox())!;
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width);
    }
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
});

test("uncertain voice delivery preserves one draft and never retries automatically", async ({
  page,
}) => {
  const fixture = await setup(page);
  fixture.uncertain();
  await start(page);
  await page.evaluate(() =>
    (window as any).transcript("Keep these exact words"),
  );
  await page.clock.fastForward(4100);
  await expect(
    page.getByRole("button", { name: "Resume conversation" }),
  ).toBeVisible();
  await expect(page.getByLabel("Message Leam")).toHaveValue(
    "Keep these exact words",
  );
  await page.clock.fastForward(30000);
  expect(fixture.sends).toHaveLength(1);
  await page.getByRole("button", { name: "Send to Leam", exact: true }).click();
  await expect.poll(() => fixture.sends.length).toBe(2);
  expect(fixture.sends[1]).toEqual(fixture.sends[0]);
});

test("typed submit and voice share synchronous exclusion against duplicate submits", async ({
  page,
}) => {
  const fixture = await setup(page);
  fixture.defer();
  await page.getByLabel("Message Leam").fill("One typed message");
  await page.evaluate(() => {
    const form = document.querySelector("form.composer");
    form?.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
    form?.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
  });
  await expect.poll(() => fixture.sends.length).toBe(1);
  fixture.release();
});

test("Speak now cancels playback before capture; late audio callbacks cannot interrupt the new turn", async ({
  page,
}) => {
  const fixture = await setup(page);
  await start(page);
  await page.evaluate(() => (window as any).transcript("First question"));
  await page.clock.fastForward(4100);
  await expect.poll(() => fixture.sends.length).toBe(1);
  fixture.setMessages([
    {
      message_id: "spoken-once",
      turn_run_id: run,
      kind: "assistant",
      status: "finalized",
      content: "A reply long enough to interrupt.",
      sequence: 1,
    },
  ]);
  await terminal(page);
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.spoken.length))
    .toBe(1);
  const captures = await page.evaluate(() => (window as any).probe.starts);
  await page.clock.fastForward(11000);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(
    captures,
  );
  await page.evaluate(() => {
    (window as any).oldUtterance = (window as any).utterance;
  });
  await page.getByRole("button", { name: "Speak now", exact: true }).click();
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.starts))
    .toBe(captures + 1);
  await page.evaluate(() => {
    const old = (window as any).oldUtterance;
    old.onend?.();
    old.onerror?.();
    old.onstart?.();
  });
  await expect(page.getByText("Listening…", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => (window as any).probe.micDuringOutput)).toBe(
    false,
  );
  await terminal(page); // Old completed run replay must not play the old answer again.
  expect(await page.evaluate(() => (window as any).probe.spoken.length)).toBe(
    1,
  );
  await page.evaluate(() => (window as any).transcript("Second question"));
  await page.clock.fastForward(4100);
  await expect.poll(() => fixture.sends.length).toBe(2);
  expect(fixture.sends[1].text).toBe("Second question");
  expect(fixture.sends[0].requestId).not.toBe(fixture.sends[1].requestId);
  expect(await page.evaluate(() => (window as any).probe.spoken.length)).toBe(
    1,
  );
});

test("default conversation pause waits four seconds, not two", async ({
  page,
}) => {
  const fixture = await setup(page);
  await start(page);
  await page.evaluate(() =>
    (window as any).transcript("Wait for my full thought"),
  );
  await page.clock.fastForward(2100);
  expect(fixture.sends).toHaveLength(0);
  await expect(page.getByText("Listening…", { exact: true })).toBeVisible();
  await page.clock.fastForward(2000);
  await expect.poll(() => fixture.sends.length).toBe(1);
});

test("central speech settings persist and configure direct dictation and conversation", async ({
  page,
}) => {
  const fixture = await setup(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await navigate(page, "Settings");
  await settingsSection(page, "Voice and playback");
  const pause = page.getByLabel("Pause before sending (seconds)");
  await expect(pause).toHaveValue("4");
  await expect(pause).toHaveAttribute("min", "1");
  await expect(pause).toHaveAttribute("max", "10");
  await pause.fill("7");
  await page.getByLabel("Speech language", { exact: true }).fill("fr-FR");
  await navigate(page, "Companion");
  await chooseConversation(page, "a");
  await page.getByRole("button", { name: "Dictate", exact: true }).click();
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(1);
  expect(await page.evaluate(() => (window as any).capture.lang)).toBe("fr-FR");
  await page
    .getByRole("button", { name: "Cancel dictation", exact: true })
    .click();
  await start(page);
  expect(await page.evaluate(() => (window as any).capture.lang)).toBe("fr-FR");
  await page.evaluate(() => (window as any).transcript("Une question"));
  await page.clock.fastForward(6100);
  expect(fixture.sends).toHaveLength(0);
  await page.clock.fastForward(1000);
  await expect.poll(() => fixture.sends.length).toBe(1);
  await page
    .getByRole("button", { name: "End conversation", exact: true })
    .click();
  await navigate(page, "Settings");
  await settingsSection(page, "Voice and playback");
  await expect(page.getByLabel("Pause before sending (seconds)")).toHaveValue(
    "7",
  );
  await expect(page.getByLabel("Speech language", { exact: true })).toHaveValue(
    "fr-FR",
  );
});

test("ten-second configured pause finalizes speech instead of discarding it as inactivity", async ({
  page,
}) => {
  const f = await setup(page);
  await page.evaluate(() =>
    localStorage.setItem(
      "leam.voice.input.v1",
      JSON.stringify({ language: "en-GB", pauseSeconds: 10 }),
    ),
  );
  await start(page);
  await page.evaluate(() =>
    (window as any).transcript("Give me time to finish"),
  );
  await page.clock.fastForward(9100);
  expect(f.sends).toHaveLength(0);
  await page.clock.fastForward(1000);
  await expect.poll(() => f.sends.length).toBe(1);
  expect(f.sends[0].text).toBe("Give me time to finish");
});

test("received busy voice follow-up clears its draft and never speaks an unrelated active reply", async ({
  page,
}) => {
  const fixture = await setup(page);
  fixture.queued();
  await start(page);
  await page.evaluate(() => (window as any).transcript("Follow up while busy"));
  await page.clock.fastForward(4100);
  await expect.poll(() => fixture.sends.length).toBe(1);
  await expect(page.getByText(/delivered as a follow-up/)).toBeVisible();
  await expect(page.getByLabel("Message Leam")).toHaveValue("");
  fixture.setMessages([
    {
      message_id: "earlier-reply",
      turn_run_id: run,
      kind: "assistant",
      content: "Earlier answer",
      sequence: 1,
      status: "finalized",
    },
  ]);
  await terminal(page);
  await page.clock.fastForward(10000);
  expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
  expect(fixture.sends).toHaveLength(1);
});
