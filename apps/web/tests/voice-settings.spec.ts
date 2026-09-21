import { chooseConversation } from "./navigation";
import { test, expect } from "@playwright/test";
const chosen = JSON.stringify(["fixture-fr", "Camille", "fr-FR"]);
async function setup(page: any) {
  await page.addInitScript(() => {
    const w = window as any;
    w.samples = [];
    w.cancels = 0;
    w.inventory = [];
    w.SpeechSynthesisUtterance = class {
      text: string;
      constructor(text: string) {
        this.text = text;
      }
    };
    const synth = new EventTarget() as any;
    synth.getVoices = () => w.inventory;
    synth.cancel = () => {
      w.cancels++;
    };
    synth.speak = (utterance: any) => {
      w.samples.push({
        text: utterance.text,
        voice: utterance.voice?.voiceURI,
        rate: utterance.rate,
        lang: utterance.lang,
      });
      w.utterance = utterance;
      utterance.onstart?.();
    };
    Object.defineProperty(w, "speechSynthesis", {
      value: synth,
      configurable: true,
    });
    w.loadVoices = (missing = false) => {
      w.inventory = [
        {
          voiceURI: "fixture-en",
          name: "Alex",
          lang: "en-GB",
          localService: true,
          default: true,
        },
        ...(missing
          ? []
          : [
              {
                voiceURI: "fixture-fr",
                name: "Camille",
                lang: "fr-FR",
                localService: false,
                default: false,
              },
            ]),
      ];
      synth.dispatchEvent(new Event("voiceschanged"));
    };
  });
  await page.route("**/api/**", async (route: any) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], providers: [], models: [], accounts: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/threads") body = { data: [] };
    if (path === "/api/companion/threads")
      body = { threads: [{ thread_id: "a", title: "Voice fixture" }] };
    if (path === "/api/companion/threads/a")
      body = {
        messages: [
          {
            message_id: "a",
            turn_run_id: "old",
            kind: "assistant",
            status: "finalized",
            content: "Use 2 * 3.",
            sequence: 1,
          },
        ],
      };
    if (path.endsWith("/events")) {
      await route.abort();
      return;
    }
    await route.fulfill({ json: body });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?view=settings");
  await page
    .locator("summary")
    .filter({ hasText: "Voice and playback" })
    .click();
}
test("async device voices and speed persist, preview stops, and Companion read-aloud uses the same preferences", async ({
  page,
}) => {
  await setup(page);
  await expect(page.getByLabel("Speaking voice").locator("option")).toHaveCount(
    1,
  );
  await page.evaluate(() => (window as any).loadVoices());
  await page.getByLabel("Speaking voice").selectOption(chosen);
  const speed = page.getByLabel("Speaking speed", { exact: true });
  await speed.focus();
  for (let i = 0; i < 7; i++) await page.keyboard.press("ArrowRight");
  await expect(speed).toHaveValue("1.35");
  await page
    .getByRole("button", { name: "Preview voice", exact: true })
    .click();
  expect(await page.evaluate(() => (window as any).samples[0])).toMatchObject({
    voice: "fixture-fr",
    rate: 1.35,
    lang: "fr-FR",
  });
  await page.getByRole("button", { name: "Stop voice preview" }).click();
  await page.reload();
  await page
    .locator("summary")
    .filter({ hasText: "Voice and playback" })
    .click();
  await expect(page.getByLabel("Speaking speed", { exact: true })).toHaveValue(
    "1.35",
  );
  await expect(page.getByText(/saved voice is unavailable/)).toBeVisible();
  await page.evaluate(() => (window as any).loadVoices());
  await expect(page.getByText(/saved voice is unavailable/)).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).samples)).toEqual([]);
  await page.getByRole("button", { name: "Companion", exact: true }).click();
  await chooseConversation(page, "a");
  await page.getByRole("button", { name: "Read aloud", exact: true }).click();
  expect(await page.evaluate(() => (window as any).samples[0])).toMatchObject({
    text: "Use 2 * 3.",
    voice: "fixture-fr",
    rate: 1.35,
    lang: "fr-FR",
  });
});
test("missing selected voice falls back and reset restores defaults", async ({
  page,
}) => {
  await setup(page);
  await page.evaluate(() => (window as any).loadVoices());
  await page.getByLabel("Speaking voice").selectOption(chosen);
  await page.evaluate(() => (window as any).loadVoices(true));
  await expect(page.getByText(/saved voice is unavailable/)).toBeVisible();
  await page
    .getByRole("button", { name: "Preview voice", exact: true })
    .click();
  expect(await page.evaluate(() => (window as any).samples[0])).toMatchObject({
    voice: "fixture-en",
    rate: 1,
  });
  await page.getByRole("button", { name: "Reset voice settings" }).click();
  await expect(page.getByLabel("Speaking voice")).toHaveValue("");
  await expect(page.getByRole("button", { name: "Preview voice", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Preview voice", exact: true }).click();
  const before = await page.evaluate(() => (window as any).cancels);
  await page.locator("summary").filter({hasText:"Voice and playback"}).click();
  await expect.poll(() => page.evaluate(() => (window as any).cancels)).toBeGreaterThan(before);
});
test("blocked browser storage keeps selected output usable in this tab", async ({
  page,
}) => {
  await setup(page);
  await page.evaluate(() => {
    (window as any).loadVoices();
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === "leam.voice.output.v1")
        throw new DOMException("Blocked", "QuotaExceededError");
      return original.call(this, key, value);
    };
  });
  await page.getByLabel("Speaking voice").selectOption(chosen);
  await expect(page.getByText(/storage is unavailable/)).toBeVisible();
  await page
    .getByRole("button", { name: "Preview voice", exact: true })
    .click();
  expect(await page.evaluate(() => (window as any).samples[0].voice)).toBe(
    "fixture-fr",
  );
});
