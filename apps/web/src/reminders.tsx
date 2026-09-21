import { useEffect, useState } from "react";
import { api, type Data } from "./api";

type Props = { fail: (error: unknown) => void; changed?: () => Promise<void> };
export function ReminderInbox({ fail, changed }: Props) {
  const [items, setItems] = useState<Data[]>([]),
    [busy, setBusy] = useState(""),
    [minutes, setMinutes] = useState(10);
  const load = () => api("/notifications").then((r) => setItems(r.items || []));
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        await load();
      } catch (e) {
        if (!stopped) fail(e);
      }
      if (!stopped) timer = setTimeout(poll, 15000);
    }
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, []);
  async function act(item: Data, action: string) {
    if (busy) return;
    setBusy(item.id);
    try {
      await api(`/notifications/${item.id}/${action}`, "POST", {
        revision: item.revision,
        ...(action === "snooze" ? { minutes } : {}),
      });
      await changed?.();
    } catch (e) {
      fail(e);
    } finally {
      await load().catch(fail);
      setBusy("");
    }
  }
  return (
    <details className="card reminder-inbox" open={items.length > 0}>
      <summary>Reminders · {items.length} waiting</summary>
      <p>
        Reminders are saved on your Leam host. Manage phone push delivery and
        device tests in Settings.
      </p>
      <div className="actions">
        <button
          className="secondary"
          onClick={async () => {
            try {
              await api("/notifications/check", "POST", {});
              await load();
            } catch (e) {
              fail(e);
            }
          }}
        >
          Check reminders
        </button>
        <label>
          Snooze duration
          <select
            aria-label="Snooze duration"
            value={minutes}
            onChange={(e) => setMinutes(Number(e.target.value))}
          >
            <option value={10}>10 minutes</option>
            <option value={30}>30 minutes</option>
            <option value={60}>1 hour</option>
          </select>
        </label>
      </div>
      {items.map((item) => (
        <article className="reminder-card" key={item.id}>
          <h3>{item.title}</h3>
          <small>
            {item.date} · Due {new Date(item.dueAt * 1000).toLocaleTimeString()}
          </small>
          <div className="actions">
            <button
              className="primary"
              disabled={!!busy}
              onClick={() => act(item, "complete")}
            >
              Mark done
            </button>
            <button
              className="secondary"
              disabled={!!busy}
              onClick={() => act(item, "snooze")}
            >
              Snooze
            </button>
            <button
              className="secondary"
              disabled={!!busy}
              onClick={() => act(item, "dismiss")}
            >
              Dismiss reminder
            </button>
          </div>
        </article>
      ))}
    </details>
  );
}

export function ReminderSettings({ fail }: Props) {
  const [status, setStatus] = useState<Data | null>(null);
  const load = () => api("/notifications/status").then(setStatus).catch(fail);
  useEffect(() => {
    load();
  }, []);
  return (
    <div className="card">
      <h3>Reminder delivery</h3>
      <p>
        In-app reminders continue while your browser is closed and catch up for
        the current day after a service restart. Phone push delivery is a
        separate connection.
      </p>
      {status && (
        <p role="status">
          {status.error
            ? "Scheduler needs attention: " + status.error
            : status.healthy
              ? "Scheduler running"
              : status.lastCheck
                ? "Scheduler heartbeat is stale; check the Leam service"
                : "Scheduler is starting; waiting for its first check"}{" "}
          · Last check{" "}
          {status.lastCheck
            ? new Date(status.lastCheck * 1000).toLocaleString()
            : "waiting"}
        </p>
      )}
      <button className="secondary" onClick={load}>
        Refresh reminder status
      </button>
    </div>
  );
}
