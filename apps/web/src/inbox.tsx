import { useEffect, useRef, useState, type ReactNode } from "react";
import { CheckCheck, Inbox, Plus, RefreshCw, Trash2 } from "lucide-react";
import { api, type Data } from "./api";
import { inboxChanged, useInboxStatus } from "./inbox-status";
import { InboxRemovalDialog, removalKey, type RemovalTarget } from "./inbox-removal";
import { EmailTask } from "./email-task";
import "./inbox.css";

type Item = {
  id: string;
  sequence: number;
  subject: string;
  body?: string;
  preview: string;
  createdAt: number;
  origin: string;
  unread: boolean;
  links: Array<{
    type: string;
    id: string;
    available: boolean;
    title: string | null;
    url: string | null;
  }>;
};
function InboxCard({
  item,
  expanded,
  mark,
  remove,
}: {
  item: Item;
  expanded: boolean;
  mark: (id: string, read: boolean) => Promise<void>;
  remove: () => void;
}) {
  const [detail, setDetail] = useState<Item | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const loading = useRef(false);
  async function load() {
    if (detail || loading.current) return;
    loading.current = true;
    try {
      setDetail((await api(`/inbox/${item.id}`)) as Item);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      loading.current = false;
    }
  }
  useEffect(() => {
    if (expanded) void load();
  }, [expanded]);
  return (
    <article className="inbox-card" data-unread={item.unread}>
      <details
        open={expanded || undefined}
        onToggle={(event) => {
          if (event.currentTarget.open) void load();
        }}
      >
        <summary>
          <span className="inbox-title"><small className="inbox-source" data-source="leam">Leam</small>
            {item.unread && (
              <span className="inbox-unread-dot" aria-label="Unread" />
            )}
            {item.subject}
          </span>
          <time dateTime={new Date(item.createdAt * 1000).toISOString()}>
            {new Date(item.createdAt * 1000).toLocaleString([], {
              dateStyle: "short",
              timeStyle: "short",
            })}
          </time>
        </summary>
        <div className="inbox-content">
          {error ? (
            <p role="alert">
              {error} <button onClick={() => void load()}>Retry</button>
            </p>
          ) : detail ? (
            <p className="inbox-body">{detail.body}</p>
          ) : (
            <p role="status">Loading note…</p>
          )}
          <div className="inbox-links">
            {(detail || item).links.map((link) =>
              link.available && link.url ? (
                <a key={`${link.type}:${link.id}`} href={link.url}>
                  {link.title || link.type}
                </a>
              ) : (
                <span key={`${link.type}:${link.id}`}>
                  Linked {link.type} unavailable
                </span>
              ),
            )}
          </div>
          <footer>
            <small>
              {item.origin === "automation"
                ? "Automated update"
                : item.origin === "companion"
                  ? "Saved by Leam"
                  : "Your note"}
            </small>
            {(
              <button
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await mark(item.id, item.unread);
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <CheckCheck size={16} /> {item.unread ? "Mark as read" : "Mark as unread"}
              </button>
            )}
            <button type="button" className="icon-button" aria-label={`Remove ${item.subject} from Inbox`} onClick={remove}><Trash2 size={17} /></button>
          </footer>
        </div>
      </details>
    </article>
  );
}
export function InboxPage({ mail = [], accounts = [], mailActions, controls, changed }: {
  mail?: Data[]; accounts?: Data[]; mailActions?: (item: Data) => ReactNode; controls?: ReactNode; changed?: () => void;
}) {
  const [source, setSource] = useState("all");
  const [onlyUnread, setOnlyUnread] = useState(false);
  const [mailReads, setMailReads] = useState<Set<string> | null>(null);
  const [mailError, setMailError] = useState("");
  const [mailRemoved, setMailRemoved] = useState<Set<string> | null>(null);
  const [mailCheckedIdentity, setMailCheckedIdentity] = useState<string | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [checked, setChecked] = useState<Map<string, RemovalTarget>>(new Map());
  const [removing, setRemoving] = useState<RemovalTarget[] | null>(null);
  const locallyRemovedNotes = useRef(new Set<string>());
  const mailReadVersion = useRef(0);
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [composing, setComposing] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [selected, setSelected] = useState<Item | null>(null);
  const pending = useRef<{
    requestId: string;
    subject: string;
    body: string;
  } | null>(null);
  const version = useRef(0);
  const target = useRef(new URLSearchParams(location.search).get("inboxItem"));
  async function load(more = false) {
    const current = ++version.current;
    try {
      const value = await api(
        `/inbox${more && data?.nextCursor ? `?before=${data.nextCursor}` : ""}`,
      );
      if (current !== version.current) return;
      setData((old) => ({
        ...value,
        items: more ? [...(old?.items || []), ...value.items] : value.items,
      }));
      setError("");
    } catch (e) {
      if (current === version.current) setError((e as Error).message);
    }
  }
  useEffect(() => {
    void load();
    let current = true;
    if (target.current)
      void api(`/inbox/${encodeURIComponent(target.current)}`)
        .then((value) => {
          if (current && !locallyRemovedNotes.current.has(value.id)) setSelected(value as Item);
        })
        .catch((e) => {
          if (current) setError(e.message);
        });
    return () => {
      current = false;
      ++version.current;
    };
  }, []);
  async function mark(id: string, read = true) {
    await api(`/inbox/${id}/${read ? "read" : "unread"}`, "POST");
    setData((old) =>
      old
        ? {
            ...old,
            items: old.items.map((item: Item) =>
              item.id === id ? { ...item, unread: !read } : item,
            ),
          }
        : old,
    );
    setSelected((old) => (old?.id === id ? { ...old, unread: !read } : old));
    inboxChanged();
    await load();
  }
  async function loadMailReads() {
    const current = ++mailReadVersion.current;
    try {
      const keys = [...new Set(mail.map(item => String(item.key)))];
      const chunks = keys.length ? Array.from({ length: Math.ceil(keys.length / 100) }, (_, index) => keys.slice(index * 100, index * 100 + 100)) : [[]];
      const reads = new Set<string>(), removals = new Set<string>();
      // Bound each request to 100 keys and concurrency to four; no provider calls.
      for (let offset = 0; offset < chunks.length; offset += 4) {
        const values = await Promise.all(chunks.slice(offset, offset + 4).map(chunk => {
          const query = new URLSearchParams(); chunk.forEach(key => query.append("keys", key));
          return api(`/inbox-mail/read${query.size ? "?" + query.toString() : ""}`);
        }));
        if (current !== mailReadVersion.current) return;
        values.forEach(value => {
          (value.readKeys || []).forEach((key: string) => reads.add(key));
          (value.removedKeys || []).forEach((key: string) => removals.add(key));
        });
      }
      if (current !== mailReadVersion.current) return;
      setMailReads(reads); setMailRemoved(removals); setMailCheckedIdentity(mailIdentity); setMailError("");
    } catch (failure) { if (current === mailReadVersion.current) { setMailReads(null); setMailError((failure as Error).message); } }
  }
  const mailIdentity = mail.map(item => item.key).join("|");
  useEffect(() => { void loadMailReads(); return () => { ++mailReadVersion.current; }; }, [mailIdentity]);
  async function markMail(keys: string[], read = true) {
    if (!keys.length) return;
    ++mailReadVersion.current;
    await api("/inbox-mail/read", "POST", { keys, read });
    ++mailReadVersion.current;
    setMailReads(old => { const next = new Set(old || []); keys.forEach(key => read ? next.add(key) : next.delete(key)); return next; });
  }
  async function markAll() {
    setBusy(true); setError("");
    try {
      if ((source === "all" || source === "leam") && data) {
        await api("/inbox/read-all", "POST", { throughSequence: data.throughSequence });
        setSelected(old => old && old.sequence <= data.throughSequence ? { ...old, unread: false } : old);
        inboxChanged(); await load();
      }
      await markMail(filteredMail.filter(item => !mailReads?.has(item.key)).map(item => item.key));
    } catch (e) { setError("Some items may already be marked read. " + (e as Error).message); }
    finally { setBusy(false); }
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    const payload =
      pending.current?.subject === subject && pending.current?.body === body
        ? pending.current
        : { requestId: crypto.randomUUID(), subject, body };
    pending.current = payload;
    setBusy(true);
    try {
      await api("/inbox", "POST", payload);
      pending.current = null;
      setSubject("");
      setBody("");
      setComposing(false);
      inboxChanged();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const listed: Item[] = data?.items || [];
  const filteredMail = mail.filter(item => mailRemoved !== null && mailCheckedIdentity === mailIdentity && !mailRemoved.has(item.key) && (source === "all" || source === "mail" || source === "account:" + item.accountId));
  const notes = source === "all" || source === "leam" ? listed : [];
  const mixed = [
    ...notes.filter(item => !onlyUnread || item.unread).map(item => ({ kind: "leam" as const, id: item.id, time: item.createdAt, item })),
    ...filteredMail.filter(item => !onlyUnread || (mailReads && !mailReads.has(item.key))).map(item => ({ kind: "mail" as const, id: item.key, time: Date.parse(item.receivedAt) / 1000 || 0, item })),
  ].sort((a,b) => b.time - a.time || (a.kind + a.id).localeCompare(b.kind + b.id));
  const extraNote = selected && (source === "all" || source === "leam") && (!onlyUnread || selected.unread) && !listed.some(item => item.id === selected.id) ? selected : null;
  const visibleTargets: RemovalTarget[] = [
    ...(extraNote ? [{ source: "leam" as const, id: extraNote.id, label: extraNote.subject }] : []),
    ...mixed.map(row => ({ source: row.kind, id: row.id, label: String(row.item.subject || "(No subject)") })),
  ];
  useEffect(() => { setChecked(new Map()); }, [source, onlyUnread]);
  const visibleIdentity = visibleTargets.map(removalKey).join("|");
  useEffect(() => {
    const visible = new Set(visibleTargets.map(removalKey));
    setChecked(old => new Map([...old].filter(([key]) => visible.has(key))));
  }, [visibleIdentity]);
  function toggle(item: RemovalTarget) {
    setChecked(old => {
      const value = new Map(old), key = removalKey(item);
      if (value.has(key)) value.delete(key);
      else if (value.size < 100) value.set(key, item);
      return value;
    });
  }
  function removed(receipt: Data) {
    const keys = new Set<string>(receipt.removedKeys || []);
    ++version.current; ++mailReadVersion.current;
    keys.forEach(key => { if (key.startsWith("leam:")) locallyRemovedNotes.current.add(key.slice(5)); });
    setData(old => old ? { ...old, ...receipt.notes, items: old.items.filter((item: Item) => !keys.has("leam:" + item.id)) } : old);
    setSelected(old => old && keys.has("leam:" + old.id) ? null : old);
    setMailRemoved(old => new Set([...(old || []), ...[...keys].filter(key => key.startsWith("email:"))]));
    setChecked(old => new Map([...old].filter(([, item]) => !keys.has(item.source === "leam" ? "leam:" + item.id : item.id))));
    inboxChanged();
  }
  function selection(item: RemovalTarget) {
    return selecting ? <label className="inbox-item-select"><input type="checkbox" aria-label={`Select ${item.label}`} checked={checked.has(removalKey(item))}
      disabled={checked.size >= 100 && !checked.has(removalKey(item))} onChange={() => toggle(item)} /></label> : null;
  }
  const mailStatusUnknown = source !== "leam" && mail.length > 0 && (mailReads === null || mailRemoved === null || mailCheckedIdentity !== mailIdentity);
  const unreadMail = mailReads ? filteredMail.filter(item => !mailReads.has(item.key)).length : 0;
  const unreadNotes = source === "all" || source === "leam" ? data?.unreadCount || 0 : 0;
  return (
    <section className="inbox-page" aria-label="Inbox">
      <header className="inbox-heading">
        <div>
          <h1>
            <Inbox size={26} /> Inbox
          </h1>
          <p>Gmail actions and Leam notes, together. Reading here never changes Gmail or completes a task.</p>
        </div>
        <div className="inbox-actions">{controls}
          <button
            className="icon-button"
            aria-label="Refresh inbox"
            onClick={() => { void load(); void loadMailReads(); }}
          >
            <RefreshCw size={19} />
          </button>
          <button
            className="icon-button"
            aria-label="Create inbox note"
            aria-expanded={composing}
            onClick={() => setComposing(!composing)}
          >
            <Plus size={20} />
          </button>
        </div>
      </header>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {composing && (
        <form className="inbox-compose" onSubmit={(event) => void save(event)}>
          <label>
            Subject
            <input
              disabled={busy}
              value={subject}
              maxLength={200}
              required
              onChange={(e) => setSubject(e.target.value)}
            />
          </label>
          <label>
            Note
            <textarea
              disabled={busy}
              value={body}
              maxLength={8192}
              rows={5}
              required
              onChange={(e) => setBody(e.target.value)}
            />
          </label>
          <div>
            <button disabled={busy || !subject.trim() || !body.trim()}>
              {busy ? "Saving…" : "Save note"}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => setComposing(false)}
            >
              Close
            </button>
          </div>
        </form>
      )}
      <div className="inbox-filters">
        <label>Source<select aria-label="Inbox source" value={source} onChange={event => setSource(event.target.value)}>
          <option value="all">All sources</option><option value="leam">Leam</option><option value="mail">Gmail</option>
          {accounts.map(account => <option key={account.accountId} value={"account:" + account.accountId}>Gmail · {account.identity}</option>)}
        </select></label>
        <label className="inbox-unread-filter"><input type="checkbox" checked={onlyUnread} onChange={event => setOnlyUnread(event.target.checked)} />Unread only</label>
      </div>
      {mailError && <p role="alert">Local mail read status unavailable: {mailError} <button onClick={() => void loadMailReads()}>Retry read status</button></p>}
      {mail.length > 0 && (mailRemoved === null || mailCheckedIdentity !== mailIdentity) && <p role="status">{mailError ? "Mail visibility could not be checked. Retry local status before viewing mail." : "Loading saved mail visibility…"}</p>}
      {(data || mail.length > 0) && (
        <div className="inbox-toolbar">
          <span>
            {unreadNotes + unreadMail} unread{mailStatusUnknown ? " · mail status unknown" : ""} · {mixed.length} shown
          </span>
          <button
            className="secondary"
            disabled={busy || (!unreadNotes && !unreadMail) || (filteredMail.length > 0 && mailReads === null)}
            onClick={() => void markAll()}
          >
            Mark all as read
          </button>
        </div>
      )}
      <div className="inbox-selection-toolbar">
        <button className="secondary" aria-pressed={selecting} onClick={() => { setSelecting(!selecting); setChecked(new Map()); }}>{selecting ? "Done selecting" : "Select items"}</button>
        {selecting && <>
          <span>{checked.size} selected</span>
          <button disabled={!visibleTargets.length} onClick={() => setChecked(new Map(visibleTargets.slice(0, 100).map(item => [removalKey(item), item])))}>Select visible{visibleTargets.length > 100 ? " (first 100)" : ""}</button>
          <button disabled={!checked.size} onClick={() => setChecked(new Map())}>Clear selection</button>
          <button disabled={!checked.size} onClick={() => setRemoving([...checked.values()])}><Trash2 size={16} /> Remove selected</button>
        </>}
      </div>
      {extraNote && <div className="inbox-selectable-row">{selection({ source: "leam", id: extraNote.id, label: extraNote.subject })}
        <InboxCard item={extraNote} expanded mark={mark} remove={() => setRemoving([{ source: "leam", id: extraNote.id, label: extraNote.subject }])} />
      </div>}
      {!data && !error && <p role="status">Loading inbox…</p>}
      {data && !mixed.length && !mailStatusUnknown && (
        <p className="empty">
          No items match this view. Leam notes and actionable saved mail appear here.
        </p>
      )}
      <div className="inbox-list">
        {mixed.map(row => {
          const item: RemovalTarget = { source: row.kind, id: row.id, label: String(row.item.subject || "(No subject)") };
          return <div className="inbox-selectable-row" key={removalKey(item)}>{selection(item)}{row.kind === "leam" ? (
            <InboxCard item={row.item as Item} expanded={row.id === target.current} mark={mark} remove={() => setRemoving([item])} />
          ) : <MailInboxCard item={row.item} account={accounts.find(account => account.accountId === row.item.accountId)?.identity}
            changed={changed} unread={mailReads ? !mailReads.has(row.id) : null} mark={read => markMail([row.id], read)} actions={mailActions?.(row.item)} remove={() => setRemoving([item])} />}</div>;
        })}
      </div>
      {removing && <InboxRemovalDialog items={removing} onRemoved={removed} onClose={() => setRemoving(null)} />}
      <InboxDeliveryStatus />
      {data?.nextCursor && (
        <button className="secondary" onClick={() => void load(true)}>
          Load older notes
        </button>
      )}
    </section>
  );
}

function MailInboxCard({ item, account, unread, mark, actions, remove, changed }: {
  item: Data; account?: string; unread: boolean | null;
  mark: (read: boolean) => Promise<void>; actions?: ReactNode; remove: () => void; changed?: () => void;
}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  return <article className="inbox-card inbox-mail-card" data-unread={unread === true}>
    <details><summary><span className="inbox-title">
      <small className="inbox-source" data-source="mail">Gmail · {account || item.identity || "Connected account"}</small>
      {unread && <span className="inbox-unread-dot" aria-label="Unread in Leam" />}<span role="heading" aria-level={3}>{item.subject || "(No subject)"}</span>
    </span><time>{new Date(item.receivedAt).toLocaleDateString()}</time></summary>
    <div className="inbox-content"><p><strong>{item.from}</strong></p>
      {item.actionability?.action && <p>{item.actionability.action}</p>}
      {item.snippet && <p>{item.snippet}</p>}
      {item.url && <a href={item.url} target="_blank" rel="noreferrer">Open email in Gmail</a>}
      {actions}
      {item.actionability?.state === "action" && <EmailTask item={item} changed={changed} />}
      <footer><small>Read status is local to Leam. Gmail labels stay unchanged.</small>
        <button disabled={busy || unread === null} onClick={async () => {
          setBusy(true); try { await mark(!!unread); setError(""); }
          catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
        }}>{unread === false ? "Mark as unread" : "Mark as read"}</button>
      <button type="button" className="icon-button" aria-label={`Remove ${item.subject || "email"} from Inbox`} onClick={remove}><Trash2 size={17} /></button>
      </footer>{error && <p role="alert">{error}</p>}
    </div></details>
  </article>;
}


function InboxDeliveryStatus() {
  const delivery = useInboxStatus()?.automationDelivery;
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState("");
  if (!delivery || (!delivery.failed && !delivery.retrying)) return null;
  return <details className="inbox-delivery-status">
    <summary>{delivery.failed + delivery.retrying} action notes awaiting delivery</summary>
    <p role="status">The original actions completed. Only their Inbox notifications are delayed.
      {delivery.paused ? " Automation is paused after a restore; resume it in Backups settings." : ""}</p>
    {delivery.failures.map(failure => <div key={failure.eventId}>
      <span>Notification {failure.eventId} · {failure.attempts >= 3 ? "needs retry" : "retry scheduled"}</span>{" "}
      <button className="secondary" disabled={busy !== null || delivery.paused} onClick={async () => {
        setBusy(failure.eventId); setError("");
        try { await api(`/inbox/automation/${failure.eventId}/retry`, "POST"); inboxChanged(); }
        catch { setError("Notification retry could not be confirmed. Refresh the Inbox before trying again."); }
        finally { setBusy(null); }
      }}>Retry notification only</button>
    </div>)}
    {error && <p role="alert">{error}</p>}
  </details>;
}
