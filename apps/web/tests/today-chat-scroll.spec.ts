import { test, expect, type Page } from '@playwright/test';

async function fixture(page: Page, long = false) {
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      constructor(url: string) {
        super();
        if (url.includes('/companion/threads/')) (window as any).todayStream = this;
      }
      close() {}
    };
  });
  const messages = Array.from({ length: long ? 24 : 2 }, (_, index) => ({
    message_id: 'message-' + index, sequence: index + 1,
    kind: index % 2 ? 'assistant' : 'user', status: index % 2 ? 'finalized' : 'submitted',
    turn_run_id: 'run-' + Math.floor(index / 2),
    content: index % 2 ? `Answer ${index}: ${long ? 'A longer synthetic reply. '.repeat(12) : 'One manageable next step.'}` : `Question ${index}`,
  }));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [], threads: [], messages: [], models: [], providers: [] };
    if (path === '/api/auth/status') body = { authenticated: true };
    if (path === '/api/agenda') body = { commitments: [], events: [], emails: [], sources: {}, total: {}, nextOffset: null };
    if (path === '/api/agenda/chat') body = { threadId: 'today-scroll' };
    if (path === '/api/companion/status') body = { configured: true, available: true };
    if (path === '/api/companion/threads/today-scroll') body = { messages };
    if (path === '/api/proposals/status') body = { pendingCount: 0, unreadCount: 0 };
    await route.fulfill({ json: body });
  });
  await page.goto('/?view=today#today/chat/2026-09-22');
  await expect(page.locator('.companion-transcript .message')).toHaveCount(messages.length);
  await page.waitForFunction(() => !!(window as any).todayStream);
  return messages.length;
}

async function gap(page: Page) {
  return page.locator('.companion-messages').evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight);
}
async function emit(page: Page, text: string) {
  await page.evaluate(text => (window as any).todayStream.dispatchEvent(new MessageEvent('projection_update', {
    data: JSON.stringify({ type: 'projection_update', state: { thread_id: 'today-scroll', items: [
      { text: { id: 'live-partial', run_id: 'new-run', body: text } },
    ] } }),
  })), text);
}

for (const viewport of [{ width: 390, height: 844 }, { width: 844, height: 390 }, { width: 1440, height: 1000 }]) {
  test(`short Today history rests at bottom ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await fixture(page);
    const viewportBox = await page.locator('.companion-messages').boundingBox();
    const last = await page.locator('.companion-transcript .message').last().boundingBox();
    expect(viewportBox).not.toBeNull();
    expect(last).not.toBeNull();
    expect(Math.abs(viewportBox!.y + viewportBox!.height - last!.y - last!.height - 12)).toBeLessThan(5);
    const composer = await page.locator('.companion-page .composer').boundingBox();
    expect(last!.y + last!.height).toBeLessThanOrEqual(composer!.y + 1);
    expect(await gap(page)).toBeLessThan(4);
    expect(await page.locator('.companion-transcript .message').allTextContents()).toEqual([
      expect.stringContaining('Question 0'), expect.stringContaining('Answer 1'),
    ]);
  });

  test(`long Today history opens latest and follows stream but respects scrollback ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await fixture(page, true);
    await expect.poll(() => gap(page)).toBeLessThan(4);
    expect(await page.locator('.companion-messages').evaluate(el => el.scrollTop)).toBeGreaterThan(100);
    await emit(page, 'Streamed reply. '.repeat(100));
    await expect(page.getByLabel('Live companion response')).toBeVisible();
    await expect.poll(() => gap(page)).toBeLessThan(4);
    // Actual pointer wheel scroll, not a fabricated helper state.
    await page.locator('.companion-messages').hover();
    await page.mouse.wheel(0, -550);
    await expect.poll(() => gap(page)).toBeGreaterThan(150);
    const before = await page.locator('.companion-messages').evaluate(el => el.scrollTop);
    await emit(page, 'Streamed reply. '.repeat(160));
    await page.waitForTimeout(100);
    expect(Math.abs(await page.locator('.companion-messages').evaluate(el => el.scrollTop) - before)).toBeLessThan(5);
    expect(await gap(page)).toBeGreaterThan(150);
    // Returning to the bottom explicitly restores following for future chunks.
    await page.mouse.wheel(0, 100000);
    await expect.poll(() => gap(page)).toBeLessThan(4);
    await emit(page, 'Streamed reply. '.repeat(200));
    await expect.poll(() => gap(page)).toBeLessThan(4);
  });
}
