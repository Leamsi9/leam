import { CodingChildren } from "./coding-children";
import { ChatDialog } from "./chat-dialog";
import { GitBranch } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";

/** Explicit identity choice and task action; ordinary chat never routes implicitly. */
export function MainCodingControl({
  thread,
  ticketId,
  draft = "",
  onChanged,
}: {
  thread?: Data | null;
  ticketId?: string;
  draft?: string;
  onChanged?: () => void;
}) {
  const [status, setStatus] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [task, setTask] = useState("");
  const [context, setContext] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [receipt, setReceipt] = useState<Data | null>(null);
  const key = `leam-main-task:${ticketId || thread?.id || "none"}`;
  const [pending, setPending] = useState<Data | null>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(key) || "null");
    } catch {
      return null;
    }
  });
  const inflight = useRef(false);
  const mounted = useRef(true);
  const report = (e: unknown) =>
    setError(e instanceof Error ? e.message : String(e));
  async function refresh() {
    const value = await api("/coding/main");
    if (mounted.current) setStatus(value);
  }
  useEffect(() => {
    mounted.current = true;
    void refresh().catch(report);
    return () => {
      mounted.current = false;
    };
  }, [key]);
  const main = status?.main;
  const isMain = !!main && main.threadId === thread?.id;
  async function select() {
    if (inflight.current || !confirmed || !thread) return;
    inflight.current = true;
    setBusy(true);
    setError("");
    try {
      const value = await api("/coding/main", "PUT", {
        threadId: thread.id,
        expectedRevision: main?.revision || 0,
        confirmed: true,
      });
      if (mounted.current) {
        setStatus(value);
        setConfirmed(false);
        onChanged?.();
      }
    } catch (e) {
      report(e);
      await refresh().catch(() => {});
    } finally {
      inflight.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  function accepted(value: Data) {
    setReceipt(value);
    if (value.state === "accepted") {
      sessionStorage.removeItem(key);
      setPending(null);
      setEditing(false);
    }
  }
  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (inflight.current || pending || !main || !task.trim()) return;
    inflight.current = true;
    setBusy(true);
    setError("");
    const body = {
      requestId: crypto.randomUUID(),
      mainRevision: main.revision,
      mainThreadId: main.threadId,
      sourceThreadId: thread?.id || null,
      sourceTicketId: ticketId || null,
      text: task,
      context,
    };
    // Identity survives reload before any network await; no automatic resubmission.
    try {
      sessionStorage.setItem(key, JSON.stringify(body));
      setPending(body);
      const value = await api("/coding/main/handoffs", "POST", body);
      if (mounted.current) accepted(value);
    } catch (e) {
      if (mounted.current) report(e);
    } finally {
      inflight.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function check() {
    if (!pending || inflight.current) return;
    inflight.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api(
        `/coding/main/handoffs/${pending.requestId}/reconcile`,
        "POST",
        {},
      );
      if (["not_recorded", "not_sent"].includes(result.state)) {
        sessionStorage.removeItem(key);
        setTask(pending.text);
        setContext(pending.context);
        setPending(null);
        setEditing(true);
        setError(
          "No task was dispatched. Review the current Main and send explicitly.",
        );
        await refresh().catch(() => {});
      } else accepted(result);
    } catch (e) {
      report(e);
    } finally {
      inflight.current = false;
      setBusy(false);
    }
  }
  return (
    <ChatDialog label="Main coding coordinator" icon={<GitBranch size={20} />} badge={isMain ? "Main" : pending || error ? "!" : undefined}>
    <aside className="main-coding-control" aria-label="Main selection and handoff">
      <div>
        {isMain ? (
          <strong className="badge">Main · coordinator</strong>
        ) : (
          <strong>Main: {main?.name || "Not selected"}</strong>
        )}
        {main && (
          <small style={{ display: "block", overflowWrap: "anywhere" }}>
            {main.threadId}
          </small>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      {!status ? (
        <p>Loading Main selection…</p>
      ) : (
        <>
          {isMain && <CodingChildren key={`${main.threadId}:${main.revision}`} main={main} />}
          {!status.bindingValid && main && (
            <p role="status">
              Main's owner binding changed. Select the exact session again
              before sending.
            </p>
          )}
          {thread && !ticketId && (!isMain || !status.bindingValid) && (
            <details>
              <summary>Use this session as Main</summary>
              <p>
                Main coordinates implementation, parallel workers, integration
                and deployment. Reassignment does not stop previous work.
              </p>
              <label>
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                />{" "}
                Make this exact session ({thread.id}) Main
              </label>
              <button
                type="button"
                disabled={busy || !confirmed}
                onClick={() => void select()}
              >
                Make main
              </button>
            </details>
          )}
          {!isMain && (thread || ticketId) && (
            <>
              <p>Discuss here. Send implementation tasks to Main.</p>
              {!main && <p>Select Main from an open Coding session first.</p>}
              {pending ? (
                <div role="status">
                  <p>
                    Saved handoff to {pending.mainThreadId}. Delivery needs
                    checking; it will not be resent.
                  </p>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void check()}
                  >
                    Check handoff receipt
                  </button>
                </div>
              ) : (
                !editing && (
                  <button
                    type="button"
                    disabled={busy || !main || !status.bindingValid}
                    onClick={() => {
                      setTask(draft);
                      setEditing(true);
                      setReceipt(null);
                    }}
                  >
                    Send to main
                  </button>
                )
              )}
              {editing && !pending && (
                <form onSubmit={send}>
                  <label>
                    Implementation task
                    <textarea
                      required
                      maxLength={8000}
                      rows={3}
                      value={task}
                      onChange={(e) => setTask(e.target.value)}
                    />
                  </label>
                  <label>
                    Concise context (optional)
                    <textarea
                      maxLength={2000}
                      rows={2}
                      value={context}
                      onChange={(e) => setContext(e.target.value)}
                    />
                  </label>
                  <p>
                    Target: {main?.name} · {main?.threadId}. Only this task,
                    context and source reference will be sent. Attachments stay
                    in the source chat.
                  </p>
                  <button
                    disabled={
                      busy || !task.trim() || !main || !status.bindingValid
                    }
                  >
                    Confirm send to main
                  </button>{" "}
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => setEditing(false)}
                  >
                    Cancel
                  </button>
                </form>
              )}
            </>
          )}
          {receipt && (
            <p role="status">
              {receipt.state === "accepted"
                ? `Accepted by Main${receipt.receipt?.operation === "steer" ? " as an active-turn follow-up" : ""}. Implementation is not yet complete.`
                : "Delivery is uncertain. Check the receipt or inspect Main; do not resend."}
            </p>
          )}
          <details>
            <summary>Coordination permissions</summary>
            <p>{status.limitation}</p>
          </details>
        </>
      )}
    </aside>
    </ChatDialog>
  );
}
