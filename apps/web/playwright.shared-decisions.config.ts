import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: ["shared-decisions.spec.ts"],
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46558" },
  outputDir: "/tmp/leam-shared-decisions-browser",
  webServer: {
    command: "npm run dev -- --port 46558 --strictPort",
    url: "http://127.0.0.1:46558",
    reuseExistingServer: false,
  },
});
