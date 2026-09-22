import { test, expect, type Page } from "@playwright/test";

async function fixture(page: Page) {
  const state = { failAgenda: false, newOnAll: false, reads: new Set<string>(), writes: [] as any[],
    notes: [{ id: "11111111-1111-4111-8111-111111111111", sequence: 1, subject: "Leam follow-up", preview: "Do not filter this note", body: "My saved note", origin: "companion", createdAt: 1790000010, unread: true, links: [] }] };
  const status = () => ({ total: state.notes.length, unreadCount: state.notes.filter(item => item.unread).length, throughSequence: state.notes.length });
  await page.addInitScript(() => { (window as any).EventSource = class extends EventTarget { close() {} }; });
  await page.route("**/api/**", async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, write = request.method() === "POST";
    let body: any = { items: [], data: [], threads: [], messages: [], providers: [] };
    if (path === "/api/auth/status") body = { authenticated: true };
    if (path === "/api/inbox/status") body = status();
    if (path === "/api/inbox") body = { items: state.notes, ...status(), nextCursor: null };
    if (path === "/api/inbox/read-all") {
      const input = request.postDataJSON(); state.writes.push({ path, input });
      if (state.newOnAll) { state.newOnAll = false; state.notes.push({ ...state.notes[0], id: "22222222-2222-4222-8222-222222222222", sequence: 2, subject: "New arrival", unread: true }); }
      state.notes.forEach(item => { if (item.sequence <= input.throughSequence) item.unread = false; }); body = status();
    } else if (/^\/api\/inbox\/[0-9a-f-]+/.test(path)) {
      const id = path.split("/")[3], item = state.notes.find(item => item.id === id)!;
      if (write) { state.writes.push({ path }); item.unread = path.endsWith("/unread"); }
      body = item;
    }
    if (path === "/api/inbox-mail/read") {
      if (write) { const input = request.postDataJSON(); state.writes.push({ path, input }); input.keys.forEach((key: string) => input.read ? state.reads.add(key) : state.reads.delete(key)); }
      body = { readKeys: [...state.reads], scope: "leam_only" };
    }
    if (path === "/api/agenda") {
      if (state.failAgenda) { await route.fulfill({ status: 503, json: { detail: "Agenda temporarily unavailable" } }); return; }
      const mail = (key: string, accountId: string, subject: string, action = "action") => ({ id: key, key, accountId, from: "Colleague", subject, receivedAt: "2026-09-21T10:00:00Z", unread: true, snippet: "Please respond", url: "https://mail.google.com/fixture", actionability: { state: action, action: "Reply" }, triage: { revision: 0, disposition: "none" } });
      body = { date: url.searchParams.get("date"), timezone: "UTC", commitments: [], events: [], nextOffset: null,
        emails: [mail("mail-a", "personal", "Personal reply"), mail("mail-b", "work", "Work reply"), mail("ignored", "personal", "Newsletter", "ignore")],
        total: { commitments: 0, events: 0, emails: 2 }, sources: { calendar: { state: "not_connected", accounts: [], snapshots: [] }, email: { state: "ready", accounts: [{ accountId: "personal", identity: "personal@example.test", granted: true, state: "ready" }, { accountId: "work", identity: "work@example.test", granted: true, state: "ready" }], classification: { state: "ready", counts: { action: 2, ignore: 1, review: 0, pending: 0 } } } } };
    }
    if (path === "/api/agenda/triage") { const input = request.postDataJSON(); state.writes.push({ path, input }); body = { ...input, revision: 1 }; }
    await route.fulfill({ json: body });
  });
  return state;
}

for (const viewport of [{ width: 390, height: 844 }, { width: 844, height: 390 }]) test(`Today combines sources and filters compact cards ${viewport.width}`, async ({ page }) => {
  await page.setViewportSize(viewport); await fixture(page);
  await page.goto("/?view=today#today/inbox/2026-09-22");
  const inbox = page.getByRole("region", { name: "Inbox", exact: true });
  await expect(inbox.locator(".inbox-card")).toHaveCount(3);
  await expect(inbox.getByText("Newsletter", { exact: true })).toHaveCount(0);
  await expect(inbox.locator(".inbox-source[data-source=leam]")).toBeVisible();
  await expect(inbox.locator(".inbox-source").filter({ hasText: /^Gmail · personal@example\.test$/ })).toBeVisible();
  await expect(inbox.locator(".inbox-card details[open]")).toHaveCount(0);
  await inbox.getByLabel("Inbox source").selectOption("account:work");
  await expect(inbox.locator(".inbox-card")).toHaveCount(1);
  await expect(inbox.getByRole("heading", { name: "Work reply" })).toBeVisible();
  await inbox.getByLabel("Inbox source").selectOption("leam");
  await expect(inbox.locator(".inbox-card")).toHaveCount(1);
  await expect(inbox.getByText("Leam follow-up", { exact: false })).toBeVisible();
  const mainNav = page.getByRole("navigation", { name: "Main navigation" });
  const more = mainNav.getByRole("button", { name: /^More/ });
  if (await more.isVisible()) {
    await more.click();
    await expect(page.getByRole("dialog", { name: "More from Leam" }).getByRole("button", { name: /^Inbox/ })).toHaveCount(0);
  } else {
    await expect(mainNav.getByRole("button", { name: /^Inbox/ })).toHaveCount(0);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("mixed snapshot mark-all and explicit unread never write to Gmail", async ({ page }) => {
  const state = await fixture(page); state.newOnAll = true;
  await page.goto("/?view=today#today/inbox/2026-09-22");
  const inbox = page.getByRole("region", { name: "Inbox", exact: true });
  await expect(inbox.locator(".inbox-card")).toHaveCount(3);
  await expect(inbox.getByRole("button", { name: "Mark all as read" })).toBeEnabled();
  await inbox.getByRole("button", { name: "Mark all as read" }).click();
  await expect(inbox.getByText("1 unread · 4 shown")).toBeVisible();
  expect(state.writes).toEqual([{ path: "/api/inbox/read-all", input: { throughSequence: 1 } }, { path: "/api/inbox-mail/read", input: { keys: ["mail-a", "mail-b"], read: true } }]);
  const card = inbox.locator(".inbox-card").filter({ has: page.getByRole("heading", { name: "Personal reply" }) });
  await card.locator(":scope > details > summary").click();
  await expect(card.getByText(/Gmail labels stay unchanged/)).toBeVisible();
  await card.getByRole("button", { name: "Mark as unread", exact: true }).click();
  await expect(card).toHaveAttribute("data-unread", "true");
  expect(state.writes.at(-1)).toEqual({ path: "/api/inbox-mail/read", input: { keys: ["mail-a"], read: false } });
  await inbox.getByLabel("Unread only", { exact: true }).check();
  await expect(inbox.locator(".inbox-card")).toHaveCount(2);
});

test("legacy deep link retains selected day and exact note; mail search and drafts remain available", async ({ page }) => {
  await fixture(page);
  const id = "11111111-1111-4111-8111-111111111111";
  await page.goto(`/?view=inbox&inboxItem=${id}#today/plan/2026-09-17`);
  await expect(page).toHaveURL(new RegExp(`view=today&inboxItem=${id}#today/inbox/2026-09-17`));
  const inbox = page.getByRole("region", { name: "Inbox", exact: true });
  await expect(inbox.getByText("My saved note", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Search mail & drafts" })).toBeVisible();
  await inbox.getByRole("button", { name: "Create inbox note" }).click();
  await inbox.getByLabel("Subject", { exact: true }).fill("A new note draft");
  await inbox.getByLabel("Inbox source").selectOption("mail");
  await expect(inbox.getByLabel("Subject", { exact: true })).toHaveValue("A new note draft");
  await expect(inbox.getByText("My saved note", { exact: true })).toHaveCount(0);
});

test("Leam notes remain usable when agenda source fails", async ({ page }) => {
  const state = await fixture(page); state.failAgenda = true;
  await page.goto("/?view=today#today/inbox/2026-09-22");
  const inbox = page.getByRole("region", { name: "Inbox", exact: true });
  await expect(inbox.locator(".inbox-card")).toHaveCount(1);
  await inbox.locator("summary").click();
  await inbox.getByRole("button", { name: "Mark as read", exact: true }).click();
  await expect(inbox.locator(".inbox-card[data-unread=true]")).toHaveCount(0);
});
