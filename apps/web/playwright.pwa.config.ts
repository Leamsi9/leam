import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: ["pwa-shell.spec.ts"],
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46559", serviceWorkers: "allow" },
  webServer: {
    command:
      "npm run build -- --outDir ../../.artifacts/pwa-test --emptyOutDir && node tests/support/pwa-server.mjs",
    url: "http://127.0.0.1:46559",
    reuseExistingServer: false,
  },
  timeout: 15000,
});
