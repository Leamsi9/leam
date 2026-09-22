import { chooseConversation } from "./navigation";
import { test, expect } from "@playwright/test";

async function setup(page: any, reply = "A spoken reply") {
  await page.addInitScript(() => {
    const w = window as any;
    w.voiceProbe = { starts: 0, aborts: 0, spoken: [], cancels: 0 };
    w.SpeechRecognition = class {
      onstart: any;
      onresult: any;
      onerror: any;
      onend: any;
      lang = "";
      continuous = false;
      interimResults = false;
      constructor() {
        w.capture = this;
      }
      start() {
        w.voiceProbe.starts++;
        w.voiceProbe.options = {
          lang: this.lang,
          continuous: this.continuous,
          interimResults: this.interimResults,
        };
        if (!w.voiceProbe.deferStart) this.onstart?.();
      }
      stop() {
        if (!w.voiceProbe.deferEnd) this.onend?.();
      }
      abort() {
        w.voiceProbe.aborts++;
      }
    };
    w.emitTranscript = (text: string, final: boolean) =>
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
          w.voiceProbe.cancels++;
        },
        speak: (utterance: any) => {
          w.voiceProbe.spoken.push(utterance.text);
          w.utterance = utterance;
          utterance.onstart?.();
        },
      },
    });
  });
  let sends = 0;
  await page.route("**/api/**", async (route: any) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/threads") body = { data: [] };
    if (path === "/api/companion/threads")
      body = {
        threads: [
          { thread_id: "a", title: "Voice fixture" },
          { thread_id: "b", title: "Other fixture" },
        ],
      };
    if (/\/companion\/threads\/[ab]$/.test(path))
      body = {
        messages: [
          {
            message_id: "reply",
            kind: "assistant",
            status: "finalized",
            content: reply,
            sequence: 1,
            turn_run_id: "older-run",
          },
        ],
      };
    if (path.endsWith("/messages")) {
      sends++;
      body = {
        outcome: "submitted",
        run_id: "19e9c46c-a47b-4e01-8cb4-f4c5c4c26c63",
      };
    }
    if (path === "/api/proposals") body = { items: [] };
    if (path === "/api/backups") body = { items: [] };
    if (path.endsWith("/events")) {
      await route.abort();
      return;
    }
    await route.fulfill({ json: body });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Companion", exact: true }).click();
  await chooseConversation(page, "a");
  return () => sends;
}

test("dictation revises interim text and commits once to editable draft without sending", async ({
  page,
}) => {
  const sends = await setup(page);
  await page.getByLabel("Message Leam").fill("Existing draft");
  await page.getByRole("button", { name: "Dictate", exact: true }).click();
  await page.evaluate(() => (window as any).emitTranscript("partial", false));
  await page.evaluate(() =>
    (window as any).emitTranscript("final words", true),
  );
  await expect(page.getByLabel("Provisional dictation")).toHaveText(
    "final words",
  );
  await page.getByRole("button", { name: "Finish dictation" }).click();
  await expect(page.getByLabel("Message Leam")).toHaveValue(
    "Existing draft final words",
  );
  expect(sends()).toBe(0);
  const options = await page.evaluate(() => (window as any).voiceProbe.options);
  expect(options.continuous).toBe(false);
  expect(options.interimResults).toBe(true);
  await page.getByRole("button", { name: "Send to Leam" }).click();
  await expect.poll(sends).toBe(1);
  await expect(
    page.getByText("Review the text, then Send.", { exact: true }),
  ).toHaveCount(0);
});

test("cancel, editing, thread switch and denied permission preserve text without sending", async ({
  page,
}) => {
  const sends = await setup(page);
  const draft = page.getByLabel("Message Leam");
  await draft.fill("Keep this");
  await page.getByRole("button", { name: "Dictate", exact: true }).click();
  await page.evaluate(() => (window as any).emitTranscript("discard", true));
  await page.getByRole("button", { name: "Cancel dictation" }).click();
  await expect(draft).toHaveValue("Keep this");
  await page.getByRole("button", { name: "Dictate", exact: true }).click();
  await draft.fill("User edit wins");
  await page.evaluate(() => {
    (window as any).emitTranscript("late", true);
    (window as any).capture.onend?.();
  });
  await expect(draft).toHaveValue("User edit wins");
  await page.getByRole("button", { name: "Dictate", exact: true }).click();
  await chooseConversation(page, "b");
  await page.evaluate(() => {
    (window as any).emitTranscript("wrong thread", true);
    (window as any).capture.onend?.();
  });
  await expect(draft).toHaveValue("");
  await page.getByRole("button", { name: "Dictate", exact: true }).click();
  await page.evaluate(() =>
    (window as any).capture.onerror?.({ error: "not-allowed" }),
  );
  await expect(
    page.getByText(/Microphone or speech permission was denied/),
  ).toBeVisible();
  expect(sends()).toBe(0);
});

test("read-aloud is explicit, cancellable and continues on navigation", async ({
  page,
}) => {
  await setup(page);
  expect(
    await page.evaluate(() => (window as any).voiceProbe.spoken.length),
  ).toBe(0);
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  await expect(
    page.locator("#companion-message-reply").getByRole("button", { name: "Stop speaking", exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => (window as any).voiceProbe.spoken)).toEqual([
    "A spoken reply",
  ]);
  await page.locator("#companion-message-reply").getByRole("button", { name: "Stop speaking", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Read aloud", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  const before = await page.evaluate(() => (window as any).voiceProbe.cancels);
  await chooseConversation(page, "b");
  await expect(
    page.getByRole("complementary", { name: "Speech playback" }),
  ).toBeVisible();
  expect(await page.evaluate(() => (window as any).voiceProbe.cancels)).toBe(
    before,
  );
  await page.getByRole("button", { name: "Stop playback" }).click();
  expect(
    await page.evaluate(() => (window as any).voiceProbe.cancels),
  ).toBeGreaterThan(before);
});

test("late microphone start cannot undo finish; timeout retains final text for review", async ({
  page,
}) => {
  const sends = await setup(page);
  await page.clock.install();
  await page.evaluate(() => {
    (window as any).voiceProbe.deferStart = true;
    (window as any).voiceProbe.deferEnd = true;
  });
  await page.getByLabel("Message Leam").fill("Typed");
  await page.getByRole("button", { name: "Dictate", exact: true }).click();
  await page.evaluate(() => (window as any).emitTranscript("recognized", true));
  await page.getByRole("button", { name: "Finish dictation" }).click();
  await page.evaluate(() => (window as any).capture.onstart?.());
  await expect(
    page.getByText("Finishing dictation…", { exact: true }),
  ).toBeVisible();
  await page.clock.fastForward(5100);
  await expect(page.getByLabel("Message Leam")).toHaveValue("Typed recognized");
  await expect(
    page.getByRole("button", { name: "Dictate", exact: true }),
  ).toBeVisible();
  expect(sends()).toBe(0);
});

test("read-aloud preserves literal mathematical and identifier punctuation", async ({
  page,
}) => {
  await setup(page, "Use 2 * 3 = 6 and the account_id field.");
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  expect(
    await page.evaluate(() => (window as any).voiceProbe.spoken.join("")),
  ).toBe("Use 2 * 3 = 6 and the account_id field.");
});
