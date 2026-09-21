import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: ["latest-exchange.spec.ts"],
  workers: 1,
  use: { baseURL: process.env.LEAM_TEST_URL || "http://127.0.0.1:46561" },
  reporter: "list",
  outputDir: "/tmp/leam-latest-exchange-browser",
  ...(!process.env.LEAM_TEST_URL
    ? {
        webServer: {
          command:
            "node_modules/.bin/vite --host 127.0.0.1 --port 46561 --strictPort",
          url: "http://127.0.0.1:46561",
          reuseExistingServer: false,
        },
      }
    : {}),
});
