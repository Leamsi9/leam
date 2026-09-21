import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Data } from "./api";
import { CalendarEventManager } from "./calendar-changes";

const storageKey = "leam-calendar-actions";
function localAttempts(): Data[] {
  try {
    return JSON.parse(sessionStorage.getItem(storageKey) || "[]");
  } catch {
    return [];
  }
}
function saveAttempt(body: Data) {
  const items = localAttempts().filter((x) => x.requestId !== body.requestId);
  sessionStorage.setItem(storageKey, JSON.stringify([...items, body]));
}
function forgetAttempt(id: string) {
  sessionStorage.setItem(
    storageKey,
    JSON.stringify(localAttempts().filter((x) => x.requestId !== id)),
  );
}
function today() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}
export function CalendarActions({
  calendar,
  fail,
}: {
  calendar: Data;
  fail: (e: unknown) => void;
}) {
  const [commitments, setCommitments] = useState<Data[]>([]),
    [history, setHistory] = useState<Data[]>([]),
    [loaded, setLoaded] = useState(false),
    [nextOffset, setNextOffset] = useState<number | null>(null);
  const [commitmentId, setCommitmentId] = useState(""),
    [date, setDate] = useState(today()),
    [time, setTime] = useState("10:00"),
    [zone, setZone] = useState(
      Intl.DateTimeFormat().resolvedOptions().timeZone,
    ),
    [minutes, setMinutes] = useState(30),
    [fold, setFold] = useState(0);
  const [review, setReview] = useState<Data | null>(null),
    [busy, setBusy] = useState(false),
    [confirmed, setConfirmed] = useState(false);
  const mounted = useRef(true);
  async function load() {
    const [items, actions] = await Promise.all([
      api("/commitments"),
      api("/calendar/actions?calendarId=" + encodeURIComponent(calendar.id)),
    ]);
    if (!mounted.current) return;
    setCommitments(items.items.filter((x: Data) => x.status === "active"));
    const local = localAttempts()
      .filter(
        (x) => !actions.items.some((a: Data) => a.requestId === x.requestId),
      )
      .map((request) => ({
        requestId: request.requestId,
        request,
        state: "unconfirmed",
        review: request.reviewForDisplay,
      }));
    setHistory(
      [...actions.items, ...local].filter(
        (a: Data) => a.request.schedule.calendarId === calendar.id,
      ),
    );
    setNextOffset(actions.nextOffset ?? null);
    setLoaded(true);
  }
  useEffect(() => {
    mounted.current = true;
    load().catch(fail);
    return () => {
      mounted.current = false;
    };
  }, []);
  const unresolved = history.filter((a) => a.state !== "complete");
  async function send(request: Data) {
    if (busy) return;
    const { reviewForDisplay, ...body } = request;
    setBusy(true);
    setConfirmed(false);
    try {
      saveAttempt(request);
      setReview(null);
      setHistory((current) => [
        ...current.filter((a) => a.requestId !== body.requestId),
        {
          requestId: body.requestId,
          request,
          state: "unconfirmed",
          review:
            reviewForDisplay ||
            current.find((a) => a.requestId === body.requestId)?.review,
        },
      ]);
      await api("/calendar/actions", "POST", body);
      forgetAttempt(body.requestId);
      if (mounted.current) {
        setReview(null);
        setConfirmed(true);
      }
    } catch (e) {
      if (e instanceof ApiError && e.actionReserved === "no") {
        forgetAttempt(body.requestId);
        if (mounted.current)
          setHistory((current) =>
            current.filter((a) => a.requestId !== body.requestId),
          );
      }
      if (mounted.current) fail(e);
    } finally {
      if (mounted.current) {
        await load().catch(fail);
        setBusy(false);
      }
    }
  }
  return (
    <div className="card settings-form">
      <h3>Schedule a commitment</h3>
      {!calendar.canWrite ? (
        <p>This calendar is read-only.</p>
      ) : (
        <>
          <p>
            Create a personal time block in {calendar.name} ·{" "}
            {calendar.identity}.
          </p>
          <fieldset
            disabled={busy || !loaded || unresolved.length > 0 || !!review}
            className="settings-form"
          >
            <label>
              Commitment to schedule
              <select
                value={commitmentId}
                onChange={(e) => setCommitmentId(e.target.value)}
              >
                <option value="">Choose a commitment</option>
                {commitments.map((c) => (
                  <option value={c.id} key={c.id}>
                    {c.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Event date
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </label>
            <label>
              Start time
              <input
                type="time"
                value={time}
                onChange={(e) => setTime(e.target.value)}
              />
            </label>
            <label>
              Event timezone
              <input value={zone} onChange={(e) => setZone(e.target.value)} />
            </label>
            <label>
              Duration in minutes
              <input
                type="number"
                min="1"
                max="1440"
                value={minutes}
                onChange={(e) => setMinutes(Number(e.target.value))}
              />
            </label>
            <label>
              If the clock repeats this time
              <select
                value={fold}
                onChange={(e) => setFold(Number(e.target.value))}
              >
                <option value="0">Use the first occurrence</option>
                <option value="1">Use the second occurrence</option>
              </select>
            </label>
            <button
              className="primary"
              disabled={
                !commitmentId ||
                !date ||
                !time ||
                !zone ||
                minutes < 1 ||
                minutes > 1440
              }
              onClick={async () => {
                setBusy(true);
                setConfirmed(false);
                try {
                  const c = commitments.find((c) => c.id === commitmentId);
                  const r = await api("/calendar/actions/preview", "POST", {
                    calendarId: calendar.id,
                    commitmentId,
                    commitmentRevision: c?.revision,
                    date,
                    time,
                    timezone: zone,
                    minutes,
                    fold,
                  });
                  if (mounted.current) setReview(r);
                } catch (e) {
                  if (mounted.current) fail(e);
                } finally {
                  if (mounted.current) setBusy(false);
                }
              }}
            >
              Preview calendar event
            </button>
          </fieldset>
          {review && (
            <div className="reminder-card">
              <h4>{review.title}</h4>
              <p>
                {review.calendar.name} · {review.calendar.identity}
              </p>
              <p>
                {review.localStart} – {review.localEnd} (
                {review.schedule.timezone})
              </p>
              <p>No invitations will be sent.</p>
              <div className="actions">
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() =>
                    send({
                      requestId: crypto.randomUUID(),
                      schedule: review.schedule,
                      previewDigest: review.digest,
                      reviewForDisplay: review,
                    })
                  }
                >
                  Create this event
                </button>
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => setReview(null)}
                >
                  Change event details
                </button>
              </div>
            </div>
          )}
        </>
      )}
      {confirmed && <p role="status">Event creation confirmed</p>}
      {history.length > 0 && <h4>Calendar actions</h4>}
      {nextOffset !== null && (
        <button
          className="secondary"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              const page = await api(
                `/calendar/actions?calendarId=${encodeURIComponent(calendar.id)}&offset=${nextOffset}`,
              );
              if (mounted.current) {
                setHistory((current) => [
                  ...current,
                  ...page.items.filter(
                    (a: Data) =>
                      !current.some((c) => c.requestId === a.requestId),
                  ),
                ]);
                setNextOffset(page.nextOffset);
              }
            } catch (e) {
              fail(e);
            } finally {
              if (mounted.current) setBusy(false);
            }
          }}
        >
          Load more calendar actions
        </button>
      )}
      {history.map((action) => (
        <article className="reminder-card" key={action.requestId}>
          <strong>{action.review?.title || "Calendar event"}</strong>
          <p>
            {action.review?.localStart} – {action.review?.localEnd}
          </p>
          {action.state === "complete" && (
            <CalendarEventManager creationId={action.requestId} fail={fail} />
          )}
          {action.state === "complete" ? (
            <p>
              Created in {action.review?.calendar?.name}. Refresh the calendar
              view to see current provider data.
            </p>
          ) : (
            <>
              <p>
                Creation is unconfirmed. Recover this action before creating
                another event in this calendar.
              </p>
              {action.error && <p role="alert">{action.error}</p>}
              <button
                className="secondary"
                disabled={busy}
                onClick={() => send(action.request)}
              >
                Recover this calendar action
              </button>
            </>
          )}
        </article>
      ))}
    </div>
  );
}
