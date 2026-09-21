import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: "item-chat.spec.ts",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46627" },
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46627 --strictPort",
    url: "http://127.0.0.1:46627",
    reuseExistingServer: false,
  },
});
