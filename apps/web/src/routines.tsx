import { useEffect, useState } from "react";
import { api, type Data } from "./api";
import { EventNotifications, EventRulesPanel } from "./routine-events";

type Props = { fail: (error: unknown) => void };
const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const defaults = () => ({
  title: "",
  message: "",
  time: "18:00",
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/London",
  days: [0, 1, 2, 3, 4, 5, 6],
  enabled: true,
});
function editable(item: Data) {
  return {
    title: item.title,
    message: item.message,
    time: item.time,
    timezone: item.timezone,
    days: item.days,
    enabled: item.enabled,
    revision: item.revision,
  };
}
const pendingKey = "leam-routine-run";
function restoredPending(): Data | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
    return value &&
      typeof value.id === "string" &&
      typeof value.requestId === "string" &&
      Number.isInteger(value.revision)
      ? value
      : null;
  } catch {
    return null;
  }
}
const dateLabel = (epoch: number) => new Date(epoch * 1000).toLocaleString();

export function RoutineInbox({ fail }: Props) {
  const [items, setItems] = useState<Data[]>([]);
  const [busy, setBusy] = useState("");
  const load = () =>
    api("/routines/notifications").then((result) => setItems(result.items));
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api("/routines/notifications");
        if (!stopped) setItems(result.items);
      } catch (error) {
        if (!stopped) fail(error);
      }
      if (!stopped) timer = setTimeout(poll, 15000);
    }
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, []);
  if (!items.length) return <EventNotifications fail={fail} />;
  return (
    <section className="card" aria-label="Routine notifications">
      <EventNotifications fail={fail} />
      <h2>Routine reminders</h2>
      {items.map((item) => (
        <article className="reminder-card" key={item.id}>
          <h3>{item.title}</h3>
          <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {item.message}
          </p>
          <small>{dateLabel(item.dueAt)}</small>
          <div className="actions">
            <button
              className="secondary"
              disabled={!!busy}
              onClick={async () => {
                setBusy(item.id);
                try {
                  await api(
                    `/routines/notifications/${item.id}/dismiss`,
                    "POST",
                    {},
                  );
                  await load();
                } catch (error) {
                  fail(error);
                } finally {
                  setBusy("");
                }
              }}
            >
              Dismiss routine reminder
            </button>
          </div>
        </article>
      ))}
      <small>
        Up to 50 waiting reminders shown; dismiss to reveal more. Delivery is
        in-app.
      </small>
    </section>
  );
}

export function RoutinesPanel({ fail }: Props) {
  const [items, setItems] = useState<Data[]>([]);
  const [runs, setRuns] = useState<Data[]>([]);
  const [status, setStatus] = useState<Data | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [draft, setDraft] = useState<Data>(defaults);
  const [editing, setEditing] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Data | null>(restoredPending);
  const [cursor, setCursor] = useState<number | null>(null);
  async function load() {
    const [list, history] = await Promise.all([
      api("/routines"),
      api("/routines/runs"),
    ]);
    setItems(list.items);
    setRuns(history.items);
    setCursor(history.nextCursor);
  }
  useEffect(() => {
    void load().catch(fail);
  }, []);
  useEffect(() => {
    let stopped = false;
    let pollTimer: ReturnType<typeof setTimeout>;
    const ageTimer = setInterval(() => setNow(Date.now() / 1000), 1000);
    async function pollStatus() {
      try {
        const health = await api("/routines/status");
        if (!stopped) setStatus(health);
      } catch {
        if (!stopped)
          setStatus((previous) => ({
            ...previous,
            healthy: false,
            error: "StatusUnavailable",
          }));
      }
      if (!stopped) pollTimer = setTimeout(pollStatus, 15000);
    }
    void pollStatus();
    return () => {
      stopped = true;
      clearTimeout(pollTimer);
      clearInterval(ageTimer);
    };
  }, []);
  const age = status?.lastCheck == null ? null : now - status.lastCheck;
  const healthy =
    status?.healthy && !status?.error && age !== null && age >= -5 && age < 45;
  async function perform(action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    try {
      await action();
      await load();
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }
  async function run(item: Data) {
    // Retain the exact operation after a lost response; Retry never invents a new identity.
    const operation = pending || {
      id: item.id,
      revision: item.revision,
      requestId: crypto.randomUUID(),
    };
    sessionStorage.setItem(pendingKey, JSON.stringify(operation));
    setPending(operation);
    await perform(async () => {
      await api(`/routines/${operation.id}/run`, "POST", {
        revision: operation.revision,
        requestId: operation.requestId,
      });
      sessionStorage.removeItem(pendingKey);
      setPending(null);
    });
  }
  return (
    <section className="page routines-page" aria-label="Routines">
      <h2>Routines</h2>
      <p>
        A small nudge on your schedule. Routines create saved in-app reminders,
        even while your browser is closed.
      </p>
      <RoutineInbox fail={fail} />
      <details
        className="card routine-editor"
        open={editorOpen}
        onToggle={(event) => setEditorOpen(event.currentTarget.open)}
      >
        <summary>{editing ? "Edit routine" : "New routine"}</summary>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void perform(async () => {
              await api(
                editing ? `/routines/${editing}` : "/routines",
                editing ? "PUT" : "POST",
                draft,
              );
              setEditing("");
              setDraft(defaults());
              setEditorOpen(false);
            });
          }}
        >
          <fieldset
            disabled={busy}
            style={{ border: 0, padding: 0, minWidth: 0 }}
          >
            <label>
              Name
              <input
                required
                maxLength={200}
                value={draft.title}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              />
            </label>
            <label>
              Reminder message
              <textarea
                required
                maxLength={2000}
                value={draft.message}
                onChange={(e) =>
                  setDraft({ ...draft, message: e.target.value })
                }
              />
            </label>
            <label>
              Local time
              <input
                type="time"
                required
                value={draft.time}
                onChange={(e) => setDraft({ ...draft, time: e.target.value })}
              />
            </label>
            <label>
              Timezone
              <input
                required
                value={draft.timezone}
                placeholder="Europe/London"
                onChange={(e) =>
                  setDraft({ ...draft, timezone: e.target.value })
                }
              />
            </label>
            <fieldset>
              <legend>Repeat on</legend>
              <div className="actions">
                {weekdays.map((name, day) => (
                  <label
                    key={day}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                    }}
                  >
                    <input
                      style={{ width: "auto" }}
                      type="checkbox"
                      checked={draft.days.includes(day)}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          days: e.target.checked
                            ? [...draft.days, day].sort()
                            : draft.days.filter(
                                (value: number) => value !== day,
                              ),
                        })
                      }
                    />
                    {name}
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="actions">
              <button className="primary" disabled={!draft.days.length}>
                {editing ? "Save routine" : "Create routine"}
              </button>
              {editing && (
                <button
                  type="button"
                  className="secondary"
                  onClick={() => {
                    setEditing("");
                    setDraft(defaults());
                    setEditorOpen(false);
                  }}
                >
                  Cancel edit
                </button>
              )}
            </div>
          </fieldset>
        </form>
        <p>
          <small>
            Edits and resuming start from the next scheduled time.
            Daylight-saving gaps use the first valid minute; repeated hours run
            once. After downtime, only the latest occurrence is considered, and
            reminders over 24 hours old expire.
          </small>
        </p>
      </details>
      {pending && (
        <div className="card" role="status">
          <p>
            A manual run is awaiting confirmation. Retry safely with the same
            request.
          </p>
          <button
            className="primary"
            disabled={busy}
            onClick={() => void run(pending)}
          >
            Retry pending run
          </button>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => {
              if (
                !window.confirm(
                  "Forget this pending request? Check run history before running again; it may already have delivered.",
                )
              )
                return;
              sessionStorage.removeItem(pendingKey);
              setPending(null);
            }}
          >
            Forget pending request
          </button>
        </div>
      )}
      {items.length === 0 && <p>No routines yet.</p>}
      {items.map((item) => (
        <article className="card" key={item.id}>
          <h3 style={{ overflowWrap: "anywhere" }}>{item.title}</h3>
          <p>
            {item.days.map((day: number) => weekdays[day]).join(", ")} at{" "}
            {item.time} · {item.timezone}
          </p>
          <p style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>
            {item.message}
          </p>
          <small>
            {item.enabled ? `Next: ${dateLabel(item.nextDueAt)}` : "Paused"}
          </small>
          <div className="actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => {
                setEditing(item.id);
                setDraft(editable(item));
                setEditorOpen(true);
                requestAnimationFrame(() =>
                  document
                    .querySelector(".routine-editor")
                    ?.scrollIntoView({ block: "start", behavior: "smooth" }),
                );
              }}
            >
              Edit routine
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={() =>
                void perform(() =>
                  api(`/routines/${item.id}`, "PUT", {
                    ...editable(item),
                    enabled: !item.enabled,
                  }),
                )
              }
            >
              {item.enabled ? "Pause routine" : "Resume routine"}
            </button>
            <button
              className="primary"
              disabled={busy || !!pending}
              onClick={() => void run(item)}
            >
              Run now
            </button>
          </div>
        </article>
      ))}
      <section className="card">
        <h3>Run history</h3>
        <p role="status">
          {healthy
            ? "Scheduler running"
            : status?.error
              ? "Scheduler needs attention"
              : age !== null
                ? "Scheduler heartbeat is stale; check the Leam service"
                : "Waiting for scheduler heartbeat"}
          {status?.lastCheck
            ? ` · Last check ${dateLabel(status.lastCheck)}`
            : ""}
        </p>
        <button
          className="secondary"
          disabled={busy}
          onClick={() => void perform(() => api("/routines/check", "POST", {}))}
        >
          Check schedules and refresh
        </button>
        {runs.length === 0 && <p>No runs yet.</p>}
        {runs.map((item) => (
          <article className="reminder-card" key={item.id}>
            <strong>{item.title}</strong>
            <p>
              {item.trigger === "manual" ? "Run now" : "Scheduled"} ·{" "}
              {dateLabel(item.dueAt)} ·{" "}
              {item.state === "expired"
                ? "Expired during downtime"
                : item.dismissedAt
                  ? "Dismissed"
                  : "Reminder delivered in-app"}
            </p>
          </article>
        ))}
        {cursor && (
          <button
            className="secondary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const result = await api(`/routines/runs?before=${cursor}`);
                setRuns([...runs, ...result.items]);
                setCursor(result.nextCursor);
              } catch (error) {
                fail(error);
              } finally {
                setBusy(false);
              }
            }}
          >
            Older runs
          </button>
        )}
      </section>
      <EventRulesPanel fail={fail} />
    </section>
  );
}
