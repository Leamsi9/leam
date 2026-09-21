import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: [
    "companion-delivery.spec.ts",
    "companion-stream.spec.ts",
    "voice-conversation.spec.ts",
  ],
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46612", serviceWorkers: "block" },
  reporter: "list",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46612 --strictPort",
    url: "http://127.0.0.1:46612",
    reuseExistingServer: false,
  },
});
