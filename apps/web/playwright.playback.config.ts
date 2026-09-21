import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: [
    "streaming-playback.spec.ts",
    "voice-interactions.spec.ts",
    "voice-conversation.spec.ts",
    "voice-coding.spec.ts",
    "voice-settings.spec.ts",
    "ticket-chat.spec.ts",
  ],
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46627", serviceWorkers: "block" },
  reporter: "list",
  outputDir: "/tmp/leam-streaming-playback-tests",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46627 --strictPort",
    url: "http://127.0.0.1:46627",
    reuseExistingServer: false,
  },
});
