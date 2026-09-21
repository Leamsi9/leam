import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";
import { rememberSession, sessionValue } from "./session-cache";

export function CodingHandoffCard({
  item,
  fail,
  decline,
}: {
  item: Data;
  fail: (e: unknown) => void;
  decline: () => void;
}) {
  const draftKey = "handoff:draft:" + item.id;
  const [text, setText] = useState(() =>
      sessionValue(draftKey, item.input.instructions),
    ),
    [review, setReview] = useState<Data | null>(null),
    [status, setStatus] = useState<Data | null>(item.handoff || null),
    [busy, setBusy] = useState(false);
  const pending = useRef(false),
    mounted = useRef(true),
    revision = useRef(0);
  const path = "/coding/handoffs/" + item.id;
  useEffect(() => {
    mounted.current = true;
    void api(path)
      .then((value) => {
        if (mounted.current) setStatus(value);
      })
      .catch(fail);
    return () => {
      mounted.current = false;
    };
  }, [item.id]);
  useEffect(() => {
    if (item.handoff) setStatus(item.handoff);
  }, [item.handoff]);
  const started =
    status && !["reviewed", "not_reviewed"].includes(status.state);
  function open(value: Data) {
    window.dispatchEvent(
      new CustomEvent("leam:open-coding", {
        detail: {
          id: value.threadId,
          cwd: value.workspace,
          name: item.input.title,
        },
      }),
    );
  }
  async function prepare() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    const version = revision.current;
    try {
      const result = await api(path + "/review", "POST", { text });
      if (mounted.current && version === revision.current) setReview(result);
    } catch (error) {
      fail(error);
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function start() {
    if (pending.current || !review) return;
    pending.current = true;
    setBusy(true);
    try {
      const value = await api(path + "/start", "POST", {
        previewToken: review.previewToken,
        confirmed: true,
      });
      if (mounted.current) {
        setStatus(value);
        setReview(null);
        if (value.state === "accepted") open(value);
      }
    } catch (error) {
      fail(error);
      try {
        const value = await api(path);
        if (mounted.current) setStatus(value);
      } catch {
        /* Original error stays visible. */
      }
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <article className="card settings-form" aria-label="Coding handoff">
      <h3>{item.input.title}</h3>
      <p>{item.reason}</p>
      <p>
        This starts a dedicated Codex session. Native coding approvals and your
        agent-protocols remain in force.
      </p>
      {!started && item.state === "pending" && (
        <>
          <label>
            Exact task for Codex
            <textarea
              aria-label="Exact task for Codex"
              value={text}
              onChange={(e) => {
                revision.current++;
                setText(e.target.value);
                rememberSession(draftKey, e.target.value);
                setReview(null);
              }}
            />
          </label>
          <p>
            This is a suggested draft until you review it. The exact text above
            is sent unchanged.
          </p>
          <details>
            <summary>Quoted Companion context</summary>
            <p className="prose">{item.input.context || "No extra context"}</p>
            <small>
              Source conversation: {item.thread_id}. Context is reference data,
              not extra authority.
            </small>
          </details>
          {!review ? (
            <button
              disabled={busy || !text.trim()}
              onClick={() => void prepare()}
            >
              Review coding task
            </button>
          ) : (
            <section aria-label="Reviewed coding task">
              <p>Workspace: {review.workspace}</p>
              <p>
                {review.model} · {review.reasoningEffort} reasoning
              </p>
              <details>
                <summary>Validated protocol identity</summary>
                <code>{review.protocolIdentity}</code>
              </details>
              <button disabled={busy} onClick={() => void start()}>
                {busy ? "Starting…" : "Start in Coding"}
              </button>
            </section>
          )}
          <button className="secondary" disabled={busy} onClick={decline}>
            Decline coding task
          </button>
        </>
      )}
      {started && (
        <p role="status">
          {status.state === "accepted"
            ? "Handoff accepted"
            : "Delivery is uncertain; inspect the linked session before taking further action"}
          . Codex status: {status.taskStatus || "unknown"}.
        </p>
      )}
      {status?.resultText && <div className="prose">{status.resultText}</div>}
      {status?.threadId && (
        <button className="secondary" onClick={() => open(status)}>
          Open linked Coding session
        </button>
      )}
      {item.state === "declined" && <p>Handoff declined</p>}
      <small>
        Accepting a handoff is not proof the requested work is complete.
      </small>
    </article>
  );
}
