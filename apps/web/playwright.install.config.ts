import { defineConfig } from "@playwright/test";
export default defineConfig({ testDir: "tests", testMatch: "install-controls.spec.ts", workers: 1,
use: { baseURL: "http://127.0.0.1:46889", serviceWorkers: "block" },
webServer: { command: "node_modules/.bin/vite --host 127.0.0.1 --port 46889 --strictPort", url: "http://127.0.0.1:46889", reuseExistingServer: false } });
