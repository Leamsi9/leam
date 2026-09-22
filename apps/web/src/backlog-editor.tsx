import { useRef, useState } from "react";
import { Pencil, Trash2, X } from "lucide-react";
import { api, ApiError, type Data } from "./api";
import { sessionValue, rememberSession } from "./session-cache";

type Draft = { revision: number; title: string; rationale: string; scope: string; subtasks: { id: string; title: string }[]; original: Data };
type Pending = { operation: "edit" | "delete" | "restore"; body: Data };
function draftOf(item: Data): Draft {
  const fields = { title: item.title, rationale: item.rationale || "", scope: item.scope || "", subtasks: (item.subtasks || []).map((task: Data) => ({ id: task.id, title: task.title })) };
  return { ...fields, revision: item.revision, original: fields };
}
export function BacklogEditor({ item, disabled, changed }: { item: Data; disabled: boolean; changed: () => Promise<unknown> }) {
  const key = "backlog:edit:" + item.feature;
  const [draft, setDraft] = useState<Draft | null>(() => sessionValue(key, null));
  const [pending, setPending] = useState<Pending | null>(() => sessionValue(key + ":request", null));
  const [saving, setSaving] = useState(false), [notice, setNotice] = useState("");
  const [deleting, setDeleting] = useState(false), [activeConfirmed, setActiveConfirmed] = useState(false);
  const [conflict, setConflict] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null), trigger = useRef<HTMLButtonElement>(null);
  const active = ["in_progress", "handover"].includes(item.deliveryState) && !!item.worker;
  function remember(value: Draft | null) { setDraft(value); rememberSession(key, value); }
  function reserve(value: Pending | null) { setPending(value); rememberSession(key + ":request", value); }
  async function send(request: Pending) {
    if (saving) return;
    reserve(request); setSaving(true); setConflict(false); setNotice("Saving…");
    let confirmed = false;
    try {
      const receipt = await api(`/backlog/${encodeURIComponent(item.feature)}/${request.operation}`, "POST", request.body);
      confirmed = true; reserve(null); remember(null); setDeleting(false);
      setNotice(receipt.cancellationRequired ? "Ticket deleted. Coordinator cancellation is pending; this does not stop the external worker itself." : "Saved.");
      await changed();
      if (request.operation === "edit") dialog.current?.close();
    } catch (error) {
      if (confirmed) { setNotice("Saved, but the latest backlog could not be loaded. Refresh the backlog."); return; }
      if (error instanceof ApiError && error.status && error.status >= 400 && error.status < 500) {
        reserve(null); setConflict(error.status === 409);
        setNotice(error.status === 409 ? "This ticket changed. Your draft is kept. Load the latest revision, review your edits, then save explicitly." : error.message);
      } else setNotice("Save confirmation unavailable. Your draft and exact request are kept. Retry the same request to check safely.");
    } finally { setSaving(false); }
  }
  async function reloadRevision() {
    setSaving(true);
    try {
      const latest = await api("/backlog");
      const current = latest.items.find((value: Data) => value.feature === item.feature);
      if (!current) { setNotice("This ticket is no longer in Backlog. Your draft is kept; check Updates or deleted tickets."); return; }
      // Keep user text; adopt only the new revision after an explicit review step.
      if (draft) remember({ ...draft, revision: current.revision });
      setConflict(false); setNotice("Latest revision loaded. Review your draft before saving; nothing has been applied.");
      await changed();
    } catch (error) { setNotice(error instanceof Error ? error.message : "Could not reload ticket."); }
    finally { setSaving(false); }
  }
  return <>
    <button ref={trigger} type="button" className="secondary" disabled={disabled} onClick={() => {
      if (!draft) remember(draftOf(item));
      dialog.current?.showModal();
    }}><Pencil size={15} aria-hidden="true" /> Edit ticket</button>
    <dialog ref={dialog} className="chat-options-dialog" aria-label={`Edit ${item.title}`} onClose={() => trigger.current?.focus()}>
      <header><h2>Edit ticket</h2><button type="button" className="icon-button" aria-label="Close ticket editor" onClick={() => dialog.current?.close()}><X /></button></header>
      {draft && <form onSubmit={event => {
        event.preventDefault();
        const changes = Object.fromEntries(["title", "rationale", "scope", "subtasks"].filter(key => JSON.stringify(draft[key as keyof Draft]) !== JSON.stringify(draft.original[key])).map(key => [key, draft[key as keyof Draft]]));
        if (!Object.keys(changes).length) { setNotice("No changes to save."); return; }
        void send({ operation: "edit", body: { requestId: crypto.randomUUID(), revision: draft.revision, changes } });
      }}>
        <fieldset disabled={saving || !!pending}>
          <label>Title<input required maxLength={200} value={draft.title} onChange={event => remember({ ...draft, title: event.target.value })} /></label>
          <label>Why this is needed<textarea maxLength={2000} value={draft.rationale} onChange={event => remember({ ...draft, rationale: event.target.value })} /></label>
          <label>Scope<textarea maxLength={4000} value={draft.scope} onChange={event => remember({ ...draft, scope: event.target.value })} /></label>
          <details><summary>Subtasks ({draft.subtasks.length})</summary>
            <p>Edit task labels and scope. Delivery progress, blockers, QA and UAT remain separately recorded.</p>
            {draft.subtasks.map((task, index) => <div className="quick-add" key={task.id}>
              <label>Subtask {index + 1}<input required maxLength={200} value={task.title} onChange={event => remember({ ...draft, subtasks: draft.subtasks.map(other => other.id === task.id ? { ...other, title: event.target.value } : other) })} /></label>
              <button type="button" className="icon-button" aria-label={`Remove subtask ${index + 1}`} onClick={() => remember({ ...draft, subtasks: draft.subtasks.filter(other => other.id !== task.id) })}><Trash2 size={17} /></button>
            </div>)}
            <button type="button" className="secondary" disabled={draft.subtasks.length >= 40} onClick={() => remember({ ...draft, subtasks: [...draft.subtasks, { id: "user-" + crypto.randomUUID(), title: "" }] })}>Add subtask</button>
          </details>
          <p>Your title, rationale, scope and task labels are preserved during coordinator reviews. New coordinator subtasks can still appear; progress is not changed by editing labels.</p>
          <button type="submit" disabled={conflict}>Save ticket</button>
          <button type="button" className="secondary" onClick={() => { remember(draftOf(item)); setNotice("Draft reset to the currently displayed ticket."); setConflict(false); }}>Discard draft</button>
        </fieldset>
      </form>}
      {notice && <p role="status">{notice}</p>}
      {pending && <button type="button" disabled={saving} onClick={() => void send(pending)}>Retry same request</button>}
      {conflict && <button type="button" disabled={saving} onClick={() => void reloadRevision()}>Load latest revision</button>}
      {!pending && <details open={deleting} onToggle={event => setDeleting(event.currentTarget.open)}><summary>Delete ticket</summary>
        <p>Remove this undeployed ticket from Backlog. Later reviews cannot recreate it without an explicit restore. Deployed Updates and acceptance records are not deleted.</p>
        {active && <label><input type="checkbox" checked={activeConfirmed} onChange={event => setActiveConfirmed(event.target.checked)} /> I understand deletion requests coordinator cancellation, but cannot itself stop the external worker. Cancellation stays pending until acknowledged.</label>}
        <button type="button" className="danger" disabled={saving || conflict || (active && !activeConfirmed)} onClick={() => void send({ operation: "delete", body: { requestId: crypto.randomUUID(), revision: draft?.revision ?? item.revision, confirmActive: activeConfirmed } })}>Confirm delete ticket</button>
      </details>}
    </dialog>
  </>;
}

export function DeletedBacklog({ changed }: { changed: () => Promise<unknown> }) {
  const [items, setItems] = useState<Data[]>([]), [notice, setNotice] = useState("");
  const [pending, setPending] = useState<{ feature: string; body: Data } | null>(() => sessionValue("backlog:restore-request", null));
  const [busy, setBusy] = useState(false);
  async function load() { try { setItems((await api("/backlog/deleted")).items); } catch { setNotice("Deleted tickets could not be loaded."); } }
  async function restore(request: { feature: string; body: Data }) {
    setBusy(true); setPending(request); rememberSession("backlog:restore-request", request);
    let confirmed = false;
    try {
      await api(`/backlog/${encodeURIComponent(request.feature)}/restore`, "POST", request.body);
      confirmed = true; setPending(null); rememberSession("backlog:restore-request", null); setNotice("Ticket restored as queued and unassigned.");
      await Promise.all([load(), changed()]);
    } catch (error) {
      if (confirmed) setNotice("Restored; refresh the backlog to load its current state.");
      else if (error instanceof ApiError && error.status && error.status >= 400 && error.status < 500) { setPending(null); rememberSession("backlog:restore-request", null); setNotice(error.message); await load(); }
      else setNotice("Restore confirmation unavailable. Retry the same request safely.");
    } finally { setBusy(false); }
  }
  return <details onToggle={event => { if (event.currentTarget.open) void load(); }}><summary>Deleted tickets</summary>
    <p>Explicitly restoring adds an undeployed ticket back as queued. It never starts a worker.</p>
    {notice && <p role="status">{notice}</p>}
    {pending && <button disabled={busy} onClick={() => void restore(pending)}>Retry same restore</button>}
    {items.map(item => <div key={item.feature}><strong>{item.title}</strong>
      {item.cancellationRequired && !item.cancellationAcknowledgedAt && <p>Coordinator cancellation acknowledgement pending.</p>}
      <button type="button" className="secondary" disabled={busy || !!pending || (item.cancellationRequired && !item.cancellationAcknowledgedAt)} onClick={() => void restore({ feature: item.feature, body: { requestId: crypto.randomUUID(), revision: item.revision } })}>Restore {item.title}</button>
    </div>)}
    {!items.length && <p>No deleted tickets.</p>}
  </details>;
}
