import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

export default defineConfig({
  testDir: "tests",
  testMatch: "daily-agenda.spec.ts",
  grep: /Mobile explicit procedure|Mobile catalog separates/,
  workers: 1,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:47793", serviceWorkers: "block" },
  webServer: {
    command: "node_modules/.bin/vite --host 127.0.0.1 --port 47793 --strictPort",
    cwd: fileURLToPath(new URL(".", import.meta.url)),
    url: "http://127.0.0.1:47793",
    reuseExistingServer: false,
  },
});
