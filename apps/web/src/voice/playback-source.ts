import { api, type Data } from "../api";
import { CodingStreamProjection } from "../coding-stream";
export type ReplyBaseline = { messageId: string; text: string }[];

/** A steered shared run may contain output from before the spoken request. */
export function afterReplyBaseline(messageId: string, text: string, baseline?: ReplyBaseline): string | null {
  const prior = baseline?.find((message) => message.messageId === messageId);
  if (!prior) return text;
  if (text.startsWith(prior.text)) return text.slice(prior.text.length);
  // An older prefix from a delayed history response is not new speech.
  return prior.text.startsWith(text) ? "" : null;
}

export type PlaybackTarget = {
  module: "companion" | "coding";
  threadId: string;
  runId: string;
  messageId?: string;
  automatic?: boolean;
  readoutId?: string;
  baseline?: ReplyBaseline;
};
export type PlaybackUpdate = { text: string; final: boolean; failed?: boolean };
export const targetKey = (t: PlaybackTarget) =>
  JSON.stringify([t.module, t.threadId, t.runId, t.messageId || "", t.readoutId || ""]);
const ended = (status: string) =>
  ["completed", "failed", "interrupted"].includes(status);

/** One read-only subscription belongs to active audio, independently of routes. */
export function followPlayback(
  target: PlaybackTarget,
  update: (value: PlaybackUpdate) => void,
  unavailable: () => void,
) {
  let closed = false,
    source: EventSource | undefined,
    timer: ReturnType<typeof setTimeout> | undefined;
  let busy = false,
    rerun = false,
    revision = 0,
    sequence = 0;
  let turns: Data[] = [];
  const projection = new CodingStreamProjection();
  const thread = encodeURIComponent(target.threadId);
  const close = () => {
    closed = true;
    source?.close();
    clearTimeout(timer);
  };
  function publish(value: PlaybackUpdate) {
    if (closed) return;
    update(value);
    if (value.final || value.failed) close();
  }
  function coding(data: Data[]) {
    const turn = data.find((turn) => turn.id === target.runId);
    if (!turn) return;
    const messages = (turn.items || []).filter(
      (item: Data) => item.type === "agentMessage",
    );
    const chosen = target.messageId
      ? messages.find((item: Data) => item.id === target.messageId)
      : [...messages]
          .reverse()
          .find((item: Data) => item.phase === "final_answer") ||
        (ended(turn.status) ? messages[messages.length - 1] : undefined);
    if (["failed", "interrupted"].includes(turn.status))
      publish({ text: chosen?.text || "", final: true, failed: true });
    else if (chosen && typeof chosen.text === "string") {
      const text = afterReplyBaseline(chosen.id, chosen.text, target.baseline);
      publish(text === null
        ? { text: "", final: true, failed: true }
        : { text, final: ended(turn.status) });
    }
  }
  async function reconcile() {
    if (closed) return;
    if (busy) {
      rerun = true;
      return;
    }
    busy = true;
    const version = revision;
    try {
      if (target.module === "companion") {
        const data = await api(`/companion/threads/${thread}?limit=60`);
        if (closed || version !== revision) return;
        const replies = (data.messages || []).filter(
          (item: Data) =>
            item.kind === "assistant" &&
            item.turn_run_id === target.runId &&
            (!target.messageId || item.message_id === target.messageId),
        );
        const reply = replies[replies.length - 1];
        if (reply)
          publish({
            text: reply.content || "",
            final: reply.status === "finalized",
          });
      } else {
        const data = await api(`/codex/threads/${thread}/turns?limit=50`);
        if (closed || version !== revision) return;
        turns = projection.reconcile(data.data || [], turns);
        coding(turns);
      }
    } catch {
      if (!closed) unavailable();
    } finally {
      busy = false;
      if (!closed) {
        clearTimeout(timer);
        timer = setTimeout(reconcile, rerun ? 300 : 5000);
        rerun = false;
      }
    }
  }
  if (target.module === "companion") {
    source = new EventSource(`/api/companion/threads/${thread}/events`);
    const receive = (event: Event) => {
      if (closed) return;
      try {
        const raw = (event as MessageEvent).data;
        if (typeof raw !== "string" || raw.length > 262144) return;
        const frame = JSON.parse(raw);
        if (frame.state?.thread_id && frame.state.thread_id !== target.threadId)
          return;
        revision++;
        if (["failed", "cancelled"].includes(frame.type)) {
          const id =
            frame.run_state?.turn_run_id ||
            frame.run_state?.run_id ||
            frame.response?.turn_run_id;
          if (id === target.runId)
            publish({ text: "", final: true, failed: true });
        }
        if (
          frame.type === "final_reply" &&
          frame.reply?.turn_run_id === target.runId &&
          !target.messageId
        )
          publish({ text: frame.reply.text || "", final: true });
        for (const item of frame.state?.items || []) {
          if (
            item.text?.run_id === target.runId &&
            !target.messageId &&
            typeof item.text.body === "string"
          )
            publish({ text: item.text.body, final: !!item.text.finalized });
          if (
            item.run_status?.run_id === target.runId &&
            ["failed", "cancelled", "interrupted"].includes(
              item.run_status.status,
            )
          )
            publish({ text: "", final: true, failed: true });
        }
        // Persisted message identity is required for an explicitly selected item.
        if (
          target.messageId ||
          frame.type === "final_reply" ||
          (frame.state?.items || []).some(
            (item: Data) =>
              item.run_status?.run_id === target.runId &&
              ["completed", "succeeded"].includes(item.run_status.status),
          )
        )
          void reconcile();
      } catch {
        unavailable();
      }
    };
    for (const name of [
      "projection_update",
      "projection_snapshot",
      "final_reply",
      "failed",
      "cancelled",
    ])
      source.addEventListener(name, receive);
  } else {
    source = new EventSource(`/api/events?thread_id=${thread}`);
    source.onmessage = (event) => {
      if (closed) return;
      try {
        const raw = event.data;
        if (typeof raw !== "string" || raw.length > 262144) return;
        const envelope = JSON.parse(raw);
        const id = Number(envelope.id || event.lastEventId);
        if (id > 0 && id <= sequence) return;
        if (id > 0) sequence = id;
        if (envelope.topic === "codex.shared") {
          if (envelope.payload?.threadId === target.threadId) {
            revision++;
            void reconcile();
          }
          return;
        }
        if (envelope.topic === "codex.connection") {
          revision++;
          void reconcile();
          return;
        }
        if (envelope.topic !== "codex") return;
        const packet = envelope.payload,
          p = packet?.params || {};
        if (
          p.threadId !== target.threadId ||
          (p.turn?.id || p.turnId) !== target.runId
        )
          return;
        revision++;
        const result = projection.receive(packet.method, p);
        if (result === "applied" && packet.method !== "turn/completed") {
          turns = projection.reconcile(turns);
          coding(turns);
        }
        if (packet.method === "turn/completed" || result === "unknown")
          void reconcile();
      } catch {
        unavailable();
      }
    };
  }
  source.onopen = () => {
    void reconcile();
  };
  source.onerror = () => {
    if (!closed) {
      unavailable();
      void reconcile();
    }
  };
  void reconcile();
  return close;
}
