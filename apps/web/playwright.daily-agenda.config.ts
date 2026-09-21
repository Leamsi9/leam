import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: "daily-agenda.spec.ts",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46643", serviceWorkers: "block" },
  reporter: "list",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46643 --strictPort",
    url: "http://127.0.0.1:46643",
    reuseExistingServer: false,
  },
});
