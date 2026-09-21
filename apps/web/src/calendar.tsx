import { useEffect, useState, useRef } from "react";
import { api, type Data } from "./api";
import { CalendarActions } from "./calendar-actions";

type Props = { fail: (error: unknown) => void };
function day(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}
function displayDate(value: string) {
  return new Date(value + "T12:00:00").toLocaleDateString();
}
function allDayRange(start: string, end: string) {
  const last = new Date(end + "T12:00:00");
  last.setDate(last.getDate() - 1);
  return (
    displayDate(start) +
    (day(last) === start ? "" : " – " + displayDate(day(last)))
  );
}
export function CalendarView({ fail }: Props) {
  const [listing, setListing] = useState<Data>({ items: [], accounts: [] }),
    [selected, setSelected] = useState(""),
    [busy, setBusy] = useState("");
  const load = async () => {
    const result = await api("/calendar");
    setListing(result);
    setSelected((current) =>
      result.items.some((c: Data) => c.id === current)
        ? current
        : result.items[0]?.id || "",
    );
  };
  useEffect(() => {
    load().catch(fail);
  }, []);
  return (
    <section className="page">
      <span className="eyebrow">TIME FOR WHAT MATTERS</span>
      <h1>Calendar</h1>
      <p>
        See your saved calendar view and refresh it from the source. Last
        successful sync stays visible if a provider is unavailable.
      </p>
      {listing.accounts.length === 0 && (
        <div className="card">
          <h3>Connect a calendar</h3>
          <p>
            Add your Google or Microsoft account in Settings, then return here
            to load its calendars.
          </p>
        </div>
      )}
      {listing.accounts.map((account: Data) => (
        <div className="card" key={account.id}>
          <h3>{account.identity}</h3>
          <p>
            {account.provider === "google" ? "Google" : "Microsoft"} · Calendar
            list{" "}
            {account.calendarsListedAt
              ? "checked " +
                new Date(account.calendarsListedAt * 1000).toLocaleString()
              : "not loaded yet"}
          </p>
          {account.state === "reconnect" && (
            <p>Reconnect this account in Settings.</p>
          )}
          {account.error && <p role="alert">{account.error}</p>}
          <button
            className="secondary"
            disabled={!!busy || account.state === "reconnect"}
            onClick={async () => {
              setBusy(account.id);
              try {
                await api(`/calendar/accounts/${account.id}/sync`, "POST", {});
              } catch (e) {
                fail(e);
              } finally {
                await load().catch(fail);
                setBusy("");
              }
            }}
          >
            {busy === account.id
              ? "Loading calendars…"
              : "Refresh calendar list"}
          </button>
        </div>
      ))}
      {listing.items.length > 0 && (
        <label>
          Calendar
          <select
            aria-label="Calendar"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            {listing.items.map((c: Data) => (
              <option key={c.id} value={c.id}>
                {c.name} · {c.identity}
              </option>
            ))}
          </select>
        </label>
      )}
      {selected && <CalendarEvents key={selected} id={selected} fail={fail} />}
      {selected && (
        <CalendarActions
          key={"actions:" + selected}
          calendar={listing.items.find((c: Data) => c.id === selected)}
          fail={fail}
        />
      )}
    </section>
  );
}
function CalendarEvents({ id, fail }: Props & { id: string }) {
  const generation = useRef(0);
  const [snapshot, setSnapshot] = useState<Data | null>(null),
    [busy, setBusy] = useState(false),
    [start, setStart] = useState(day(new Date())),
    [end, setEnd] = useState(day(new Date(Date.now() + 6 * 86400000)));
  useEffect(() => {
    const operation = ++generation.current;
    let stopped = false;
    api(`/calendar/${id}`)
      .then((r) => {
        if (!stopped && operation === generation.current) setSnapshot(r);
      })
      .catch((e) => {
        if (!stopped && operation === generation.current) fail(e);
      });
    return () => {
      stopped = true;
      generation.current++;
    };
  }, [id]);
  async function refresh() {
    const operation = ++generation.current;
    setBusy(true);
    try {
      const first = new Date(start + "T00:00:00"),
        last = new Date(end + "T00:00:00");
      last.setDate(last.getDate() + 1);
      const result = await api(`/calendar/${id}/sync`, "POST", {
        start: first.toISOString(),
        end: last.toISOString(),
      });
      if (operation === generation.current) setSnapshot(result);
    } catch (e) {
      if (operation !== generation.current) return;
      fail(e);
      await api(`/calendar/${id}`)
        .then((r) => {
          if (operation === generation.current) setSnapshot(r);
        })
        .catch((e) => {
          if (operation === generation.current) fail(e);
        });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="card settings-form">
      <h3>{snapshot?.name || "Calendar view"}</h3>
      <fieldset disabled={busy} className="calendar-window">
        <label>
          First day
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label>
          Last day
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
        <button className="primary" onClick={refresh} disabled={!start || !end}>
          {busy ? "Syncing…" : "Sync this date range"}
        </button>
      </fieldset>
      {snapshot?.syncedAt ? (
        <p>
          Last successful sync:{" "}
          {new Date(snapshot.syncedAt * 1000).toLocaleString()}. Saved view:{" "}
          {new Date(snapshot.start).toLocaleString()} to{" "}
          {new Date(snapshot.end).toLocaleString()}.
        </p>
      ) : (
        <p>No calendar events have been synchronized yet.</p>
      )}
      {snapshot?.error && <p role="alert">{snapshot.error}</p>}
      {snapshot?.syncedAt && snapshot.events.length === 0 && (
        <p>No events in the saved date range.</p>
      )}
      {(snapshot?.events || []).map((event: Data) => (
        <article className="reminder-card" key={event.id}>
          <h4>{event.title}</h4>
          <p>
            {event.allDay
              ? `All day · ${allDayRange(event.start, event.end)}`
              : `${new Date(event.start).toLocaleString()} – ${new Date(event.end).toLocaleString()}`}
          </p>
          <small>
            {snapshot?.identity} · {snapshot?.name}
          </small>
          {event.url && (
            <p>
              <a href={event.url} target="_blank" rel="noreferrer">
                Open in{" "}
                {snapshot?.provider === "google"
                  ? "Google Calendar"
                  : "Outlook"}
              </a>
            </p>
          )}
        </article>
      ))}
    </div>
  );
}
