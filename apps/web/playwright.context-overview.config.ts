import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: ["context-overview.spec.ts"],
  workers: 1,
  use: { serviceWorkers: "block", baseURL: "http://127.0.0.1:46605" },
  outputDir: "/tmp/leam-context-overview-browser",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46605 --strictPort",
    url: "http://127.0.0.1:46605",
    reuseExistingServer: false,
  },
});
