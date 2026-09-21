import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: "mobile-layout.spec.ts",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46553" },
  reporter: "list",
  outputDir: "/tmp/leam-mobile-layout",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46553 --strictPort",
    url: "http://127.0.0.1:46553",
    reuseExistingServer: false,
  },
});
