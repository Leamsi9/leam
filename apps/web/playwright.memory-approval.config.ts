import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: ["memory-approval.spec.ts", "proposals.spec.ts"],
  workers: 1,
  use: { serviceWorkers: "block", baseURL: "http://127.0.0.1:46615" },
  outputDir: "/tmp/leam-memory-approval-browser",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46615 --strictPort",
    url: "http://127.0.0.1:46615",
    reuseExistingServer: false,
  },
});
