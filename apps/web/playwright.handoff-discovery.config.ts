import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: ["handoff-discovery.spec.ts", "conversation-list.spec.ts"],
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46631", serviceWorkers: "block" },
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46631 --strictPort",
    url: "http://127.0.0.1:46631",
    reuseExistingServer: false,
  },
});
