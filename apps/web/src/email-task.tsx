import { useEffect, useRef, useState } from "react";
import { ListPlus, X } from "lucide-react";
import { api, type Data } from "./api";
import { rememberSession, sessionValue } from "./session-cache";

export function EmailTask({ item, changed }: {item: Data; changed?: () => void}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const alive = useRef(0);
  useEffect(() => () => { ++alive.current; }, []);
  const draftKey = `email-task:${item.key}`;
  const [draft, setDraft] = useState<Data>(() => sessionValue(draftKey, {title:"",capacityId:null}));
  const [receipt, setReceipt] = useState<Data | null>(null);
  const [capacities, setCapacities] = useState<Data[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const writeDraft = (next:Data) => { setDraft(next); rememberSession(draftKey,next); };
  async function inspect() {
    const generation = ++alive.current;
    setBusy(true); setError("");
    try {
      const [status, capacity] = await Promise.all([api(`/inbox-mail/task?key=${encodeURIComponent(item.key)}`), api("/capacities")]);
      if (generation !== alive.current) return;
      setReceipt(status); setCapacities(capacity.items || []);
      if (status.draft && (status.state !== "new" || !draft.title)) writeDraft(status.draft);
    } catch (e) { if (generation === alive.current) setError((e as Error).message); }
    finally { if (generation === alive.current) setBusy(false); }
  }
  async function convert() {
    if (busy) return;
    setBusy(true); setError("");
    try { const result = await api("/inbox-mail/task", "POST", {key:item.key,...draft}); setReceipt(result); changed?.(); }
    catch (e) { setError(`${(e as Error).message} Use Check saved request before retrying if delivery is uncertain.`); }
    finally { setBusy(false); }
  }
  const editable = receipt?.state === "new";
  const retryable = receipt?.state === "prepared";
  return <>
    <button type="button" className="secondary" onClick={() => { dialog.current?.showModal(); void inspect(); }}><ListPlus size={16} /> Convert to task</button>
    <dialog ref={dialog} className="chat-options-dialog" aria-label="Convert email to task">
      <header><h2>Convert email to task</h2><button type="button" className="icon-button" aria-label="Close task conversion" onClick={() => dialog.current?.close()}><X size={20}/></button></header>
      <div className="chat-options-body"><p>{item.subject}</p><p>Email stays in Gmail. Your Today approval setting applies.</p>
      {error && <p role="alert">{error}</p>}
      {busy && <p role="status">Checking task request…</p>}
      {(editable || retryable) && <form onSubmit={e => { e.preventDefault(); void convert(); }}>
        <label>Task title<input required maxLength={500} value={draft.title} disabled={busy || retryable} onChange={e=>writeDraft({...draft,title:e.target.value})}/></label>
        <label>Capacity<select value={draft.capacityId || ""} disabled={busy || retryable} onChange={e=>writeDraft({...draft,capacityId:e.target.value || null})}><option value="">Personal · no capacity</option>{capacities.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <button disabled={busy || !draft.title.trim()}>{retryable ? "Retry saved conversion" : "Create task"}</button>
      </form>}
      {receipt?.state === "complete" && <p role="status">{receipt.removed ? "The linked task was removed. This conversion will not create it again." : <>Task created. <a href={`/?view=goals&commitment=${encodeURIComponent(receipt.commitmentId)}`}>Open task</a></>}</p>}
      {receipt && !["new","prepared","complete"].includes(receipt.state) && <p role="status">{receipt.state === "pending" ? "Task awaits approval." : `Task request: ${receipt.state}.`} {!["declined","superseded"].includes(receipt.state) && <a href={`/?view=approvals&proposal=${encodeURIComponent(receipt.proposalId)}`}>Open Approvals</a>}</p>}
      <button type="button" className="secondary" disabled={busy} onClick={() => void inspect()}>Check saved request</button>
      </div>
    </dialog>
  </>;
}
