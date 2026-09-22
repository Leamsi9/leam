import { test, expect, type Page } from '@playwright/test';
async function setup(page: Page) {
  const dates: string[] = [];
  await page.addInitScript(() => {
    (window as any).EventSource = class extends EventTarget { close() {} };
  });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname;
    let body: any = { items: [], data: [], threads: [], messages: [], models: [], providers: [] };
    if (path === '/api/auth/status') body = { authenticated: true };
    if (path === '/api/agenda') body = { commitments: [], events: [], emails: [], sources: {}, total: {}, nextOffset: null };
    if (path === '/api/agenda/chat') {
      const date = route.request().method() === 'POST' ? route.request().postDataJSON().date : url.searchParams.get('date');
      dates.push(date); body = { threadId: 'day-' + date };
    }
    if (path.startsWith('/api/companion/threads/day-')) {
      const date = path.split('/').at(-1)!.slice(4);
      body = { messages: [{ message_id: date, turn_run_id: date, sequence: 1, kind: 'assistant', status: 'finalized', content: `Saved reply for ${date}` }] };
    }
    await route.fulfill({ json: body });
  });
  return dates;
}
test('Today cold root without hash resumes selected dated conversation', async ({ page }) => {
  const dates = await setup(page);
  await page.goto('/?view=today#today/chat/2026-09-21');
  await expect(page.getByText('Saved reply for 2026-09-21')).toBeVisible();
  await page.goto('/?view=today');
  await expect(page.getByLabel('Viewing date')).toHaveValue('2026-09-21');
  await expect(page.getByText('Saved reply for 2026-09-21')).toBeVisible();
  await expect(page).toHaveURL(/#today\/chat\/2026-09-21$/);
  expect(dates.every(date => date === '2026-09-21')).toBe(true);
});
test('explicit date wins; prior chat can be resumed and browser back/forward restores dates', async ({ page }) => {
  await setup(page);
  await page.goto('/?view=today#today/chat/2026-09-21');
  await expect(page.getByText('Saved reply for 2026-09-21')).toBeVisible();
  await page.goto('/?view=today#today/chat/2026-09-22');
  await expect(page.getByText('Saved reply for 2026-09-22')).toBeVisible();
  await page.getByRole('button', { name: 'Resume Today chat from 2026-09-21', exact: true }).click();
  await expect(page.getByText('Saved reply for 2026-09-21')).toBeVisible();
  await page.goBack();
  await expect(page.getByText('Saved reply for 2026-09-22')).toBeVisible();
  await page.goForward();
  await expect(page.getByText('Saved reply for 2026-09-21')).toBeVisible();
});
test('malformed remembered route cannot select an invalid date or page', async ({ page }) => {
  await setup(page);
  await page.addInitScript(() => {
    sessionStorage.setItem('leam-view:today:route', JSON.stringify({ page: 'chat', date: '2026-02-31' }));
    sessionStorage.setItem('leam-view:today:chatDates', JSON.stringify(['2026-02-31', null, {}]));
  });
  await page.goto('/?view=today');
  await expect(page.getByLabel('Viewing date')).not.toHaveValue('2026-02-31');
  await expect(page.getByRole('button', { name: /Resume Today chat from/ })).toHaveCount(0);
});
