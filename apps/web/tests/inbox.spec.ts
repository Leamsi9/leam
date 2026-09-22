import { test, expect, type Page } from "@playwright/test";
import { navigate } from "./navigation";
async function fixture(page: Page) {
  const item = (id: string, sequence: number) => ({
    id,
    sequence,
    subject: `Note ${sequence}`,
    preview: "Private body",
    body: "<script>window.inboxInjected = true</script> A note, not permission.",
    createdAt: 1790000000,
    unread: true,
    readAt: null as number | null,
    origin: "companion",
    links: [],
  });
  const state = {
    rows: [item("11111111-1111-4111-8111-111111111111", 1)],
    writes: [] as any[],
    failCreate: false,
    newOnReadAll: false,
  };
  const status = () => ({
    total: state.rows.length,
    unreadCount: state.rows.filter((x) => x.unread).length,
    throughSequence: Math.max(0, ...state.rows.map((x) => x.sequence)),
  });
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      close() {}
    };
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    let body: any = {
      items: [],
      threads: [],
      models: [],
      providers: [],
      data: [],
      messages: [],
    };
    if (path === "/api/auth/status")
      body = { authenticated: true, configured: true };
    if (path === "/api/agenda") body = { date: new URL(request.url()).searchParams.get("date"), timezone: "UTC", commitments: [], events: [], emails: [], nextOffset: null, total: { commitments: 0, events: 0, emails: 0 }, sources: { calendar: { state: "not_connected", accounts: [], snapshots: [] }, email: { state: "not_connected", accounts: [] } } };
    if (path === "/api/inbox-mail/read") body = { readKeys: [], scope: "leam_only" };
    if (path === "/api/inbox/status") body = status();
    if (path === "/api/inbox") {
      if (request.method() === "POST") {
        const saved = request.postDataJSON();
        state.writes.push(saved);
        if (state.failCreate) {
          state.failCreate = false;
          return route.fulfill({
            status: 503,
            json: { detail: "Saved outcome unavailable" },
          });
        }
        state.rows.push({
          ...item(saved.requestId, 2),
          subject: saved.subject,
          body: saved.body,
        });
        body = { state: "saved", item: state.rows.at(-1) };
      } else
        body = {
          items: [...state.rows].reverse(),
          nextCursor: null,
          ...status(),
        };
    }
    if (path === "/api/inbox/read-all") {
      const snapshot = request.postDataJSON();
      state.writes.push(snapshot);
      if (state.newOnReadAll)
        state.rows.push(item("22222222-2222-4222-8222-222222222222", 2));
      state.rows.forEach((row) => {
        if (row.sequence <= snapshot.throughSequence) row.unread = false;
      });
      body = status();
    } else if (path.startsWith("/api/inbox/") && path !== "/api/inbox/status") {
      const id = path.split("/")[3];
      const row = state.rows.find((x) => x.id === id);
      if (path.endsWith("/read")) {
        state.writes.push({ read: id });
        if (row) row.unread = false;
        body = { item: row, ...status() };
      } else body = row;
    }
    await route.fulfill({ json: body });
  });
  return state;
}
for (const viewport of [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
]) {
  test(`inbox note and explicit snapshot reads at ${viewport.width}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const state = await fixture(page);
    await page.goto("/?view=inbox");
    const panel = page.getByRole("region", { name: "Inbox", exact: true });
    await expect(
      panel.getByRole("heading", { name: "Inbox", exact: true }),
    ).toBeVisible();
    await panel.locator("summary").click();
    await expect(panel.getByText(/window.inboxInjected/)).toBeVisible();
    expect(
      await page.evaluate(() => (window as any).inboxInjected),
    ).toBeUndefined();
    expect(state.writes).toHaveLength(0);
    await expect(panel.locator(".inbox-card[data-unread=true]")).toHaveCount(1);
    state.newOnReadAll = true;
    await panel
      .getByRole("button", { name: "Mark all as read", exact: true })
      .click();
    await expect(panel.locator(".inbox-card[data-unread=true]")).toHaveCount(1);
    expect(state.writes[0]).toEqual({ throughSequence: 1 });
    await expect(panel.getByText("1 unread · 2 shown")).toBeVisible();
    await panel.locator("summary").filter({ hasText: "Note 2" }).click();
    await panel
      .getByRole("button", { name: "Mark as read", exact: true })
      .click();
    await expect(panel.locator(".inbox-card[data-unread=true]")).toHaveCount(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}
test("create retry retains UUID, draft and Today destination", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await fixture(page);
  await page.goto("/?view=resources");
  await navigate(page, "Today");
  await page.getByRole("link", { name: /^Inbox/ }).click();
  const panel = page.getByRole("region", { name: "Inbox", exact: true });
  await panel.getByRole("button", { name: "Create inbox note" }).click();
  await panel.getByLabel("Subject", { exact: true }).fill("Remember report");
  await panel
    .getByLabel("Note", { exact: true })
    .fill("Read the report later.");
  state.failCreate = true;
  await panel.getByRole("button", { name: "Save note", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText(
    "Saved outcome unavailable",
  );
  await expect(panel.getByLabel("Subject", { exact: true })).toHaveValue(
    "Remember report",
  );
  await panel.getByRole("button", { name: "Save note", exact: true }).click();
  await expect(
    panel.locator("summary").filter({ hasText: "Remember report" }),
  ).toBeVisible();
  expect(state.writes[0].requestId).toBe(state.writes[1].requestId);
});
