import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: "attachments.spec.ts",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46637" },
  reporter: "list",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46637 --strictPort",
    url: "http://127.0.0.1:46637",
    reuseExistingServer: false,
  },
});
