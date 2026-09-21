import { deliveryReceipt, queuedNotice } from "./companion-delivery";
import {
  cachedChat,
  rememberChat,
  sessionValue,
  rememberSession,
  clearAcceptedDraft,
  forgetChat,
} from "./session-cache";
import { useEffect, useState, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, chatRead, type Data } from "./api";
import { useChatScroll } from "./chat-scroll";
import { AttachmentComposer, useAttachmentDraft } from "./attachments";
import { ConversationList } from "./conversation-list";
import { CodingModelSettings } from "./coding-model-settings";
import { ProposalList } from "./proposals";
import { MemoryApprovalSettings } from "./memory-approval";
import { useCompanionStream } from "./companion-stream";
import { ReadAloud } from "./voice/controls";
import { VoiceComposer } from "./voice/composer";
import { stopConversation, type Receipt } from "./voice/conversation";
import { stopRecognition } from "./voice/speech";

type Props = { fail: (error: unknown) => void };
export function ModelSettings({ fail }: Props) {
  const [providers, setProviders] = useState<Data[]>([]),
    [selected, setSelected] = useState("openai_codex"),
    [model, setModel] = useState("gpt-5.6-sol"),
    [models, setModels] = useState<Data[]>([]),
    [effort, setEffort] = useState("medium"),
    [catalogError, setCatalogError] = useState(""),
    [baseUrl, setBaseUrl] = useState(""),
    [key, setKey] = useState(""),
    [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false),
    [login, setLogin] = useState<Data | null>(null);
  const provider = providers.find((p) => p.id === selected);
  const subscription = selected === "openai_codex";
  const selectedModel = models.find((m) => m.model === model);
  const efforts = selectedModel?.supportedReasoningEfforts || [];
  const load = async () => {
    const results = await Promise.allSettled([
      api("/settings/providers"),
      api("/settings/models"),
    ]);
    if (results[0].status === "fulfilled") {
      const r = results[0].value;
      setProviders(r.providers || []);
      if (r.active) {
        setSelected(r.active.provider_id);
        setModel(r.active.model || "gpt-5.6-sol");
        setEffort(r.active.reasoning_effort || "medium");
      }
    } else fail(results[0].reason);
    if (results[1].status === "fulfilled") {
      setModels(results[1].value.models || []);
      setCatalogError("");
    } else
      setCatalogError(
        "Could not load your available models. Refresh to retry.",
      );
  };
  useEffect(() => {
    load();
  }, []);
  function choose(id: string) {
    const p = providers.find((p) => p.id === id);
    setSelected(id);
    setModel(
      id === "openai_codex"
        ? "gpt-5.6-sol"
        : p?.active_model || p?.default_model || "",
    );
    setEffort("medium");
    setBaseUrl(p?.base_url || "");
    setKey("");
    setStatus("");
  }
  async function operate(action: string) {
    if (!provider) return;
    setBusy(true);
    setStatus("");
    const data = {
      id: selected,
      adapter: provider.adapter,
      model,
      baseUrl: baseUrl || undefined,
      apiKey: key || undefined,
    };
    try {
      if (action === "active") {
        await api("/settings/providers/active", "POST", {
          providerId: selected,
          model,
          ...(subscription ? { reasoningEffort: effort } : {}),
        });
        setStatus(
          "Model settings saved. New companion requests use this selection; current coding sessions keep their settings.",
        );
      } else if (action === "test") {
        const r = await api("/settings/providers/test", "POST", data);
        setStatus(r.message);
      } else {
        await api("/settings/providers", "POST", data);
        setKey("");
        setStatus("Provider saved.");
      }
      await load();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="card settings-form">
      <h3>Companion model</h3>
      <p>
        Choose the model your companion uses. New coding session defaults are
        configured separately below.
      </p>
      <label>
        Model provider
        <select
          aria-label="Model provider"
          disabled={busy}
          value={selected}
          onChange={(e) => choose(e.target.value)}
        >
          <option value="">Choose a provider</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.id === "openai_codex" ? "ChatGPT subscription" : p.id}
              {p.active ? " · active" : ""}
            </option>
          ))}
        </select>
      </label>
      {provider && (
        <>
          <p>{provider.description}</p>
          <label>
            Model
            {subscription ? (
              <select
                aria-label="Companion model"
                value={model}
                disabled={busy || !models.length}
                onChange={(e) => {
                  setModel(e.target.value);
                  const next = models.find((m) => m.model === e.target.value);
                  const supported = next?.supportedReasoningEfforts || [];
                  if (
                    !supported.some((v: Data) => v.reasoningEffort === effort)
                  )
                    setEffort(next?.defaultReasoningEffort || "medium");
                }}
              >
                {!models.some((m) => m.model === model) && (
                  <option value={model}>
                    {model} · availability unconfirmed
                  </option>
                )}
                {models.map((m) => (
                  <option key={m.model} value={m.model}>
                    {m.displayName || m.model}
                  </option>
                ))}
              </select>
            ) : (
              <input value={model} onChange={(e) => setModel(e.target.value)} />
            )}
          </label>
          {subscription && (
            <label>
              Reasoning effort
              <select
                aria-label="Reasoning effort"
                disabled={busy || !efforts.length}
                value={effort}
                onChange={(e) => setEffort(e.target.value)}
              >
                {efforts.map((item: Data) => (
                  <option
                    key={item.reasoningEffort}
                    value={item.reasoningEffort}
                  >
                    {item.reasoningEffort}
                  </option>
                ))}
              </select>
            </label>
          )}
          {subscription && (
            <p>
              Uses your ChatGPT subscription through a separate Leam sign-in.
              Available models are read from your Codex account.
            </p>
          )}
          {catalogError && subscription && <p role="alert">{catalogError}</p>}
          {!subscription && (
            <label>
              Provider endpoint
              <input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="Provider default"
              />
            </label>
          )}
          {provider.accepts_api_key && (
            <label>
              API key
              <input
                type="password"
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder={
                  provider.api_key_set
                    ? "Saved · leave blank to keep"
                    : "Enter your key"
                }
              />
            </label>
          )}
          <div className="actions">
            {!subscription && (
              <>
                <button
                  className="secondary"
                  disabled={busy || !model}
                  onClick={() => operate("save")}
                >
                  Save provider
                </button>
                <button
                  className="secondary"
                  disabled={busy || !model}
                  onClick={() => operate("test")}
                >
                  Test connection
                </button>
              </>
            )}
            <button
              className="primary"
              disabled={
                busy ||
                !model ||
                (subscription &&
                  (!selectedModel ||
                    !efforts.some(
                      (item: Data) => item.reasoningEffort === effort,
                    )))
              }
              onClick={() => operate("active")}
            >
              Use this model
            </button>
          </div>
        </>
      )}
      <details>
        <summary>Use a ChatGPT subscription</summary>
        <p>Connect IronClaw separately with your ChatGPT account.</p>
        <button
          className="secondary"
          onClick={() =>
            api("/settings/providers/codex-login", "POST", {})
              .then(setLogin)
              .catch(fail)
          }
        >
          Start device sign-in
        </button>
        {login && (
          <p>
            Enter <strong>{login.user_code}</strong> at{" "}
            <a
              href={
                /^https:\/\//.test(login.verification_uri)
                  ? login.verification_uri
                  : undefined
              }
              target="_blank"
              rel="noopener noreferrer"
            >
              the sign-in page
            </a>
            , then refresh providers.
          </p>
        )}
      </details>
      <button className="secondary" onClick={load}>
        Refresh providers
      </button>
      {status && <p role="status">{status}</p>}
      <CodingModelSettings fail={fail} />
    </div>
  );
}

export function MemorySettings({ fail }: Props) {
  const [items, setItems] = useState<Data[]>([]),
    [editing, setEditing] = useState<Data | null>(null),
    [text, setText] = useState(""),
    [source, setSource] = useState("Told directly by me"),
    [saving, setSaving] = useState(false),
    [activityOpen, setActivityOpen] = useState(false);
  const load = () =>
    api("/memory")
      .then((r) => setItems(r.items || []))
      .catch(fail);
  useEffect(() => {
    load();
  }, []);
  return (
    <fieldset className="card settings-form" disabled={saving}>
      <h3>What Leam remembers</h3>
      <p>
        Inspect and correct the context your companion draws on. Each memory
        keeps its source. Forget removes it from future context; earlier
        conversations and delivery records still contain their historical
        copies.
      </p>
      <MemoryApprovalSettings fail={fail} />
      <details
        className="settings-section"
        onToggle={(e) => setActivityOpen(e.currentTarget.open)}
      >
        <summary>Memory suggestions and activity</summary>
        {activityOpen && (
          <ProposalList memoryOnly fail={fail} onChanged={load} />
        )}
      </details>
      <details className="settings-section" open={Boolean(editing)}>
        <summary>{editing ? "Edit memory" : "Add a memory"}</summary>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (saving) return;
            setSaving(true);
            try {
              await api(
                editing ? "/memory/" + editing.id : "/memory",
                editing ? "PATCH" : "POST",
                {
                  text,
                  source,
                  ...(editing ? { revision: editing.revision } : {}),
                },
              );
              setText("");
              setEditing(null);
              await load();
            } catch (e) {
              fail(e);
            } finally {
              setSaving(false);
            }
          }}
        >
          <label>
            Memory
            <textarea
              aria-label="Memory"
              required
              value={text}
              onChange={(e) => {
                stopRecognition();
                setText(e.target.value);
              }}
            />
          </label>
          <label>
            Source
            <input
              required
              value={source}
              onChange={(e) => setSource(e.target.value)}
            />
          </label>
          <button className="secondary">
            {editing ? "Save correction" : "Remember this"}
          </button>
        </form>
      </details>
      {items.map((item) => (
        <div className="memory-entry" key={item.id}>
          <p>{item.text}</p>
          <small>{item.source}</small>
          <button
            className="secondary"
            onClick={() => {
              setEditing(item);
              setText(item.text);
              setSource(item.source);
            }}
          >
            Edit
          </button>
          <button
            className="secondary"
            onClick={async () => {
              setSaving(true);
              try {
                await api(
                  `/memory/${item.id}?revision=${item.revision}`,
                  "DELETE",
                );
                await load();
              } catch (e) {
                fail(e);
              } finally {
                setSaving(false);
              }
            }}
          >
            Forget
          </button>
        </div>
      ))}
    </fieldset>
  );
}

export function Companion({ fail, fixedThread, onChanged }: Props & { fixedThread?: string; onChanged?: () => void }) {
  const initialId = useRef(fixedThread || sessionValue("companion:selected", "")).current;
  const initial = cachedChat("companion:" + initialId);
  const [system, setSystem] = useState<Data | null>(null);
  const [threads, setThreads] = useState<Data[]>(
      () => cachedChat("companion:threads")?.threads || [],
    ),
    [thread, setThread] = useState(initialId),
    [messages, setMessages] = useState<Data[]>(() => initial?.messages || []),
    [text, setText] = useState(() =>
      sessionValue("companion:draft:" + initialId, ""),
    ),
    [busy, setBusy] = useState(false),
    [fresh, setFresh] = useState(""),
    [cursor, setCursor] = useState<string | null>(null),
    [loadingEarlier, setLoadingEarlier] = useState(false);
  const chatScroll = useChatScroll(thread);
  const files = useAttachmentDraft("companion:" + thread);
  const [uploading, setUploading] = useState(false);
  const [stoppingRun, setStoppingRun] = useState("");
  const submitting = useRef(false);
  const olderLoaded = useRef(Boolean(initial?.olderLoaded));
  const refreshSequence = useRef(0);
  const validatedAt = useRef(initial?.validatedAt || 0);
  const selected = useRef(initialId),
    attempt = useRef<{ thread: string; text: string; id: string; attachmentIds?: string[] } | null>(null);
  const [threadCursor, setThreadCursor] = useState<string | null>(
    () => cachedChat("companion:threads")?.nextCursor || null,
  );
  const [listing, setListing] = useState(false);
  const listRequest = useRef(0),
    listBusy = useRef(false);
  async function load(more = false) {
    if (fixedThread || listBusy.current || (more && !threadCursor)) return;
    listBusy.current = true;
    setListing(true);
    const request = ++listRequest.current;
    try {
      const r = await api(
        "/companion/threads" +
          (more ? "?cursor=" + encodeURIComponent(threadCursor!) : ""),
      );
      if (request !== listRequest.current) return;
      const next = r.next_cursor || null;
      setThreads((previous) => {
        const rows = more
          ? [
              ...new Map(
                [...previous, ...(r.threads || [])].map((item) => [
                  item.thread_id,
                  item,
                ]),
              ).values(),
            ]
          : r.threads || [];
        const chosen = previous.find(
          (item) => item.thread_id === selected.current,
        );
        if (
          chosen &&
          !rows.some((item: Data) => item.thread_id === chosen.thread_id)
        )
          rows.unshift(chosen);
        rememberChat("companion:threads", { threads: rows, nextCursor: next });
        return rows;
      });
      setThreadCursor(next === threadCursor && more ? null : next);
    } catch (e) {
      if (request === listRequest.current) fail(e);
    } finally {
      if (request === listRequest.current) {
        listBusy.current = false;
        setListing(false);
      }
    }
  }
  function changedConversation(id: string, value: Data | null) {
    listRequest.current++;
    listBusy.current = false;
    setListing(false);
    setThreads((previous) => {
      const rows = value
        ? previous.map((item) =>
            item.thread_id === id ? { ...item, ...value } : item,
          )
        : previous.filter((item) => item.thread_id !== id);
      rememberChat("companion:threads", {
        threads: rows,
        nextCursor: threadCursor,
      });
      return rows;
    });
    if (value && selected.current === id)
      rememberSession("companion:selectedItem", {
        ...(threads.find((item) => item.thread_id === id) || {}),
        ...value,
      });
    if (!value) {
      forgetChat("companion:" + id);
      rememberSession("companion:draft:" + id, "");
      try {
        sessionStorage.removeItem("leam-companion-submission:" + id);
      } catch {
        /* Runtime deletion succeeded; display cleanup must continue. */
      }
      if (selected.current === id) choose("");
    }
  }
  async function refresh(id: string, allowFresh = false) {
    if (allowFresh && Date.now() - validatedAt.current < 5000) return;
    const seq = ++refreshSequence.current;
    const r = await chatRead("/companion/threads/" + id);
    if (selected.current === id && seq === refreshSequence.current) {
      validatedAt.current = Date.now();
      setMessages((previous) =>
        [
          ...new Map(
            [...previous, ...(r.messages || [])].map((m) => [m.message_id, m]),
          ).values(),
        ].sort((a, b) => a.sequence - b.sequence),
      );
      if (!olderLoaded.current) setCursor(r.next_cursor || null);
      setFresh(new Date().toLocaleTimeString());
    }
  }
  const live = useCompanionStream(thread, () => {
    if (selected.current === thread) void refresh(thread).catch(fail);
  });
  async function stopRun(runId: string) {
    const ownerThread = thread;
    const key = `leam-companion-stop:${ownerThread}:${runId}`;
    const requestId = sessionStorage.getItem(key) || crypto.randomUUID();
    sessionStorage.setItem(key, requestId);
    setStoppingRun(runId);
    try {
      const receipt = await api(
        `/companion/threads/${encodeURIComponent(ownerThread)}/runs/${encodeURIComponent(runId)}/cancel`,
        "POST",
        { requestId },
      );
      if (selected.current === ownerThread)
        live.stopAcknowledged(runId, receipt.status);
    } catch (error) {
      fail(error);
    } finally {
      if (selected.current === ownerThread) {
        setStoppingRun("");
        await refresh(ownerThread).catch(fail);
      }
    }
  }
  const [deliveryNotice, setDeliveryNotice] = useState("");
  function acceptDelivery(
    accepted: Data,
    id: string,
    submission: { thread: string; text: string; id: string; attachmentIds?: string[] },
  ): Receipt {
    const receipt = deliveryReceipt(accepted, id);
    if (!receipt) {
      if (
        accepted.outcome === "rejected_busy" &&
        (accepted.thread_id === undefined || accepted.thread_id === id)
      ) {
        sessionStorage.removeItem("leam-companion-submission:" + id);
        if (selected.current === id && attempt.current?.id === submission.id) {
          attempt.current = null;
          setDeliveryNotice(
            "This message was not sent because Leam is busy. Your draft is kept. Wait for the current response, then send it explicitly.",
          );
        }
        throw new Error(
          "Leam is busy and did not accept this message. Your draft is kept; wait for the current response before sending.",
        );
      }
      throw new Error(
        "Message delivery is uncertain. Check the conversation before retrying this saved message.",
      );
    }
    sessionStorage.removeItem("leam-companion-submission:" + id);
    clearAcceptedDraft("companion", id, submission.text);
    files.clear(submission.attachmentIds || []);
    if (selected.current === id && attempt.current?.id === submission.id) {
      live.accepted(receipt.run_id);
      attempt.current = null;
      setText((current) => (current === submission.text ? "" : current));
      setDeliveryNotice(
        receipt.outcome === "deferred_busy" ? queuedNotice : "",
      );
    }
    void Promise.all([refresh(id), load()]).catch(fail);
    return receipt;
  }
  async function reconcileSubmission(
    id: string,
    submission: { thread: string; text: string; id: string; attachmentIds?: string[] },
  ) {
    try {
      const saved = await api(
        `/companion/threads/${encodeURIComponent(id)}/submissions/${encodeURIComponent(submission.id)}`,
      );
      if (selected.current !== id || attempt.current?.id !== submission.id)
        return;
      if (saved.state === "recorded" && saved.receipt)
        acceptDelivery(saved.receipt, id, submission);
    } catch {
      // Missing/pending/unavailable receipts retain the saved action. Never replay automatically.
    }
  }
  async function submitText(value: string): Promise<Receipt> {
    if ((!value.trim() && !files.ids.length && !attempt.current?.attachmentIds?.length) || submitting.current || uploading)
      throw new Error("A message is already being sent.");
    const id = selected.current;
    if (!id) throw new Error("Choose a conversation first.");
    if (
      attempt.current &&
      (attempt.current.thread !== id || attempt.current.text !== value)
    )
      throw new Error(
        "A previous message has an uncertain outcome. Review that message before sending another.",
      );
    submitting.current = true;
    setBusy(true);
    try {
      attempt.current ||= { thread: id, text: value, id: crypto.randomUUID(), attachmentIds: files.ids };
      const submission = attempt.current;
      setDeliveryNotice("");
      sessionStorage.setItem(
        "leam-companion-submission:" + id,
        JSON.stringify(submission),
      );
      setText(value);
      const accepted = await api(
        "/companion/threads/" + encodeURIComponent(id) + "/messages",
        "POST",
        { text: submission.text, requestId: submission.id, attachmentIds: submission.attachmentIds || [] },
      );
      return acceptDelivery(accepted, id, submission);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  function choose(id: string) {
    selected.current = id;
    setStoppingRun("");
    setUploading(false);
    setDeliveryNotice("");
    if (!fixedThread) rememberSession("companion:selected", id);
    const item = threads.find((item) => item.thread_id === id);
    if (!fixedThread && (item || !id)) rememberSession("companion:selectedItem", item || null);
    const saved = cachedChat("companion:" + id);
    validatedAt.current = saved?.validatedAt || 0;
    setThread(id);
    setMessages(saved?.messages || []);
    setCursor(saved?.cursor || null);
    olderLoaded.current = Boolean(saved?.olderLoaded);
    try {
      attempt.current = JSON.parse(
        sessionStorage.getItem("leam-companion-submission:" + id) || "null",
      );
    } catch {
      attempt.current = null;
    }
    setText(
      sessionValue<string | null>("companion:draft:" + id, null) ??
        attempt.current?.text ??
        "",
    );
    if (id) {
      refresh(id, true).catch(fail);
      if (
        attempt.current?.thread === id &&
        typeof attempt.current?.id === "string" &&
        typeof attempt.current?.text === "string"
      )
        void reconcileSubmission(id, attempt.current);
    }
  }
  useEffect(() => {
    load();
    if (initialId) choose(initialId);
    let stopped = false;
    const refreshSystem = () =>
      api("/companion/system")
        .then((value) => {
          if (!stopped) setSystem(value);
        })
        .catch(() => {
          if (!stopped) setSystem(null);
        });
    void refreshSystem();
    const timer = setInterval(refreshSystem, 15000);
    return () => {
      stopped = true;
      selected.current = "";
      listRequest.current++;
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    if (!thread) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        await refresh(thread, true);
      } catch (e) {
        if (!stopped) fail(e);
      }
      if (!stopped) timer = setTimeout(poll, 2000);
    }
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [thread]);
  useEffect(() => {
    if (thread && selected.current === thread)
      rememberChat("companion:" + thread, {
        messages,
        cursor,
        olderLoaded: olderLoaded.current,
        validatedAt: validatedAt.current,
      });
  }, [thread, messages, cursor]);
  useEffect(() => {
    if (thread && selected.current === thread)
      rememberSession("companion:draft:" + thread, text);
  }, [thread, text]);
  return (
    <section
      className={`page companion-page ${thread ? "has-conversation" : ""}`}
    >
      {!thread && (
        <header className="chat-empty-heading">
          <h1>Companion</h1>
          <p className="intro">
            Your priorities, your context, your next step.
          </p>
        </header>
      )}
      <small aria-label="Companion model">
        {system?.available
          ? `Configured model: ${system.activeModel.model} · ${system.activeModel.reasoning_effort || "provider default"} reasoning`
          : "Model configuration unavailable"}
      </small>
      {!fixedThread && <div className="actions chat-thread-controls">
        <ConversationList
          kind="companion"
          items={threads}
          selected={thread}
          selectedItem={sessionValue("companion:selectedItem", null)}
          loading={listing}
          hasMore={!!threadCursor}
          loadMore={() => void load(true)}
          onSelect={(item) => choose(item.thread_id)}
          onChanged={changedConversation}
        />
        <button
          className="secondary"
          onClick={async () => {
            try {
              const r = await api("/companion/threads", "POST", {
                requestId: crypto.randomUUID(),
              });
              await load();
              choose(r.thread.thread_id);
            } catch (e) {
              fail(e);
            }
          }}
        >
          New conversation
        </button>
      </div>}
      {thread ? (
        <>
          <small>
            Updated {fresh || "…"} · {live.connection}
          </small>
          <div
            className="companion-messages"
            ref={chatScroll.viewport}
            onScroll={chatScroll.onScroll}
          >
            <div ref={chatScroll.content}>
              {cursor && (
                <button
                  className="secondary"
                  disabled={loadingEarlier}
                  onClick={async () => {
                    const id = thread;
                    setLoadingEarlier(true);
                    try {
                      const result = await api(
                        `/companion/threads/${id}?cursor=${encodeURIComponent(cursor)}`,
                      );
                      if (selected.current === id) {
                        chatScroll.preservePrepend();
                        olderLoaded.current = true;
                        setMessages((previous) =>
                          [
                            ...new Map(
                              [...(result.messages || []), ...previous].map(
                                (m) => [m.message_id, m],
                              ),
                            ).values(),
                          ].sort((a, b) => a.sequence - b.sequence),
                        );
                        setCursor(result.next_cursor || null);
                      }
                    } catch (e) {
                      fail(e);
                    } finally {
                      setLoadingEarlier(false);
                    }
                  }}
                >
                  Earlier messages
                </button>
              )}
              {messages
                .filter((m) => ["user", "assistant"].includes(m.kind))
                .map((m) => (
                  <article
                    key={`${thread}:${m.message_id}`}
                    id={`companion-message-${m.message_id}`}
                    className={
                      "message " + (m.kind === "user" ? "user" : "assistant")
                    }
                  >
                    <span className="message-label">
                      {m.kind === "user" ? "YOU" : "LEAM"} · {m.status}
                    </span>
                    {m.leamAttachments?.length > 0 && (
                      <ul aria-label="Message attachments">
                        {m.leamAttachments.map((a: Data) => (
                          <li key={a.id}>
                            <a href={"/api/attachments/" + encodeURIComponent(a.id)} download={a.filename}>{a.filename}</a>
                          </li>
                        ))}
                      </ul>
                    )}
                    <div className="prose markdown">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {m.content || ""}
                      </ReactMarkdown>
                    </div>
                  {m.kind === "assistant" && m.content && (
                    <ReadAloud
                      text={m.content}
                      final={m.status === "finalized"}
                      target={{
                        module: "companion",
                        threadId: thread,
                        runId: m.turn_run_id,
                        messageId: m.message_id,
                      }}
                    />
                  )}
                  </article>
                ))}
              {busy && (
                <p role="status">
                  <em>Sending your message…</em>
                </p>
              )}
              {live.runs
                .filter(
                  (run) =>
                    !messages.some(
                      (message) =>
                        message.kind === "assistant" &&
                        message.turn_run_id === run.id &&
                        (message.content === run.text || !run.text),
                    ),
                )
                .map((run) => (
                  <article
                    key={run.id}
                    className="message assistant"
                    aria-label="Live companion response"
                  >
                    <span className="message-label">
                      LEAM ·{" "}
                      {run.terminal
                        ? run.failed
                          ? "Stopped"
                          : "Saving response"
                        : "Responding"}
                    </span>
                    <p role="status">
                      <em>
                        {run.stopRequested ? "Stop requested…" : run.status}
                      </em>
                    </p>
                    {!run.terminal && (
                      <button
                        className="secondary"
                        disabled={!!stoppingRun || run.stopRequested}
                        onClick={() => void stopRun(run.id)}
                      >
                        {stoppingRun === run.id
                          ? "Requesting stop…"
                          : "Stop response"}
                      </button>
                    )}
                  {run.text && (
                    <ReadAloud
                      text={run.text}
                      final={!!run.textFinal && !run.failed}
                      target={{
                        module: "companion",
                        threadId: thread,
                        runId: run.id,
                      }}
                    />
                  )}
                    {run.text && (
                      <div className="prose markdown">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {run.text}
                        </ReactMarkdown>
                      </div>
                    )}
                  </article>
                ))}
              <ProposalList key={thread} threadId={thread} fail={fail} onChanged={onChanged} />
              {deliveryNotice && (
                <p className="notice" role="status">
                  {deliveryNotice}
                </p>
              )}
              {attempt.current && !busy && (
                <div className="notice">
                  A saved message is awaiting confirmation. Sending again uses
                  its original action ID.
                  <button
                    className="secondary"
                    onClick={() => {
                      if (attempt.current)
                        void reconcileSubmission(thread, attempt.current);
                    }}
                  >
                    Check saved receipt
                  </button>
                  <button
                    className="secondary"
                    onClick={() => {
                      if (
                        !window.confirm(
                          "Set this draft aside? This does not cancel or undo a message already received by Leam.",
                        )
                      )
                        return;
                      sessionStorage.removeItem(
                        "leam-companion-submission:" + thread,
                      );
                      attempt.current = null;
                      setText("");
                    }}
                  >
                    Set draft aside
                  </button>
                </div>
              )}
            </div>
          </div>
          <form
            className="composer"
            onSubmit={async (e) => {
              e.preventDefault();
              stopConversation();
              stopRecognition();
              try {
                await submitText(text);
              } catch (error) {
                fail(error);
              }
            }}
          >
            <textarea
              aria-label="Message Leam"
              placeholder="What’s on your mind?"
              value={text}
              onChange={(e) => {
                stopConversation("Conversation stopped to protect your draft.");
                stopRecognition();
                setText(e.target.value);
              }}
              rows={2}
            />
            <AttachmentComposer
              key={thread + ":attachments"}
              items={files.items}
              onChange={files.change}
              disabled={busy || !!attempt.current}
              onBusyChange={setUploading}
            />
            <VoiceComposer
              key={thread}
              threadId={thread}
              playbackSource={{ module: "companion", threadId: thread }}
              draft={text}
              onDraft={setText}
              disabled={busy || uploading || !!attempt.current}
              submit={submitText}
              messages={[
                ...messages
                  .filter((message) => message.kind === "assistant")
                  .map((message) => ({
                    runId: message.turn_run_id,
                    messageId: message.message_id,
                    text: message.content || "",
                    final: message.status === "finalized",
                    proseElementId: `companion-message-${message.message_id}`,
                  })),
                ...live.runs
                  .filter(
                    (run) =>
                      !!run.text &&
                      !messages.some(
                        (message) =>
                          message.kind === "assistant" &&
                          message.turn_run_id === run.id &&
                          message.status === "finalized",
                      ),
                  )
                  .map((run) => ({
                    runId: run.id,
                    messageId: run.id,
                    text: run.text,
                    final: !!run.textFinal,
                  })),
              ]}
              runs={live.runs}
            />
            <button className="primary" disabled={busy || uploading || (!text.trim() && !files.ids.length)}>
              Send to Leam
            </button>
          </form>
        </>
      ) : (
        <div className="empty-card">
          <h3>Bring it all into one conversation.</h3>
          <p>
            Choose a conversation or start a new one. Set up your companion
            model in Settings before sending.
          </p>
        </div>
      )}
    </section>
  );
}
