import { test, expect, type Page } from '@playwright/test';
import { chooseConversation } from './navigation';

const row = (id: string, run: string, sequence: number, kind: string, content: string) => ({
  message_id: id, turn_run_id: run, sequence, kind, content,
  status: kind === 'assistant' ? 'finalized' : 'submitted',
});
const failure = { run_status: { run_id: 'failed-run', status: 'failed', failure_summary: 'Provider unavailable' } };
const cancelled = { run_status: { run_id: 'cancelled-run', status: 'cancelled' } };
async function emit(page: Page, items: any[], type = 'projection_snapshot') {
  await page.waitForFunction(() => !!(window as any).statusStream);
  await page.evaluate(({ items, type }) => (window as any).statusStream.dispatchEvent(new MessageEvent(type, {
    data: JSON.stringify({ type, state: { thread_id: 'status-thread', items } }),
  })), { items, type });
}
async function fixture(page: Page, surface: string, initial: any[]) {
  const state = { messages: initial, earlier: [] as any[] };
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget {
      constructor(url: string) { super(); if (url.includes('/companion/threads/')) (window as any).statusStream = this; }
      close() {}
    };
  });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname;
    let body: any = { items: [], data: [], threads: [], messages: [], models: [], providers: [] };
    if (path === '/api/auth/status') body = { authenticated: true };
    if (path === '/api/agenda') body = { commitments: [], events: [], emails: [], sources: {}, total: {}, nextOffset: null };
    if (path === '/api/agenda/chat') body = { threadId: 'status-thread' };
    if (path === '/api/companion/status') body = { configured: true, available: true };
    if (path === '/api/companion/threads') body = { threads: [{ thread_id: 'status-thread', title: 'Fixture' }] };
    if (path === '/api/companion/threads/status-thread') body = url.searchParams.has('cursor')
      ? { messages: state.earlier } : { messages: state.messages, next_cursor: state.earlier.length ? 'earlier' : null };
    await route.fulfill({ json: body });
  });
  await page.goto(surface === 'today' ? '/?view=today#today/chat/2026-09-21' : '/?view=companion');
  if (surface !== 'today') await chooseConversation(page, 'status-thread');
  await expect(page.locator('.companion-transcript .message').first()).toBeVisible();
  return state;
}
async function order(page: Page, expected: string[]) {
  await expect.poll(() => page.locator('.companion-transcript .message').allTextContents())
    .toEqual(expected.map(text => expect.stringContaining(text)));
}
for (const surface of ['today', 'companion']) for (const width of [390, 844]) {
  test(`${surface} old failures and cancellations stay with their attempts after stream and reload ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 844 ? 390 : 844 });
    const state = await fixture(page, surface, [
      row('failed-input', 'failed-run', 1, 'user', 'Earlier request'),
      row('cancelled-input', 'cancelled-run', 2, 'user', 'Cancelled request'),
      row('new-input', 'new-run', 3, 'user', 'Current request'),
    ]);
    // Reverse arrival order deliberately differs from canonical conversation order.
    await emit(page, [cancelled, failure, { text: { run_id: 'new-run', body: 'Current streaming answer' } }]);
    await order(page, ['Earlier request', 'Provider unavailable', 'Cancelled request', 'Response cancelled.', 'Current request', 'Current streaming answer']);
    await emit(page, [failure, cancelled]);
    await expect(page.getByText('Provider unavailable', { exact: true })).toHaveCount(1);
    state.messages.push(row('new-answer', 'new-run', 4, 'assistant', 'Newest saved answer'));
    await emit(page, [{ text: { run_id: 'new-run', body: 'Newest saved answer', finalized: true } }, { run_status: { run_id: 'new-run', status: 'completed' } }]);
    await order(page, ['Earlier request', 'Provider unavailable', 'Cancelled request', 'Response cancelled.', 'Current request', 'Newest saved answer']);
    await page.reload();
    await emit(page, [cancelled, failure]);
    await order(page, ['Earlier request', 'Provider unavailable', 'Cancelled request', 'Response cancelled.', 'Current request', 'Newest saved answer']);
    await expect.poll(() => page.locator('.companion-messages').evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(4);
    const last = await page.locator('.companion-transcript .message').last().boundingBox();
    const composer = await page.locator('.companion-page .composer').boundingBox();
    expect(last!.y + last!.height).toBeLessThanOrEqual(composer!.y + 1);
  });
}
test('failed same-run follow-up remains after previous saved answer; latest failure rests last', async ({ page }) => {
  await fixture(page, 'today', [row('u1', 'failed-run', 1, 'user', 'First request'), row('a1', 'failed-run', 2, 'assistant', 'Saved first answer'), row('u2', 'failed-run', 3, 'user', 'Follow-up request')]);
  await emit(page, [{ text: { run_id: 'failed-run', body: 'Saved first answer', finalized: true } }, failure]);
  await order(page, ['First request', 'Saved first answer', 'Follow-up request', 'Provider unavailable']);
  await expect(page.getByText('Saved first answer', { exact: true })).toHaveCount(1);
});
test('unloaded historical failure moves to canonical position when earlier messages load', async ({ page }) => {
  const state = await fixture(page, 'companion', [row('new-u', 'new-run', 4, 'user', 'Latest request'), row('new-a', 'new-run', 5, 'assistant', 'Latest answer')]);
  state.earlier = [row('old-u', 'failed-run', 1, 'user', 'Earlier original'), row('middle-u', 'middle-run', 2, 'user', 'Middle request'), row('middle-a', 'middle-run', 3, 'assistant', 'Middle answer')];
  await emit(page, [failure]);
  await order(page, ['Provider unavailable', 'Latest request', 'Latest answer']);
  await page.getByRole('button', { name: 'Earlier messages', exact: true }).click();
  await order(page, ['Earlier original', 'Provider unavailable', 'Middle request', 'Middle answer', 'Latest request', 'Latest answer']);
});
