import "./today-checks.css";
import { useEffect, useRef, useState } from "react";
import { SemanticBadge } from "./semantic-badge";
import { api } from "./api";

type Check = {
  id: string;
  turnId: string;
  revision: number;
  state: "waiting_exchange" | "queued" | "checking" | "done" | "failed";
  outcome:
    | null
    | "no_action"
    | "proposals_pending"
    | "changes_confirmed_complete"
    | "clarification_needed"
    | "check_failed";
  proposalIds: string[];
  clarification: string | null;
  error: string | null;
  attempts: number;
  nextRetryAt: number | null;
};
type Snapshot = {
  items: Check[];
  coverage: {
    enabledAt: number;
    scope: string;
    error?: string | null;
    retryable?: boolean;
    state?: "queued" | "checking" | "idle" | "failed";
    nextRetryAt?: number | null;
  };
};
const pending = (job: Check) =>
  ["waiting_exchange", "queued", "checking"].includes(job.state);
function label(job: Check) {
  if (job.state === "waiting_exchange")
    return "Waiting for this exchange to finish.";
  if (job.state === "queued") return "Queued to check this exchange for Today.";
  if (job.state === "checking")
    return "Checking this exchange for Today changes…";
  if (job.state === "failed" || job.outcome === "check_failed")
    return "Today check failed. No result is confirmed.";
  if (job.state !== "done") return "Today check status is unavailable.";
  switch (job.outcome) {
    case "no_action":
      return "No Today action identified. This does not mark a task complete.";
    case "proposals_pending":
      return "Suggestions need review. They are not active tracking yet.";
    case "changes_confirmed_complete":
      return "Today changes confirmed by saved action receipts.";
    case "clarification_needed":
      return "A detail is needed before Today changes can be checked.";
    default:
      return "Check finished; no outcome was reported.";
  }
}

/** Read-only progress observation; only an explicit failed-job retry can write. */
export function TodayReconciliation({
  threadId,
  refreshVersion,
  onChanged,
}: {
  threadId: string;
  refreshVersion: number;
  onChanged: () => void;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [reload, setReload] = useState(0);
  const alive = useRef(true);
  const current = useRef(threadId);
  current.current = threadId;
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const latest = useRef<Snapshot | null>(null);
  const refresh = useRef<() => void>(() => {});
  const seen = useRef(new Set<string>());
  const retrying = useRef(false);
  useEffect(() => {
    alive.current = true;
    latest.current = null;
    setSnapshot(null);
    setError("");
    setBusy("");
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | null = null;
    let again = false;
    const valid = () => !stopped && current.current === threadId;
    const schedule = (delay: number) => {
      clearTimeout(timer);
      if (valid() && document.visibilityState === "visible")
        timer = setTimeout(() => void read(), delay);
    };
    async function read() {
      if (!valid() || document.visibilityState !== "visible") return;
      if (request) {
        again = true;
        return;
      }
      request = new AbortController();
      try {
        const value = (await api(
          `/agenda/reconciliation?threadId=${encodeURIComponent(threadId)}`,
          "GET",
          undefined,
          request.signal,
        )) as Snapshot;
        if (!valid()) return;
        if (!Array.isArray(value.items) || !value.coverage)
          throw new Error("Invalid status response");
        // An older GET must not undo an authoritative retry receipt.
        value.items = value.items.map((job) => {
          const previous = latest.current?.items.find(
            (item) => item.id === job.id,
          );
          return previous && previous.revision > job.revision ? previous : job;
        });
        latest.current = value;
        setSnapshot(value);
        setError("");
        for (const job of value.items) {
          const key = `${threadId}:${job.id}:${job.revision}`;
          if (
            job.state === "done" &&
            job.outcome === "changes_confirmed_complete" &&
            !seen.current.has(key)
          ) {
            seen.current.add(key);
            changed.current();
          }
        }
      } catch {
        if (valid() && !request.signal.aborted)
          setError(
            "Today check status is unavailable. Your conversation is unchanged.",
          );
      } finally {
        request = null;
        if (!valid()) return;
        if (again) {
          again = false;
          schedule(200);
        } else if (
          latest.current?.items.some(pending) ||
          ["queued", "checking"].includes(latest.current?.coverage.state || "")
        )
          schedule(3000);
      }
    }
    refresh.current = () => {
      if (request) again = true;
      else schedule(200);
    };
    function visibility() {
      clearTimeout(timer);
      if (document.visibilityState === "visible") void read();
      else {
        request?.abort();
        again = false;
      }
    }
    document.addEventListener("visibilitychange", visibility);
    void read();
    return () => {
      stopped = true;
      alive.current = false;
      clearTimeout(timer);
      request?.abort();
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [threadId]);
  useEffect(() => {
    refresh.current();
  }, [refreshVersion, reload]);

  async function retry(job: Check) {
    if (retrying.current || job.state !== "failed") return;
    retrying.current = true;
    setBusy(job.id);
    setError("");
    const owner = threadId;
    const key = `leam-today-check-retry:${owner}:${job.id}:${job.revision}`;
    try {
      const requestId = sessionStorage.getItem(key) || crypto.randomUUID();
      sessionStorage.setItem(key, requestId);
      const result = await api(
        `/agenda/reconciliation/${encodeURIComponent(job.id)}/retry`,
        "POST",
        { revision: job.revision, requestId },
      );
      if (!alive.current || current.current !== owner) return;
      if (
        result.id !== job.id ||
        !Number.isInteger(result.revision) ||
        result.revision <= job.revision
      )
        throw new Error("No matching retry receipt");
      const before = latest.current;
      if (before) {
        const updated = {
          ...before,
          items: before.items.map((item) =>
            item.id === job.id && item.revision <= result.revision
              ? (result as Check)
              : item,
          ),
        };
        latest.current = updated;
        setSnapshot(updated);
      }
      sessionStorage.removeItem(key);
      refresh.current();
    } catch {
      if (alive.current && current.current === owner) {
        setError(
          "Retry could not be confirmed. Refresh the check status before trying again.",
        );
      }
    } finally {
      retrying.current = false;
      if (alive.current && current.current === owner) setBusy("");
    }
  }
  async function retryScan() {
    if (retrying.current || !snapshot?.coverage.retryable) return;
    retrying.current = true;
    setBusy("scan");
    setError("");
    const owner = threadId;
    const key = `leam-today-scan-retry:${owner}`;
    try {
      const requestId = sessionStorage.getItem(key) || crypto.randomUUID();
      sessionStorage.setItem(key, requestId);
      const result = await api("/agenda/reconciliation/scan/retry", "POST", {
        threadId: owner,
        requestId,
      });
      if (!alive.current || current.current !== owner) return;
      if (result.queued !== true) throw new Error("No scan retry receipt");
      if (latest.current) {
        const updated: Snapshot = {
          ...latest.current,
          coverage: {
            ...latest.current.coverage,
            state: "queued",
            error: null,
            retryable: false,
          },
        };
        latest.current = updated;
        setSnapshot(updated);
      }
      sessionStorage.removeItem(key);
      refresh.current();
    } catch {
      if (alive.current && current.current === owner)
        setError(
          "Conversation check retry could not be confirmed. Refresh its status before trying again.",
        );
    } finally {
      retrying.current = false;
      if (alive.current && current.current === owner) setBusy("");
    }
  }
  const items = snapshot?.items || [];
  const active = items.filter(pending);
  const failures = items.filter((job) => job.state === "failed");
  const primary = active[0] || items[0];
  const question =
    items[0]?.state === "done" &&
    items[0]?.outcome === "clarification_needed" &&
    items[0]?.clarification
      ? items[0]
      : null;
  const awaitingReview = items.filter(
    (job) => job.outcome === "proposals_pending",
  ).length;
  const coverageActive = ["queued", "checking"].includes(
    snapshot?.coverage.state || "",
  );
  const problem = !!error || !!snapshot?.coverage.error || failures.length > 0;
  const summary = [
    problem
      ? failures.length
        ? `${failures.length} failed`
        : "Status unavailable"
      : "",
    awaitingReview ? "Review needed" : "",
    question ? "Clarification needed" : "",
    active.length || coverageActive ? "Checking…" : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <section
      className="today-reconciliation contextual-checks"
      aria-label="Today conversation checks"
    >
      <details className="contextual-check-details">
        <summary>
          <SemanticBadge
            value={
              problem
                ? "failed"
                : awaitingReview || question
                  ? "pending"
                  : active.length || coverageActive
                    ? "checking"
                    : "unknown"
            }
          >
            Chat obligation checks
          </SemanticBadge>
          {summary && <span aria-live="polite">{summary}</span>}
        </summary>
        <div className="contextual-check-content">
          <p role="status" aria-live="polite">
            <SemanticBadge
              value={
                error || snapshot?.coverage.error
                  ? "failed"
                  : primary?.state === "failed"
                    ? "failed"
                    : primary?.state === "done"
                      ? primary.outcome || "unknown"
                      : primary?.state || snapshot?.coverage.state || "unknown"
              }
            >
              Chat obligation check
            </SemanticBadge>
            {" · "}
            {snapshot?.coverage.error && !primary
              ? "Conversation coverage is unavailable."
              : !primary &&
                  ["queued", "checking"].includes(
                    snapshot?.coverage.state || "",
                  )
                ? "Checking conversation history for Today…"
                : primary
                  ? label(primary)
                  : snapshot
                    ? "No exchanges checked yet."
                    : error
                      ? "Status unavailable."
                      : "Loading check status…"}
          </p>
          {active.length > 1 && (
            <small>
              {active.length} exchanges are waiting or being checked.
            </small>
          )}
          {error && (
            <p role="alert">
              {error}{" "}
              <button
                type="button"
                className="secondary"
                onClick={() => setReload((value) => value + 1)}
              >
                Refresh checks
              </button>
            </p>
          )}
          {snapshot?.coverage.error && (
            <div role="alert" className="notice">
              <p>
                Today could not finish checking this conversation. Some
                exchanges may be missing.
              </p>
              {snapshot.coverage.retryable && (
                <button
                  type="button"
                  className="secondary"
                  disabled={!!busy}
                  onClick={() => void retryScan()}
                >
                  {busy === "scan"
                    ? "Requesting retry…"
                    : "Retry conversation check"}
                </button>
              )}
            </div>
          )}
          {failures.map((job) => (
            <div key={job.id} className="notice">
              <p>Today check failed. This exchange has no confirmed result.</p>
              <button
                type="button"
                className="secondary"
                disabled={!!busy}
                onClick={() => void retry(job)}
              >
                {busy === job.id ? "Requesting retry…" : "Retry check"}
              </button>
            </div>
          ))}
          {question && (
            <div className="notice">
              <p>{question.clarification}</p>
              <small>
                Answer in the regular chat. No answer is sent automatically.
              </small>
            </div>
          )}
          {snapshot && (
            <details>
              <summary>
                Check history · {items.length} exchange
                {items.length === 1 ? "" : "s"}
              </summary>
              <p>
                Checks cover new Today exchanges. Older conversations are
                outside this coverage.{" "}
                {Number.isFinite(snapshot.coverage.enabledAt) &&
                  `Enabled ${new Date(snapshot.coverage.enabledAt * 1000).toLocaleString()}.`}
              </p>
              {items.map((job) => (
                <p key={job.id}>
                  {label(job)}{" "}
                  <small>
                    {job.proposalIds.length
                      ? `${job.proposalIds.length} suggestion reference${job.proposalIds.length === 1 ? "" : "s"}. `
                      : ""}
                    Attempt {job.attempts}.
                  </small>
                </p>
              ))}
              {items.some((job) => job.outcome === "proposals_pending") && (
                <p>
                  Open Approvals under More to review proposed changes. A
                  proposal is not an active commitment.
                </p>
              )}
            </details>
          )}
        </div>
      </details>
    </section>
  );
}
