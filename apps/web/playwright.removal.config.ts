import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  testMatch: [
    "removal.spec.ts",
    "mobile-forms.spec.ts",
    "draft-revision.spec.ts",
    "today-interactions.spec.ts",
  ],
  workers: 1,
  use: { baseURL: "http://127.0.0.1:5190" },
  reporter: "list",
  webServer: {
    command: "npm run dev -- --port 5190",
    url: "http://127.0.0.1:5190",
    reuseExistingServer: false,
  },
});
