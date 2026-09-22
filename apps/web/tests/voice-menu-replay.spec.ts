import { test, expect, type Page } from "@playwright/test";
import { chooseConversation, navigate } from "./navigation";

// Projection and agenda fixtures use the existing voice-interactions and
// today-reconciliation caller contracts; only browser speech is substituted.
const reply = (id: string, text: string, sequence: number) => ({
  message_id: id,
  kind: "assistant",
  status: "finalized",
  content: text,
  sequence,
  turn_run_id: `run-${id}`,
});
async function setup(
  page: Page,
  surface: "Companion" | "Today",
  empty = false,
) {
  await page.addInitScript(() => {
    const w = window as any;
    w.probe = { starts: 0, spoken: [], cancelled: 0 };
    w.streams = [];
    w.EventSource = class extends EventTarget {
      closed = false;
      constructor(public url: string) {
        super();
        w.streams.push(this);
      }
      close() {
        this.closed = true;
      }
    };
    w.emitProjection = (text: string, final = false, status?: string) => {
      for (const stream of w.streams.filter((s: any) => !s.closed && s.url.includes("/companion/threads/a/events"))) {
        stream.dispatchEvent(new MessageEvent("projection_update", { data: JSON.stringify({
          type: "projection_update", state: { thread_id: "a", items: [
            { text: { run_id: "live-run", body: text, finalized: final } },
            { run_status: { run_id: "live-run", status: status || (final ? "completed" : "running") } },
          ] },
        }) }));
      }
    };
    w.SpeechRecognition = class {
      onstart: any;
      start() {
        w.probe.starts++;
        this.onstart?.();
      }
      stop() {
        w.probe.cancelled++;
      }
      abort() {
        w.probe.cancelled++;
      }
    };
    w.SpeechSynthesisUtterance = class {
      constructor(public text: string) {}
    };
    Object.defineProperty(w, "speechSynthesis", {
      configurable: true,
      value: {
        getVoices: () => [],
        cancel: () => {},
        pause: () => {},
        resume: () => {},
        speak: (utterance: any) => {
          w.probe.spoken.push(utterance.text);
          w.utterance = utterance;
          utterance.onstart?.();
        },
      },
    });
  });
  const sends: unknown[] = [];
  const reads: string[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname;
    let body: any = {
      items: [],
      data: [],
      messages: [],
      threads: [],
      accounts: [],
      providers: [],
      models: [],
    };
    if (path === "/api/auth/status")
      body = { configured: true, authenticated: true };
    if (path === "/api/companion/status")
      body = { configured: true, available: true };
    if (path === "/api/companion/threads")
      body = {
        threads: [
          { thread_id: "a", title: "Replay A" },
          { thread_id: "b", title: "Replay B" },
        ],
      };
    if (/^\/api\/companion\/threads\/(a|b|day-fixture)$/.test(path)) {
      const id = path.split("/").at(-1)!;
      reads.push(id);
      body = {
        messages: empty
          ? []
          : id === "b"
            ? [reply("other", "Other conversation reply.", 1)]
            : [
                reply("old", "Earlier reply must stay silent.", 1),
                reply("latest", "Latest selected reply.", 2),
                reply("empty", "  ", 3),
                {
                  message_id: "user-last",
                  kind: "user",
                  content: "User text must stay silent.",
                  sequence: 4,
                },
              ],
      };
    }
    if (path.endsWith("/messages") && request.method() === "POST") {
      sends.push(request.postDataJSON());
      body = { outcome: "submitted", run_id: "unexpected-send" };
    }
    if (path === "/api/agenda") {
      const date = url.searchParams.get("date");
      body = {
        date,
        timezone: url.searchParams.get("timezone"),
        observedAt: Date.now() / 1000,
        window: { start: `${date}T00:00:00Z`, end: `${date}T23:59:59Z` },
        commitments: [],
        events: [],
        emails: [],
        total: { commitments: 0, events: 0, emails: 0 },
        nextOffset: null,
        partial: false,
        sources: {
          calendar: { state: "not_connected", accounts: [], snapshots: [] },
          email: { state: "not_connected", accounts: [] },
        },
      };
    }
    if (path === "/api/agenda/chat") body = { threadId: "day-fixture" };
    if (path === "/api/agenda/reconciliation")
      body = {
        items: [],
        coverage: {
          enabledAt: 1790000000,
          state: "idle",
          error: null,
          retryable: false,
        },
      };
    await route.fulfill({ json: body });
  });
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto(surface === "Today" ? "/?view=today" : "/");
  if (surface === "Today")
    await page
      .getByRole("navigation", { name: "Today pages" })
      .getByRole("link", { name: "Chat", exact: true })
      .click();
  else {
    await navigate(page, "Companion");
    await chooseConversation(page, "a");
  }
  await expect(page.getByLabel("Message Leam")).toBeVisible();
  return { sends, reads };
}
const replay = (page: Page) =>
  page
    .locator(".voice-status-row")
    .getByRole("button", { name: "Replay last reply", exact: true });

for (const status of ["failed", "interrupted"]) {
test(`older ${status} live partial does not replace a newer saved reply in the voice menu`, async ({ page }) => {
  const f = await setup(page, "Companion");
  // A replayed failed run remains visible after newer canonical history loads.
  await page.evaluate((status) => (window as any).emitProjection("Older failed partial.", false, status), status);
  await expect(page.getByText("Older failed partial.", { exact: true })).toBeVisible();
  await replay(page).click();
  await expect.poll(() => page.evaluate(() => (window as any).probe.spoken)).toEqual(["Latest selected reply."]);
  expect(f.sends).toEqual([]);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(0);
});
}

for (const surface of ["Companion", "Today"] as const) {
  test(`${surface} voice menu replays latest nonempty reply without sending draft or opening microphone`, async ({
    page,
  }) => {
    const f = await setup(page, surface);
    const draft = page.getByLabel("Message Leam");
    await draft.fill("Keep this unsent draft");
    expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
    await replay(page).click();
    await expect
      .poll(() => page.evaluate(() => (window as any).probe.spoken))
      .toEqual(["Latest selected reply."]);
    await expect(draft).toHaveValue("Keep this unsent draft");
    expect(f.sends).toEqual([]);
    expect(await page.evaluate(() => (window as any).probe.starts)).toBe(0);
    expect(
      await page.locator(".voice-status-row").evaluate((row) =>
        Array.from(row.querySelectorAll("button")).every((button) => {
          const box = button.getBoundingClientRect();
          return box.left >= 0 && box.right <= innerWidth;
        }),
      ),
    ).toBe(true);
    await page.evaluate(() => (window as any).utterance.onend?.());
    expect(await page.evaluate(() => (window as any).probe.starts)).toBe(0);
  });
  test(`${surface} voice menu has no replay action without an assistant reply`, async ({
    page,
  }) => {
    const f = await setup(page, surface, true);
    await expect(replay(page)).toHaveCount(0);
    expect(await page.evaluate(() => (window as any).probe.spoken)).toEqual([]);
    expect(f.sends).toEqual([]);
  });
}

test("voice menu switches replay to the selected Companion thread without replaying the previous source", async ({
  page,
}) => {
  const f = await setup(page, "Companion");
  await replay(page).click();
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.spoken))
    .toEqual(["Latest selected reply."]);
  await page
    .getByRole("button", { name: "Dismiss playback", exact: true })
    .click();
  await chooseConversation(page, "b");
  await expect(
    page.getByText("Other conversation reply.", { exact: true }),
  ).toBeVisible();
  const before = f.reads.length;
  await replay(page).click();
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.spoken))
    .toEqual(["Latest selected reply.", "Other conversation reply."]);
  expect(f.reads.slice(before)).not.toContain("a");
  expect(f.sends).toEqual([]);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(0);
});

test("voice menu current live reply overrides older saved history and follows its final projection", async ({ page }) => {
  const f = await setup(page, "Companion");
  await page.evaluate(() => (window as any).emitProjection("First live sentence. More follows"));
  await expect(replay(page)).toBeVisible();
  await replay(page).click();
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.spoken))
    .toEqual(["First live sentence. "]);
  await page.evaluate(() => (window as any).utterance.onend?.());
  await page.evaluate(() =>
    (window as any).emitProjection(
      "First live sentence. More follows here.",
      true,
    ),
  );
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.spoken))
    .toEqual(["First live sentence. ", "More follows here."]);
  expect(f.sends).toEqual([]);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(0);
});

for (const viewport of [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
])
  test(`Today read-aloud exposes usable global playback controls at ${viewport.width}`, async ({
    page,
  }, info) => {
    const state = await setup(page, "Today");
    await page.setViewportSize(viewport);
    await page.getByLabel("Message Leam").fill("Keep this unsent draft");
    await page
      .locator(".companion-messages article")
      .filter({ hasText: "Latest selected reply." })
      .getByRole("button", { name: "Read aloud", exact: true })
      .click();
    await expect
      .poll(() => page.evaluate(() => (window as any).probe.spoken.length))
      .toBe(1);
    const panel = page.getByRole("complementary", { name: "Speech playback" });
    await expect(panel).toBeVisible();
    const geometry = await panel.evaluate((element) => {
      const box = element.getBoundingClientRect();
      return {
        top: box.top,
        bottom: box.bottom,
        left: box.left,
        right: box.right,
        width: innerWidth,
        height: innerHeight,
        controls: [...element.querySelectorAll("button")].map((button) => {
          const rect = button.getBoundingClientRect();
          return {
            name: button.getAttribute("aria-label"),
            visible:
              rect.top >= 0 &&
              rect.bottom <= innerHeight &&
              rect.left >= 0 &&
              rect.right <= innerWidth,
            hit: button.contains(
              document.elementFromPoint(
                rect.x + rect.width / 2,
                rect.y + rect.height / 2,
              ),
            ),
          };
        }),
      };
    });
    await info.attach("playback-geometry", {
      body: JSON.stringify(geometry),
      contentType: "application/json",
    });
    await page.screenshot({
      path: info.outputPath(`today-playback-${viewport.width}.png`),
    });
    expect(geometry.top).toBeGreaterThanOrEqual(0);
    expect(geometry.bottom).toBeLessThanOrEqual(viewport.height);
    expect(geometry.controls.every((value) => value.visible && value.hit)).toBe(
      true,
    );
    await panel
      .getByRole("button", { name: "Pause playback", exact: true })
      .click();
    await expect(
      panel.getByRole("button", { name: "Resume playback", exact: true }),
    ).toBeVisible();
    await panel
      .getByRole("button", { name: "Stop playback", exact: true })
      .click();
    await expect(page.getByLabel("Message Leam")).toHaveValue(
      "Keep this unsent draft",
    );
    expect(state.sends).toEqual([]);
    expect(await page.evaluate(() => (window as any).probe.starts)).toBe(0);
  });

test("leaving Today Chat cancels microphone capture and closes its live streams", async ({
  page,
}) => {
  const state = await setup(page, "Today");
  await page.getByRole("button", { name: "Dictate", exact: true }).click();
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.starts))
    .toBe(1);
  await page
    .getByRole("navigation", { name: "Today pages" })
    .getByRole("link", { name: "Overview", exact: true })
    .click();
  await expect
    .poll(() => page.evaluate(() => (window as any).probe.cancelled))
    .toBeGreaterThan(0);
  expect(
    await page.evaluate(
      () =>
        (window as any).streams.filter(
          (stream: any) =>
            !stream.closed && stream.url.includes("/companion/threads/"),
        ).length,
    ),
  ).toBe(0);
  expect(state.sends).toEqual([]);
  expect(await page.evaluate(() => (window as any).probe.starts)).toBe(1);
});
