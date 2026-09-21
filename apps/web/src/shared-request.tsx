import { useEffect, useRef, useState } from "react";
import { api } from "./api";

type Card = {
  id: string;
  generation: string;
  method: string;
  params: Record<string, any>;
  changes?: unknown[];
  decisions?: string[];
  connected: boolean;
  submitted?: boolean;
  receipt?: { state: string; detail: string };
};
export function SharedRequest({ request }: { request: Card }) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [receipt, setReceipt] = useState<Card["receipt"]>();
  const attempted = useRef(false),
    mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const state =
    request.receipt?.state === "resolved"
      ? request.receipt
      : receipt || request.receipt;
  async function send(response: Record<string, unknown>) {
    if (attempted.current || request.submitted || !request.connected) return;
    attempted.current = true;
    setReceipt({ state: "pending", detail: "Submitting your response…" });
    try {
      const result = await api(
        `/codex/shared/requests/${encodeURIComponent(request.id)}`,
        "POST",
        {
          generation: request.generation,
          requestId: crypto.randomUUID(),
          response,
        },
      );
      if (mounted.current)
        setReceipt({
          state: String(result.state),
          detail: String(result.detail),
        });
    } catch {
      if (mounted.current)
        setReceipt({
          state: "uncertain",
          detail:
            "This request changed, or response delivery is uncertain. Refresh its status or inspect Codex. Leam will not send it again automatically.",
        });
    }
  }
  const p = request.params;
  return (
    <section className="request-card" aria-label="Shared Codex request">
      <h3>
        {request.method === "item/tool/requestUserInput"
          ? "Codex needs your input"
          : "Review Codex approval"}
      </h3>
      {state || request.submitted ? (
        <p role="status">
          {state?.detail || "Waiting for Codex to update this request."}
        </p>
      ) : (
        <>
          {!request.connected && <p>Reconnect to Codex before responding.</p>}
          {request.method === "item/tool/requestUserInput" ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void send({
                  answers: Object.fromEntries(
                    (p.questions || []).map((q: any) => [
                      q.id,
                      { answers: [answers[q.id]] },
                    ]),
                  ),
                });
              }}
            >
              {(p.questions || []).map((q: any) => (
                <fieldset key={q.id} style={{ minWidth: 0 }}>
                  <legend>{q.header}</legend>
                  <p>{q.question}</p>
                  {(q.options || []).map((o: any) => (
                    <label
                      key={o.label}
                      style={{ display: "flex", gap: 8, alignItems: "start" }}
                    >
                      <input
                        type="radio"
                        name={`${request.id}:${q.id}`}
                        value={o.label}
                        checked={answers[q.id] === o.label}
                        onChange={() =>
                          setAnswers((a) => ({ ...a, [q.id]: o.label }))
                        }
                        style={{ width: "auto", marginTop: 6 }}
                      />
                      <span>
                        {o.label}
                        <small style={{ display: "block" }}>
                          {o.description}
                        </small>
                      </span>
                    </label>
                  ))}
                  {(!q.options?.length || q.isOther) && (
                    <label>
                      Write your answer
                      <input
                        aria-label={q.header}
                        value={answers[q.id] || ""}
                        maxLength={4096}
                        onChange={(e) =>
                          setAnswers((a) => ({ ...a, [q.id]: e.target.value }))
                        }
                      />
                    </label>
                  )}
                </fieldset>
              ))}
              <button
                className="primary"
                disabled={
                  !request.connected ||
                  p.questions?.some((q: any) => !answers[q.id]?.trim())
                }
              >
                Reply to Codex
              </button>
            </form>
          ) : (
            <>
              <p>{p.reason || "Review this action before continuing."}</p>
              {p.command && (
                <>
                  <strong>Command</strong>
                  <pre
                    style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                  >
                    {p.command}
                  </pre>
                  <p>
                    Working directory: <code>{p.cwd}</code>
                  </p>
                </>
              )}
              {request.changes && (
                <>
                  <strong>Full proposed file changes</strong>
                  <pre
                    style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                  >
                    {JSON.stringify(request.changes, null, 2)}
                  </pre>
                </>
              )}
              <details>
                <summary>Complete approval request</summary>
                <pre
                  style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                >
                  {JSON.stringify(p, null, 2)}
                </pre>
              </details>
              <p>
                <small>
                  Allow once applies only to this action. No persistent policy
                  is granted.
                </small>
              </p>
              <div className="actions">
                {request.decisions?.map((d) => (
                  <button
                    key={d}
                    className={d === "accept" ? "primary" : "secondary"}
                    disabled={!request.connected}
                    onClick={() => void send({ decision: d })}
                  >
                    {d === "accept" ? "Allow once" : "Decline"}
                  </button>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
