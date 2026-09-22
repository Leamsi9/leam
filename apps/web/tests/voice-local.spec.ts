import { test, expect } from "@playwright/test";
import { chooseConversation } from "./navigation";
import { pocketMediaProbe } from "./pocket-media-fixture";

async function setup(
  page: any,
  engines = { input: "browser", output: "browser" },
  reply = "A synthetic answer for playback.",
) {
  await page.addInitScript((choice: any) => {
    const w = window as any;
    localStorage.setItem("leam.voice.engines.v1", JSON.stringify(choice));
    w.localProbe = {
      resumed: 0,
      stopped: 0,
      played: [],
      playedRates: [],
      modules: [],
      micStopped: 0,
    };
    w.AudioContext = class {
      state = "suspended";
      destination = {};
      sampleRate = 48000;
      get currentTime() {
        return performance.now() / 1000;
      }
      audioWorklet = {
        addModule: async (url: string) => w.localProbe.modules.push(url),
      };
      resume() {
        w.localProbe.resumed++;
        this.state = "running";
        return Promise.resolve();
      }
      suspend() {
        this.state = "suspended";
        return Promise.resolve();
      }
      close() {
        this.state = "closed";
        return Promise.resolve();
      }
      createBuffer(_channels: number, length: number, rate: number) {
        return { length, duration: length / rate, copyToChannel() {} };
      }
      createBufferSource() {
        return {
          buffer: null as any,
          playbackRate: { value: 1 },
          onended: null as any,
          connect() {},
          disconnect() {},
          start() {
            if (this.buffer?.length > 1)
              {
              w.localProbe.played.push(this.buffer.length);
              w.localProbe.playedRates.push(this.playbackRate.value);
            }
            if (!w.localProbe.holdAudio) setTimeout(() => this.onended?.(), 80);
          },
          stop() {
            w.localProbe.stopped++;
          },
        };
      }
      createMediaStreamSource() {
        return { connect() {} };
      }
      createGain() {
        return { gain: { value: 1 }, connect() {} };
      }
    };
    w.AudioWorkletNode = class {
      port = {
        onmessage: null as any,
        postMessage: () => this.port.onmessage?.({ data: { ended: true } }),
      };
      constructor() {
        w.localWorklet = this;
      }
      connect() {}
      disconnect() {}
    };
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => ({
          getTracks: () => [{ stop: () => w.localProbe.micStopped++ }],
        }),
      },
    });
  }, engines);
  await pocketMediaProbe(page);
  const calls: any[] = [];
  await page.route("**/api/**", async (route: any) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], accounts: [], providers: [], models: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/voice/status")
      body = { ready: true, language: "en", voices: ["alba"] };
    if (path === "/api/codex/threads") body = { data: [] };
    if (path === "/api/companion/threads")
      body = { threads: [{ thread_id: "a", title: "Local voice" }] };
    if (path === "/api/companion/threads/a")
      body = {
        messages: [
          {
            message_id: "answer",
            turn_run_id: "old",
            kind: "assistant",
            status: "finalized",
            sequence: 1,
            content: reply,
          },
        ],
      };
    if (path === "/api/voice/capture") {
      const input = route.request().postDataJSON();
      calls.push(input);
      body = {
        ...input,
        finished: !!input.finish,
        finalText: input.finish ? "Recognized local words" : "",
        interimText: input.pcm ? "Recognized local" : "",
      };
    }
    if (path.endsWith("/events")) {
      await route.abort();
      return;
    }
    await route.fulfill({ json: body });
  });
  return calls;
}
async function settings(page: any) {
  await page.goto("/?view=settings");
  await page
    .locator("summary")
    .filter({ hasText: "Voice and playback" })
    .click();
}
async function companion(page: any) {
  await page.goto("/");
  await page.getByRole("button", { name: "Companion", exact: true }).click();
  await chooseConversation(page, "a");
}
const audio =
  JSON.stringify({
    sequence: 0,
    sampleRate: 24000,
    pcm: Buffer.alloc(400).toString("base64"),
  }) + '\n{"done":true}\n';

test("active local listening recycles bounded silent captures without a model request or deadline extension", async ({ page }) => {
  await setup(page, { input: "moonshine", output: "browser" });
  const captures: any[] = [], sends: any[] = [];
  await page.route("**/api/voice/capture", async route => {
    const body = route.request().postDataJSON(); captures.push(body);
    await route.fulfill({ json: { ...body, finalText: "", interimText: "" } });
  });
  await page.route("**/api/companion/threads/a/messages", async route => {
    sends.push(route.request().postDataJSON());
    await route.fulfill({ json: { outcome: "submitted", run_id: "unexpected" } });
  });
  await companion(page);
  await page.clock.install();
  await page.getByRole("button", { name: "Active listening (5 minutes)", exact: true }).click();
  await expect(page.getByText("Microphone on", { exact: false })).toBeVisible();
  for (let cycle = 1; cycle <= 4; cycle++) {
    await page.clock.fastForward(60000);
    await expect.poll(() => captures.filter(c => c.finish).length).toBe(cycle);
    await page.clock.fastForward(200);
    await expect.poll(() => new Set(captures.map(c => c.captureId)).size).toBe(cycle + 1);
  }
  await page.clock.fastForward(60000);
  await expect(page.getByRole("button", { name: "Stop active listening", exact: true })).toHaveCount(0);
  expect(sends).toHaveLength(0);
  expect(new Set(captures.map(c => c.captureId)).size).toBe(5);
  expect(captures.every(c => c.sequence <= 180)).toBeTruthy();
});

test("local active listening preserves an overlong utterance as a draft without chopping or duplicate sending", async ({ page }) => {
  await setup(page, { input: "moonshine", output: "browser" });
  const captures: any[] = [], sends: any[] = [];
  let first = "", chunks = 0;
  await page.route("**/api/voice/capture", async route => {
    const body = route.request().postDataJSON(); captures.push(body);
    first ||= body.captureId;
    if (body.pcm) chunks++;
    const words = body.captureId === first ? `First segment ${chunks}` : "second segment";
    await route.fulfill({ json: {
      ...body,
      finalText: body.finish || (body.pcm && body.captureId !== first) ? words : "",
      interimText: body.pcm && body.captureId === first && !body.finish ? words : "",
    } });
  });
  await page.route("**/api/companion/threads/a/messages", async route => {
    sends.push(route.request().postDataJSON());
    await route.fulfill({ json: { outcome: "submitted", run_id: "19e9c46c-a47b-4e01-8cb4-f4c5c4c26c63" } });
  });
  await companion(page);
  // Wall time spent awaiting mocked transport must not consume the narrow
  // 75-second deadline; advance recognition time explicitly below.
  const clockStart = Date.UTC(2030, 0, 1);
  await page.clock.install({ time: clockStart });
  await page.clock.pauseAt(clockStart + 1000);
  await page.getByRole("button", { name: "Active listening (5 minutes)", exact: true }).click();
  await expect(page.getByText("Microphone on", { exact: false })).toBeVisible();
  await page.clock.fastForward(59900);
  await page.evaluate(() => (window as any).localWorklet.port.onmessage({ data: { pcm: new Float32Array(8000).fill(0.1) } }));
  await expect.poll(() => captures.filter(c => c.pcm).length).toBe(1);
  await expect(page.getByLabel("Conversation speech")).toContainText("First segment 1");
  await page.clock.fastForward(200);
  expect(captures.filter(c => c.finish)).toHaveLength(0);
  // Keep producing provisional speech rather than an end-of-utterance pause.
  for (let part = 0; part < 5; part++) {
    await page.clock.fastForward(2900);
    await page.evaluate(() => (window as any).localWorklet.port.onmessage({ data: { pcm: new Float32Array(8000).fill(0.1) } }));
    await expect.poll(() => captures.filter(c => c.pcm).length).toBe(part + 2);
    await expect(page.getByLabel("Conversation speech")).toContainText(`First segment ${part + 2}`);
  }
  await page.clock.fastForward(700);
  await expect(page.getByLabel("Message Leam")).toHaveValue("First segment 6");
  await expect(page.getByText(/Long speech was kept as a draft/)).toBeVisible();
  expect(sends).toHaveLength(0);
  expect(new Set(captures.map(c => c.captureId)).size).toBe(1);
});

test("independent engine choices preserve browser defaults and unlock Pocket from preview gesture", async ({
  page,
}) => {
  await setup(page);
  let unlocked = false,
    spokenVoice = "";
  await page.route("**/api/voice/speak", async (route) => {
    unlocked = await page.evaluate(
      () => (window as any).mediaProbe.loads > 0,
    );
    spokenVoice = route.request().postDataJSON().voice;
    await route.fulfill({ contentType: "application/x-ndjson", body: audio });
  });
  await settings(page);
  await expect(page.getByLabel("Speech recognition engine")).toHaveValue(
    "browser",
  );
  await expect(page.getByLabel("Speech playback engine")).toHaveValue(
    "browser",
  );
  await page.getByLabel("Speech playback engine").selectOption("pocket");
  await expect(page.getByLabel("Speech recognition engine")).toHaveValue(
    "browser",
  );
  await expect(page.getByLabel("Speaking voice")).toBeEnabled();
  await expect(page.getByLabel("Speaking voice")).toHaveValue("alba");
  await expect(page.getByLabel("Speaking voice").locator("option")).toHaveText([
    "Alba · local English",
  ]);
  await page.getByLabel("Speaking voice").selectOption("alba");
  await page
    .getByRole("button", { name: "Preview voice", exact: true })
    .click();
  await expect
    .poll(() => page.evaluate(() => (window as any).localProbe.played.length))
    .toBe(1);
  expect(unlocked).toBe(true);
  expect(
    await page.evaluate(() =>
      JSON.parse(localStorage.getItem("leam.voice.engines.v1")!),
    ),
  ).toEqual({ input: "browser", output: "pocket" });
  expect(spokenVoice).toBe("alba");
  expect(
    await page.evaluate(() =>
      JSON.parse(localStorage.getItem("leam.voice.output.v1")!),
    ),
  ).toMatchObject({ pocketVoice: "alba" });
});

test("local microphone chunks finish into editable draft without a model send", async ({
  page,
}) => {
  const calls = await setup(page, { input: "moonshine", output: "browser" });
  let sends = 0;
  page.on("request", (request) => {
    if (request.url().endsWith("/messages") && request.method() === "POST")
      sends++;
  });
  await companion(page);
  await page.getByLabel("Message Leam").fill("Keep draft");
  await page.getByRole("button", { name: "Dictate", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Finish dictation" }),
  ).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => Boolean((window as any).localWorklet)))
    .toBe(true);
  await page.evaluate(() =>
    (window as any).localWorklet.port.onmessage({
      data: { pcm: new Float32Array([0.2, -0.2]) },
    }),
  );
  await expect(page.getByLabel("Provisional dictation")).toHaveText(
    "Recognized local",
  );
  await page.getByRole("button", { name: "Finish dictation" }).click();
  await expect(page.getByLabel("Message Leam")).toHaveValue(
    "Keep draft Recognized local words",
  );
  expect(calls.map((call) => call.sequence)).toEqual([0, 1, 2]);
  expect(calls.at(-1).finish).toBe(true);
  expect(sends).toBe(0);
  expect(
    await page.evaluate(() => (window as any).localProbe.micStopped),
  ).toBeGreaterThan(0);
  expect(
    await page.evaluate(() => (window as any).localProbe.modules[0]),
  ).not.toMatch(/^data:|^blob:/);
});

test("cancelled local dictation fences a held recognition response", async ({
  page,
}) => {
  await setup(page, { input: "moonshine", output: "browser" });
  let release!: () => void,
    held = false;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/voice/capture", async (route) => {
    const input = route.request().postDataJSON();
    if (input.sequence > 0) {
      held = true;
      await pending;
    }
    await route
      .fulfill({
        json: {
          ...input,
          finalText: "Late words",
          interimText: "",
          finished: false,
        },
      })
      .catch(() => undefined);
  });
  await companion(page);
  await page.getByLabel("Message Leam").fill("Original");
  await page.getByRole("button", { name: "Dictate", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Finish dictation" }),
  ).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => Boolean((window as any).localWorklet)))
    .toBe(true);
  await page.evaluate(() =>
    (window as any).localWorklet.port.onmessage({
      data: { pcm: new Float32Array([0.2]) },
    }),
  );
  await expect.poll(() => held).toBe(true);
  await page.getByRole("button", { name: "Cancel dictation" }).click();
  release();
  await expect(page.getByLabel("Message Leam")).toHaveValue("Original");
  await expect(page.getByLabel("Provisional dictation")).toHaveCount(0);
});

test("Pocket stop fences late audio and allows a new playback", async ({
  page,
}) => {
  await setup(page, { input: "browser", output: "pocket" });
  let release!: () => void,
    calls = 0;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/voice/speak", async (route) => {
    calls++;
    if (calls === 1) await pending;
    await route
      .fulfill({ contentType: "application/x-ndjson", body: audio })
      .catch(() => undefined);
  });
  await companion(page);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect.poll(() => calls).toBe(1);
  await page.locator("#companion-message-answer")
    .getByRole("button", { name: "Stop speaking", exact: true })
    .click();
  release();
  expect(await page.evaluate(() => (window as any).localProbe.played)).toEqual(
    [],
  );
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect
    .poll(() => page.evaluate(() => (window as any).localProbe.played.length))
    .toBe(1);
  expect(calls).toBe(2);
});

test("Pocket failure retries the bounded phrase once with browser speech", async ({
  page,
}) => {
  const firstPhrase = "word ".repeat(48);
  const lastPhrase = "remaining words.";
  await setup(
    page,
    { input: "browser", output: "pocket" },
    firstPhrase + lastPhrase,
  );
  await page.addInitScript(() => {
    const w = window as any;
    w.browserSpoken = [];
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
        cancel() {},
        resume() {},
        speak(utterance: any) {
          w.browserSpoken.push(utterance.text);
          w.browserUtterance = utterance;
          utterance.onstart?.();
        },
      },
    });
  });
  const stopped =
    JSON.stringify({
      sequence: 0,
      sampleRate: 24000,
      pcm: Buffer.alloc(400).toString("base64"),
    }) + '\n{"error":"Local speech stopped"}\n';
  let localCalls = 0,
    release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/voice/speak", async (route) => {
    localCalls++;
    await held;
    await route.fulfill({
      contentType: "application/x-ndjson",
      body: stopped,
    });
  });
  await companion(page);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect.poll(() => localCalls).toBe(1);
  await page.evaluate(() =>
    localStorage.setItem(
      "leam.voice.engines.v1",
      JSON.stringify({ input: "browser", output: "browser" }),
    ),
  );
  release();
  await expect
    .poll(() => page.evaluate(() => (window as any).browserSpoken))
    .toEqual([firstPhrase]);
  expect(localCalls).toBe(1);
  await expect(
    page.getByText("Pocket stopped; continuing with the device voice."),
  ).toBeVisible();
  await page.evaluate(() => (window as any).browserUtterance.onend?.());
  await expect
    .poll(() => page.evaluate(() => (window as any).browserSpoken))
    .toEqual([firstPhrase, lastPhrase]);
  expect(localCalls).toBe(1);
  await page.evaluate(() => (window as any).browserUtterance.onend?.());
  await expect(page.getByText("Playback finished.")).toBeVisible();
});

test("Pocket failure remains terminal when browser speech is unavailable", async ({
  page,
}) => {
  await setup(page, { input: "browser", output: "pocket" });
  await page.addInitScript(() => {
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(window, "SpeechSynthesisUtterance", {
      configurable: true,
      value: undefined,
    });
  });
  let localCalls = 0;
  await page.route("**/api/voice/speak", async (route) => {
    localCalls++;
    await route.fulfill({
      contentType: "application/x-ndjson",
      body: '{"error":"Local speech stopped"}\n',
    });
  });
  await companion(page);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect(
    page.getByText(
      "Playback is unavailable. Use Stop, then tap Read aloud to try again.",
    ),
  ).toBeVisible();
  expect(localCalls).toBe(1);
});

test("failed browser fallback does not retry Pocket or loop", async ({
  page,
}) => {
  await setup(page, { input: "browser", output: "pocket" });
  await page.addInitScript(() => {
    const w = window as any;
    w.browserCalls = 0;
    w.SpeechSynthesisUtterance = class {
      constructor(public text: string) {}
    };
    Object.defineProperty(w, "speechSynthesis", {
      configurable: true,
      value: {
        getVoices: () => [],
        cancel() {},
        resume() {},
        speak(utterance: any) {
          w.browserCalls++;
          utterance.onerror?.();
        },
      },
    });
  });
  let localCalls = 0;
  await page.route("**/api/voice/speak", async (route) => {
    localCalls++;
    await route.fulfill({
      contentType: "application/x-ndjson",
      body: '{"error":"Local speech stopped"}\n',
    });
  });
  await companion(page);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect(
    page.getByText(
      "Playback is unavailable. Use Stop, then tap Read aloud to try again.",
    ),
  ).toBeVisible();
  expect(localCalls).toBe(1);
  expect(await page.evaluate(() => (window as any).browserCalls)).toBe(1);
});

test("Pocket waits for microphone release and keeps read-aloud across app navigation", async ({
  page,
}) => {
  await setup(page, { input: "moonshine", output: "pocket" });
  let release!: () => void,
    cancelling = false,
    spoken = 0,
    spokenVoice = "";
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/voice/capture/cancel", async (route) => {
    cancelling = true;
    await held;
    await route.fulfill({ json: { cancelled: true } });
  });
  await page.route("**/api/voice/speak", async (route) => {
    spoken++;
    spokenVoice = route.request().postDataJSON().voice;
    await route.fulfill({ contentType: "application/x-ndjson", body: audio });
  });
  await companion(page);
  await page.evaluate(() => {
    (window as any).localProbe.holdAudio = true;
  });
  await page.getByRole("button", { name: "Dictate", exact: true }).click();
  await expect
    .poll(() => page.evaluate(() => Boolean((window as any).localWorklet)))
    .toBe(true);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect.poll(() => cancelling).toBe(true);
  expect(
    await page.evaluate(() => (window as any).localProbe.micStopped),
  ).toBeGreaterThan(0);
  expect(spoken).toBe(0);
  await page.evaluate(() =>
    localStorage.setItem(
      "leam.voice.output.v1",
      JSON.stringify({
        version: 1,
        voice: null,
        pocketVoice: "changed_after_start",
        rate: 1,
      }),
    ),
  );
  release();
  await expect
    .poll(() => page.evaluate(() => (window as any).localProbe.played.length))
    .toBe(1);
  expect(spokenVoice).toBe("alba");
  const stopped = await page.evaluate(() => (window as any).localProbe.stopped);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Stop playback", exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => (window as any).localProbe.stopped)).toBe(
    stopped,
  );
  await page
    .getByRole("button", { name: "Stop playback", exact: true })
    .click();
  expect(
    await page.evaluate(() => (window as any).localProbe.stopped),
  ).toBeGreaterThan(stopped);
});

for (const width of [390, 1440]) test(`provider profiles preserve independent speeds and preview voice without switching playback ${width}`, async ({page}) => {
  await setup(page, {input: "browser", output: "pocket"});
  await page.setViewportSize({width, height: 950});
  await page.route("**/api/voice/status", route => route.fulfill({json:{ready:true, voices:["alba","marius"]}}));
  const requests: any[] = [];
  await page.route("**/api/voice/speak", route => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({contentType:"application/x-ndjson",body:audio});
  });
  await settings(page);
  await page.getByLabel("Configure voice provider").selectOption("browser");
  const speed = page.getByLabel("Speaking speed", {exact:true});
  await speed.focus();
  for(let i=0;i<5;i++) await page.keyboard.press("ArrowRight");
  await expect(speed).toHaveValue("1.25");
  await expect(page.getByLabel("Speech playback engine")).toHaveValue("pocket");
  await page.getByLabel("Configure voice provider").selectOption("pocket");
  await expect(speed).toHaveValue("1");
  await page.getByLabel("Speaking voice").selectOption("marius");
  await page.getByRole("button",{name:"Preview voice",exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>(window as any).localProbe.playedRates)).toEqual([1]);
  expect(requests[0].voice).toBe("marius");
  await page.reload();
  await page.locator("summary").filter({hasText:"Voice and playback"}).click();
  await expect(page.getByLabel("Speaking voice")).toHaveValue("marius");
  await expect(speed).toHaveValue("1");
  await page.getByLabel("Configure voice provider").selectOption("browser");
  await expect(speed).toHaveValue("1.25");
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test("coalesced valid Pocket frames remain local and drain successfully", async ({page}) => {
  await setup(page, {input:"browser",output:"pocket"});
  const pcm=Buffer.alloc(12000*4).toString("base64");
  const body=[0,1,2].map(sequence=>JSON.stringify({sequence,sampleRate:24000,pcm})).join("\n")+'\n{"done":true}\n';
  expect(body.length).toBeGreaterThan(150000);
  await page.addInitScript((frames) => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const url = input instanceof Request ? input.url : String(input);
      if (new URL(url, location.href).pathname === "/api/voice/speak") {
        return Promise.resolve(new Response(new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(frames));
            controller.close();
          },
        }), {headers: {"Content-Type": "application/x-ndjson"}}));
      }
      return originalFetch(input, init);
    };
  }, body);
  await companion(page);
  await page.getByRole("button",{name:"Read aloud",exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>(window as any).localProbe.played)).toEqual([36000]);
  await expect(page.getByText("Playback finished.")).toBeVisible();
  await expect(page.getByText(/switching to.*browser|Playback is unavailable/i)).toHaveCount(0);
});

test("Pocket playback watchdog excludes an explicit pause", async ({page}) => {
  await page.clock.install();
  await setup(page,{input:"browser",output:"pocket"});
  await page.route("**/api/voice/speak", route => route.fulfill({contentType:"application/x-ndjson",body:audio}));
  await companion(page);
  await page.evaluate(()=>(window as any).localProbe.holdAudio=true);
  await page.getByRole("button",{name:"Read aloud",exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>(window as any).localProbe.played.length)).toBe(1);
  await page.getByRole("button",{name:"Pause playback",exact:true}).click();
  await page.clock.fastForward(95000);
  await expect(page.getByRole("button",{name:"Resume playback",exact:true})).toBeVisible();
  await page.getByRole("button",{name:"Resume playback",exact:true}).click();
  await expect(page.getByRole("button",{name:"Pause playback",exact:true})).toBeVisible();
  await page.getByRole("button",{name:"Stop playback",exact:true}).click();
});

test("Pocket background media uses real WAV and generic pause/resume/stop controls", async ({ page }) => {
  await setup(page, { input: "browser", output: "pocket" });
  await page.route("**/api/voice/speak", route => route.fulfill({ contentType: "application/x-ndjson", body: audio }));
  await companion(page);
  await page.evaluate(() => (window as any).localProbe.holdAudio = true);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).mediaProbe.plays)).toBe(1);
  const before = await page.evaluate(() => (window as any).mediaProbe.pauses);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(page.getByRole("button", { name: "Pause playback", exact: true })).toBeVisible();
  const details = await page.evaluate(() => {
    const p = (window as any).mediaProbe;
    return { pauses: p.pauses, title: p.metadata.title, artist: p.metadata.artist, position: p.position, wav: p.wav };
  });
  expect(details).toMatchObject({ pauses: before, title: "Leam read-aloud", artist: "Leam", position: null,
    wav: { type: "audio/wav", bytes: 244, sampleRate: 24000, channels: 1, bits: 16, dataBytes: 200 } });
  await page.evaluate(() => (window as any).mediaProbe.handlers.pause());
  await expect(page.getByRole("button", { name: "Resume playback", exact: true })).toBeVisible();
  await page.evaluate(() => (window as any).mediaProbe.handlers.play());
  await expect.poll(() => page.evaluate(() => (window as any).mediaProbe.plays)).toBe(2);
  await page.evaluate(() => (window as any).mediaProbe.handlers.stop());
  await expect(page.getByRole("button", { name: "Stop playback", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => {
    const p = (window as any).mediaProbe;
    return { metadata: p.metadata, state: p.playbackState, src: p.elements[0].src, revoked: p.revoked.length,
      handlers: Object.values(p.handlers).filter(Boolean).length };
  })).toEqual({ metadata: null, state: "none", src: "", revoked: 1, handlers: 0 });
});

test("Pocket pause during synthesis retains the phrase without starting until explicit Resume", async ({ page }) => {
  await setup(page, { input: "browser", output: "pocket" });
  let release!: () => void, requests = 0;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/voice/speak", async route => {
    requests++; await held;
    await route.fulfill({ contentType: "application/x-ndjson", body: audio });
  });
  await companion(page);
  await page.evaluate(() => (window as any).localProbe.holdAudio = true);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect.poll(() => requests).toBe(1);
  await page.getByRole("button", { name: "Pause playback", exact: true }).click();
  release();
  await expect.poll(() => page.evaluate(() => (window as any).mediaProbe.urls.length)).toBe(1);
  expect(await page.evaluate(() => (window as any).mediaProbe.plays)).toBe(0);
  await page.getByRole("button", { name: "Resume playback", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).mediaProbe.plays)).toBe(1);
  expect(requests).toBe(1);
});

test("Pocket browser permission rejection retains real audio for explicit retry without synthesis or fallback", async ({ page }) => {
  await setup(page, { input: "browser", output: "pocket" });
  let requests = 0;
  await page.route("**/api/voice/speak", route => {
    requests++;
    return route.fulfill({ contentType: "application/x-ndjson", body: audio });
  });
  await companion(page);
  await page.evaluate(() => { (window as any).mediaProbe.rejectNext = true; (window as any).localProbe.holdAudio = true; });
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect(page.getByText("Audio needs your permission. Tap Resume playback to listen.")).toBeVisible();
  await page.getByRole("button", { name: "Resume playback", exact: true }).click();
  await expect(page.getByText("Reading the reply…")).toBeVisible();
  expect(requests).toBe(1);
  expect(await page.evaluate(() => {
    const p = (window as any).mediaProbe;
    return { plays: p.plays, urls: p.urls.length, rejected: p.rejected, title: p.metadata.title };
  })).toEqual({ plays: 2, urls: 1, rejected: 1, title: "Leam read-aloud" });
});

test("Pocket replacement and auth loss fence old media callbacks and clear private audio", async ({ page }) => {
  await setup(page, { input: "browser", output: "pocket" });
  await page.route("**/api/voice/speak", route => route.fulfill({ contentType: "application/x-ndjson", body: audio }));
  await companion(page);
  await page.evaluate(() => (window as any).localProbe.holdAudio = true);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).mediaProbe.plays)).toBe(1);
  await page.evaluate(() => { (window as any).oldEnded = (window as any).mediaProbe.elements[0].onended; });
  await page.getByRole("button", { name: "Replay last output", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).mediaProbe.plays)).toBe(2);
  await page.evaluate(() => (window as any).oldEnded());
  await expect(page.getByRole("button", { name: "Pause playback", exact: true })).toBeVisible();
  await page.evaluate(() => window.dispatchEvent(new Event("leam:auth-lost")));
  await expect(page.getByRole("complementary", { name: "Speech playback" })).toHaveCount(0);
  expect(await page.evaluate(() => {
    const p = (window as any).mediaProbe;
    return { count: p.elements.length, src: p.elements[0].src, metadata: p.metadata, urls: p.urls.length, revoked: p.revoked.length };
  })).toEqual({ count: 1, src: "", metadata: null, urls: 2, revoked: 2 });
});

test("backgrounding still stops local microphone and never rearms on return", async ({ page }) => {
  await setup(page, { input: "moonshine", output: "pocket" });
  await companion(page);
  await page.getByRole("button", { name: "Active listening (5 minutes)", exact: true }).click();
  await expect(page.getByText("Microphone on", { exact: false })).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(page.getByRole("button", { name: "Stop active listening", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).localProbe.micStopped)).toBeGreaterThan(0);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(page.getByRole("button", { name: "Stop active listening", exact: true })).toHaveCount(0);
});

const longLookaheadReply = Array.from({ length: 70 }, (_, index) => `word${index}`).join(" ") + ".";
test("Pocket prepares only the next phrase during playback and consumes it once in order", async ({ page }) => {
  await setup(page, { input: "browser", output: "pocket" }, longLookaheadReply);
  const requests: string[] = [];
  await page.route("**/api/voice/speak", async route => {
    requests.push(route.request().postDataJSON().text);
    await route.fulfill({ contentType: "application/x-ndjson", body: audio });
  });
  await companion(page);
  await page.evaluate(() => (window as any).localProbe.holdAudio = true);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(await page.evaluate(() => (window as any).mediaProbe.plays)).toBe(1);
  expect(requests.every(text => text.length <= 240)).toBe(true);
  // Synthesis of phrase two precedes the end of phrase one; it has no media yet.
  expect(await page.evaluate(() => (window as any).mediaProbe.urls.length)).toBe(1);
  let completed = 0;
  while (completed < 4) {
    await page.evaluate(() => (window as any).mediaProbe.elements[0].onended?.());
    completed++;
    if (requests.join("") === longLookaheadReply && completed === requests.length) break;
    await expect.poll(() => page.evaluate(() => (window as any).mediaProbe.plays)).toBe(completed + 1);
    expect(requests.length).toBeLessThanOrEqual(completed + 2);
  }
  await expect(page.getByText("Playback finished.", { exact: true })).toBeVisible();
  expect(requests.join("")).toBe(longLookaheadReply);
  expect(new Set(requests).size).toBe(requests.length);
  expect(await page.evaluate(() => (window as any).mediaProbe.elements.length)).toBe(1);
});

test("Pocket lookahead completion while paused cannot advance or restart audio", async ({ page }) => {
  await setup(page, { input: "browser", output: "pocket" }, longLookaheadReply);
  let release!: () => void, requests = 0;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/voice/speak", async route => {
    requests++;
    if (requests === 2) await held;
    await route.fulfill({ contentType: "application/x-ndjson", body: audio });
  });
  await companion(page);
  await page.evaluate(() => (window as any).localProbe.holdAudio = true);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect.poll(() => requests).toBe(2);
  await page.getByRole("button", { name: "Pause playback", exact: true }).click();
  const response = page.waitForResponse(r => new URL(r.url()).pathname === "/api/voice/speak");
  release(); await (await response).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(await page.evaluate(() => (window as any).mediaProbe.plays)).toBe(1);
  await expect(page.getByRole("button", { name: "Resume playback", exact: true })).toBeVisible();
  expect(requests).toBe(2);
  await page.getByRole("button", { name: "Resume playback", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).mediaProbe.plays)).toBe(2);
  await page.evaluate(() => (window as any).mediaProbe.elements[0].onended?.());
  await expect.poll(() => page.evaluate(() => (window as any).mediaProbe.plays)).toBe(3);
  await page.getByRole("button", { name: "Stop playback", exact: true }).click();
});

for (const action of ["stop", "auth", "provider"] as const)
test(`Pocket lookahead is cancelled without late playback after ${action}`, async ({ page }) => {
  await setup(page, { input: "browser", output: "pocket" }, longLookaheadReply);
  let release!: () => void, requests = 0;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/voice/speak", async route => {
    requests++;
    if (requests === 2) await held;
    await route.fulfill({ contentType: "application/x-ndjson", body: audio }).catch(() => undefined);
  });
  await companion(page);
  await page.evaluate(() => (window as any).localProbe.holdAudio = true);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect.poll(() => requests).toBe(2);
  await page.evaluate(() => { (window as any).lookaheadOldEnded = (window as any).mediaProbe.elements[0].onended; });
  if (action === "stop") await page.getByRole("button", { name: "Stop playback", exact: true }).click();
  else if (action === "auth") await page.evaluate(() => window.dispatchEvent(new Event("leam:auth-lost")));
  else {
    // An in-app navigation preserves playback until the explicit engine change.
    await page.locator('nav[aria-label="Main navigation"]:visible').getByRole("button", { name: "Settings", exact: true }).click();
    await page.locator(".settings-section").filter({ has: page.locator(":scope > summary", { hasText: /^Voice and playback$/ }) })
      .locator(":scope > summary").click();
    await page.getByLabel("Speech playback engine", { exact: true }).selectOption("browser");
  }
  release();
  await page.evaluate(() => { (window as any).lookaheadOldEnded?.(); });
  await expect(page.getByRole("button", { name: "Stop playback", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => ({ plays: (window as any).mediaProbe.plays, src: (window as any).mediaProbe.elements[0].src })))
    .toEqual({ plays: 1, src: "" });
  expect(requests).toBe(2);
});
