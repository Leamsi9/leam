import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";
import { approvalsChanged } from "./approvals-status";
import { SemanticBadge } from "./semantic-badge";
import { recoveryReason, type ModelRecovery } from "./model-recovery-data";
import "./model-recovery.css";

type Diagnosis = {
  requestId: string;
  threadId: string;
  operation: "coding.handoff";
  input: { title: string; instructions: string; context: string };
  reason: string;
};

/** Response-local feedback. A pending diagnosis is never a dispatched coding task. */
export function ModelRecoveryNotice({
  threadId,
  runId,
  recovery,
  terminal,
  failed,
  prepareRetry,
}: {
  threadId: string;
  runId: string;
  recovery?: ModelRecovery;
  terminal: boolean;
  failed: boolean;
  prepareRetry?: () => string;
}) {
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const [proposalReady, setProposalReady] = useState(false);
  const operation = useRef<AbortController | null>(null);
  const alive = useRef(true);
  const key = `leam-model-diagnosis:${threadId}:${runId}`;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      operation.current?.abort();
    };
  }, []);
  async function diagnose() {
    if (operation.current) return;
    const controller = new AbortController();
    operation.current = controller;
    setPending(true);
    setNotice("");
    try {
      const main = await api(
        "/coding/main",
        "GET",
        undefined,
        controller.signal,
      );
      if (!alive.current || controller.signal.aborted) return;
      if (!main.main || main.bindingValid !== true) {
        if (alive.current)
          setNotice(
            "Choose and connect your Main session in Coding, then prepare the diagnosis again. Nothing was sent.",
          );
        return;
      }
      let request: Diagnosis;
      const previous = sessionStorage.getItem(key);
      if (previous) {
        request = JSON.parse(previous) as Diagnosis;
        if (
          request.threadId !== threadId ||
          request.operation !== "coding.handoff" ||
          typeof request.requestId !== "string"
        )
          throw new Error("Invalid saved diagnosis");
      } else {
        request = {
          requestId: crypto.randomUUID(),
          threadId,
          operation: "coding.handoff",
          input: {
            title: "Diagnose Companion model recovery",
            instructions:
              "Diagnose this Companion model recovery using the referenced thread/run and sanitized technical evidence. Inspect the relevant source and authorized operational metadata; propose and implement a bounded correction if warranted. Follow pinned agent-protocols and the current Main coordination policy. Do not interrupt active user runs or change services without the existing authorized deployment procedure. Do not copy conversation contents, credentials or raw provider errors into reports. A recovery symptom does not establish its cause.",
            context: JSON.stringify({
              threadId,
              runId,
              terminal,
              failed,
              ...(recovery ? { model_recovery: recovery } : {}),
            }),
          },
          reason:
            "User requested a reviewed coding diagnosis of Companion response recovery.",
        };
        // Store only this bounded, sanitized exact request for uncertain receipt retries.
        sessionStorage.setItem(key, JSON.stringify(request));
      }
      const result: Data = await api(
        "/proposals",
        "POST",
        request,
        controller.signal,
      );
      if (!alive.current) return;
      if (result.state !== "pending" || result.operation !== "coding.handoff") {
        setNotice(
          "This diagnosis already has a decision. Inspect Approvals; no new work was dispatched.",
        );
        return;
      }
      setProposalReady(true);
      setNotice(
        "Diagnosis prompt ready in Approvals. Review the exact Main target before sending. No coding task has been dispatched.",
      );
      approvalsChanged();
    } catch {
      if (alive.current)
        setNotice(
          "Diagnosis preparation could not be confirmed. Retry preparation to check the same proposal; do not create a separate task.",
        );
    } finally {
      if (operation.current === controller) operation.current = null;
      if (alive.current) setPending(false);
    }
  }
  return (
    <div className="model-recovery" aria-label="Response recovery">
      <p role="status">
        <SemanticBadge
          value={terminal ? (failed ? "failed" : "unknown") : "pending"}
        >
          {terminal
            ? failed
              ? "Response failed"
              : "Response ended"
            : "Last model recovery"}
        </SemanticBadge>{" "}
        {recovery && (
          <span>
            {recoveryReason[recovery.reason]}. Retry {recovery.retries_used}/
            {recovery.max_retries}; recorded delay{" "}
            {Math.ceil(recovery.retry_after_ms / 1000)}s. Run elapsed then{" "}
            {Math.floor(recovery.elapsed_ms / 1000)}s.
          </span>
        )}
        {!terminal && (
          <span>
            {" "}
            Waiting for the current run; no duplicate request has been sent.
          </span>
        )}
      </p>
      {terminal && failed && (
        <div className="actions">
          {prepareRetry && (
            <button
              type="button"
              className="secondary"
              onClick={() => setNotice(prepareRetry())}
            >
              Prepare response retry
            </button>
          )}
          {!proposalReady && (
            <button
              type="button"
              className="secondary"
              disabled={pending}
              onClick={() => void diagnose()}
            >
              {pending ? "Preparing diagnosis…" : "Prepare coding diagnosis"}
            </button>
          )}
          {proposalReady && (
            <button
              type="button"
              className="secondary"
              onClick={() =>
                window.dispatchEvent(
                  new CustomEvent("leam:navigate", { detail: "approvals" }),
                )
              }
            >
              Review diagnosis in Approvals
            </button>
          )}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
    </div>
  );
}
