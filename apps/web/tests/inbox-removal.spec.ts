import { test, expect } from "@playwright/test";

for (const width of [390, 1280])
  test(`Inbox single and selected removal stays local across refresh at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    const note = {
      id: "11111111-1111-4111-8111-111111111111",
      sequence: 1,
      subject: "Leam note",
      preview: "Review",
      body: "Private fixture",
      origin: "user",
      createdAt: 1790000010,
      unread: true,
      links: [],
    };
    const notes = [note],
      removed = new Set<string>(),
      calls: any[] = [];
    const mail = ["a", "b"].map((letter, index) => ({
      id: "same-provider-id",
      key: "email:" + letter.repeat(64),
      accountId: index ? "work" : "personal",
      subject: index ? "Work reply" : "Personal reply",
      from: "Sender",
      receivedAt: "2026-09-22T10:00:00Z",
      snippet: "Please reply",
      actionability: { state: "action", action: "Reply" },
      triage: { revision: 0, disposition: "none" },
    }));
    const status = () => ({
      total: notes.length,
      unreadCount: notes.filter((n) => n.unread).length,
      throughSequence: 1,
    });
    await page.addInitScript(() => {
      (window as any).EventSource = class extends EventTarget {
        close() {}
      };
    });
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: any = {
        items: [],
        data: [],
        threads: [],
        messages: [],
        providers: [],
      };
      if (path === "/api/auth/status") body = { authenticated: true };
      if (path === "/api/inbox/status") body = status();
      if (path === "/api/inbox")
        body = { items: notes, ...status(), nextCursor: null };
      if (path === "/api/inbox/" + note.id) body = note;
      if (path === "/api/inbox-mail/read")
        body = {
          readKeys: [],
          removedKeys: [...removed].filter((key) => key.startsWith("email:")),
          scope: "leam_only",
        };
      if (path === "/api/inbox/remove") {
        const request = route.request().postDataJSON();
        calls.push({ path, request });
        expect(request.confirmed).toBe(true);
        request.items.forEach((item: any) => {
          const key = item.source === "leam" ? "leam:" + item.id : item.id;
          removed.add(key);
          if (item.source === "leam") {
            const index = notes.findIndex((n) => n.id === item.id);
            if (index >= 0) notes.splice(index, 1);
          }
        });
        body = {
          requestId: request.requestId,
          removedKeys: [...removed],
          notes: status(),
          providerChanged: false,
          scope: "leam_inbox_only",
        };
      } else if (route.request().method() !== "GET") calls.push({ path });
      if (path === "/api/agenda")
        body = {
          date: "2026-09-22",
          timezone: "UTC",
          commitments: [],
          events: [],
          emails: mail,
          nextOffset: null,
          total: { commitments: 0, events: 0, emails: 2 },
          sources: {
            calendar: { state: "not_connected", accounts: [], snapshots: [] },
            email: {
              state: "ready",
              accounts: [
                {
                  accountId: "personal",
                  identity: "personal@example.test",
                  granted: true,
                  state: "ready",
                },
                {
                  accountId: "work",
                  identity: "work@example.test",
                  granted: true,
                  state: "ready",
                },
              ],
              classification: {
                state: "ready",
                counts: { action: 2, ignore: 0, review: 0, pending: 0 },
              },
            },
          },
        };
      await route.fulfill({ json: body });
    });
    await page.goto("/?view=today#today/inbox/2026-09-22");
    const inbox = page.getByRole("region", { name: "Inbox", exact: true });
    await expect(inbox.locator(".inbox-card")).toHaveCount(3);
    await inbox.getByRole("heading", { name: "Personal reply" }).click();
    await inbox
      .getByRole("button", {
        name: "Remove Personal reply from Inbox",
        exact: true,
      })
      .click();
    let dialog = page.getByRole("dialog", {
      name: "Remove item from Inbox?",
      exact: true,
    });
    await expect(dialog).toContainText("Gmail messages are not deleted");
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    expect(calls).toHaveLength(0);
    await inbox
      .getByRole("button", {
        name: "Remove Personal reply from Inbox",
        exact: true,
      })
      .click();
    await dialog
      .getByRole("button", { name: "Remove from Leam Inbox", exact: true })
      .click();
    await expect(inbox.locator(".inbox-card")).toHaveCount(2);
    await inbox
      .getByRole("button", { name: "Refresh inbox", exact: true })
      .click();
    await expect(
      inbox.getByRole("heading", { name: "Personal reply" }),
    ).toHaveCount(0);
    await expect(
      inbox.getByRole("heading", { name: "Work reply" }),
    ).toBeVisible();
    await inbox
      .getByRole("button", { name: "Select items", exact: true })
      .click();
    await inbox
      .getByRole("button", { name: "Select visible", exact: true })
      .click();
    await expect(inbox.getByText("2 selected", { exact: true })).toBeVisible();
    await inbox
      .getByRole("button", { name: "Clear selection", exact: true })
      .click();
    await expect(
      inbox.getByRole("button", { name: "Remove selected", exact: true }),
    ).toBeDisabled();
    await inbox
      .getByRole("button", { name: "Select visible", exact: true })
      .click();
    await inbox
      .getByRole("button", { name: "Remove selected", exact: true })
      .click();
    dialog = page.getByRole("dialog", {
      name: "Remove 2 items from Inbox?",
      exact: true,
    });
    await expect(dialog.locator("li")).toHaveCount(2);
    await dialog
      .getByRole("button", { name: "Remove from Leam Inbox", exact: true })
      .click();
    await expect(inbox.locator(".inbox-card")).toHaveCount(0);
    expect(calls).toHaveLength(2);
    expect(calls.every((call) => call.path === "/api/inbox/remove")).toBe(true);
    await page.reload();
    await expect(inbox.locator(".inbox-card")).toHaveCount(0);
    await expect(inbox.getByText(/0 unread/)).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });

test("large visible mail snapshots check local tombstones in bounded batches", async ({
  page,
}) => {
  const mail = Array.from({ length: 201 }, (_, index) => ({
    id: `message-${index}`,
    key: "email:" + index.toString(16).padStart(64, "0"),
    accountId: "personal",
    subject: `Reply ${index}`,
    from: "Sender",
    receivedAt: "2026-09-22T10:00:00Z",
    actionability: { state: "action", action: "Reply" },
    triage: { revision: 0, disposition: "none" },
  }));
  const chunks: string[][] = [];
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname;
    let body: any = {
      items: [],
      data: [],
      threads: [],
      messages: [],
      providers: [],
    };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/inbox" || path === "/api/inbox/status")
      body = {
        items: [],
        total: 0,
        unreadCount: 0,
        throughSequence: 0,
        nextCursor: null,
      };
    if (path === "/api/inbox-mail/read") {
      const keys = url.searchParams.getAll("keys");
      expect(keys.length).toBeLessThanOrEqual(100);
      if (keys.length) chunks.push(keys);
      body = {
        readKeys: [],
        removedKeys: keys.includes(mail[200].key) ? [mail[200].key] : [],
        scope: "leam_only",
      };
    }
    if (path === "/api/agenda")
      body = {
        date: "2026-09-22",
        timezone: "UTC",
        commitments: [],
        events: [],
        emails: mail,
        nextOffset: null,
        total: { commitments: 0, events: 0, emails: 201 },
        sources: {
          calendar: { state: "not_connected", accounts: [], snapshots: [] },
          email: {
            state: "ready",
            accounts: [],
            classification: {
              state: "ready",
              counts: { action: 201, ignore: 0, review: 0, pending: 0 },
            },
          },
        },
      };
    await route.fulfill({ json: body });
  });
  await page.goto("/?view=today#today/inbox/2026-09-22");
  const inbox = page.getByRole("region", { name: "Inbox", exact: true });
  await expect(inbox.locator(".inbox-mail-card")).toHaveCount(200);
  await expect(
    inbox.getByRole("heading", { name: "Reply 200", exact: true }),
  ).toHaveCount(0);
  expect(new Set(chunks.flat()).size).toBe(201);
  expect(chunks.some((keys) => keys.length === 100)).toBe(true);
});
