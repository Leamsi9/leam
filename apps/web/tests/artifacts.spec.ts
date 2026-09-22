import { test, expect } from '@playwright/test';

test('phone report opens the artifact after sign-in and keeps raw HTML inert', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let authenticated = false;
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/login') authenticated = true;
    const body = path.startsWith('/api/auth/') ? { authenticated, configured: true } : path === '/api/artifacts/review-v1' ? {
      id: 'review-v1', title: 'Review and plan', kind: 'markdown', content: '# Read this on your phone\n\n<script>window.bad = true</script>\n\n[Local](/tmp/private-report.md)', publishedAt: 1789990000, sha256: 'fixture',
    } : {};
    await route.fulfill({ json: body });
  });
  await page.goto('/?artifact=review-v1');
  await page.getByLabel('Password', { exact: true }).fill('fixture-password');
  await page.getByRole('button', { name: 'Enter Leam' }).click();
  await expect(page.getByRole('heading', { name: 'Read this on your phone' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Download' })).toHaveAttribute('href', '/api/artifacts/review-v1/download');
  await expect(page.getByText('(local file; needs a published link)')).toBeVisible();
  expect(await page.evaluate(() => (window as any).bad)).toBeUndefined();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('missing artifact is explicit rather than silently reopening chat', async ({ page }) => {
  await page.route('**/api/auth/status', (route) => route.fulfill({ json: { authenticated: true } }));
  await page.route('**/api/artifacts/missing', (route) => route.fulfill({ status: 404, json: { detail: 'Artifact not found' } }));
  await page.goto('/?artifact=missing');
  await expect(page.getByRole('heading', { name: 'Unable to open this artifact' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});
