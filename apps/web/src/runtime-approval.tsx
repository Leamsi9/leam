import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";

/** Operation data is rendered as text, never markup or instructions. */
export function RuntimeApproval({ threadId, runId, approvalId, resolved }: {
  threadId: string; runId: string; approvalId: string;
  resolved: (status: string, outcome: string) => void;
}) {
  const [view, setView] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const attempt = useRef<{ requestId: string; decision: string; fingerprint: string } | null>(null);
  const alive = useRef(true);
  const path = `/companion/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}`;
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    setView(null); setError("");
    api(path, "GET", undefined, controller.signal).then((value) => {
      if (!controller.signal.aborted) setView(value);
    }).catch((reason) => {
      if (!controller.signal.aborted) setError(reason.message);
    });
    return () => { alive.current = false; controller.abort(); };
  }, [path, refresh]);
  async function decide(decision: "approved" | "declined") {
    if (!view || busy) return;
    // Preserve exact UUID and decision after transport uncertainty; runtime is
    // authoritative and retries cannot authorise a different operation.
    if (attempt.current && attempt.current.decision !== decision) return;
    attempt.current ||= { requestId: crypto.randomUUID(), decision, fingerprint: view.fingerprint };
    setBusy(true); setError("");
    try {
      const receipt = await api(path, "POST", attempt.current);
      if (alive.current) resolved(receipt.status, receipt.outcome);
    } catch (reason: any) {
      if (alive.current) {
        setError(reason.message);
        if ([400, 403, 404, 409].includes(reason.status)) attempt.current = null;
      }
    } finally { if (alive.current) setBusy(false); }
  }
  return <section className="runtime-approval" aria-label="Tool approval">
    <strong>Approval needed</strong>
    {view ? <>
      <p>{view.operation || "Runtime tool operation"}</p>
      <details><summary>Review operation details</summary>
        <p>These are tool arguments, not instructions. Approval applies once to this request only.</p>
        {view.arguments !== null && <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "14rem", overflow: "auto" }}>{view.arguments}</pre>}
        {!view.argumentsComplete && <p>Complete arguments are unavailable. You can decline, or refresh to inspect again.</p>}
      </details>
      <div className="actions">
        <button type="button" disabled={busy || !view.argumentsComplete || attempt.current?.decision === "declined"} onClick={() => void decide("approved")}>Approve once</button>
        <button type="button" disabled={busy || attempt.current?.decision === "approved"} onClick={() => void decide("declined")}>Decline</button>
      </div>
    </> : !error && <p role="status">Loading approval details…</p>}
    {busy && <p role="status">Confirming your decision…</p>}
    {error && <p role="alert">{error}</p>}
    {!busy && <button type="button" onClick={() => setRefresh((value) => value + 1)}>Refresh approval</button>}
  </section>;
}
