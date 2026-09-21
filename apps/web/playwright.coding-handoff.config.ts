import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: "coding-handoff.spec.ts",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46617", serviceWorkers: "block" },
  outputDir: "/tmp/leam-handoff-browser",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46617 --strictPort",
    url: "http://127.0.0.1:46617",
    reuseExistingServer: false,
  },
});
