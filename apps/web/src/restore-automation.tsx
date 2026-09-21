import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";

export function RestoreAutomationSettings({
  fail,
}: {
  fail: (error: unknown) => void;
}) {
  const [status, setStatus] = useState<Data | null>(null),
    [review, setReview] = useState<Data | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [busy, setBusy] = useState(false),
    [confirmed, setConfirmed] = useState(false),
    [message, setMessage] = useState("");
  const mounted = useRef(false),
    generation = useRef(0),
    pending = useRef(false);
  const active = (token: number) =>
    mounted.current && token === generation.current;
  async function load() {
    const token = ++generation.current;
    setReview(null);
    setConfirmed(false);
    try {
      const value = await api("/automation/restore");
      if (active(token)) {
        setStatus(value);
        setUncertain(false);
      }
    } catch (error) {
      if (active(token)) fail(error);
    }
  }
  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
      generation.current++;
    };
  }, []);
  async function inspect() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setReview(null);
    setConfirmed(false);
    setMessage("");
    const token = ++generation.current;
    try {
      const value = await api("/automation/restore/preview", "POST", {});
      if (active(token)) setReview(value);
    } catch (error) {
      if (active(token)) fail(error);
    } finally {
      if (active(token)) {
        pending.current = false;
        setBusy(false);
      }
    }
  }
  async function resume() {
    if (pending.current || !review || !confirmed) return;
    pending.current = true;
    setBusy(true);
    setMessage("");
    const token = ++generation.current;
    const body = {
      requestId: crypto.randomUUID(),
      previewToken: review.previewToken,
      cutoff: review.cutoff,
      confirmed: true,
    };
    setReview(null);
    setConfirmed(false);
    try {
      const result = await api("/automation/restore/resume", "POST", body);
      if (active(token)) {
        setStatus({ held: false, invalid: false, lastResume: result });
        setMessage(
          "Future schedules resumed. Past work was kept in the records and will not be resent.",
        );
      }
    } catch (error) {
      if (active(token)) {
        setUncertain(true);
        setMessage(
          "The request was not repeated. Refresh automation status before reviewing another request.",
        );
        fail(error);
      }
    } finally {
      if (active(token)) {
        pending.current = false;
        setBusy(false);
      }
    }
  }
  return (
    <div aria-label="Restored automations" className="restore-automation">
      <h4>Scheduled automations</h4>
      {!status ? (
        <p>Checking automation status…</p>
      ) : status.held ? (
        <>
          <p role="status">
            <strong>Paused after restore.</strong> Reminders, push delivery and
            scheduled routines stay paused until you review future-only resume.
            Manual routine runs are paused too.
          </p>
          {status.invalid ? (
            <p>
              The restore marker needs operator review. Automations remain
              paused; ask in Coding to inspect it.
            </p>
          ) : (
            <button
              className="secondary"
              disabled={busy || uncertain}
              onClick={() => void inspect()}
            >
              Review future schedules
            </button>
          )}
        </>
      ) : (
        <p>Scheduled automations are enabled.</p>
      )}
      <button className="secondary" disabled={busy} onClick={() => void load()}>
        Refresh automation status
      </button>
      {review && (
        <div className="card" aria-label="Review automation resume">
          <p>{review.warning}</p>
          <ul>
            <li>{review.pendingPushes} pending pushes will be skipped.</li>
            <li>
              {review.pastReminders} ready or past-due reminder records will
              expire.
            </li>
            <li>
              {review.overdueRoutines} overdue routine schedules will advance.
            </li>
          </ul>
          <p>
            Older reminder occurrences without saved records will also stay
            expired. Future dates and reminder settings are preserved.
          </p>
          <label>
            <input
              type="checkbox"
              checked={confirmed}
              disabled={busy}
              onChange={(event) => setConfirmed(event.target.checked)}
            />{" "}
            Resume future schedules without resending past work.
          </label>
          <button disabled={busy || !confirmed} onClick={() => void resume()}>
            Confirm future-only resume
          </button>
        </div>
      )}
      {status?.lastResume && (
        <p>
          Last resume skipped {status.lastResume.pendingPushes} pending pushes,
          expired {status.lastResume.pastReminders} reminder records and
          advanced {status.lastResume.overdueRoutines} overdue routines.
        </p>
      )}
      {message && <p role="status">{message}</p>}
    </div>
  );
}
