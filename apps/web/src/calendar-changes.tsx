import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Data } from "./api";

export function CalendarEventManager({
  creationId,
  fail,
}: {
  creationId: string;
  fail: (e: unknown) => void;
}) {
  const key = "leam-calendar-change:" + creationId;
  const [current, setCurrent] = useState<Data | null>(null),
    [edit, setEdit] = useState<Data | null>(null),
    [review, setReview] = useState<Data | null>(null),
    [changes, setChanges] = useState<Data[]>([]),
    [busy, setBusy] = useState(false),
    [confirmed, setConfirmed] = useState(false),
    [loaded, setLoaded] = useState(false);
  const mounted = useRef(true);
  function local() {
    try {
      return JSON.parse(sessionStorage.getItem(key) || "null");
    } catch {
      return null;
    }
  }
  async function load() {
    const result = await api(`/calendar/actions/${creationId}/changes`);
    const pending = local();
    if (mounted.current) {
      setLoaded(true);
      setChanges([
        ...result.items,
        ...(pending &&
        !result.items.some((a: Data) => a.requestId === pending.requestId)
          ? [
              {
                requestId: pending.requestId,
                request: pending,
                state: "pending",
              },
            ]
          : []),
      ]);
    }
  }
  useEffect(() => {
    mounted.current = true;
    load().catch(fail);
    return () => {
      mounted.current = false;
    };
  }, []);
  async function inspect() {
    setBusy(true);
    setReview(null);
    try {
      const r = await api(`/calendar/actions/${creationId}/event`);
      if (mounted.current) {
        setCurrent(r);
        setEdit(r.edit);
      }
    } catch (e) {
      if (mounted.current) fail(e);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  async function preview(operation: string) {
    setBusy(true);
    setConfirmed(false);
    try {
      const r = await api("/calendar/changes/preview", "POST", {
        requestId: crypto.randomUUID(),
        creationId,
        operation,
        expectedEtag: current?.event.etag,
        ...(operation === "edit" ? { edit } : {}),
      });
      if (mounted.current) setReview(r);
    } catch (e) {
      if (mounted.current) fail(e);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  async function send(request: Data) {
    if (busy) return;
    setBusy(true);
    setConfirmed(false);
    try {
      sessionStorage.setItem(key, JSON.stringify(request));
      setReview(null);
      setChanges((items) => [
        ...items.filter((a) => a.requestId !== request.requestId),
        { requestId: request.requestId, request, state: "pending" },
      ]);
      await api("/calendar/changes", "POST", request);
      sessionStorage.removeItem(key);
      if (mounted.current) {
        setConfirmed(true);
        setCurrent(null);
        setEdit(null);
      }
    } catch (e) {
      if (e instanceof ApiError && e.actionReserved === "no") {
        sessionStorage.removeItem(key);
        if (mounted.current) {
          setCurrent(null);
          setEdit(null);
          setChanges((items) =>
            items.filter((a) => a.requestId !== request.requestId),
          );
        }
      }
      if (mounted.current) fail(e);
    } finally {
      if (mounted.current) {
        await load().catch(fail);
        setBusy(false);
      }
    }
  }
  const pending = changes.filter((a) => a.state === "pending");
  const change = (field: string, value: unknown) =>
    setEdit((e) => ({ ...e, [field]: value }));
  return (
    <div className="settings-form">
      <button
        className="secondary"
        disabled={busy || !loaded || pending.length > 0}
        onClick={inspect}
      >
        Inspect event
      </button>
      {confirmed && <p role="status">Event change confirmed</p>}
      {pending.map((action) => (
        <div key={action.requestId}>
          <p>
            Event {action.request.operation} is unconfirmed. Recover this change
            before another edit.
          </p>
          {action.error && <p role="alert">{action.error}</p>}
          <button
            className="secondary"
            disabled={busy}
            onClick={() => send(action.request)}
          >
            Recover event change
          </button>
        </div>
      ))}
      {changes
        .filter((a) => a.state === "conflict")
        .slice(0, 3)
        .map((a) => (
          <p role="alert" key={a.requestId}>
            {a.error}
          </p>
        ))}
      {current && (
        <>
          <p>
            Provider checked{" "}
            {new Date(current.checkedAt * 1000).toLocaleString()}.
          </p>
          {current.deleted ? (
            <p>This event has been removed from the provider calendar.</p>
          ) : (
            <>
              <h4>{current.event.title}</h4>
              <p>
                {new Date(current.event.start).toLocaleString()} –{" "}
                {new Date(current.event.end).toLocaleString()}
              </p>
              {current.event.url && (
                <a href={current.event.url} target="_blank" rel="noreferrer">
                  Open provider event
                </a>
              )}
              {current.editBlocked ? (
                <p>{current.editBlocked}</p>
              ) : (
                edit && (
                  <fieldset
                    className="settings-form"
                    disabled={busy || !loaded || pending.length > 0 || !!review}
                  >
                    <label>
                      Event title
                      <input
                        value={edit.title}
                        onChange={(e) => change("title", e.target.value)}
                      />
                    </label>
                    <label>
                      Edited event date
                      <input
                        type="date"
                        value={edit.date}
                        onChange={(e) => change("date", e.target.value)}
                      />
                    </label>
                    <label>
                      Edited start time
                      <input
                        type="time"
                        value={edit.time}
                        onChange={(e) => change("time", e.target.value)}
                      />
                    </label>
                    <label>
                      Edited timezone
                      <input
                        value={edit.timezone}
                        onChange={(e) => change("timezone", e.target.value)}
                      />
                    </label>
                    <label>
                      Edited duration in minutes
                      <input
                        type="number"
                        min="1"
                        max="1440"
                        value={edit.minutes}
                        onChange={(e) =>
                          change("minutes", Number(e.target.value))
                        }
                      />
                    </label>
                    <label>
                      If the edited time repeats
                      <select
                        value={edit.fold}
                        onChange={(e) => change("fold", Number(e.target.value))}
                      >
                        <option value="0">First occurrence</option>
                        <option value="1">Second occurrence</option>
                      </select>
                    </label>
                    <div className="actions">
                      <button
                        className="primary"
                        disabled={
                          !edit.title ||
                          !edit.date ||
                          !edit.time ||
                          edit.minutes < 1 ||
                          edit.minutes > 1440
                        }
                        onClick={() => preview("edit")}
                      >
                        Preview event edit
                      </button>
                      <button
                        className="secondary"
                        onClick={() => preview("delete")}
                      >
                        Review event removal
                      </button>
                    </div>
                  </fieldset>
                )
              )}
            </>
          )}
        </>
      )}
      {review && (
        <div className="reminder-card">
          <h4>
            {review.request.operation === "delete"
              ? "Remove this event?"
              : "Review event edit"}
          </h4>
          <p>
            Current: {review.before.title} ·{" "}
            {new Date(review.before.start).toLocaleString()} –{" "}
            {new Date(review.before.end).toLocaleString()}
          </p>
          {review.after && (
            <p>
              New: {review.after.title} · {review.after.localStart} –{" "}
              {review.after.localEnd} ({review.after.timezone})
            </p>
          )}
          <p>No invitations will be sent.</p>
          <div className="actions">
            <button
              className="primary"
              disabled={busy || !loaded || pending.length > 0}
              onClick={() => send(review.request)}
            >
              {review.request.operation === "delete"
                ? "Confirm event removal"
                : "Confirm event edit"}
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setReview(null)}
            >
              Cancel event change
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
