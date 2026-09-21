import { navigate, settingsSection } from "./navigation";
import { test, expect } from "@playwright/test";

// Full Chromium supports notifications; the lightweight headless shell denies them.
test.use({ channel: "chromium" });

test("push settings separate provider acceptance from confirmed receipt", async ({
  page,
}) => {
  let confirmed = false;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/push/deliveries/test/confirm") confirmed = true;
    const body =
      path === "/api/auth/status"
        ? { authenticated: true }
        : path === "/api/push/status"
          ? {
              contact: "mailto:owner@example.com",
              publicKey: "test",
              devices: [{ id: "phone", name: "My phone", state: "active" }],
              deliveries: [
                {
                  id: "test",
                  deviceId: "phone",
                  kind: "test",
                  state: "accepted",
                  updated: 1800000000,
                  confirmedAt: confirmed ? 1800000000 : null,
                },
              ],
            }
          : path === "/api/settings/providers"
            ? { providers: [] }
            : { items: [], data: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await navigate(page, "Settings");
  await settingsSection(page, "Phone notifications");
  await page.getByText("Recent delivery attempts", { exact: true }).click();
  await expect(
    page.getByText(/Accepted by push service; device receipt unconfirmed/),
  ).toBeVisible();
  await expect(
    page.getByText("You confirmed receipt on the device", { exact: false }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "I received this test notification" })
    .click();
  await expect(
    page.getByText(/You confirmed receipt on the device/),
  ).toBeVisible();
});

test("service worker displays a push event and uses a stable tag", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["notifications"]);
  const cdp = await context.newCDPSession(page);
  let registrationId = "";
  let origin = "";
  cdp.on("ServiceWorker.workerRegistrationUpdated", ({ registrations }) => {
    registrationId =
      registrations.find((r) => r.scopeURL === origin + "/")?.registrationId ||
      registrationId;
  });
  await cdp.send("ServiceWorker.enable");
  await page.goto("/");
  origin = new URL(page.url()).origin;
  await cdp.send("ServiceWorker.disable");
  await cdp.send("ServiceWorker.enable");
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  await expect.poll(() => registrationId).not.toBe("");
  await cdp.send("ServiceWorker.deliverPushMessage", {
    origin,
    registrationId,
    data: JSON.stringify({
      body: "A synthetic notification for worker testing",
      tag: "leam-test-worker",
    }),
  });
  await expect
    .poll(() =>
      page.evaluate(async () => {
        const registration = await navigator.serviceWorker.ready;
        const notifications = await registration.getNotifications({
          tag: "leam-test-worker",
        });
        return notifications.map((n) => ({
          title: n.title,
          body: n.body,
          url: n.data.url,
        }));
      }),
    )
    .toEqual([
      {
        title: "Leam",
        body: "A synthetic notification for worker testing",
        url: "/?view=today",
      },
    ]);
  await page.evaluate(async () => {
    for (const n of await (
      await navigator.serviceWorker.ready
    ).getNotifications())
      n.close();
  });
});
