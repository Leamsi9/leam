import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: [
    "ticket-followup.spec.ts",
    "ticket-chat.spec.ts",
    "ticket-history-replay.spec.ts",
  ],
  workers: 1,
  projects: [
    { name: "mobile", use: { viewport: { width: 390, height: 844 } } },
    { name: "desktop", use: { viewport: { width: 1440, height: 1000 } } },
  ],
  use: { baseURL: "http://127.0.0.1:46876" },
  reporter: "list",
  outputDir: "/tmp/leam-ticket-followup-browser",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46876 --strictPort",
    url: "http://127.0.0.1:46876",
    reuseExistingServer: false,
  },
});
