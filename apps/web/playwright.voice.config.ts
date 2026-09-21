import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests",
  testMatch: [
    "voice-interactions.spec.ts",
    "voice-conversation.spec.ts",
    "voice-coding.spec.ts",
    "voice-settings.spec.ts",
    "ticket-chat.spec.ts",
    "mobile-layout.spec.ts",
  ],
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46551" },
  reporter: "list",
  outputDir: "/tmp/leam-voice-conversation-browser",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46551 --strictPort",
    url: "http://127.0.0.1:46551",
    reuseExistingServer: false,
  },
});
