import { useEffect, useRef, useState } from "react";
import { ListTodo } from "lucide-react";
import { api, type Data } from "./api";
import { ChatDialog } from "./chat-dialog";
import { RuntimeApproval } from "./runtime-approval";
import { sessionValue, rememberSession } from "./session-cache";

const terminal = new Set(["completed", "failed", "cancelled"]);
export function BackgroundWork({ threadId, draft }: { threadId: string; draft: string }) {
  const [items, setItems] = useState<Data[]>([]), [error, setError] = useState("");
  const [task, setTask] = useState(() => sessionValue(`background:draft:${threadId}`, ""));
  const [busy, setBusy] = useState("");
  const [detail, setDetail] = useState<Record<string, Data>>({});
  const generation = useRef(0), attempt = useRef<Data | null>(null);
  async function refresh() {
    if (!threadId) return;
    const current = generation.current;
    try {
      const value = await api(`/companion/jobs?threadId=${encodeURIComponent(threadId)}`);
      if (current === generation.current) setItems(value.items || []);
    } catch (reason: any) { if (current === generation.current) setError(reason.message); }
  }
  useEffect(() => {
    generation.current++;
    setItems([]); setDetail({}); setError(""); setBusy("");
    setTask(sessionValue(`background:draft:${threadId}`, ""));
    attempt.current = sessionValue(`background:attempt:${threadId}`, null);
    void refresh();
    // Only deterministic status reads; no model calls and no hidden-tab polling.
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 7000);
    const visible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener("visibilitychange", visible);
    return () => { generation.current++; clearInterval(timer); document.removeEventListener("visibilitychange", visible); };
  }, [threadId]);
  function edit(value: string) { setTask(value); rememberSession(`background:draft:${threadId}`, value); }
  async function start() {
    if (busy || !task.trim()) return;
    const current = generation.current;
    attempt.current ||= { requestId: crypto.randomUUID(), threadId, title: task.trim().slice(0, 100), task };
    rememberSession(`background:attempt:${threadId}`, attempt.current);
    setBusy("start"); setError("");
    try {
      await api("/companion/jobs", "POST", attempt.current);
      rememberSession(`background:attempt:${threadId}`, null);
      if (current === generation.current) { attempt.current = null; edit(""); await refresh(); }
    } catch (reason: any) {
      if (current === generation.current) setError(reason.message + " Your exact request is kept; retrying uses the same job ID.");
    } finally { if (current === generation.current) setBusy(""); }
  }
  async function change(item: Data, action: string) {
    if (busy) return;
    setBusy(item.id); setError("");
    const current = generation.current;
    try { await api(`/companion/jobs/${item.id}`, "POST", { revision: item.revision, action }); }
    catch (reason: any) { if (current === generation.current) setError(reason.message); }
    finally { if (current === generation.current) { setBusy(""); await refresh(); } }
  }
  const active = items.filter(item => !terminal.has(item.state)).length;
  return <ChatDialog label="Background work" icon={<ListTodo size={20} />} badge={active ? String(active) : undefined}>
    <p>Delegate a report or task while you keep chatting. Results arrive in Today’s Inbox. Worker changes may need separate approval.</p>
    <details><summary>Start a background task</summary>
      <label>Explicit task<textarea value={task} maxLength={8000} disabled={!!attempt.current} onChange={event => edit(event.target.value)} /></label>
      {draft.trim() && !attempt.current && <button type="button" className="secondary" disabled={draft.length > 8000} onClick={() => edit(draft)}>Copy current draft</button>}
      <p>Text only in this first version; attachments and your full chat are not copied. Your chat draft is kept.</p>
      <button type="button" disabled={!!busy || !task.trim()} onClick={() => void start()}>{busy === "start" ? "Saving…" : attempt.current ? "Retry exact task" : "Run in background"}</button>
    </details>
    <button type="button" className="secondary" onClick={() => void refresh()}>Refresh background status</button>
    {error && <p role="alert">{error}</p>}
    {!items.length && <p>No background work in this conversation.</p>}
    {items.map(item => <details key={item.id} onToggle={event => {
      if (event.currentTarget.open) {
        const current = generation.current;
        void api(`/companion/jobs/${item.id}`).then(value => { if (current === generation.current) setDetail(rows => ({ ...rows, [item.id]: value })); }).catch(reason => setError(reason.message));
      }
    }}>
      <summary>{item.title} · {item.state.replaceAll("_", " ")}</summary>
      <p>{item.status}</p>
      <small>Last confirmed {item.observedAt ? new Date(item.observedAt * 1000).toLocaleString() : "not yet observed"}. Job {item.id}</small>
      {item.error && <p role="alert">{item.error}</p>}
      {item.notificationError && <p role="alert">{item.notificationError}</p>}
      {detail[item.id]?.result && <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{detail[item.id].result}</pre>}
      {item.approvalId && item.state === "awaiting_approval" && <RuntimeApproval threadId={item.workerThreadId} runId={item.runId} approvalId={item.approvalId} resolved={() => { void refresh(); }} />}
      {!!item.proposalIds?.length && <><a href="/?view=approvals">Review proposed changes</a><p>After reviewing every change, resume the worker. Pending proposals are not completed work.</p><button type="button" disabled={!!busy} onClick={() => void change(item, "resume")}>Resume after review</button></>}
      {item.inboxId && <a href={`/?view=today&inboxItem=${encodeURIComponent(item.inboxId)}#today/inbox`}>Open Inbox result</a>}
      {item.state === "unknown" && <button type="button" disabled={!!busy} onClick={() => void change(item, "retry")}>Reconcile exact request</button>}
      {!terminal.has(item.state) && <button type="button" disabled={!!busy || item.state === "cancel_requested"} onClick={() => void change(item, "cancel")}>Cancel background job</button>}
      <p><small>One worker at a time, with an eight-minute active time budget. Cancelling does not undo completed actions.</small></p>
    </details>)}
  </ChatDialog>;
}
