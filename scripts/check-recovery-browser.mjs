// Run explicitly against a separate recovery candidate, never the main app.
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { readFile } from "node:fs/promises";
const require = createRequire(resolve("apps/web/package.json"));
const { chromium } = require("@playwright/test");
const address = process.env.LEAM_RECOVERY_TEST_URL;
const directory = process.env.LEAM_RECOVERY_TEST_DIR;
const password = process.env.LEAM_RECOVERY_TEST_PASSWORD;
if (!address || !directory || !password) throw new Error("Set LEAM_RECOVERY_TEST_URL, LEAM_RECOVERY_TEST_DIR and LEAM_RECOVERY_TEST_PASSWORD for an isolated test candidate");
const browser = await chromium.launch();
try {
  for (const width of [390, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    const page = await context.newPage();
    await page.goto(address);
    await page.getByLabel("Recovery password", { exact: true }).waitFor();
    if (await page.getByLabel("Pairing code").isVisible()) {
      await page.getByLabel("Pairing code").fill((await readFile(resolve(directory, "bootstrap-token"), "utf8")).trim());
    }
    await page.getByLabel("Recovery password", { exact: true }).fill(password);
    await page.getByRole("button", { name: /^(Pair recovery access|Sign in to recovery)$/ }).click();
    await page.getByRole("heading", { name: "Candidate services" }).waitFor();
    if (await page.getByRole("button", { name: /^Restart / }).count() !== 3) throw new Error("Expected exactly three candidate restart controls");
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error("Horizontal overflow");
    await page.getByRole("button", { name: "Refresh status", exact: true }).click();
    await page.getByText(/Checked /).waitFor();
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await page.getByLabel("Recovery password", { exact: true }).waitFor();
    await context.close();
    console.log(`Recovery pairing/login, status, controls and logout passed at ${width}px; no service was mutated.`);
  }
} finally { await browser.close(); }
