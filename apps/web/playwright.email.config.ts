import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: ["accounts.spec.ts", "email.spec.ts"],
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46619", serviceWorkers: "block" },
  reporter: "list",
  outputDir: "/tmp/leam-email-browser",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46619 --strictPort",
    url: "http://127.0.0.1:46619",
    reuseExistingServer: false,
  },
});
