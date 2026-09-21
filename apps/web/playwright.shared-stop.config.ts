import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: ["shared-stop.spec.ts"],
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46557" },
  webServer: {
    command: "npm run dev -- --port 46557 --strictPort",
    url: "http://127.0.0.1:46557",
    reuseExistingServer: false,
  },
});
