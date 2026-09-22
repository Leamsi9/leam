import { defineConfig } from "@playwright/test";
declare const process: { env: Record<string, string | undefined> };
const baseURL = process.env.LEAM_QA_BASE_URL;
if (!baseURL)
  throw new Error(
    "Set LEAM_QA_BASE_URL to the root-deployed candidate; no local server is started",
  );
export default defineConfig({
  testDir: "tests",
  testMatch: "backlog.spec.ts",
  workers: 1,
  use: { baseURL, serviceWorkers: "block" },
  reporter: "list",
});
