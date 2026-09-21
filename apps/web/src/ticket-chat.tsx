import {
  AttachmentComposer,
  AttachmentList,
  useAttachmentDraft,
} from "./attachments";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useChatScroll } from "./chat-scroll";
import { api, ApiError, type Data } from "./api";
import { VoiceComposer } from "./voice/composer";
import { ReadAloud } from "./voice/controls";
import { stopRecognition } from "./voice/speech";
import { stopConversation, type Receipt } from "./voice/conversation";

type Pending = {
  id: string;
  text: string;
  attachmentIds?: string[];
  expectedTurnId?: string;
};

function savedPending(key: string): Pending | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "null");
    return value &&
      typeof value.id === "string" &&
      typeof value.text === "string"
      ? {
          id: value.id,
          text: value.text,
          ...(typeof value.expectedTurnId === "string"
            ? { expectedTurnId: value.expectedTurnId }
            : {}),
          attachmentIds: Array.isArray(value.attachmentIds)
            ? value.attachmentIds
            : [],
        }
      : null;
  } catch {
    return null;
  }
}

/** Closed panels do not mount a transport or make any Codex request. */
export function TicketChat({ ticket }: { ticket: Data }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Chat about this update</summary>
      {open && <TicketConversation key={ticket.id} ticket={ticket} />}
    </details>
  );
}

function TicketConversation({ ticket }: { ticket: Data }) {
  const draftKey = `leam-ticket-draft:${ticket.id}`;
  const pendingKey = `leam-ticket-pending:${ticket.id}`;
  const files = useAttachmentDraft("ticket:" + ticket.id);
  const [uploading, setUploading] = useState(false);
  const pending = useRef<Pending | null>(savedPending(pendingKey));
  const [thread, setThread] = useState<Data | null>(null);
  const chatScroll = useChatScroll(thread?.threadId || ticket.id);
  const [turns, setTurns] = useState<Data[]>([]);
  const [text, setText] = useState(
    () => sessionStorage.getItem(draftKey) || pending.current?.text || "",
  );
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [acceptedRun, setAcceptedRun] = useState("");
  const [requests, setRequests] = useState<Data[]>([]);
  const sending = useRef(false);
  const current = useRef<Data | null>(null);
  const mounted = useRef(true);
  const streamRevision = useRef(0);
  const projectedTurns = useRef(new Set<string>());
  const initializedTurns = useRef(new Set<string>());
  const [, setPendingRevision] = useState(0);
  const endpoint = `/updates/${encodeURIComponent(ticket.id)}/chat`;
  const report = (value: unknown) => {
    if (mounted.current)
      setError(value instanceof Error ? value.message : String(value));
  };
  function draft(value: string) {
    sessionStorage.setItem(draftKey, value);
    setText(value);
  }
  async function refresh(id: string) {
    const revision = streamRevision.current;
    const [history, input] = await Promise.all([
      api(`/codex/threads/${encodeURIComponent(id)}/turns`),
      api("/codex/requests"),
    ]);
    if (!mounted.current || current.current?.threadId !== id) return;
    if (revision !== streamRevision.current) return;
    setTurns((previous) => {
      const incoming: Data[] = [...(history.data || [])].reverse();
      // turns/list may omit active partials. Keep live projection until the
      // authoritative terminal turn arrives; a snapshot cannot erase it.
      for (const turn of previous) {
        if (!projectedTurns.current.has(turn.id)) continue;
        const index = incoming.findIndex((value) => value.id === turn.id);
        if (
          index >= 0 &&
          ["completed", "failed", "interrupted"].includes(
            incoming[index].status,
          )
        ) {
          projectedTurns.current.delete(turn.id);
        } else if (index >= 0) incoming[index] = turn;
        else incoming.push(turn);
      }
      return incoming;
    });
    setRequests(
      (input.items || []).filter((item: Data) => item.params?.threadId === id),
    );
  }
  useEffect(() => {
    mounted.current = true;
    const accepted = (event: Event) => {
      if ((event as CustomEvent).detail !== ticket.id) return;
      pending.current = savedPending(pendingKey);
      setText(sessionStorage.getItem(draftKey) || "");
      setPendingRevision((value) => value + 1);
    };
    window.addEventListener("leam-ticket-submitted", accepted);
    void api(endpoint)
      .then(async (status) => {
        if (!mounted.current) return;
        current.current = status;
        setThread(status);
        if (status.threadId) {
          pending.current =
            savedPending(pendingKey) ||
            savedPending("leam-submission:" + status.threadId);
          if (pending.current)
            sessionStorage.setItem(pendingKey, JSON.stringify(pending.current));
          if (pending.current && !sessionStorage.getItem(draftKey))
            draft(pending.current.text);
          await refresh(status.threadId);
        }
      })
      .catch(report)
      .finally(() => {
        if (mounted.current) setLoading(false);
      });
    return () => {
      mounted.current = false;
      window.removeEventListener("leam-ticket-submitted", accepted);
    };
  }, [ticket.id]);
  useEffect(() => {
    const id = thread?.threadId;
    if (!id) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let lastSequence = Number(thread?.eventCursor) || 0;
    const stream = new EventSource(`/api/events?after=${lastSequence}`);
    const schedule = () => {
      if (!timer)
        timer = setTimeout(() => {
          timer = null;
          void refresh(id).catch(report);
        }, 300);
    };
    stream.onopen = schedule;
    stream.onmessage = (event) => {
      try {
        const packet = JSON.parse(event.data);
        if (typeof packet.id === "number") {
          if (packet.id <= lastSequence) return;
          lastSequence = packet.id;
        }
        if (packet.topic === "codex.connection") {
          if (current.current) current.current.connected = false;
          setThread((value) =>
            value ? { ...value, connected: false } : value,
          );
        }
        if (packet.topic !== "codex" || packet.payload?.params?.threadId !== id)
          return;
        const method = packet.payload.method;
        const params = packet.payload.params;
        const turnId = params.turn?.id || params.turnId;
        if (method === "turn/started" && turnId)
          initializedTurns.current.add(turnId);
        if (
          ["item/agentMessage/delta", "item/completed"].includes(method) &&
          !initializedTurns.current.has(turnId)
        ) {
          // No retained start means snapshot/replay overlap is unknowable.
          // Reconcile instead of appending a delta onto snapshot partial text.
          schedule();
          return;
        }
        if (
          turnId &&
          [
            "item/agentMessage/delta",
            "item/completed",
            "turn/started",
            "turn/completed",
          ].includes(method)
        ) {
          streamRevision.current++;
          projectedTurns.current.add(turnId);
          setTurns((previous) => {
            const next: Data[] = previous.map((turn) => ({
              ...turn,
              items: [...(turn.items || [])],
            }));
            let turn = next.find((value) => value.id === turnId);
            if (!turn) {
              turn = { id: turnId, status: "inProgress", items: [] };
              next.push(turn);
            }
            if (
              ["completed", "failed", "interrupted"].includes(turn.status) &&
              method !== "turn/completed"
            )
              return next;
            if (method === "turn/completed") {
              turn.status = params.turn?.status || "completed";
              if (params.turn?.items?.length) turn.items = params.turn.items;
            } else if (method === "turn/started") {
              turn.status = params.turn?.status || "inProgress";
              turn.items = turn.items.filter(
                (item: Data) => item.type !== "agentMessage",
              );
            } else {
              const itemId = params.item?.id || params.itemId;
              if (
                !itemId ||
                (method === "item/agentMessage/delta" &&
                  turn.status !== "inProgress")
              )
                return next;
              const index = turn.items.findIndex(
                (item: Data) => item.id === itemId,
              );
              const item =
                method === "item/completed"
                  ? params.item
                  : {
                      id: itemId,
                      type: "agentMessage",
                      text:
                        (index >= 0 ? turn.items[index].text || "" : "") +
                        (params.delta || ""),
                    };
              if (index < 0) turn.items.push(item);
              else turn.items[index] = item;
            }
            return next;
          });
        }
        if (method !== "item/agentMessage/delta") schedule();
      } catch {
        report(
          new Error("Could not read a Codex event; refresh this conversation."),
        );
      }
    };
    return () => {
      stream.close();
      if (timer) clearTimeout(timer);
    };
  }, [thread?.threadId]);
  const activeTurnId =
    turns.find((turn) => turn.status === "inProgress")?.id ||
    (acceptedRun &&
    !turns.some(
      (turn) =>
        turn.id === acceptedRun &&
        ["completed", "failed", "interrupted"].includes(turn.status),
    )
      ? acceptedRun
      : undefined);

  async function submitText(raw: string): Promise<Receipt> {
    if (sending.current || uploading)
      throw new Error("Wait for the current message delivery or upload.");
    if (!raw.trim() && !files.ids.length)
      throw new Error("Write a message or attach a file first.");
    sending.current = true;
    setBusy(true);
    setError("");
    try {
      // Reserve the user's intent before the first await, even when there is
      // no thread yet. Reopened panels must share the same idempotency key.
      const attempt = savedPending(pendingKey) || {
        id: crypto.randomUUID(),
        text: raw,
        attachmentIds: files.ids,
        ...(activeTurnId ? { expectedTurnId: activeTurnId } : {}),
      };
      if (attempt.text !== raw)
        throw new Error(
          "Check the saved message before sending different text.",
        );
      pending.current = attempt;
      sessionStorage.setItem(pendingKey, JSON.stringify(attempt));
      let status = current.current;
      if (!status?.connected) {
        status = await api(endpoint, "POST", {});
        current.current = status;
        if (mounted.current) setThread(status);
      }
      if (!mounted.current)
        throw new Error(
          "Ticket chat closed before dispatch. Reopen to check the saved message.",
        );
      const id = status.threadId;
      sessionStorage.setItem("leam-submission:" + id, JSON.stringify(attempt));
      const delivery = await api(`/codex/submissions/${attempt.id}`);
      let result: Data;
      if (delivery.state === "complete") result = delivery.result;
      else if (delivery.state === "pending") {
        const reconciled = await api(
          `/codex/threads/${encodeURIComponent(id)}/submissions/${attempt.id}/reconcile`,
          "POST",
          {
            text: attempt.text,
            ...(attempt.expectedTurnId
              ? { expectedTurnId: attempt.expectedTurnId }
              : {}),
            ...(attempt.attachmentIds?.length
              ? { attachmentIds: attempt.attachmentIds }
              : {}),
          },
        );
        if (reconciled.state !== "complete")
          throw new Error(
            "Delivery is uncertain. Inspect the conversation; this message has not been resent.",
          );
        result = reconciled.result;
      } else {
        if (!mounted.current)
          throw new Error(
            "Ticket chat closed before dispatch. Reopen to check the saved message.",
          );
        result = await api(
          `/codex/threads/${encodeURIComponent(id)}/turns`,
          "POST",
          {
            text: attempt.text,
            requestId: attempt.id,
            ...(attempt.expectedTurnId
              ? { expectedTurnId: attempt.expectedTurnId }
              : {}),
            ...(attempt.attachmentIds?.length
              ? { attachmentIds: attempt.attachmentIds }
              : {}),
          },
        );
      }
      if (typeof result?.turn?.id !== "string" || !result.turn.id.trim())
        throw new Error(
          "No matching turn receipt. Check delivery before retrying.",
        );
      if (savedPending("leam-submission:" + id)?.id === attempt.id)
        sessionStorage.removeItem("leam-submission:" + id);
      if (savedPending(pendingKey)?.id === attempt.id) {
        sessionStorage.removeItem(pendingKey);
        if (sessionStorage.getItem(draftKey) === attempt.text)
          sessionStorage.removeItem(draftKey);
      }
      window.dispatchEvent(
        new CustomEvent("leam-ticket-submitted", { detail: ticket.id }),
      );
      pending.current = null;
      files.clear(attempt.attachmentIds || []);
      if (mounted.current) {
        setText((value) => (value === attempt.text ? "" : value));
        setAcceptedRun(result.turn.id);
        void refresh(id).catch(report);
      }
      return { outcome: "submitted", run_id: result.turn.id, autoReply: true };
    } catch (value) {
      if (value instanceof ApiError && value.actionReserved === "no") {
        // An explicit admission rejection is safe to leave as an editable draft.
        // Unknown transport outcomes retain their original target and receipt.
        const rejected = pending.current;
        if (rejected && savedPending(pendingKey)?.id === rejected.id)
          sessionStorage.removeItem(pendingKey);
        const id = current.current?.threadId;
        if (id && savedPending("leam-submission:" + id)?.id === rejected?.id)
          sessionStorage.removeItem("leam-submission:" + id);
        pending.current = null;
        if (mounted.current) {
          setAcceptedRun("");
          setPendingRevision((revision) => revision + 1);
          if (id) void refresh(id).catch(report);
        }
      }
      report(value);
      throw value;
    } finally {
      sending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <section aria-label={`Chat about ${ticket.title}`}>
      <p>
        Direct to Codex in a separate ticket conversation. Sending includes this
        update's context and agent-protocols. Chat does not mark UAT.
      </p>
      {loading && <p>Loading ticket conversation…</p>}
      {error && <p role="alert">{error}</p>}
      {thread?.state === "uncertain" && (
        <p>
          Session creation is uncertain. Inspect Coding before recovery; another
          session will not be created automatically.
        </p>
      )}
      <div
        aria-label="Ticket messages"
        ref={chatScroll.viewport}
        onScroll={chatScroll.onScroll}
        style={{
          maxHeight: "50vh",
          overflowY: "auto",
          overflowWrap: "anywhere",
        }}
      >
        <div ref={chatScroll.content}>
          {turns.map((turn) => (
            <div key={turn.id}>
              <AttachmentList items={turn.leamAttachments || []} />
              {(turn.items || []).map((item: Data) => {
                if (item.type === "userMessage")
                  return (
                    <article key={item.id} className="message user">
                      <strong>You</strong>
                      <p className="prose" style={{ whiteSpace: "pre-wrap" }}>
                        {(item.content || [])
                          .map((part: Data) => part.text || "")
                          .join("\n")}
                      </p>
                    </article>
                  );
                if (item.type === "agentMessage")
                  return (
                    <article
                      id={`ticket-${ticket.id}-${item.id}`}
                      key={item.id}
                      className="message assistant"
                    >
                      <strong>Codex</strong>
                      {item.text?.trim() && thread?.threadId && (
                        <ReadAloud
                          text={item.text}
                          final={turn.status === "completed"}
                          target={{
                            module: "coding",
                            threadId: thread.threadId,
                            runId: turn.id,
                            messageId: item.id,
                          }}
                        />
                      )}
                      <div className="prose markdown">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {item.text || ""}
                        </ReactMarkdown>
                      </div>
                    </article>
                  );
                if (item.type === "reasoning") return null;
                return (
                  <details key={item.id}>
                    <summary>Activity · {item.type}</summary>
                    <pre style={{ whiteSpace: "pre-wrap" }}>
                      {item.command ||
                        item.aggregatedOutput ||
                        JSON.stringify(item, null, 2)}
                    </pre>
                  </details>
                );
              })}
            </div>
          ))}
        </div>
      </div>
      {!!requests.length && (
        <p role="status">
          Codex needs input or approval. Open Coding and select this ticket
          session to respond; no permission has been granted here.
        </p>
      )}
      {activeTurnId && (
        <p role="status">Codex is working… You can send a follow-up.</p>
      )}
      {thread?.threadId && (
        <>
          <details>
            <summary>Ticket session</summary>
            <p>
              Codex thread: <code>{thread.threadId}</code>
            </p>
          </details>
          <button
            type="button"
            className="secondary"
            onClick={() => void refresh(thread.threadId).catch(report)}
          >
            Refresh ticket conversation
          </button>
        </>
      )}
      {pending.current && !busy && (
        <div>
          <p role="status">
            A saved message needs checking. Send checks its receipt before any
            retry.
          </p>
          <button
            type="button"
            className="secondary"
            disabled={loading}
            onClick={() => {
              const saved = savedPending(pendingKey);
              if (saved) void submitText(saved.text).catch(() => {});
            }}
          >
            Retry saved message
          </button>
        </div>
      )}
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          stopConversation();
          stopRecognition();
          void submitText(text).catch(() => {});
        }}
      >
        <AttachmentComposer
          items={files.items}
          onChange={files.change}
          disabled={busy || !!pending.current}
          onBusyChange={setUploading}
        />
        <label>
          Message about this update
          <textarea
            rows={2}
            value={text}
            onChange={(event) => {
              stopConversation();
              stopRecognition();
              draft(event.target.value);
            }}
          />
        </label>
        <VoiceComposer
          threadId={`ticket:${ticket.id}`}
          playbackSource={{
            module: "coding",
            threadId: thread?.threadId || "",
          }}
          draft={text}
          onDraft={draft}
          disabled={
            loading ||
            busy ||
            uploading ||
            !!pending.current ||
            thread?.state === "uncertain"
          }
          dictationDisabled={busy || uploading || !!pending.current}
          submit={submitText}
          messages={turns.flatMap((turn) => {
            const answers = (turn.items || []).filter(
              (item: Data) => item.type === "agentMessage",
            );
            const last =
              answers
                .filter((item: Data) => item.phase === "final_answer")
                .at(-1) ||
              (turn.status === "completed" ? answers.at(-1) : undefined);
            return last
              ? [
                  {
                    runId: turn.id,
                    messageId: last.id,
                    text: last.text || "",
                    final: turn.status === "completed",
                    proseElementId: `ticket-${ticket.id}-${last.id}`,
                  },
                ]
              : [];
          })}
          runs={turns.map((turn) => ({
            id: turn.id,
            terminal: ["completed", "failed", "interrupted"].includes(
              turn.status,
            ),
            failed: ["failed", "interrupted"].includes(turn.status),
          }))}
        />
        <button
          className="primary"
          disabled={
            loading ||
            busy ||
            uploading ||
            (!text.trim() && !files.ids.length) ||
            thread?.state === "uncertain"
          }
        >
          {busy ? "Sending…" : "Send to Codex"}
        </button>
      </form>
    </section>
  );
}
