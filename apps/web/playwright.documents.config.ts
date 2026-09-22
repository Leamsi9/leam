import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
export default defineConfig({
  testDir: "tests",
  testMatch: "document-links.spec.ts",
  workers: 1,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:47794", serviceWorkers: "block" },
  webServer: {
    command:
      "node_modules/.bin/vite --host 127.0.0.1 --port 47794 --strictPort",
    cwd: fileURLToPath(new URL(".", import.meta.url)),
    url: "http://127.0.0.1:47794",
    reuseExistingServer: false,
  },
});
