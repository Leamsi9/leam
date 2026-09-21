import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";

type Props = { changed: () => Promise<void> };
/** Visibility is a Leam preference for one exact calendar/provider event ID. */
export function HideCalendarEvent({ item, changed }: Props & { item: Data }) {
  const active = useRef(true),
    pending = useRef(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(
    () => () => {
      active.current = false;
    },
    [],
  );
  async function hide() {
    if (pending.current || !Number.isInteger(item.visibility?.revision)) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await api("/agenda/calendar-visibility", "PUT", {
        key: item.key,
        revision: item.visibility.revision,
        hidden: true,
      });
      if (active.current) await changed();
    } catch (e) {
      if (active.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      pending.current = false;
      if (active.current) setBusy(false);
    }
  }
  return (
    <div className="calendar-visibility-action">
      <button
        className="secondary"
        disabled={busy || !Number.isInteger(item.visibility?.revision)}
        onClick={() => void hide()}
        title="Hide this calendar entry from Leam across all days; undo under Hidden calendar events"
      >
        {busy ? "Hiding…" : "Hide always"}
      </button>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
export function HiddenCalendarEvents({
  changed,
  version = 0,
}: Props & { version?: number }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="agenda-hidden-calendars"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>Hidden calendar events</summary>
      {open && <HiddenList changed={changed} version={version} />}
    </details>
  );
}
function HiddenList({ changed, version }: Props & { version: number }) {
  const [rows, setRows] = useState<Data[]>([]),
    [next, setNext] = useState<number | null>(null);
  const [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState("");
  const active = useRef(true),
    sequence = useRef(0),
    writing = useRef(false);
  async function load(more = false) {
    const n = ++sequence.current;
    setLoading(true);
    setError("");
    try {
      const result = await api(
        `/agenda/calendar-visibility?offset=${more ? next : 0}&limit=50`,
      );
      if (!active.current || n !== sequence.current) return;
      setRows((previous) =>
        more
          ? [
              ...new Map(
                [...previous, ...result.items].map((item) => [item.key, item]),
              ).values(),
            ]
          : result.items,
      );
      setNext(result.nextOffset ?? null);
    } catch (e) {
      if (active.current && n === sequence.current)
        setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (active.current && n === sequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    active.current = true;
    void load();
    return () => {
      active.current = false;
      sequence.current++;
    };
  }, [version]);
  async function restore(item: Data) {
    if (writing.current) return;
    writing.current = true;
    setBusy(item.key);
    setError("");
    try {
      await api("/agenda/calendar-visibility", "PUT", {
        key: item.key,
        revision: item.revision,
        hidden: false,
      });
      if (active.current) {
        await load();
        await changed();
      }
    } catch (e) {
      if (active.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      writing.current = false;
      if (active.current) setBusy("");
    }
  }
  return (
    <div>
      <p className="agenda-hint">
        Hide always applies to one calendar entry across all Leam dates. It does
        not delete the event or hide its whole recurring series. Show again
        removes this preference even if the entry is no longer in a saved
        calendar snapshot.
      </p>
      <button
        className="secondary"
        disabled={loading || !!busy}
        onClick={() => void load()}
      >
        Refresh hidden events
      </button>
      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">Loading hidden events…</p>}
      {!loading && !rows.length && !error && (
        <p>No calendar events are hidden.</p>
      )}
      {rows.map((item) => (
        <article className="card agenda-event" key={item.key}>
          <h3>{item.title || "Calendar entry"}</h3>
          <small>
            {item.calendarName}
            {!item.sourceAvailable &&
              " · No longer in the saved calendar snapshot"}
          </small>
          <button
            className="secondary"
            disabled={!!busy || loading}
            onClick={() => void restore(item)}
          >
            {busy === item.key ? "Restoring…" : "Show again"}
          </button>
        </article>
      ))}
      {next !== null && (
        <button
          className="secondary"
          disabled={loading || !!busy}
          onClick={() => void load(true)}
        >
          Load more hidden events
        </button>
      )}
    </div>
  );
}
