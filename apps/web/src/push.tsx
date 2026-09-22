import { useSettingsActive } from "./settings-lifecycle";
import { useEffect, useState, useRef } from "react";
import { api, type Data } from "./api";

function bytes(value: string) {
  return Uint8Array.from(
    atob(
      value.replace(/-/g, "+").replace(/_/g, "/") +
        "=".repeat((4 - (value.length % 4)) % 4),
    ),
    (c) => c.charCodeAt(0),
  );
}
async function deviceId(subscription: PushSubscription) {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(subscription.endpoint),
  );
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
export function PushSettings({ fail }: { fail: (error: unknown) => void }) {
  const panelActive = useSettingsActive();
  const contactLoaded = useRef(false);
  const acting = useRef(false);
  const [status, setStatus] = useState<Data | null>(null),
    [contact, setContact] = useState(""),
    [name, setName] = useState("My phone"),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const supported =
    window.isSecureContext &&
    "PushManager" in window &&
    "Notification" in window &&
    "serviceWorker" in navigator;
  const load = async () => {
    const result = await api("/push/status");
    setStatus(result);
    return result;
  };
  useEffect(() => {
    if (!panelActive) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    load()
      .then((r) => {
        if (!stopped && !contactLoaded.current) { setContact(r.contact || ""); contactLoaded.current = true; }
      })
      .catch(fail);
    async function poll() {
      try {
        await load();
      } catch (e) {
        if (!stopped) fail(e);
      }
      if (!stopped) timer = setTimeout(poll, 10000);
    }
    timer = setTimeout(poll, 10000);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [panelActive]);
  async function act(operation: () => Promise<void>) {
    if (acting.current) return;
    acting.current = true;
    setBusy(true);
    setMessage("");
    try {
      await operation();
      await load();
    } catch (e) {
      fail(e);
    } finally {
      acting.current = false;
      setBusy(false);
    }
  }
  async function enable() {
    if (acting.current) return;
    // Request permission directly from the click gesture (required by mobile Safari).
    const permission = Notification.requestPermission();
    await act(async () => {
      if ((await permission) !== "granted")
        throw new Error(
          "Notifications were not allowed. Change this site's browser notification permission to enable them.",
        );
      // Read fresh state: the worker may have expired this endpoint since render.
      const current = await load();
      const publicKey = bytes(current.publicKey);
      const registration = await navigator.serviceWorker.ready;
      let subscription = await registration.pushManager.getSubscription();
      if (subscription) {
        const id = await deviceId(subscription);
        const expired = current.devices.some(
          (device: Data) => device.id === id && device.state === "expired",
        ) || (subscription.expirationTime !== null &&
          subscription.expirationTime <= Date.now());
        const changedKey = String(new Uint8Array(
          subscription.options.applicationServerKey || new ArrayBuffer(0),
        )) !== String(publicKey);
        if (expired || changedKey) {
          await subscription.unsubscribe();
          // Do not register a stale endpoint if the browser failed to remove it.
          if (await registration.pushManager.getSubscription())
            throw new Error(
              "The browser could not renew this notification subscription. Close other Leam tabs, then enable notifications again.",
            );
          subscription = null;
        }
      }
      subscription ||= await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: publicKey,
      });
      await api("/push/devices", "POST", {
        name,
        subscription: subscription.toJSON(),
      });
      setMessage(
        "Device registered. Send a test, then confirm when you see it on this device.",
      );
    });
  }
  return (
    <fieldset className="card settings-form" disabled={busy}>
      <h3>Phone notifications</h3>
      <p>
        Reminders show generic text on your lock screen. Your commitment details
        stay inside Leam. On iPhone or iPad, add Leam to your Home Screen and
        open it there first.
      </p>
      {!supported && (
        <p role="status">
          This browser cannot register push here. Use a supported browser over
          HTTPS; on iPhone open the installed Home Screen app.
        </p>
      )}
      <label>
        Push contact email
        <input
          aria-label="Push contact email"
          type="email"
          value={contact.replace(/^mailto:/, "")}
          onChange={(e) => {
            // A delayed first status read must not replace an edited draft.
            contactLoaded.current = true;
            setContact("mailto:" + e.target.value);
          }}
        />
      </label>
      <p>
        This contact is sent to browser push services so their operators can
        reach you about delivery problems.
      </p>
      <button
        className="secondary"
        onClick={() =>
          act(async () => {
            await api("/push/config", "PUT", { contact });
            setMessage("Push contact saved.");
          })
        }
      >
        Save push contact
      </button>
      <label>
        Device name
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={80}
        />
      </label>
      <button
        className="primary"
        disabled={!supported || !status?.contact || !name.trim()}
        onClick={enable}
      >
        Enable notifications on this device
      </button>
      <details><summary>Notification popups</summary>
        <p>When Leam is visible, a small popup offers Open Today or Dismiss. System notifications also remain enabled for background delivery.</p>
        <p>On Android, press and hold a delivered Leam notification, open its notification settings, and enable Alerting or Pop on screen if offered. Check Do Not Disturb too. Names vary by phone. Android and your browser control system banners; Leam cannot force them.</p>
        <a href="https://developer.android.com/develop/ui/compose/notifications/channels" target="_blank" rel="noreferrer">About Android notification controls</a>
      </details>
      {message && <p role="status">{message}</p>}
      {status?.automationHeld && (
        <p role="status">
          {status.automationInvalid
            ? "Push delivery paused: restore marker needs operator review"
            : "Push delivery paused after restore; review in Settings → Backups"}
        </p>
      )}
      {status?.error && (
        <p role="alert">Push worker needs attention: {status.error}</p>
      )}
      {status?.lastCheck && !status.healthy && (
        <p role="status">
          Push worker heartbeat is stale. Check the Leam service.
        </p>
      )}
      {(status?.devices || []).map((device: Data) => (
        <article className="reminder-card" key={device.id}>
          <h4>{device.name}</h4>
          <p>
            {device.state === "active"
              ? "Registered; device delivery requires a test"
              : "Subscription expired; enable this device again"}
          </p>
          <div className="actions">
            <button
              className="secondary"
              disabled={device.state !== "active" || !!status?.automationHeld}
              onClick={() =>
                act(async () => {
                  await api(`/push/devices/${device.id}/test`, "POST", {});
                  setMessage(
                    "Test queued. Service acceptance will appear below; confirm only after it appears on your device.",
                  );
                })
              }
            >
              Send test to {device.name}
            </button>
            <button
              className="danger"
              onClick={() =>
                act(async () => {
                  await api(`/push/devices/${device.id}`, "DELETE");
                  if (supported) {
                    const subscription = await (
                      await navigator.serviceWorker.ready
                    ).pushManager.getSubscription();
                    if (
                      subscription &&
                      (await deviceId(subscription)) === device.id
                    )
                      await subscription.unsubscribe();
                  }
                  setMessage("Device removed.");
                })
              }
            >
              Remove {device.name}
            </button>
          </div>
        </article>
      ))}
      <details>
        <summary>Recent delivery attempts</summary>
        {(status?.deliveries || []).map((delivery: Data) => (
          <article className="reminder-card" key={delivery.id}>
            <p>
              {delivery.kind === "test" ? "Test notification" : "Reminder"} ·{" "}
              {status?.devices?.find((d: Data) => d.id === delivery.deviceId)
                ?.name || "Device"}
            </p>
            <p>
              {delivery.confirmedAt
                ? "You confirmed receipt on the device"
                : delivery.state === "accepted"
                  ? "Accepted by push service; device receipt unconfirmed"
                  : delivery.state}{" "}
              · {new Date(delivery.updated * 1000).toLocaleString()}
            </p>
            {delivery.error && <p>{delivery.error}</p>}
            {delivery.kind === "test" &&
              delivery.state === "accepted" &&
              !delivery.confirmedAt && (
                <button
                  className="secondary"
                  onClick={() =>
                    act(async () => {
                      await api(
                        `/push/deliveries/${delivery.id}/confirm`,
                        "POST",
                        {},
                      );
                    })
                  }
                >
                  I received this test notification
                </button>
              )}
          </article>
        ))}
      </details>
    </fieldset>
  );
}
