import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: [
    "coding-stream.spec.ts",
    "coding-interactions.spec.ts",
    "shared-coding.spec.ts",
    "voice-coding.spec.ts",
    "session-cache.spec.ts",
  ],
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46558" },
  reporter: "list",
  outputDir: "/tmp/leam-coding-stream-browser",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46558 --strictPort",
    url: "http://127.0.0.1:46558",
    reuseExistingServer: false,
  },
});
