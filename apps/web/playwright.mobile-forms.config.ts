import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: [
    "today-interactions.spec.ts",
    "mobile-forms.spec.ts",
    "draft-revision.spec.ts",
  ],
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46552" },
  reporter: "list",
  outputDir: "/tmp/leam-mobile-forms-tests",
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 46552 --strictPort",
    url: "http://127.0.0.1:46552",
    reuseExistingServer: false,
  },
});
