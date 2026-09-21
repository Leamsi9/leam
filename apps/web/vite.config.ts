import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { pwaShell } from "./pwa-build";
export default defineConfig({
  plugins: [react(), pwaShell()],
  server: { proxy: { "/api": "http://127.0.0.1:46400" } },
});
