import { test, expect, type Page } from '@playwright/test';

async function setup(page: Page, active = false) {
  const state = { lose: false, conflict: false, writes: [] as any[], deleted: [] as any[], receipts: new Map<string, any>(), items: [{
    feature: 'fixture', title: 'Fixture work', rationale: 'Original reason', scope: 'Original scope', currentStep: 'Measured operator progress',
    revision: 1, rank: 1, percent: 30, assessedAt: new Date().toISOString(), blockers: [], deliveryState: active ? 'in_progress' : 'queued',
    worker: active ? 'worker-a' : null, owner: active ? 'main' : null,
    subtasks: [{ id: 'first', title: 'Existing task', state: 'done' }],
  }] as any[] };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [], data: [] };
    if (path === '/api/auth/status') body = { authenticated: true };
    if (path === '/api/backlog') body = { items: state.items, ordering: { revision: 1 }, review: { current: true }, staleAfterSeconds: 1200 };
    if (path === '/api/backlog/deleted') body = { items: state.deleted };
    if (/\/api\/backlog\/fixture\/(edit|delete|restore)$/.test(path)) {
      const request = route.request().postDataJSON(); state.writes.push({ path, body: request });
      if (state.conflict) { state.conflict = false; state.items[0].revision++; return route.fulfill({ status: 409, json: { detail: 'Revision changed' } }); }
      body = state.receipts.get(request.requestId);
      if (!body) {
        const operation = path.split('/').at(-1);
        body = { requestId: request.requestId, operation, feature: 'fixture', revision: request.revision + 1, cancellationRequired: operation === 'delete' && active };
        if (operation === 'edit') state.items = state.items.map(item => ({ ...item, ...request.changes, revision: body.revision }));
        if (operation === 'delete') { state.deleted = [{ ...state.items[0], revision: body.revision, cancellationRequired: active }]; state.items = []; }
        if (operation === 'restore') { state.items = [{ ...state.deleted[0], revision: body.revision, deliveryState: 'queued', worker: null }]; state.deleted = []; }
        state.receipts.set(request.requestId, body);
      }
      if (state.lose) return route.abort();
    }
    await route.fulfill({ json: body });
  });
  await page.goto('/?view=backlog');
  await page.locator('[data-backlog-feature="fixture"] > details > summary').click();
  await page.getByRole('button', { name: 'Edit ticket', exact: true }).click();
  return state;
}
for (const width of [390, 844]) test(`ticket rationale and subtasks edit sends only user fields and survives dialog close ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: width === 844 ? 390 : 844 });
  const state = await setup(page);
  const dialog = page.getByRole('dialog', { name: 'Edit Fixture work' });
  await dialog.getByLabel('Why this is needed').fill('Clear user rationale');
  await page.getByRole('button', { name: 'Close ticket editor' }).click();
  await page.getByRole('button', { name: 'Edit ticket', exact: true }).click();
  await expect(dialog.getByLabel('Why this is needed')).toHaveValue('Clear user rationale');
  await dialog.locator('summary').filter({ hasText: 'Subtasks' }).click();
  await dialog.getByLabel('Subtask 1', { exact: true }).fill('Renamed user step');
  await dialog.getByRole('button', { name: 'Add subtask', exact: true }).click();
  await dialog.getByLabel('Subtask 2', { exact: true }).fill('Extra requested step');
  await dialog.getByRole('button', { name: 'Save ticket', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0].body).toMatchObject({ revision: 1, changes: { rationale: 'Clear user rationale', subtasks: [{ id: 'first', title: 'Renamed user step' }, { title: 'Extra requested step' }] } });
  expect(Object.keys(state.writes[0].body.changes).sort()).toEqual(['rationale', 'subtasks']);
  await expect(page.getByText('Measured operator progress')).toBeVisible();
});
test('conflict preserves draft, reloads revision explicitly and retries safely after unknown result', async ({ page }) => {
  const state = await setup(page); state.conflict = true;
  await page.getByLabel('Why this is needed').fill('User rationale');
  await page.getByRole('button', { name: 'Save ticket', exact: true }).click();
  await expect(page.getByText(/This ticket changed/)).toBeVisible();
  await expect(page.getByLabel('Why this is needed')).toHaveValue('User rationale');
  await page.getByRole('button', { name: 'Load latest revision' }).click();
  state.lose = true;
  await page.getByRole('button', { name: 'Save ticket', exact: true }).click();
  await expect(page.getByText(/Save confirmation unavailable/)).toBeVisible();
  await expect(page.getByLabel('Why this is needed')).toBeDisabled();
  state.lose = false;
  await page.getByRole('button', { name: 'Retry same request' }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  expect(state.writes[1].body.revision).toBe(2);
  expect(state.writes[2]).toEqual(state.writes[1]);
});
test('assigned ticket deletion requires explicit acknowledgement and shows pending cancellation', async ({ page }) => {
  const state = await setup(page, true);
  const dialog = page.getByRole('dialog');
  await dialog.locator('summary').filter({ hasText: 'Delete ticket' }).click();
  const remove = dialog.getByRole('button', { name: 'Confirm delete ticket' });
  await expect(remove).toBeDisabled();
  await dialog.getByRole('checkbox').check();
  await remove.click();
  await expect(page.locator('[data-backlog-feature="fixture"]')).toHaveCount(0);
  await page.locator('summary').filter({ hasText: 'Deleted tickets' }).click();
  await expect(page.getByText('Coordinator cancellation acknowledgement pending.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Restore Fixture work' })).toBeDisabled();
  expect(state.writes[0].body).toMatchObject({ revision: 1, confirmActive: true });
});
test('queued deletion can be explicitly restored without starting a worker', async ({ page }) => {
  const state = await setup(page);
  await page.getByRole('dialog').locator('summary').filter({ hasText: 'Delete ticket' }).click();
  await page.getByRole('button', { name: 'Confirm delete ticket' }).click();
  await page.locator('summary').filter({ hasText: 'Deleted tickets' }).click();
  await page.getByRole('button', { name: 'Restore Fixture work' }).click();
  await expect(page.locator('[data-backlog-feature="fixture"]')).toBeVisible();
  expect(state.writes).toHaveLength(2);
  expect(state.writes[1].body.revision).toBe(2);
  expect(state.items[0].deliveryState).toBe('queued');
  expect(state.items[0].worker).toBeNull();
});
