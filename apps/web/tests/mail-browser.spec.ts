import { test, expect, type Page } from "@playwright/test";

async function setup(page: Page) {
  const state = { searches: [] as any[], reads: [] as string[], writes: [] as any[], deletes: [] as any[], boxes: 0,
    drafts: new Map<string, any>(), emoji: false, loseSave: false, conflictSave: false, conflictRead: false, heldRead: null as Promise<void> | null };
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url()), path = url.pathname, method = route.request().method();
    let body: any = { items: [], accounts: [], providers: [], models: [], messages: [], threads: [] };
    if (path === "/api/auth/status") body = { authenticated: true, configured: true };
    if (path === "/api/agenda") body = { date: url.searchParams.get("date"), commitments: [], emails: [], events: [], total: { events: 0, emails: 0 },
      sources: { calendar: { state: "current", accounts: [], snapshots: [] }, email: { state: "not_connected", accounts: [] } } };
    if (path === "/api/email/mailboxes") { state.boxes++; body = { items: [
      { accountId: "account-a", identity: "a@example.test", granted: true, state: "connected" },
      { accountId: "account-b", identity: "b@example.test", granted: true, state: "connected" },
    ], nextOffset: null }; }
    if (path === "/api/email/search") {
      const request = route.request().postDataJSON(); state.searches.push(request);
      body = { ...request, items: [{ accountId: request.accountId, messageId: request.pageToken ? "m2" : "m1", subject: request.pageToken ? "Second message" : "Archived message", from: "sender@example.test", receivedAt: "2026-08-01T10:00:00Z" }], nextPageToken: request.pageToken ? null : "older-page" };
    }
    if (/\/email\/accounts\/.*\/messages\//.test(path)) {
      state.reads.push(path + url.search);
      if (state.heldRead) await state.heldRead;
      if (state.conflictRead && url.searchParams.has("offset")) return route.fulfill({ status: 409, json: { detail: "Message text changed; restart from offset zero" } });
      const more = url.searchParams.has("offset");
      body = { accountId: path.split("/")[4], messageId: path.split("/")[6], subject: "Archived message", from: "sender@example.test", to: "a@example.test", receivedAt: "2026-08-01T10:00:00Z",
        text: more ? "the remainder." : "<script>external text</script> ", totalCharacters: 45, nextOffset: more ? null : 31, contentRevision: "a".repeat(64), offset: more ? 31 : 0,
        attachments: [{ filename: "attachment.pdf", mimeType: "application/pdf", size: 2000, contentFetched: false }] };
      if (state.emoji) body = { ...body, text: "😀", totalCharacters: 1, nextOffset: null };
    }
    if (path === "/api/email/drafts") body = { items: [...state.drafts.values()], nextOffset: null };
    if (path.startsWith("/api/email/drafts/")) {
      const id = path.split("/").at(-1)!;
      if (method === "PUT") {
        const request = route.request().postDataJSON(); state.writes.push({ id, ...request });
        if (state.conflictSave) return route.fulfill({ status: 409, json: { detail: "Local draft changed; reload before saving" } });
        const old = state.drafts.get(id);
        if (old && old.revision === request.revision + 1) body = old;
        else { body = { ...request, id, revision: request.revision + 1, createdAt: 1790000000, updatedAt: 1790000000, storage: "leam", sent: false, savedInGmail: false }; state.drafts.set(id, body); }
        if (state.loseSave) { state.loseSave = false; return route.abort(); }
      } else if (method === "DELETE") { state.deletes.push({ id, ...route.request().postDataJSON() }); state.drafts.delete(id); body = { removed: true, gmailChanged: false }; }
      else { body = state.drafts.get(id); if (!body) return route.fulfill({ status: 404, json: { detail: "Local draft not found" } }); }
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/?view=today#today/inbox/2026-09-21");
  await expect(page.getByRole("button", { name: "Search mail & drafts" })).toBeVisible();
  return state;
}
async function open(page: Page) {
  await page.getByRole("button", { name: "Search mail & drafts" }).click();
  await expect(page.getByRole("combobox", { name: "Mailbox", exact: true })).toHaveValue("account-a");
}
async function compose(page: Page) {
  await page.getByRole("button", { name: "Local drafts", exact: true }).click();
  await page.getByRole("button", { name: "Compose local draft" }).click();
  await page.getByRole("textbox", { name: "To · one address per line", exact: true }).fill("one@example.test\ntwo@example.test");
  await page.getByRole("textbox", { name: "Subject", exact: true }).fill("My local reply");
  await page.getByRole("textbox", { name: "Draft message", exact: true }).fill("Private editable text\nKeep whitespace.");
}

test("Inbox opens local identities lazily and only explicit searches/read page exact mailbox data", async ({ page }) => {
  const state = await setup(page);
  expect(state.boxes).toBe(0); expect(state.searches).toEqual([]); expect(state.reads).toEqual([]);
  await open(page); expect(state.searches).toEqual([]);
  await page.getByLabel("Search Gmail").fill("in:sent before:2025/01/01");
  await page.getByLabel("Include spam and trash").check();
  await page.locator('form').filter({ has: page.getByLabel("Search Gmail") }).getByRole("button", { name: "Search mailbox", exact: true }).click();
  await page.getByRole("button", { name: /Archived message/ }).click();
  await expect(page.getByRole("region", { name: "Message text" })).toContainText("<script>external text</script>");
  expect(await page.locator('.mail-reading script').count()).toBe(0);
  await page.getByRole("button", { name: "Read more message text" }).click();
  await expect(page.locator('.mail-reading pre')).toHaveText("<script>external text</script> the remainder.");
  expect(state.reads[1]).toContain("account-a/messages/m1");
  expect(state.reads[1]).toContain("offset=31"); expect(state.reads[1]).toContain("revision=" + "a".repeat(64));
  await page.getByRole("button", { name: "More search results" }).click();
  await expect(page.getByRole("button", { name: /Second message/ })).toBeVisible();
  expect(state.searches).toEqual([
    { accountId: "account-a", query: "in:sent before:2025/01/01", includeSpamTrash: true, limit: 20 },
    { accountId: "account-a", query: "in:sent before:2025/01/01", includeSpamTrash: true, limit: 20, pageToken: "older-page" },
  ]);
});

test("local draft keeps edits across close/pages and retries same UUID payload after lost receipt", async ({ page }) => {
  const state = await setup(page); await open(page); await compose(page);
  await page.getByRole("button", { name: "Close mail browser" }).click();
  await page.getByRole("navigation", { name: "Today pages" }).getByRole("link", { name: "Overview", exact: true }).click();
  await page.getByRole("navigation", { name: "Today pages" }).getByRole("link", { name: "Inbox", exact: true }).click();
  await page.getByRole("button", { name: "Search mail & drafts" }).click();
  await expect(page.getByRole("textbox", { name: "Draft message", exact: true })).toHaveValue("Private editable text\nKeep whitespace.");
  state.loseSave = true;
  await page.getByRole("button", { name: "Save local draft", exact: true }).click();
  await expect(page.getByRole("button", { name: "Retry same save" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Draft message", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Retry same save" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Saved in Leam, not Gmail." })).toBeVisible();
  expect(state.writes).toHaveLength(2); expect(state.writes[1]).toEqual(state.writes[0]);
  expect(state.writes[0].to).toEqual(["one@example.test", "two@example.test"]);
  expect(state.writes[0].revision).toBe(0); expect(state.drafts.size).toBe(1);
  await page.getByRole("textbox", { name: "Draft message", exact: true }).fill("Updated text");
  await page.getByRole("button", { name: "Save local draft", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(3); expect(state.writes[2].revision).toBe(1);
  expect(state.searches).toEqual([]); expect(state.reads).toEqual([]);
});

test("conflict preserves draft and explicit confirmed deletion uses saved revision", async ({ page }) => {
  const state = await setup(page); await open(page); await compose(page);
  await page.getByRole("button", { name: "Save local draft", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Saved in Leam, not Gmail." })).toBeVisible();
  state.conflictSave = true;
  await page.getByRole("textbox", { name: "Draft message", exact: true }).fill("Do not lose this conflict edit");
  await page.getByRole("button", { name: "Save local draft", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Local draft changed");
  await expect(page.getByRole("textbox", { name: "Draft message", exact: true })).toHaveValue("Do not lose this conflict edit");
  page.once("dialog", dialog => dialog.dismiss());
  await page.getByRole("button", { name: "Remove local draft" }).click(); expect(state.deletes).toEqual([]);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Remove local draft" }).click();
  await expect.poll(() => state.deletes.length).toBe(1); expect(state.deletes[0]).toMatchObject({ revision: 1, confirmed: true });
  await expect(page.getByRole("form", { name: "Draft editor" })).toHaveCount(0);
});

test("changed message continuation retains current text and account switch fences late read", async ({ page }) => {
  const state = await setup(page); await open(page);
  await page.locator('form').filter({ has: page.getByLabel("Search Gmail") }).getByRole("button", { name: "Search mailbox", exact: true }).click();
  await page.getByRole("button", { name: /Archived message/ }).click();
  await expect(page.locator('.mail-reading pre')).toBeVisible(); state.conflictRead = true;
  await page.getByRole("button", { name: "Read more message text" }).click();
  await expect(page.getByRole("alert")).toContainText("Message text changed");
  await expect(page.locator('.mail-reading pre')).toHaveText("<script>external text</script> ");
  let release!: () => void; state.heldRead = new Promise(resolve => { release = resolve; });
  await page.getByRole("button", { name: "Reload message from start" }).click();
  await expect.poll(() => state.reads.length).toBe(3);
  await page.getByRole("combobox", { name: "Mailbox", exact: true }).selectOption("account-b"); release();
  await expect(page.getByRole("region", { name: "Message text" })).toHaveCount(0);
  expect(state.searches).toHaveLength(1);
});

test("auth loss clears private editor and fences a late provider read", async ({ page }) => {
  const state = await setup(page); await open(page); await compose(page);
  await page.getByRole("button", { name: "Search mailbox", exact: true }).first().click();
  await page.locator('form').filter({ has: page.getByLabel("Search Gmail") }).getByRole("button", { name: "Search mailbox", exact: true }).click();
  let release!: () => void; state.heldRead = new Promise(resolve => { release = resolve; });
  await page.getByRole("button", { name: /Archived message/ }).click();
  await expect.poll(() => state.reads.length).toBe(1);
  await page.evaluate(() => window.dispatchEvent(new Event("leam:auth-lost")));
  release();
  await expect(page.getByRole("dialog", { name: "Mail browser" })).toHaveCount(0);
  expect(await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }).includes("Private editable text"))).toBe(false);
  expect(state.writes).toEqual([]);
});

for (const size of [{ width: 360, height: 800 }, { width: 844, height: 390 }]) test(`mail search/read/editor fit ${size.width}x${size.height}`, async ({ page }, info) => {
  await page.setViewportSize(size); await setup(page); await open(page);
  await page.screenshot({ path: info.outputPath("mail-search.png") });
  await page.locator('form').filter({ has: page.getByLabel("Search Gmail") }).getByRole("button", { name: "Search mailbox", exact: true }).click();
  await page.getByRole("button", { name: /Archived message/ }).click();
  await expect(page.locator('.mail-reading pre')).toBeVisible();
  await page.locator('.mail-reading').scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("mail-read.png") });
  await compose(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const modal = await page.getByRole("dialog", { name: "Mail browser" }).boundingBox();
  expect(modal!.x).toBeGreaterThanOrEqual(0); expect(modal!.y).toBeGreaterThanOrEqual(0);
  expect(modal!.width).toBeLessThanOrEqual(size.width); expect(modal!.height).toBeLessThanOrEqual(size.height);
  await page.getByRole("button", { name: "Save local draft", exact: true }).scrollIntoViewIfNeeded();
  const save = await page.getByRole("button", { name: "Save local draft", exact: true }).boundingBox();
  expect(save!.height).toBeGreaterThanOrEqual(44); expect(save!.y + save!.height).toBeLessThanOrEqual(size.height);
});


test("Unicode message completeness uses the server character cursor", async ({ page }) => {
  const state = await setup(page); await open(page); state.emoji = true;
  await page.locator('form').filter({ has: page.getByLabel("Search Gmail") }).getByRole("button", { name: "Search mailbox", exact: true }).click();
  await page.getByRole("button", { name: /Archived message/ }).click();
  await expect(page.locator('.mail-reading pre')).toHaveText("😀");
  await expect(page.getByText("1 of 1 characters loaded.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Read more message text" })).toHaveCount(0);
});
