import { useEffect, useRef, useState } from "react";
import { Mail, X } from "lucide-react";
import { api, ApiError, type Data } from "./api";
import "./mail-browser.css";

const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const path = (account: string, id: string) => `/email/accounts/${encodeURIComponent(account)}/messages/${encodeURIComponent(id)}`;

/** Mounted shell keeps edits in memory across Today pages; opening is explicit. */
export function MailBrowser({ active }: { active: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [opened, setOpened] = useState(false), [visible, setVisible] = useState(false);
  useEffect(() => { if (!active) { dialog.current?.close(); setVisible(false); } }, [active]);
  return <>
    {active && <button className="secondary mail-browser-entry" type="button" onClick={() => {
      setOpened(true); setVisible(true); dialog.current?.showModal();
    }}><Mail size={18} /> Search mail & drafts</button>}
    <dialog ref={dialog} className="mail-browser-dialog" aria-label="Mail browser" onClose={() => setVisible(false)}>
      <header><div><h2>Mail</h2><small>Search your mailbox. Keep drafts in Leam.</small></div>
        <button type="button" className="icon-button" aria-label="Close mail browser" onClick={() => dialog.current?.close()}><X size={20} /></button>
      </header>
      {opened && <MailContent visible={visible} />}
    </dialog>
  </>;
}

function MailContent({ visible }: { visible: boolean }) {
  const [accounts, setAccounts] = useState<Data[]>([]), [account, setAccount] = useState("");
  const [accountOffset, setAccountOffset] = useState<number | null>(null);
  const [tab, setTab] = useState<"search" | "drafts">("search");
  const [query, setQuery] = useState(""), [spam, setSpam] = useState(false);
  const [search, setSearch] = useState<Data | null>(null), [read, setRead] = useState<Data | null>(null);
  const [drafts, setDrafts] = useState<Data | null>(null), [draft, setDraft] = useState<Data | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [editorVersion, setEditorVersion] = useState(0), [editorBusy, setEditorBusy] = useState(false);
  const dirty = useRef(false), request = useRef<AbortController | null>(null);
  const stop = () => { request.current?.abort(); request.current = null; setBusy(false); };
  async function load(url: string, method: string, body: unknown, accept: (result: Data) => void) {
    stop();
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError("");
    try {
      const result = await api(url, method, body, controller.signal);
      if (request.current === controller && !controller.signal.aborted) accept(result);
    } catch (e) {
      if (request.current === controller && !controller.signal.aborted) setError(message(e));
    } finally {
      if (request.current === controller) { request.current = null; setBusy(false); }
    }
  }
  const mailboxes = (offset = 0) => load(`/email/mailboxes?offset=${offset}&limit=20`, "GET", undefined, result => {
    setAccounts(previous => offset ? [...new Map([...previous, ...result.items].map(item => [item.accountId, item])).values()] : result.items);
    setAccountOffset(result.nextOffset ?? null);
    if (!offset) setAccount(current => current || result.items[0]?.accountId || "");
  });
  useEffect(() => {
    void mailboxes();
    const clear = () => { stop(); setAccounts([]); setAccount(""); setSearch(null); setRead(null); setDrafts(null); setDraft(null); setQuery(""); };
    window.addEventListener("leam:auth-lost", clear);
    return () => { request.current?.abort(); request.current = null; window.removeEventListener("leam:auth-lost", clear); };
  }, []);
  useEffect(() => { if (!visible) stop(); }, [visible]);
  function find(more = false) {
    const chosen = more && search ? { accountId: search.accountId, query: search.query, includeSpamTrash: search.includeSpamTrash } : { accountId: account, query, includeSpamTrash: spam };
    if (!chosen.accountId || (more && !search?.nextPageToken)) return;
    void load("/email/search", "POST", { ...chosen, limit: 20, ...(more ? { pageToken: search!.nextPageToken } : {}) }, result => {
      setSearch(previous => more && previous ? { ...result, items: [...new Map([...previous.items, ...result.items].map(item => [item.accountId + ":" + item.messageId, item])).values()] } : result);
      setRead(null);
    });
  }
  function openMessage(item: Data, more = false) {
    const params = new URLSearchParams({ limit: "12000" });
    if (more) { params.set("offset", String(item.nextOffset)); params.set("revision", item.contentRevision); }
    void load(path(item.accountId, item.messageId) + "?" + params, "GET", undefined, result => {
      if (result.accountId !== item.accountId || result.messageId !== item.messageId || (more && result.contentRevision !== item.contentRevision)) { setError("Mailbox returned a different message. Search again."); return; }
      // Keep the current page if continuation conflicts. Never mix changed bodies.
      setRead(previous => more && previous ? { ...result, text: previous.text + result.text, offset: 0 } : result);
    });
  }
  function listDrafts(more = false, selected = account) {
    const params = new URLSearchParams({ limit: "20", offset: String(more ? drafts?.nextOffset ?? 0 : 0) });
    if (selected) params.set("accountId", selected);
    void load("/email/drafts?" + params, "GET", undefined, result => setDrafts(previous => more && previous ? { ...result, items: [...previous.items, ...result.items] } : result));
  }
  function replaceEditor() {
    return !dirty.current || window.confirm("Replace this editor? Unsaved edits will be lost.");
  }
  function compose() {
    if (!account || editorBusy || !replaceEditor()) return;
    stop(); setEditorVersion(value => value + 1);
    dirty.current = false;
    setDraft({ id: crypto.randomUUID(), revision: 0, accountId: account, to: [], cc: [], bcc: [], subject: "", body: "", sourceMessageId: null });
    setTab("drafts");
  }
  const selectedAccount = accounts.find(item => item.accountId === account);
  return <div className="mail-browser-body">
    <div className="mail-browser-tabs" aria-label="Mail sections">
      <button type="button" className="secondary" aria-pressed={tab === "search"} onClick={() => { stop(); setTab("search"); }}>Search mailbox</button>
      <button type="button" className="secondary" aria-pressed={tab === "drafts"} onClick={() => { setTab("drafts"); listDrafts(); }}>Local drafts</button>
    </div>
    <label>Mailbox<select value={account} onChange={e => {
      stop(); setAccount(e.target.value); setSearch(null); setRead(null); setDrafts(null);
      if (tab === "drafts") listDrafts(false, e.target.value);
    }}><option value="">Choose a mailbox</option>{accounts.map(item => <option key={item.accountId} value={item.accountId}>{item.identity}</option>)}</select></label>
    {!accounts.length && !busy && <p>Connect a Google account in Settings to use mail.</p>}
    <div className="actions"><button className="secondary" type="button" disabled={busy} onClick={() => void mailboxes()}>Refresh mailboxes</button>
      {accountOffset != null && <button className="secondary" type="button" disabled={busy} onClick={() => void mailboxes(accountOffset)}>More mailboxes</button>}
    </div>
    {error && <p role="alert">{error}</p>}
    {busy && <p role="status">Loading mail…</p>}
    {tab === "search" && <>
      <form onSubmit={e => { e.preventDefault(); find(); }}>
        <label>Search Gmail<input value={query} maxLength={2000} placeholder="from:someone@example.com or a phrase" onChange={e => setQuery(e.target.value)} /></label>
        <label className="mail-checkbox"><input type="checkbox" checked={spam} onChange={e => setSpam(e.target.checked)} /> Include spam and trash</label>
        <button type="submit" disabled={busy || !selectedAccount?.granted || selectedAccount?.state !== "connected"}>Search mailbox</button>
      </form>
      {account && (!selectedAccount?.granted || selectedAccount?.state !== "connected") && <p>Enable or reconnect read-only Gmail access in Settings to search this mailbox.</p>}
      <small>Search runs only when requested. It is separate from Today’s priority feed and selected day.</small>
      {search && <section aria-label="Mailbox results"><h3>{search.items.length} messages loaded</h3><small>Results for: {search.query || "All mail"}{search.includeSpamTrash ? " · Including spam and trash" : ""}</small>
        {!search.items.length && <p>No matching messages.</p>}
        <div className="mail-results">{search.items.map((item: Data) => <button type="button" className="secondary mail-result" key={item.accountId + ":" + item.messageId} disabled={busy} onClick={() => openMessage(item)}>
          <strong>{item.subject}</strong><span>{item.from}</span><small>{new Date(item.receivedAt).toLocaleString()}</small>
        </button>)}</div>
        {search.nextPageToken && <button type="button" className="secondary" disabled={busy} onClick={() => find(true)}>More search results</button>}
      </section>}
      {read && <section className="mail-reading" aria-label="Message text">
        <h3>{read.subject}</h3><p>From: {read.from}<br />To: {read.to}</p>
        <small>{new Date(read.receivedAt).toLocaleString()} · Plain text</small>
        {read.encodingLoss && <p role="status">Some characters could not be decoded reliably. Check the original in Gmail.</p>}
        {read.headersPartial && <p role="status">Some long headers are shortened.</p>}
        <pre>{read.text || "No displayable message text."}</pre>
        <p>{read.nextOffset ?? read.totalCharacters} of {read.totalCharacters} characters loaded.</p>
        {read.nextOffset != null && <button type="button" className="secondary" disabled={busy} onClick={() => openMessage(read, true)}>Read more message text</button>}
        {!!read.attachments?.length && <details><summary>Attachments · {read.attachments.length}</summary><ul>{read.attachments.map((item: Data, n: number) => <li key={n}>{item.filename || "Unnamed attachment"} · {item.mimeType} · {item.size} bytes · Contents not read</li>)}</ul></details>}
        <button type="button" className="secondary" disabled={busy} onClick={() => openMessage(read)}>Reload message from start</button>
      </section>}
    </>}
    <section hidden={tab !== "drafts"} aria-label="Local email drafts">
      <p>Saved in Leam, not Gmail. These drafts are never sent.</p>
      <button type="button" disabled={!account || busy || editorBusy} onClick={compose}>Compose local draft</button>
      {drafts && <div className="mail-results">{drafts.items.map((item: Data) => <button type="button" className="secondary mail-result" key={item.id} disabled={busy || editorBusy} onClick={() => {
        if (!replaceEditor()) return;
        void load("/email/drafts/" + item.id, "GET", undefined, result => { dirty.current = false; setEditorVersion(value => value + 1); setDraft(result); });
      }}><strong>{item.subject || "Untitled draft"}</strong><small>Updated {new Date(item.updatedAt * 1000).toLocaleString()}</small></button>)}
        {!drafts.items.length && <p>No local drafts for this mailbox.</p>}
        {drafts.nextOffset != null && <button type="button" className="secondary" disabled={busy} onClick={() => listDrafts(true)}>More local drafts</button>}
      </div>}
      {draft && <DraftEditor key={draft.id + ":" + editorVersion} initial={draft} accounts={accounts} blocked={busy} writing={setEditorBusy} dirty={value => { dirty.current = value; }} removed={() => { setDraft(null); dirty.current = false; listDrafts(); }} />}
    </section>
  </div>;
}

const editorFields = (value: Data) => ({ ...value, toText: value.to.join("\n"), ccText: value.cc.join("\n"), bccText: value.bcc.join("\n") });
function DraftEditor({ initial, accounts, blocked, writing, dirty, removed }: { initial: Data; accounts: Data[]; blocked: boolean; writing: (value: boolean) => void; dirty: (value: boolean) => void; removed: () => void }) {
  const [form, setForm] = useState<Data>(() => editorFields(initial)), [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [pending, setPending] = useState<Data | null>(null);
  const locked = useRef(false), alive = useRef(true), controller = useRef<AbortController | null>(null);
  useEffect(() => {
    alive.current = true;
    const cancel = () => { alive.current = false; controller.current?.abort(); };
    window.addEventListener("leam:auth-lost", cancel);
    return () => { cancel(); writing(false); window.removeEventListener("leam:auth-lost", cancel); };
  }, []);
  function edit(key: string, value: unknown) { setForm(current => ({ ...current, [key]: value })); dirty(true); setNotice(""); }
  async function act(action: "save" | "reload" | "delete") {
    if (locked.current || blocked) return;
    if (action === "delete" && !window.confirm("Remove this local draft from Leam? Gmail is unchanged.")) return;
    if (action === "reload" && !window.confirm("Replace the editor with the saved draft? Unsaved edits will be lost.")) return;
    locked.current = true; writing(true); setBusy(true); setError(""); setNotice("");
    controller.current = new AbortController();
    const payload = pending || { revision: form.revision, accountId: form.accountId, to: form.toText.split("\n").filter((value: string) => value.trim()), cc: form.ccText.split("\n").filter((value: string) => value.trim()), bcc: form.bccText.split("\n").filter((value: string) => value.trim()), subject: form.subject, body: form.body, sourceMessageId: form.sourceMessageId || null };
    try {
      const result = await api("/email/drafts/" + initial.id, action === "save" ? "PUT" : action === "delete" ? "DELETE" : "GET",
        action === "save" ? payload : action === "delete" ? { revision: form.revision, confirmed: true } : undefined, controller.current.signal);
      if (!alive.current) return;
      if (action === "delete") { removed(); return; }
      setPending(null); setForm(editorFields(result)); dirty(false);
      setNotice(action === "save" ? "Saved in Leam, not Gmail." : "Saved draft loaded.");
    } catch (e) {
      if (!alive.current) return;
      if (action === "save" && (!(e instanceof ApiError) || !e.status || e.status >= 500)) { setPending(payload); dirty(true); }
      else if (action === "save") setPending(null);
      setError(message(e));
    } finally {
      locked.current = false; writing(false);
      if (alive.current) setBusy(false);
    }
  }
  return <form className="mail-draft-editor" aria-label="Draft editor" onSubmit={e => { e.preventDefault(); void act("save"); }}>
    <h3>{form.revision ? "Edit local draft" : "New local draft"}</h3>
    <small>Save before leaving Today. Closing this panel keeps your edits in memory.</small>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {pending && <p role="status">The previous save may have succeeded. Retry the same save or reload its saved version.</p>}
    <fieldset disabled={busy || blocked || !!pending}>
      <label>Draft mailbox<select value={form.accountId} onChange={e => edit("accountId", e.target.value)}>
        {!accounts.some(item => item.accountId === form.accountId) && <option value={form.accountId}>Original mailbox unavailable</option>}
        {accounts.map(item => <option key={item.accountId} value={item.accountId}>{item.identity}</option>)}
      </select></label>
      <label>To · one address per line<textarea rows={2} value={form.toText} onChange={e => edit("toText", e.target.value)} /></label>
      <details><summary>Cc and Bcc</summary>{["cc", "bcc"].map(key => <label key={key}>{key === "cc" ? "Cc" : "Bcc"} · one address per line<textarea rows={2} value={form[key + "Text"]} onChange={e => edit(key + "Text", e.target.value)} /></label>)}</details>
      <label>Subject<input value={form.subject} maxLength={500} onChange={e => edit("subject", e.target.value)} /></label>
      <label>Draft message<textarea rows={10} value={form.body} maxLength={100000} onChange={e => edit("body", e.target.value)} /></label>
    </fieldset>
    <div className="actions"><button type="submit" disabled={busy || blocked}>{pending ? "Retry same save" : "Save local draft"}</button>
      <button type="button" className="secondary" disabled={busy || blocked || (!form.revision && !pending)} onClick={() => void act("reload")}>Reload saved draft</button>
      {!!form.revision && <button type="button" className="secondary" disabled={busy || blocked || !!pending} onClick={() => void act("delete")}>Remove local draft</button>}
    </div>
  </form>;
}
