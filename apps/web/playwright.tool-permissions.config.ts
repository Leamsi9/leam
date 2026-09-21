import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: "tool-permissions.spec.ts",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46606" },
  reporter: "list",
  outputDir: "/tmp/leam-tool-permissions-browser",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46606 --strictPort",
    url: "http://127.0.0.1:46606",
    reuseExistingServer: false,
  },
});
