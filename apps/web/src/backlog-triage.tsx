import { useEffect, useRef, useState } from "react";
import { ListFilter } from "lucide-react";
import { api, ApiError, type Data } from "./api";

const key = "leam-triage-progress";
function restored(): Data | null {
  try {
    const current = JSON.parse(sessionStorage.getItem(key) || "null");
    if (current?.requestId && labels[current.state]) return current;
    const legacy = JSON.parse(sessionStorage.getItem("leam-backlog-triage") || "null");
    if (legacy?.body?.sourceTicketId === "feature:backlog-triage" && typeof legacy.body.requestId === "string")
      return {requestId: legacy.body.requestId, mainThreadId: legacy.body.mainThreadId, state: "uncertain", detail: "Checking the original triage receipt; completion has not been confirmed.", legacy: true};
    return null;
  }
  catch { return null; }
}
const labels: Record<string, string> = {
  sending: "Sending", in_progress: "In progress", completed: "Completed",
  failed: "Failed", uncertain: "Uncertain",
};
export function BacklogTriage({ changed }: { changed: () => Promise<unknown> }) {
  const [job, setJob] = useState<Data | null>(restored);
  const [priorities, setPriorities] = useState("");
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const epoch = useRef(0), readSequence = useRef(0);
  const lock = useRef(false), alive = useRef(true), current = useRef(job);
  current.current = job;
  function remember(value: Data | null) {
    if (!alive.current) return;
    if (value) sessionStorage.setItem(key, JSON.stringify(value));
    else sessionStorage.removeItem(key);
    current.current = value; setJob(value);
  }
  async function refresh() {
    if (lock.current) return;
    const version = epoch.current, sequence = ++readSequence.current;
    const relevant = () => alive.current && version === epoch.current && sequence === readSequence.current && !lock.current;
    try {
      const pending = current.current;
      const result = pending && !["completed", "failed"].includes(pending.state)
        ? await api(`/coding/main/triage/${pending.requestId}`)
        : (await api("/coding/main/triage")).job;
      if (!relevant()) return;
      // A preflight rejection has no server row. Do not erase it with an older job.
      if (!(pending?.localOnly && (!result || (["completed", "failed"].includes(result.state) && result.created <= pending.created)))) remember(result);
      setError("");
      if (result?.state === "completed" && pending?.state !== "completed") void changed().catch(e => { if (relevant()) setError(String(e)); });
    } catch (e) {
      if (relevant()) setError(e instanceof Error ? e.message : String(e));
    } finally { if (relevant()) setLoading(false); }
  }
  useEffect(() => {
    alive.current = true; void refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden && current.current && ["sending", "in_progress", "uncertain"].includes(current.current.state)) void refresh();
    }, 4000);
    const wake = () => { if (!document.hidden) void refresh(); };
    document.addEventListener("visibilitychange", wake);
    return () => { alive.current = false; epoch.current++; clearInterval(timer); document.removeEventListener("visibilitychange", wake); };
  }, []);
  const pending = !!job && !["completed", "failed"].includes(job.state);
  async function run(check = false) {
    if (lock.current || (!check && (pending || loading))) return;
    lock.current = true; const version = ++epoch.current; setBusy(true); setError("");
    const relevant = () => alive.current && version === epoch.current;
    try {
      if (check && job) {
        const result = await api(`/coding/main/triage/${job.requestId}/reconcile`, "POST", {});
        if (relevant()) remember(result);
        return;
      }
      const body = { requestId: crypto.randomUUID(), priorities: priorities.trim() };
      remember({ ...body, created: Date.now() / 1000, state: "sending", detail: "Sending the review request to Main." });
      const result = await api("/coding/main/triage", "POST", body);
      if (relevant()) remember(result);
    } catch (e) {
      if (relevant()) {
        setError(e instanceof Error ? e.message : String(e));
        const pending = current.current;
        if (pending && e instanceof ApiError && e.actionReserved === "false") {
          remember({ ...pending, state: "failed", localOnly: true, detail: e.message });
        } else if (pending) {
          try {
            const result = await api(`/coding/main/triage/${pending.requestId}`);
            if (relevant()) remember(result);
          } catch { if (relevant()) remember({ ...pending, state: "uncertain", detail: "Delivery is unconfirmed. Check the original request before trying again." }); }
        }
      }
    } finally { lock.current = false; if (relevant()) setBusy(false); }
  }
  async function openMain() {
    const owner = job;
    if (!owner?.mainThreadId) return;
    try {
      const result = await api(`/codex/threads/${encodeURIComponent(owner.mainThreadId)}`);
      if (result.thread?.id !== owner.mainThreadId) throw new Error("Main conversation is unavailable.");
      if (alive.current && current.current?.requestId === owner.requestId) window.dispatchEvent(new CustomEvent("leam:open-coding", { detail: result.thread }));
    } catch (e) { if (alive.current) setError(e instanceof Error ? e.message : String(e)); }
  }
  return <section className="backlog-triage" aria-label="Backlog triage">
    <div className="actions"><button type="button" className="secondary" disabled={busy || pending || loading} onClick={() => void run()}>
      <ListFilter size={18} aria-hidden="true" /> Triage backlog
    </button><small>Quick wins · greatest UX impact</small></div>
    <details><summary>Alternative triage priorities</summary>
      <label>Priorities for this review<textarea value={priorities} maxLength={2000} rows={3} disabled={busy || pending}
        placeholder="For example: focus on reliability before new features" onChange={e => setPriorities(e.target.value)} /></label>
      <small>Leave blank to use fastest completion and greatest user-experience impact.</small>
    </details>
    {error && <p role="alert">{error}</p>}
    {job && <div role="status" className={`triage-status triage-${job.state}`}>
      <strong>{labels[job.state] || "Uncertain"}</strong><p>{job.detail}</p>
      {["completed", "failed"].includes(job.state) && Number.isFinite(job.updated) && <small className="triage-recorded-at">
        {job.state === "completed" ? "Completion" : "Failure"} recorded at <time dateTime={new Date(job.updated * 1000).toISOString()}>{new Date(job.updated * 1000).toLocaleString()}</time>
      </small>}
      {job.state === "in_progress" && <small>Acceptance is not completion. Main must record the review outcome.</small>}
      {job.mainThreadId && <button type="button" className="secondary" onClick={() => void openMain()}>Open Main conversation</button>}
      {["sending", "uncertain"].includes(job.state) && <button type="button" className="secondary" disabled={busy} onClick={() => void run(true)}>Check triage delivery</button>}
      <button type="button" className="secondary" onClick={() => { void refresh(); void changed().catch(e => { if (alive.current) setError(String(e)); }); }}>Refresh backlog</button>
    </div>}
  </section>;
}
