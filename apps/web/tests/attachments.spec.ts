import { test, expect } from "@playwright/test";
import { chooseConversation, navigate } from "./navigation";

// These fixtures exercise the deployed browser caller and API request contract.
// API responses and file bytes are synthetic: archive parsing and model delivery
// require separate backend and live acceptance evidence.
const officeFormats = [
  ["docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
  ["xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
  ["pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"],
] as const;

for (const width of [390, 1440])
  for (const surface of ["coding", "companion"] as const)
    test(`Office browser fixture uploads with MIME fallback and sends exact IDs in ${surface} at ${width}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 850 });
      await page.addInitScript(() => {
        (window as any).EventSource = class extends EventTarget {
          onopen: any;
          constructor() {
            super();
            setTimeout(() => this.onopen?.(), 0);
          }
          close() {}
        };
      });
      const files = officeFormats.flatMap(([extension, mimeType], index) =>
        ["", "application/octet-stream"].map((browserMime, variant) => ({
          id: `44444444-4444-4444-8444-${String(index * 2 + variant + 1).padStart(12, "0")}`,
          filename: `office-${variant}.${variant ? extension.toUpperCase() : extension}`,
          mimeType,
          browserMime,
          sizeBytes: 24,
          sha256: "d".repeat(64),
          state: "uploaded",
        })),
      );
      const uploads: string[] = [];
      const sent: any[] = [];
      await page.route("**/api/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const path = url.pathname;
        let body: any = { items: [], data: [], threads: [], models: [], providers: [] };
        if (path === "/api/auth/status") body = { authenticated: true };
        if (path === "/api/codex/threads")
          body = { data: [{ id: "office", name: "Office browser fixture" }] };
        if (path === "/api/codex/threads/office")
          body = { thread: { id: "office" }, connected: true };
        if (path === "/api/companion/threads")
          body = { threads: [{ thread_id: "office", title: "Office browser fixture", created_at: "2026-09-21T01:00:00Z" }] };
        if (path === "/api/companion/threads/office") body = { messages: [] };
        if (path.includes("/submissions/")) body = { state: "notSubmitted" };
        if (path === "/api/attachments" && request.method() === "POST") {
          const filename = url.searchParams.get("filename");
          const file = files.find((row) => row.filename === filename);
          expect(file, "upload identifies the selected file").toBeDefined();
          expect(request.headers()["content-type"]).toBe(file!.mimeType);
          expect(request.postDataBuffer()?.toString()).toBe(`Browser fixture: ${filename}`);
          uploads.push(filename!);
          const { browserMime: _, ...attachment } = file!;
          body = attachment;
        }
        if (
          request.method() === "POST" &&
          (path === "/api/codex/threads/office/turns" ||
            path === "/api/companion/threads/office/messages")
        ) {
          sent.push(request.postDataJSON());
          body = surface === "coding"
            ? { turn: { id: "office-run", status: "inProgress" } }
            : { outcome: "deferred_busy", thread_id: "office", accepted_message_ref: "msg:office-fixture", active_run_id: "44444444-4444-4444-8444-444444444444", status: "Running" };
        }
        await route.fulfill({ json: body });
      });
      await page.goto(`/?view=${surface}`);
      await chooseConversation(page, "office", surface);
      const picker = page.getByLabel("Attach files", { exact: true });
      const accepted = (await picker.getAttribute("accept"))?.split(",");
      for (const [extension, mime] of officeFormats) {
        expect(accepted).toContain(`.${extension}`);
        expect(accepted).toContain(mime);
      }
      for (const extension of ["png", "jpg", "jpeg", "webp", "pdf", "txt", "md", "csv", "json"])
        expect(accepted).toContain(`.${extension}`);
      await picker.setInputFiles(files.map((file) => ({
        name: file.filename,
        mimeType: file.browserMime,
        buffer: Buffer.from(`Browser fixture: ${file.filename}`),
      })));
      for (const file of files)
        await expect(page.getByLabel("Attached files")).toContainText(file.filename);
      expect(uploads).toEqual(files.map((file) => file.filename));
      expect(sent).toHaveLength(0);
      await page.getByRole("textbox", { name: surface === "coding" ? "Message Codex" : "Message Leam", exact: true }).fill("Inspect these Office files");
      await page.getByRole("button", { name: surface === "coding" ? "Send message" : "Send to Leam", exact: true }).click();
      await expect.poll(() => sent.length).toBe(1);
      expect(sent[0]).toMatchObject({
        text: "Inspect these Office files",
        attachmentIds: files.map((file) => file.id),
      });
      await expect(page.getByLabel("Attached files")).toHaveCount(0);
    });

for (const width of [390, 1440])
  test(`Coding uploads privately, restores files across navigation and sends exact input ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 850 });
    await page.addInitScript(() => {
      (window as any).EventSource = class extends EventTarget {
        onopen: any;
        onmessage: any;
        constructor() {
          super();
          setTimeout(() => this.onopen?.(), 0);
        }
        close() {}
      };
    });
    let uploads = 0;
    const sent: any[] = [];
    let accepted = false;
    const file = {
      id: "11111111-1111-4111-8111-111111111111",
      filename: "note.txt",
      mimeType: "text/plain",
      sizeBytes: 17,
      sha256: "a".repeat(64),
      state: "uploaded",
    };
    await page.route("**/api/**", async (route) => {
      const p = new URL(route.request().url()).pathname;
      let body: any = {
        items: [],
        data: [],
        threads: [],
        models: [],
        providers: [],
      };
      if (p === "/api/auth/status") body = { authenticated: true };
      if (p === "/api/codex/threads")
        body = { data: [{ id: "t", name: "Files fixture" }] };
      if (p === "/api/codex/threads/t")
        body = { thread: { id: "t" }, connected: true };
      if (p === "/api/attachments" && route.request().method() === "POST") {
        uploads++;
        expect(route.request().postDataBuffer()?.toString()).toBe(
          "Synthetic content",
        );
        body = file;
      }
      if (p.startsWith("/api/codex/submissions/"))
        body = { state: "notSubmitted" };
      if (
        p === "/api/codex/threads/t/turns" &&
        route.request().method() === "POST"
      ) {
        sent.push(route.request().postDataJSON());
        accepted = true;
        body = { turn: { id: "run", status: "inProgress" } };
      }
      if (
        p === "/api/codex/threads/t/turns" &&
        route.request().method() === "GET"
      )
        body = {
          data: accepted
            ? [
                {
                  id: "run",
                  status: "completed",
                  leamAttachments: [file],
                  items: [
                    {
                      id: "user",
                      type: "userMessage",
                      content: [{ type: "text", text: "  Exact question\n" }],
                    },
                  ],
                },
              ]
            : [],
        };
      await route.fulfill({ json: body });
    });
    await page.goto("/?view=coding");
    await chooseConversation(page, "t", "coding");
    await page.getByLabel("Attach files", { exact: true }).setInputFiles({
      name: "note.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("Synthetic content"),
    });
    await expect(page.getByLabel("Attached files")).toContainText("note.txt");
    await page.getByLabel("Message Codex").fill("  Exact question\n");
    await navigate(page, "Today");
    await navigate(page, "Coding");
    await expect(page.getByLabel("Attached files")).toContainText("note.txt");
    expect(uploads).toBe(1);
    await page
      .getByRole("button", { name: "Send message", exact: true })
      .click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0].text).toBe("  Exact question\n");
    expect(sent[0].attachmentIds).toEqual([file.id]);
    await expect(page.getByLabel("Attached files")).toHaveCount(0);
    await expect(page.getByLabel("Message attachments")).toContainText(
      "note.txt",
    );
  });

test("Uncertain attachment delivery preserves original attachment IDs and never sends automatically again", async ({
  page,
}) => {
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      onopen: any;
      onmessage: any;
      constructor() {
        super();
        setTimeout(() => this.onopen?.(), 0);
      }
      close() {}
    };
  });
  let sends = 0;
  const id = "22222222-2222-4222-8222-222222222222";
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [], threads: [] };
    let status = 200;
    if (p === "/api/auth/status") body = { authenticated: true };
    if (p === "/api/codex/threads")
      body = { data: [{ id: "t", name: "Files fixture" }] };
    if (p === "/api/codex/threads/t")
      body = { thread: { id: "t" }, connected: true };
    if (p === "/api/attachments")
      body = {
        id,
        filename: "note.txt",
        mimeType: "text/plain",
        sizeBytes: 4,
        sha256: "b".repeat(64),
        state: "uploaded",
      };
    if (p.startsWith("/api/codex/submissions/"))
      body = { state: sends ? "pending" : "notSubmitted" };
    if (p.includes("/reconcile")) body = { state: "pending" };
    if (
      p === "/api/codex/threads/t/turns" &&
      route.request().method() === "POST"
    ) {
      sends++;
      expect(route.request().postDataJSON().attachmentIds).toEqual([id]);
      status = 502;
      body = { detail: "Delivery outcome unknown" };
    }
    await route.fulfill({ status, json: body });
  });
  await page.goto("/?view=coding");
  await chooseConversation(page, "t", "coding");
  await page.getByLabel("Attach files", { exact: true }).setInputFiles({
    name: "note.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("note"),
  });
  await expect(page.getByLabel("Attached files")).toContainText("note.txt");
  await page.getByLabel("Message Codex").fill("Please inspect");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect.poll(() => sends).toBe(1);
  await expect(
    page.getByRole("button", { name: "Remove note.txt" }),
  ).toBeDisabled();
  await navigate(page, "Today");
  await navigate(page, "Coding");
  await expect(page.getByLabel("Attached files")).toContainText("note.txt");
  expect(sends).toBe(1);
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(
    page.getByText(/previous submission may already be running/),
  ).toBeVisible();
  expect(sends).toBe(1);
});

test("Held upload from old conversation cannot release the new conversation upload guard", async ({
  page,
}) => {
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      onopen: any;
      onmessage: any;
      constructor() {
        super();
        setTimeout(() => this.onopen?.(), 0);
      }
      close() {}
    };
  });
  let releaseA!: () => void, releaseB!: () => void, completeA!: () => void;
  const completedA = new Promise<void>((resolve) => (completeA = resolve));
  let uploads = 0;
  const a = new Promise<void>((r) => (releaseA = r)),
    b = new Promise<void>((r) => (releaseB = r));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url()),
      p = url.pathname;
    let body: any = { items: [], data: [], threads: [] };
    if (p === "/api/auth/status") body = { authenticated: true };
    if (p === "/api/codex/threads")
      body = { data: ["a", "b"].map((id) => ({ id, name: "Thread " + id })) };
    if (p === "/api/codex/threads/a" || p === "/api/codex/threads/b")
      body = { thread: { id: p.split("/").at(-1) }, connected: true };
    if (p === "/api/attachments") {
      uploads++;
      const filename = url.searchParams.get("filename");
      await (filename === "a.txt" ? a : b);
      body = {
        id: filename === "a.txt" ? "a" : "b",
        filename,
        mimeType: "text/plain",
        sizeBytes: 1,
        sha256: "a".repeat(64),
        state: "uploaded",
      };
    }
    await route.fulfill({ json: body }).catch(() => {});
    if (url.searchParams.get("filename") === "a.txt") completeA();
  });
  await page.goto("/?view=coding");
  await chooseConversation(page, "a", "coding");
  await page.getByLabel("Attach files", { exact: true }).setInputFiles({
    name: "a.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("a"),
  });
  await expect.poll(() => uploads).toBe(1);
  await chooseConversation(page, "b", "coding");
  await expect(page.getByLabel("Attach files", { exact: true })).toHaveCount(1);
  await page.getByLabel("Message Codex").fill("B message");
  await page.getByLabel("Attach files", { exact: true }).setInputFiles({
    name: "b.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("b"),
  });
  await expect.poll(() => uploads).toBe(2);
  releaseA();
  await completedA;
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeDisabled();
  releaseB();
  await expect(page.getByLabel("Attached files")).toContainText("b.txt");
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeEnabled();
  await expect(page.getByLabel("Attached files")).not.toContainText("a.txt");
});

test("Mobile ticket attachment survives collapse and uses saved exact request IDs", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 850 });
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      onopen: any;
      onmessage: any;
      close() {}
    };
  });
  const sent: any[] = [];
  let connected = false;
  const file = {
    id: "33333333-3333-4333-8333-333333333333",
    filename: "ticket.txt",
    mimeType: "text/plain",
    sizeBytes: 7,
    sha256: "c".repeat(64),
    state: "uploaded",
  };
  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [], threads: [] };
    if (p === "/api/auth/status") body = { authenticated: true };
    if (p === "/api/updates")
      body = {
        items: [
          {
            id: "ticket",
            title: "Attachment fixture",
            summary: "Fixture",
            stage: "UAT",
            revision: 1,
            sequence: 1,
            deploymentId: "fixture",
            deployedAt: "2026-09-20T20:00:00Z",
            qa: { state: "passed" },
            uat: { state: "pending" },
          },
        ],
        unreadCount: 0,
      };
    if (p === "/api/updates/ticket/chat") {
      if (route.request().method() === "POST") connected = true;
      body = {
        state: connected ? "ready" : "unconnected",
        threadId: connected ? "ticket-thread" : undefined,
        connected,
      };
    }
    if (p === "/api/attachments") body = file;
    if (p.startsWith("/api/codex/submissions/"))
      body = { state: "notSubmitted" };
    if (
      p === "/api/codex/threads/ticket-thread/turns" &&
      route.request().method() === "POST"
    ) {
      sent.push(route.request().postDataJSON());
      body = { turn: { id: "ticket-run" } };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/?view=updates");
  const card = page.getByRole("article", {
    name: "Attachment fixture",
    exact: true,
  });
  await card.getByText("Chat about this update", { exact: true }).click();
  await card.getByLabel("Attach files", { exact: true }).setInputFiles({
    name: "ticket.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Ticket!"),
  });
  await expect(card.getByLabel("Attached files")).toContainText("ticket.txt");
  await card
    .getByLabel("Message about this update")
    .fill("  Ticket exact text\n");
  await card.getByText("Chat about this update", { exact: true }).click();
  await card.getByText("Chat about this update", { exact: true }).click();
  await expect(card.getByLabel("Attached files")).toContainText("ticket.txt");
  await card
    .getByRole("button", { name: "Send to Codex", exact: true })
    .click();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0].text).toBe("  Ticket exact text\n");
  expect(sent[0].attachmentIds).toEqual([file.id]);
});
