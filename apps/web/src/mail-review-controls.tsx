import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";

/** An explicit local owner decision; it never sends or alters Gmail messages. */
export function MailReviewControls({
  item,
  saved,
}: {
  item: Data;
  saved: (message: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draftRevision, setDraftRevision] = useState<string | null>(null);
  const [action, setAction] = useState(item.actionability?.action || "");
  const [kind, setKind] = useState(
    item.actionability?.kind === "reply" ? "reply" : "todo",
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      request.current?.abort();
    };
  }, []);
  if (!item.reviewRevision) return null;
  const draftStale = editing && draftRevision !== item.reviewRevision;
  async function decide(decision: "action" | "ignore") {
    if (
      request.current ||
      (decision === "action" && (!action.trim() || draftStale))
    )
      return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const result = await api(
        "/email/triage/review",
        "PUT",
        {
          accountId: item.accountId,
          messageId: item.id,
          reviewRevision:
            decision === "action" ? draftRevision : item.reviewRevision,
          decision,
          kind: decision === "action" ? kind : null,
          action: decision === "action" ? action.trim() : "",
        },
        controller.signal,
      );
      if (!active.current) return;
      if (
        result.accountId !== item.accountId ||
        result.messageId !== item.id ||
        result.actionability?.state !== decision ||
        result.actionability?.reviewedBy !== "user"
      )
        throw new Error("The review receipt could not be confirmed.");
      setEditing(false);
      saved(
        decision === "action"
          ? "Added to the Action inbox. Choose Focus on its card to place it at the top of Today."
          : "Excluded from Today. Gmail is unchanged.",
      );
    } catch (e) {
      if (active.current)
        setError(
          (controller.signal.aborted
            ? "The response timed out; the decision may have been saved."
            : e instanceof Error
              ? e.message
              : String(e)) +
            " Refresh triage status before retrying. Your draft is retained.",
        );
    } finally {
      clearTimeout(timer);
      if (request.current === controller) request.current = null;
      if (active.current) setBusy(false);
    }
  }
  return (
    <div className="agenda-mail-review-controls">
      {editing ? (
        <fieldset disabled={busy}>
          <legend>Add this message to Today</legend>
          {draftStale && (
            <div role="alert">
              <p>
                This message or its review changed. Check the current message
                before applying your retained draft.
              </p>
              <button
                className="secondary"
                onClick={() => setDraftRevision(item.reviewRevision)}
              >
                Confirm updated message
              </button>
            </div>
          )}
          <label>
            Action for Today
            <input
              value={action}
              maxLength={240}
              onChange={(e) => setAction(e.target.value)}
              placeholder="What do you need to do?"
            />
          </label>
          <label>
            Action type
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="todo">Task</option>
              <option value="reply">Reply</option>
            </select>
          </label>
          <p className="agenda-hint">
            This is your local triage decision. It does not send a reply or
            create a commitment.
          </p>
          <div className="agenda-triage">
            <button
              disabled={!action.trim() || draftStale}
              onClick={() => void decide("action")}
            >
              Add to Today
            </button>
            <button className="secondary" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </fieldset>
      ) : (
        <div className="agenda-triage">
          <button
            className="secondary"
            disabled={busy}
            onClick={() => {
              setDraftRevision(item.reviewRevision);
              setEditing(true);
            }}
          >
            Add to Today…
          </button>
          {item.actionability?.state !== "ignore" && (
            <button
              className="secondary"
              disabled={busy}
              onClick={() => void decide("ignore")}
            >
              Exclude
            </button>
          )}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
