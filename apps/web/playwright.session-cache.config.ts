import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: [
    "session-cache.spec.ts",
    "coding-interactions.spec.ts",
    "companion-interactions.spec.ts",
    "companion-stream.spec.ts",
    "shared-coding.spec.ts",
  ],
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46603" },
  reporter: "list",
  outputDir: "/tmp/leam-session-cache-browser",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46603 --strictPort",
    url: "http://127.0.0.1:46603",
    reuseExistingServer: false,
  },
});
