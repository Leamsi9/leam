import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests",
  testMatch: ["event-inputs.spec.ts"],
  workers: 1,
  use: { baseURL: "http://127.0.0.1:46553" },
  reporter: "list",
  outputDir: "/tmp/leam-event-inputs-browser",
  webServer: [
    {
      command: `${process.env.LEAM_TEST_PYTHON || "python3"} ../../tests/support/event_inputs_server.py`,
      url: "http://127.0.0.1:46554/api/health",
      reuseExistingServer: false,
    },
    {
      command: `node --input-type=module -e 'import {createServer} from "vite"; const s=await createServer({server:{host:"127.0.0.1",port:46553,strictPort:true,proxy:{"/api":"http://127.0.0.1:46554"}}});await s.listen();'`,
      url: "http://127.0.0.1:46553",
      reuseExistingServer: false,
    },
  ],
});
