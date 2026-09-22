import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { navigate, settingsSection } from './navigation';

const oldEndpoint = 'https://fcm.googleapis.com/fcm/send/old-synthetic';
const id = createHash('sha256').update(oldEndpoint).digest('hex');

for (const scenario of ['expired', 'changed-key', 'browser-expiry', 'valid', 'unsubscribe-failed', 'subscribe-failed', 'permission-denied']) {
  test(`explicit Enable handles ${scenario} without automatic test sends`, async ({ page }) => {
    const posted: any[] = [];
    let tests = 0;
    let reads = 0;
    await page.addInitScript(({ scenario, oldEndpoint }) => {
      const events: string[] = [];
      (window as any).pushEvents = events;
      Object.defineProperty(window, 'PushManager', { value: function () {}, configurable: true });
      Object.defineProperty(window, 'Notification', { value: {
        requestPermission: async () => { events.push('permission'); return scenario === 'permission-denied' ? 'denied' : 'granted'; },
      }, configurable: true });
      let current: any;
      const make = (endpoint: string) => ({
        endpoint,
        expirationTime: scenario === 'browser-expiry' && endpoint === oldEndpoint ? 1 : null,
        options: { applicationServerKey: new Uint8Array([1, 2, 3]).buffer },
        toJSON: () => ({ endpoint, keys: { p256dh: 'synthetic', auth: 'synthetic' } }),
        unsubscribe: async () => {
          events.push('unsubscribe');
          if (scenario === 'unsubscribe-failed') return false;
          current = null;
          return true;
        },
      });
      current = make(oldEndpoint);
      Object.defineProperty(navigator.serviceWorker, 'ready', { value: Promise.resolve({
        pushManager: {
          getSubscription: async () => current,
          subscribe: async (options: any) => {
            events.push('subscribe:' + String(new Uint8Array(options.applicationServerKey)));
            if (scenario === 'subscribe-failed') throw new Error('Synthetic subscription failure');
            current = make('https://fcm.googleapis.com/fcm/send/new-synthetic');
            return current;
          },
        },
      }), configurable: true });
    }, { scenario, oldEndpoint });
    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith('/test')) tests++;
      if (path === '/api/push/devices' && route.request().method() === 'POST') posted.push(route.request().postDataJSON());
      if (path === '/api/push/status') reads++;
      const stale = reads <= 1;
      const expired = ['expired', 'unsubscribe-failed', 'subscribe-failed'].includes(scenario);
      await route.fulfill({ json: path === '/api/auth/status' ? { authenticated: true }
        : path === '/api/push/status' ? {
          contact: 'mailto:owner@example.com',
          publicKey: scenario === 'changed-key' && !stale ? 'BAUG' : 'AQID',
          devices: [{ id, name: 'Phone', state: expired && !stale ? 'expired' : 'active' }], deliveries: [],
        } : path === '/api/settings/providers' ? { providers: [] } : { items: [], data: [] } });
    });
    await page.goto('/');
    await navigate(page, 'Settings');
    await settingsSection(page, 'Phone notifications');
    const button = page.getByRole('button', { name: 'Enable notifications on this device' });
    await expect(button).toBeEnabled();
    expect(posted).toHaveLength(0);
    await button.click();
    if (scenario === 'permission-denied') {
      await expect(page.getByText(/Notifications were not allowed/)).toBeVisible();
    } else if (scenario === 'unsubscribe-failed') {
      await expect(page.getByText(/browser could not renew/)).toBeVisible();
    } else if (scenario === 'subscribe-failed') {
      await expect(page.getByText(/Synthetic subscription failure/)).toBeVisible();
    } else {
      await expect(page.getByText(/Device registered\. Send a test/)).toBeVisible();
    }
    const events = await page.evaluate(() => (window as any).pushEvents);
    if (scenario === 'valid') {
      expect(events).toEqual(['permission']);
      expect(posted[0].subscription.endpoint).toBe(oldEndpoint);
    } else if (scenario === 'permission-denied') {
      expect(events).toEqual(['permission']);
      expect(posted).toHaveLength(0);
    } else if (scenario === 'unsubscribe-failed') {
      expect(events).toEqual(['permission', 'unsubscribe']);
      expect(posted).toHaveLength(0);
    } else {
      expect(events).toEqual(['permission', 'unsubscribe', scenario === 'changed-key' ? 'subscribe:4,5,6' : 'subscribe:1,2,3']);
      if (scenario === 'subscribe-failed') expect(posted).toHaveLength(0);
      else expect(posted[0].subscription.endpoint).toContain('new-synthetic');
    }
    expect(tests).toBe(0);
  });
}
