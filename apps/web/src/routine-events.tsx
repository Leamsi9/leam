import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";

type Props = { fail: (error: unknown) => void };
const changedEvent = "leam:routine-event-input";
const changed = () => window.dispatchEvent(new Event(changedEvent));
const when = (epoch: number) => new Date(epoch * 1000).toLocaleString();
const defaults = () => ({
  title: "",
  message: "",
  source: "manual",
  eventType: "check-in",
  cooldownSeconds: 60,
  enabled: true,
  action: "notification",
});
const ruleBody = (item: Data) => ({
  title: item.title,
  message: item.message,
  source: item.source,
  eventType: item.eventType,
  cooldownSeconds: item.cooldownSeconds,
  enabled: item.enabled,
  action: "notification",
  match: item.match || null,
});
const pendingKey = "leam-event-input-v1";
function restored() {
  try {
    const value = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
    return value?.version === 1 && typeof value?.requestId === "string"
      ? value
      : null;
  } catch {
    return null;
  }
}

export function EventNotifications({ fail }: Props) {
  const [items, setItems] = useState<Data[]>([]),
    [busy, setBusy] = useState("");
  const refresh = useRef<() => void>(() => {});
  useEffect(() => {
    let stopped = false,
      sequence = 0;
    async function load() {
      const request = ++sequence;
      try {
        const result = await api("/routine-event-notifications");
        if (!stopped && request === sequence) setItems(result.items);
      } catch (error) {
        if (!stopped) fail(error);
      }
    }
    refresh.current = () => {
      void load();
    };
    void load();
    const timer = setInterval(() => void load(), 15000);
    window.addEventListener(changedEvent, refresh.current);
    return () => {
      stopped = true;
      clearInterval(timer);
      window.removeEventListener(changedEvent, refresh.current);
    };
  }, []);
  if (!items.length) return null;
  return (
    <section className="card" aria-label="Event reminders">
      <h3>Event reminders</h3>
      {items.map((item) => (
        <article className="reminder-card" key={item.id}>
          <strong>{item.title}</strong>
          <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {item.message}
          </p>
          <small>
            {item.source} · {item.eventType} · {when(item.createdAt)}
          </small>
          <button
            className="secondary"
            disabled={!!busy}
            onClick={async () => {
              setBusy(item.id);
              try {
                await api(
                  `/routine-event-notifications/${item.id}/dismiss`,
                  "POST",
                  {},
                );
                refresh.current();
              } catch (error) {
                fail(error);
              } finally {
                setBusy("");
              }
            }}
          >
            Dismiss event reminder
          </button>
        </article>
      ))}
      <small>Up to 50 waiting reminders shown. Dismiss to reveal more.</small>
    </section>
  );
}

export function EventRulesPanel({ fail }: Props) {
  const [rules, setRules] = useState<Data[]>([]),
    [events, setEvents] = useState<Data[]>([]),
    [next, setNext] = useState<number | null>(null);
  const [draft, setDraft] = useState<Data>(defaults),
    [editing, setEditing] = useState<Data | null>(null),
    [open, setOpen] = useState(false);
  const [field, setField] = useState(""),
    [equals, setEquals] = useState('"home"');
  const [source, setSource] = useState("manual"),
    [type, setType] = useState("check-in"),
    [attributes, setAttributes] = useState("{}");
  const [pending, setPending] = useState<Data | null>(restored),
    [receipt, setReceipt] = useState<Data | null>(null),
    [busy, setBusy] = useState(false);
  const working = useRef(false),
    attempt = useRef<Data | null>(pending);
  async function load() {
    const [r, e] = await Promise.all([
      api("/routine-event-rules"),
      api("/routine-events"),
    ]);
    setRules(r.items);
    setEvents(e.items);
    setNext(e.nextOffset);
  }
  useEffect(() => {
    void load().catch(fail);
  }, []);
  async function perform(action: () => Promise<void>) {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    try {
      await action();
      await load();
      changed();
    } catch (error) {
      fail(error);
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  function edit(item: Data) {
    setEditing(item);
    setDraft(ruleBody(item));
    setField(item.match?.field || "");
    setEquals(JSON.stringify(item.match ? item.match.equals : "home"));
    setOpen(true);
  }
  async function submitEvent() {
    await perform(async () => {
      attempt.current ||= {
        version: 1,
        requestId: crypto.randomUUID(),
        source,
        type,
        occurredAt: new Date().toISOString(),
        attributes: JSON.parse(attributes),
      };
      sessionStorage.setItem(pendingKey, JSON.stringify(attempt.current));
      setPending(attempt.current);
      const accepted = await api("/routine-events", "POST", attempt.current);
      setReceipt(accepted);
      sessionStorage.removeItem(pendingKey);
      attempt.current = null;
      setPending(null);
    });
  }
  return (
    <section className="event-rules" aria-label="Event-based routines">
      <h2>Event-based routines</h2>
      <p>
        Respond to a named event with an in-app reminder. Inputs and rules are
        saved; no model or connected account is needed.
      </p>
      <details
        className="card"
        open={open}
        onToggle={(e) => setOpen(e.currentTarget.open)}
      >
        <summary>{editing ? "Edit event rule" : "New event rule"}</summary>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void perform(async () => {
              const body = {
                ...ruleBody(draft),
                match: field ? { field, equals: JSON.parse(equals) } : null,
                ...(editing ? { revision: editing.revision } : {}),
              };
              await api(
                editing
                  ? `/routine-event-rules/${editing.id}`
                  : "/routine-event-rules",
                editing ? "PUT" : "POST",
                body,
              );
              setDraft(defaults());
              setEditing(null);
              setField("");
              setOpen(false);
            });
          }}
        >
          <fieldset
            disabled={busy}
            style={{ border: 0, padding: 0, minWidth: 0 }}
          >
            <label>
              Reminder title
              <input
                required
                maxLength={200}
                value={draft.title}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              />
            </label>
            <label>
              Reminder text
              <textarea
                aria-label="Reminder text"
                required
                maxLength={2000}
                value={draft.message}
                onChange={(e) =>
                  setDraft({ ...draft, message: e.target.value })
                }
              />
            </label>
            <label>
              Event source
              <input
                required
                maxLength={80}
                pattern={String.raw`[A-Za-z0-9][A-Za-z0-9_.:\-]*`}
                value={draft.source}
                onChange={(e) => setDraft({ ...draft, source: e.target.value })}
              />
            </label>
            <label>
              Event type
              <input
                required
                maxLength={80}
                pattern={String.raw`[A-Za-z0-9][A-Za-z0-9_.:\-]*`}
                value={draft.eventType}
                onChange={(e) =>
                  setDraft({ ...draft, eventType: e.target.value })
                }
              />
            </label>
            <label>
              Minimum seconds between reminders
              <input
                type="number"
                required
                min={0}
                max={86400}
                value={draft.cooldownSeconds}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    cooldownSeconds: Number(e.target.value),
                  })
                }
              />
            </label>
            <details>
              <summary>Optional exact field match</summary>
              <label>
                Attribute name
                <input
                  maxLength={48}
                  value={field}
                  onChange={(e) => setField(e.target.value)}
                />
              </label>
              <label>
                Exact JSON value
                <input
                  value={equals}
                  onChange={(e) => setEquals(e.target.value)}
                />
              </label>
              <small>
                For example: "home", true, or 3. No nested objects, expressions
                or wildcards.
              </small>
            </details>
            <button className="primary">
              {editing ? "Save event rule" : "Create event rule"}
            </button>
            {editing && (
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setEditing(null);
                  setDraft(defaults());
                  setField("");
                  setOpen(false);
                }}
              >
                Cancel rule edit
              </button>
            )}
          </fieldset>
        </form>
      </details>
      {!rules.length && <p>No event rules yet.</p>}
      {rules.map((item) => (
        <article className="card" key={item.id}>
          <h3>{item.title}</h3>
          <p>{item.message}</p>
          <small>
            {item.source} · {item.eventType} ·{" "}
            {item.enabled ? "Active" : "Paused"} · {item.cooldownSeconds}s
            minimum interval
          </small>
          {item.match && (
            <p>
              <code>
                {item.match.field} = {JSON.stringify(item.match.equals)}
              </code>
            </p>
          )}
          <div className="actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => edit(item)}
            >
              Edit event rule
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  await api(`/routine-event-rules/${item.id}`, "PUT", {
                    ...ruleBody(item),
                    enabled: !item.enabled,
                    revision: item.revision,
                  });
                })
              }
            >
              {item.enabled ? "Pause event rule" : "Resume event rule"}
            </button>
          </div>
        </article>
      ))}
      <details className="card">
        <summary>Send a test event</summary>
        <p>
          This is a real saved input and can create matching reminders. Source
          is your declared label, not verified device evidence.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submitEvent();
          }}
        >
          <fieldset
            disabled={busy || !!pending}
            style={{ border: 0, padding: 0, minWidth: 0 }}
          >
            <label>
              Input source
              <input
                required
                maxLength={80}
                value={source}
                onChange={(e) => setSource(e.target.value)}
              />
            </label>
            <label>
              Input type
              <input
                required
                maxLength={80}
                value={type}
                onChange={(e) => setType(e.target.value)}
              />
            </label>
            <label>
              Attributes JSON
              <textarea
                aria-label="Attributes JSON"
                required
                value={attributes}
                maxLength={8192}
                onChange={(e) => setAttributes(e.target.value)}
              />
            </label>
          </fieldset>
          <button className="primary" disabled={busy}>
            {pending ? "Retry same event" : "Send event"}
          </button>
        </form>
        {pending && (
          <div className="notice">
            <p>
              A saved input awaits confirmation. Retry retains its original
              time, attributes and request ID.
            </p>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => {
                if (
                  !window.confirm(
                    "Check input history first. Forgetting this draft cannot undo an accepted event and does not resend it.",
                  )
                )
                  return;
                sessionStorage.removeItem(pendingKey);
                attempt.current = null;
                setPending(null);
              }}
            >
              Forget pending input
            </button>
          </div>
        )}
        {receipt && (
          <p role="status">
            Input saved · {receipt.deliveries.length} reminder(s) created ·{" "}
            {receipt.suppressed.length} rule(s) cooling down
          </p>
        )}
      </details>
      <details className="card">
        <summary>Recent event inputs · {events.length} shown</summary>
        <button
          className="secondary"
          disabled={busy}
          onClick={() => void perform(async () => {})}
        >
          Refresh event inputs
        </button>
        {events.map((item) => (
          <details key={item.id} className="event-input">
            <summary>
              {item.source} · {item.type} · {when(item.receivedAt)}
            </summary>
            <small>
              Owner session · received {when(item.receivedAt)} · occurred{" "}
              {new Date(item.occurredAt).toLocaleString()}
            </small>
            <p>
              {item.receipt.deliveries.length} reminder(s);{" "}
              {item.receipt.suppressed.length} cooling down
            </p>
            <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {JSON.stringify(
                {
                  requestId: item.requestId,
                  attributes: item.attributes,
                  provenance: item.provenance,
                  receipt: item.receipt,
                },
                null,
                2,
              )}
            </pre>
          </details>
        ))}
        {next !== null && (
          <button
            className="secondary"
            disabled={busy}
            onClick={async () => {
              if (working.current) return;
              working.current = true;
              setBusy(true);
              try {
                const result = await api(`/routine-events?offset=${next}`);
                setEvents((previous) => [
                  ...new Map(
                    [...previous, ...result.items].map((item) => [
                      item.id,
                      item,
                    ]),
                  ).values(),
                ]);
                setNext(result.nextOffset);
              } catch (error) {
                fail(error);
              } finally {
                working.current = false;
                setBusy(false);
              }
            }}
          >
            Earlier event inputs
          </button>
        )}
        <small>
          Latest 1,000 input records retained. Exact delivery receipts remain
          for retry protection. Refresh if new inputs arrive while browsing.
        </small>
      </details>
      <details>
        <summary>Integration and limits</summary>
        <p>
          Version 1 accepts a source, type, timezone-aware occurredAt, UUID
          requestId and up to 16 scalar attributes. POST /api/routine-events
          uses your authenticated Leam session and trusted Origin; there is no
          broad bearer-token bypass. Source claims are not independently
          verified. Sensors and dedicated connector credentials are deferred.
        </p>
        <p>
          Rules only create saved in-app reminders. At most 50 rules, 60 new
          events per minute and 1,000 waiting reminders. Retry an uncertain
          input with its unchanged body and request ID.
        </p>
      </details>
    </section>
  );
}
