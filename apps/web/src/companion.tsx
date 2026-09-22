import { currentResponseRun } from "./companion-current-run";
import { LazySettingsSection } from "./settings-lifecycle";
import { ModelRecoveryNotice } from "./model-recovery";
import { receiptHistory, keepReceiptIdentity, updateReceiptHistory, type SavedReceipt } from "./companion-recovery";
import { ChatDialog } from "./chat-dialog";
import { Plus, ArrowUp } from "lucide-react";
import { ArtifactLink } from "./artifacts";
import { ProcedurePicker, type ProcedureSelection } from "./procedures";
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
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import { documentLink } from "./document-links";
import remarkGfm from "remark-gfm";
import { api, ApiError, chatRead, type Data } from "./api";
import { useChatScroll } from "./chat-scroll";
import { AttachmentComposer, useAttachmentDraft } from "./attachments";
import { ConversationList } from "./conversation-list";
import { CodingModelSettings } from "./coding-model-settings";
import { ProposalList } from "./proposals";
import { approvalsChanged } from "./approvals-status";
import { MemoryApprovalSettings } from "./memory-approval";
import { useCompanionStream, type LiveRun } from "./companion-stream";
import { RuntimeApproval } from "./runtime-approval";
import { BackgroundWork } from "./background-jobs";
import { companionTimeline } from "./companion-timeline";
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
    [saving, setSaving] = useState(false);
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
      <LazySettingsSection title="Memory suggestions and activity">
        <ProposalList memoryOnly fail={fail} onChanged={load} />
      </LazySettingsSection>
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

export function Companion({ fail, fixedThread, onChanged, procedures = false, onExchangeActivity }: Props & { fixedThread?: string; onChanged?: () => void; procedures?: boolean; onExchangeActivity?: (threadId: string) => void }) {
  const [procedure, setProcedure] = useState<ProcedureSelection | null>(null);
  const initialId = useRef(fixedThread || sessionValue("companion:selected", "")).current;
  const initial = cachedChat("companion:" + initialId);
  const [system, setSystem] = useState<Data | null>(null);
  const [rejectedDraftNotice, setRejectedDraftNotice] = useState<{ threadId: string; messageId: string; text: string } | null>(null);
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
  const composerInput = useRef<HTMLTextAreaElement | null>(null);
  const [stoppingRun, setStoppingRun] = useState("");
  const [acceptedRun, setAcceptedRun] = useState<{thread: string; id: string; afterSequence: number} | null>(null);
  const stopTarget = useRef<LiveRun | null>(null);
  const submitting = useRef(false);
  const olderLoaded = useRef(Boolean(initial?.olderLoaded));
  const refreshSequence = useRef(0);
  const validatedAt = useRef(initial?.validatedAt || 0);
  const selected = useRef(initialId),
    attempt = useRef<{ thread: string; text: string; id: string; attachmentIds?: string[]; procedure?: ProcedureSelection | null } | null>(null);
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
    if (selected.current === thread) {
      void refresh(thread).catch(fail);
      onExchangeActivity?.(thread);
      approvalsChanged();
    }
  });
  const currentRun = currentResponseRun(messages, live.runs, acceptedRun?.thread === thread ? acceptedRun : undefined);
  stopTarget.current = currentRun;
  async function stopRun(runId: string) {
    if (selected.current !== thread || stopTarget.current?.id !== runId || stoppingRun) return;
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
  const [savedReceipts, setSavedReceipts] = useState<SavedReceipt[]>(() => receiptHistory(initialId));
  const [checkingReceipt, setCheckingReceipt] = useState("");
  const receiptChecks = useRef(new Set<string>());
  function releaseForEditing() {
    const pending = attempt.current;
    if (!pending || busy || !window.confirm("This message may already have been delivered. Keep editing preserves your text and attachments, but sending an edited message creates a new request and may duplicate earlier work. Continue?")) return;
    try {
      const rows = keepReceiptIdentity(pending.thread, pending.id);
      sessionStorage.removeItem("leam-companion-submission:" + pending.thread);
      setSavedReceipts(rows);
      attempt.current = null;
      setDeliveryNotice("Editing unlocked. Your draft and attachments are kept. The earlier receipt remains in chat options; its delivery is still unconfirmed.");
    } catch (error) {
      setDeliveryNotice(error instanceof Error ? error.message : String(error));
    }
  }
  async function checkPreviousReceipt(row: SavedReceipt) {
    if (receiptChecks.current.has(row.id)) return;
    receiptChecks.current.add(row.id);
    setCheckingReceipt(row.id);
    let notice = "Receipt unavailable. Delivery remains unconfirmed.", confirmed = false;
    try {
      const saved = await api(`/companion/threads/${encodeURIComponent(row.thread)}/submissions/${encodeURIComponent(row.id)}`);
      confirmed = saved.state === "recorded" && !!deliveryReceipt(saved.receipt || {}, row.thread);
      notice = confirmed ? "Delivery confirmed" : saved.state === "pending" ? "Receipt pending. Delivery remains unconfirmed." : "No confirmed receipt found. The message may still have been delivered.";
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) notice = "Receipt not found. This does not prove the message was not delivered.";
    }
    finally {
      receiptChecks.current.delete(row.id);
      if (selected.current === row.thread) {
        setCheckingReceipt("");
        try { setSavedReceipts(updateReceiptHistory(row.thread, row.id, { notice, confirmed })); }
        catch { setDeliveryNotice("Could not save the receipt check. The earlier identity is retained."); }
      }
    }
  }
  function acceptDelivery(
    accepted: Data,
    id: string,
    submission: { thread: string; text: string; id: string; attachmentIds?: string[]; procedure?: ProcedureSelection | null },
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
            "Leam is busy and did not accept this message. Your draft and attachments are kept. Wait for the current response, then send explicitly.",
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
      setAcceptedRun({thread: id, id: receipt.run_id, afterSequence: Math.max(-1, ...messages.filter(message => Number.isSafeInteger(message.sequence)).map(message => message.sequence))});
      live.accepted(receipt.run_id);
      setProcedure(null);
      attempt.current = null;
      setText((current) => (current === submission.text ? "" : current));
      setDeliveryNotice(
        receipt.outcome === "deferred_busy" ? queuedNotice : "",
      );
    }
    onExchangeActivity?.(id);
    approvalsChanged();
    void Promise.all([refresh(id), load()]).catch(fail);
    return receipt;
  }
  async function reconcileSubmission(
    id: string,
    submission: { thread: string; text: string; id: string; attachmentIds?: string[]; procedure?: ProcedureSelection | null },
  ) {
    if (receiptChecks.current.has(submission.id)) return;
    receiptChecks.current.add(submission.id);
    setCheckingReceipt(submission.id);
    try {
      const saved = await api(
        `/companion/threads/${encodeURIComponent(id)}/submissions/${encodeURIComponent(submission.id)}`,
      );
      if (selected.current !== id || attempt.current?.id !== submission.id) return;
      if (saved.state === "recorded" && saved.receipt) {
        acceptDelivery(saved.receipt, id, submission);
      } else {
        setDeliveryNotice(saved.state === "pending"
          ? "Receipt pending. Delivery remains unconfirmed; nothing has been resent."
          : "No confirmed receipt found. This does not prove the message was not delivered.");
      }
    } catch (error) {
      if (selected.current === id && attempt.current?.id === submission.id)
        setDeliveryNotice(error instanceof ApiError && error.status === 404
          ? "Receipt not found. Delivery remains unconfirmed; this does not prove the message was not delivered."
          : "Receipt unavailable. Delivery remains unconfirmed; nothing has been resent.");
    } finally {
      receiptChecks.current.delete(submission.id);
      if (selected.current === id) setCheckingReceipt("");
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
    chatScroll.follow();
    setBusy(true);
    let dispatched = false;
    let submission = attempt.current;
    try {
      attempt.current ||= { thread: id, text: value, id: crypto.randomUUID(), attachmentIds: files.ids, procedure };
      submission = attempt.current;
      setDeliveryNotice("");
      sessionStorage.setItem(
        "leam-companion-submission:" + id,
        JSON.stringify(submission),
      );
      setText(value);
      dispatched = true;
      const accepted = await api(
        "/companion/threads/" + encodeURIComponent(id) + "/messages",
        "POST",
        { text: submission.text, requestId: submission.id, attachmentIds: submission.attachmentIds || [], ...(submission.procedure ? {procedure: submission.procedure} : {}) },
      );
      return acceptDelivery(accepted, id, submission);
    } catch (error) {
      if (submission && selected.current === id && attempt.current?.id === submission.id) {
        if (!dispatched || (error instanceof ApiError && error.actionReserved === "no")) {
          sessionStorage.removeItem("leam-companion-submission:" + id);
          attempt.current = null;
          setDeliveryNotice("Not sent. " + (error instanceof Error ? error.message : "Request rejected before dispatch.") + " Your draft and attachments are kept; you can edit or send explicitly.");
        } else {
          setDeliveryNotice("Message delivery is uncertain. Your draft and attachments are kept. Checking its receipt without resending…");
          void reconcileSubmission(id, submission);
        }
      }
      throw error;
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
    setCheckingReceipt("");
    setSavedReceipts(receiptHistory(id));
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
    setProcedure(attempt.current?.procedure || null);
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
  const renderMessage = (m: Data) => (
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
                            <>{a.state === "deleted" ? <span className="attachment-deleted">{a.filename} · Attachment deleted</span> : <a href={"/api/attachments/" + encodeURIComponent(a.id)} download={a.filename}>{a.filename}</a>}</>
                          </li>
                        ))}
                      </ul>
                    )}
                    <div className="prose markdown">
                      <ReactMarkdown
                        components={{ a: ({ node: _node, ...props }) => <ArtifactLink {...props} /> }}
                        remarkPlugins={[remarkGfm]}
                        urlTransform={(url, key) =>
                          key === "href" ? documentLink(url, thread) : defaultUrlTransform(url)
                        }
                      >
                        {m.content || ""}
                      </ReactMarkdown>
                    </div>
                  {m.kind === "user" && m.status === "rejected_busy" && (
                    <div aria-label="Unprocessed message">
                      <p role="status">This saved message was not processed. Use it as a draft to send it again.</p>
                      <button type="button" className="secondary" disabled={busy || uploading || !!attempt.current} onClick={() => {
                        if (selected.current !== thread || busy || uploading || attempt.current || !messages.some(message => message.message_id === m.message_id && message.kind === "user" && message.status === "rejected_busy")) return;
                        let notice: string;
                        if (text.trim() || files.items.length) {
                          notice = "Your existing draft and attachments are preserved. Review them before replacing anything; nothing was sent.";
                        } else if (typeof m.content !== "string" || !m.content.trim()) {
                          notice = "There is no message text to restore." + (m.leamAttachments?.length || m.attachments?.length ? " Review and reattach any original files before sending." : "");
                        } else {
                          stopConversation();
                          stopRecognition();
                          setText(m.content);
                          notice = "Text copied to your draft. Review it before pressing Send; nothing was sent." + (m.leamAttachments?.length || m.attachments?.length ? " Original files are not restored—review and reattach any needed files." : "");
                        }
                        setRejectedDraftNotice({ threadId: thread, messageId: m.message_id, text: notice });
                        composerInput.current?.focus();
                      }}>Use as draft</button>
                      {rejectedDraftNotice?.threadId === thread && rejectedDraftNotice.messageId === m.message_id && <p role="status">{rejectedDraftNotice.text}</p>}
                    </div>
                  )}
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
  );
  const renderRun = (run: LiveRun) => (
<article
                    key={`${thread}:run:${run.id}`}
                    data-run-id={run.id}
                    className="message assistant"
                    aria-label="Live companion response"
                  >
                    <span className="message-label">
                      LEAM ·{" "}
                      {run.terminal
                        ? run.failed
                          ? "Stopped"
                          : "Saving response"
                        : run.approvalId ? "Waiting for approval" : "Responding"}
                    </span>
                    <p role="status">
                      <em>
                        {run.stopRequested ? "Stop requested…" : run.approvalId ? "Review the operation below to continue." : run.status}
                      </em>
                    </p>
                    {run.approvalId && !run.terminal && <RuntimeApproval
                      key={`${thread}:${run.id}:${run.approvalId}`}
                      threadId={thread} runId={run.id} approvalId={run.approvalId}
                      resolved={(status, outcome) => live.approvalResolved(run.id, status, outcome)}
                    />}
                    {(run.recovery || run.terminalStatus === "failed") &&
                      messages.filter((message) => message.kind === "user").at(-1)?.turn_run_id === run.id && <ModelRecoveryNotice
                      key={`${thread}:${run.id}:recovery`}
                      threadId={thread}
                      runId={run.id}
                      recovery={run.recovery}
                      terminal={run.terminal}
                      failed={run.terminalStatus === "failed"}
                      prepareRetry={run.terminal && run.terminalStatus === "failed" &&
                        messages.filter((message) => message.kind === "user").at(-1)?.turn_run_id === run.id &&
                        !live.runs.some((other) => !other.terminal) ? () => {
                          if (selected.current !== thread || busy || attempt.current) return "Wait until the current submission is resolved.";
                          if (text.trim() || files.items.length) {
                            composerInput.current?.focus();
                            return "Your existing draft and attachments are preserved. Edit or send them explicitly; nothing was resent.";
                          }
                          const original = messages.filter((message) => message.kind === "user" && message.turn_run_id === run.id).at(-1);
                          if (!original || typeof original.content !== "string") return "The original request is unavailable. Write a new message in the composer.";
                          stopConversation();
                          stopRecognition();
                          setText(original.content);
                          composerInput.current?.focus();
                          return "Request copied to your draft. Review the text and any attachments before pressing Send; nothing was resent.";
                        } : undefined}
                    />}
                    {run.text && (
                      <div className="prose markdown">
                        <ReactMarkdown
                        components={{ a: ({ node: _node, ...props }) => <ArtifactLink {...props} /> }}
                        remarkPlugins={[remarkGfm]}
                        urlTransform={(url, key) =>
                          key === "href" ? documentLink(url, thread) : defaultUrlTransform(url)
                        }
                      >
                          {run.text}
                        </ReactMarkdown>
                      </div>
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
                  </article>
  );
  return (
    <section
      className={`page companion-page ${thread ? "has-conversation" : ""}`}
      data-has-exchanges={messages.length > 0 || live.runs.length > 0 || busy || !!attempt.current || acceptedRun?.thread === thread}
    >
      {!thread && (
        <header className="chat-empty-heading">
          <h1>Companion</h1>
          <p className="intro">
            Your priorities, your context, your next step.
          </p>
        </header>
      )}
      <div className="chat-toolbar" aria-label="Companion chat controls">
      {!fixedThread && <>
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
          className="icon-button"
          aria-label="New conversation" title="New conversation"
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
          <Plus size={20} aria-hidden="true" />
        </button>
      </>}
        {thread && <BackgroundWork threadId={thread} draft={text} />}
        <ChatDialog label="Companion chat options">
      <small aria-label="Companion model">
        {system?.available
          ? `Configured model: ${system.activeModel.model} · ${system.activeModel.reasoning_effort || "provider default"} reasoning`
          : "Model configuration unavailable"}
      </small>
          <p>Updated {fresh || "…"} · {live.connection}</p>
          {!!savedReceipts.length && <details className="receipt-history"><summary>Earlier message receipts ({savedReceipts.length}/8)</summary>
            {savedReceipts.map(row => <div key={row.id}>
              <code>{row.id}</code><p>{row.notice}</p>
              <button type="button" className="secondary" disabled={!!checkingReceipt} onClick={() => void checkPreviousReceipt(row)}>Check earlier receipt</button>
              {row.confirmed && <button type="button" className="secondary" onClick={() => { try { setSavedReceipts(updateReceiptHistory(thread, row.id, null)); } catch { setDeliveryNotice("Could not remove the confirmed receipt."); } }}>Remove confirmed receipt</button>}
            </div>)}
          </details>}

            {procedures && <ProcedurePicker value={procedure} disabled={busy || !!attempt.current} change={(choice) => { setProcedure(choice); if (choice && !text.trim()) setText(choice.id === "what-now" ? "What should I do now?" : "I feel overloaded. Help me choose one manageable next step."); }} />}
        </ChatDialog>
      </div>
      {thread ? (
        <>
          {live.connection !== "Live progress connected" && live.connection !== "Connecting to live progress…" && <small className="chat-connection-notice" role="status">{live.connection}</small>}
          <div
            className="companion-messages"
            ref={chatScroll.viewport}
            onScroll={chatScroll.onScroll}
          >
            <div className="companion-transcript" ref={chatScroll.content}>
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
              {companionTimeline(messages, live.runs).map(entry => entry.kind === "message"
                ? renderMessage(entry.message)
                : renderRun(entry.run))}
              {busy && <p role="status"><em>Sending your message…</em></p>}
            </div>
          </div>
          {(deliveryNotice || attempt.current) && <aside className="companion-delivery-recovery" aria-label="Message delivery recovery">
            <p role="status">{deliveryNotice || "A saved message has an unconfirmed delivery outcome."}</p>
            {attempt.current && <div className="actions">
              <button type="button" className="secondary" disabled={busy || !!checkingReceipt} onClick={() => { if (attempt.current) void reconcileSubmission(thread, attempt.current); }}>{checkingReceipt ? "Checking receipt…" : "Check saved receipt"}</button>
              <button type="button" className="secondary" disabled={busy || !!checkingReceipt} onClick={() => { if (attempt.current) void submitText(attempt.current.text).catch(() => {}); }}>Retry saved message</button>
              <button type="button" className="secondary" disabled={busy} onClick={releaseForEditing}>Keep editing</button>
            </div>}
          </aside>}
          <form
            className="composer"
            onSubmit={async (e) => {
              e.preventDefault();
              stopConversation();
              stopRecognition();
              try {
                await submitText(text);
              } catch {
                // submitText owns the visible receipt recovery state.
              }
            }}
          >
            <textarea
              ref={composerInput}
              aria-label="Message Leam"
              readOnly={!!attempt.current}
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
              compact
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
                    playbackMessageId: null,
                    replayEligible: !run.failed,
                    text: run.text,
                    final: !!run.textFinal,
                  })),
              ]}
              runs={live.runs}
            />
            {currentRun && (
              <button type="button" className="secondary companion-stop" disabled={!!stoppingRun || currentRun.stopRequested} onClick={() => void stopRun(currentRun.id)}>
                {stoppingRun === currentRun.id ? "Requesting stop…" : currentRun.stopRequested ? "Stop requested…" : "Stop response"}
              </button>
            )}
            <button className="primary send" aria-label="Send to Leam" title="Send to Leam" disabled={busy || uploading || !!attempt.current || (!text.trim() && !files.ids.length)}>
              <ArrowUp size={20} aria-hidden="true" />
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
