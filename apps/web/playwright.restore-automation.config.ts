import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: "restore-automation.spec.ts",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46609", serviceWorkers: "block" },
  reporter: "list",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46609 --strictPort",
    url: "http://127.0.0.1:46609",
    reuseExistingServer: false,
  },
});
