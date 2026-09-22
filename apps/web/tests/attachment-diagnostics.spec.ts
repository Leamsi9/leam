import { test, expect } from "@playwright/test";
import { chooseConversation } from "./navigation";
for (const surface of ["coding", "companion"] as const) test(`Rejected screenshot keeps the exact safe diagnostic in ${surface}`, async ({ page }) => {
  const detail = "Attachment bytes do not match the selected supported format. Selected: image/jpeg; detected signature: JPEG. JPEG end marker is not at the end; the file may be truncated or contain trailing data.";
  let uploads = 0, turns = 0;
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { onopen: any; constructor() { super(); setTimeout(() => this.onopen?.(), 0); } close() {} }; });
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [], threads: [], models: [], providers: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/codex/threads") body = { data: [{ id: "diagnostic", name: "Image diagnostic fixture" }] };
    if (path === "/api/codex/threads/diagnostic") body = { thread: { id: "diagnostic" }, connected: true };
    if (path === "/api/companion/threads") body = { threads: [{ thread_id: "diagnostic", title: "Image diagnostic fixture", created_at: "2026-09-22T01:00:00Z" }] };
    if (path === "/api/companion/threads/diagnostic") body = { messages: [] };
    if (path === "/api/attachments") { uploads++; return route.fulfill({ status: 422, json: { detail } }); }
    if (route.request().method() === "POST" && /\/(turns|messages)$/.test(path)) turns++;
    await route.fulfill({ json: body });
  });
  await page.goto(`/?view=${surface}`);
  await chooseConversation(page, "diagnostic", surface);
  await page.getByLabel("Attach files", { exact: true }).setInputFiles({ name: "Screenshot.jpg", mimeType: "image/jpeg", buffer: Buffer.from("synthetic rejected bytes") });
  await expect(page.getByRole("alert").filter({ hasText: "JPEG end marker" })).toHaveText(detail);
  await expect(page.getByLabel("Attached files")).toHaveCount(0);
  expect(uploads).toBe(1); expect(turns).toBe(0);
});
