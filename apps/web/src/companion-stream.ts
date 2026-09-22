import { useEffect, useRef, useState } from "react";
import type { Data } from "./api";
import { modelRecovery, type ModelRecovery } from "./model-recovery-data";

export type LiveRun = {
  id: string;
  text: string;
  textFinal?: boolean;
  status: string;
  terminal: boolean;
  stopRequested: boolean;
  failed: boolean;
  terminalStatus?: string;
  recovery?: ModelRecovery;
  approvalId?: string | null;
  retrying?: boolean;
};
const terminal = new Set([
  "completed",
  "succeeded",
  "failed",
  "cancelled",
  "canceled",
  "interrupted",
  "recovery_required",
]);
const eventNames = [
  "accepted",
  "running",
  "capability_progress",
  "capability_activity",
  "projection_update",
  "projection_snapshot",
  "final_reply",
  "failed",
  "cancelled",
  "gate",
  "auth_required",
];

// Canonical shapes: IronClaw WebChatV2EventFrame and ProductProjectionItem.
export function useCompanionStream(thread: string, reconcile: () => void) {
  const [runs, setRuns] = useState<Record<string, LiveRun>>({});
  const runThread = useRef(thread);
  const [connection, setConnection] = useState("Connecting to live progress…");
  const reconcileRef = useRef(reconcile);
  reconcileRef.current = reconcile;
  useEffect(() => {
    runThread.current = thread;
    setRuns({});
    if (!thread) return;
    let stopped = false;
    setConnection("Connecting to live progress…");
    const source = new EventSource(
      `/api/companion/threads/${encodeURIComponent(thread)}/events`,
    );
    function receive(event: MessageEvent) {
      if (stopped || event.data.length > 262144) return;
      let frame: Data;
      try {
        frame = JSON.parse(event.data);
      } catch {
        return;
      }
      if (frame.state?.thread_id && frame.state.thread_id !== thread) return;
      let shouldReconcile = false;
      const edits: { id: string; patch: Partial<LiveRun> }[] = [];
      const retryStatuses = new Map<string, string>();
      function edit(id: unknown, patch: Partial<LiveRun>) {
        if (typeof id === "string" && id.length <= 128)
          edits.push({ id, patch });
      }
      for (const item of Array.isArray(frame.state?.items)
        ? frame.state.items.slice(0, 512)
        : []) {
        if (item.text && typeof item.text.body === "string")
          edit(item.text.run_id, {
            text: item.text.body.slice(0, 100000),
            textFinal: !!item.text.finalized,
          });
        if (item.work_summary?.phase === "retrying" && modelRecovery(item.work_summary.model_recovery)) {
          const recovery = modelRecovery(item.work_summary.model_recovery)!;
          retryStatuses.set(item.work_summary.run_id, `Retrying model request (${recovery.retries_used}/${recovery.max_retries}). Waiting for model progress…`);
        }
        if (item.work_summary)
          edit(item.work_summary.run_id, {
            retrying: item.work_summary.phase === "retrying" && !!modelRecovery(item.work_summary.model_recovery),
            status:
              typeof item.work_summary.body === "string"
                ? item.work_summary.body.slice(0, 500)
                : "Working…",
            ...(item.work_summary.phase === "retrying" && modelRecovery(item.work_summary.model_recovery)
              ? { recovery: modelRecovery(item.work_summary.model_recovery) } : {}),
          });
        if (item.capability_activity) {
          const activity = item.capability_activity;
          edit(activity.turn_run_id, {
            status:
              activity.status === "started"
                ? `Using ${String(activity.capability_id).slice(0, 150)}…`
                : "Reviewing tool result…",
          });
        }
        if (item.run_status) {
          const state = item.run_status,
            ended = terminal.has(state.status),
            failed = [
              "failed",
              "cancelled",
              "canceled",
              "interrupted",
            ].includes(state.status);
          edit(state.run_id, {
            terminal: ended,
            ...(ended ? { terminalStatus: state.status } : {}),
            ...(["queued", "running", ...terminal].includes(state.status) ? { approvalId: null } : {}),
            failed,
            status: failed
              ? state.failure_summary?.slice(0, 500) ||
                `Response ${state.status}.`
              : ended
                ? "Completed"
                : "Working…",
          });
          shouldReconcile ||= ended;
        }
        if (item.gate) {
          const approval = item.gate.gate_kind === "approval" &&
            /^gate:approval-[a-f0-9-]{36}$/.test(item.gate.gate_ref || "")
              ? item.gate.gate_ref.slice("gate:approval-".length) : null;
          edit(item.gate.run_id, {
            approvalId: approval,
            status: approval ? "Waiting for your approval." : "Waiting for runtime input.",
          });
        }
      }
      if (frame.type === "final_reply") {
        edit(frame.reply?.turn_run_id, {
          text: frame.reply?.text?.slice(0, 100000) || "",
          textFinal: true,
          terminal: true,
          terminalStatus: "completed",
          status: "Completed",
        });
        shouldReconcile = true;
      }
      if (frame.progress)
        edit(frame.progress.turn_run_id, {
          status:
            frame.progress.kind === "tool_running"
              ? "Using a tool…"
              : "Working…",
        });
      if (frame.activity)
        edit(frame.activity.turn_run_id, {
          status: `Using ${String(frame.activity.capability_id).slice(0, 150)}…`,
        });
      if (["failed", "cancelled"].includes(frame.type)) {
        const id =
          frame.run_state?.turn_run_id ||
          frame.run_state?.run_id ||
          frame.response?.turn_run_id;
        edit(id, {
          terminal: true,
          terminalStatus: frame.type,
          failed: true,
          status: `Response ${frame.type}.`,
        });
        shouldReconcile = true;
      }
      setRuns((previous) => {
        const next = { ...previous };
        for (const { id, patch } of edits) {
          const before = next[id] || {
            id,
            text: "",
            status: "Working…",
            terminal: false,
            stopRequested: false,
            failed: false,
          };
          // Replayed older progress must never resurrect a completed response.
          if (before.terminal && !patch.terminal && !patch.textFinal) continue;
          if (before.textFinal && patch.textFinal === false) continue;
          if (patch.recovery && before.recovery && (
            patch.recovery.retries_used < before.recovery.retries_used ||
            patch.recovery.elapsed_ms < before.recovery.elapsed_ms
          )) continue;
          const newText = patch.text !== undefined && patch.text !== before.text;
          const keepRetry = before.retrying && patch.retrying === undefined && !patch.terminal && !newText;
          next[id] = {
            ...before,
            ...patch,
            ...(keepRetry ? { status: before.status } : {}),
            ...(newText && patch.retrying === undefined && !patch.terminal ? { retrying: false, status: "Responding…" } : {}),
            stopRequested: patch.terminal ? false : before.stopRequested,
          };
        }
        // A snapshot may include historical tool activities after its current
        // retry summary. Generic progress must not hide the typed retry phase.
        for (const [id, status] of retryStatuses) {
          if (next[id] && !next[id].terminal && !next[id].approvalId) next[id] = { ...next[id], status };
        }
        return Object.fromEntries(Object.entries(next).slice(-10));
      });
      if (shouldReconcile) reconcileRef.current();
    }
    eventNames.forEach((name) =>
      source.addEventListener(name, receive as EventListener),
    );
    source.onopen = () => {
      if (!stopped) setConnection("Live progress connected");
    };
    source.onerror = () => {
      if (!stopped)
        setConnection("Live progress reconnecting; checking saved messages…");
    };
    source.addEventListener("stream_error", (event) => {
      if (stopped) return;
      setConnection("Live progress unavailable; checking saved messages…");
      try {
        if (JSON.parse((event as MessageEvent).data).retryable === false)
          source.close();
      } catch {
        source.close();
      }
    });
    return () => {
      stopped = true;
      source.close();
    };
  }, [thread]);
  return {
    runs: runThread.current === thread ? Object.values(runs) : [],
    connection,
    approvalResolved: (id: string, status: string, outcome: string) => {
      setRuns((previous) => previous[id] ? { ...previous, [id]: {
        ...previous[id], approvalId: null,
        status: outcome === "cancelled" ? "Declined. Completed actions are not undone." : "Decision recorded. Continuing…",
        terminal: ["Cancelled", "Completed", "Failed"].includes(status),
        failed: status === "Cancelled" || status === "Failed",
      } } : previous);
      reconcileRef.current();
    },
    stopAcknowledged: (id: string, status: string) => {
      setRuns((previous) =>
        previous[id] && !previous[id].terminal
          ? {
              ...previous,
              [id]: {
                ...previous[id],
                terminal: status !== "CancelRequested",
                stopRequested: status === "CancelRequested",
                failed: status === "Cancelled",
                status:
                  status === "CancelRequested"
                    ? "Stop requested…"
                    : status === "Cancelled"
                      ? "Stopped. Completed actions are not undone."
                      : "Response ended.",
              },
            }
          : previous,
      );
    },
    accepted: (id: string) => {
      if (typeof id === "string" && id.length <= 128)
        setRuns((previous) =>
          previous[id]
            ? previous
            : {
                ...previous,
                [id]: {
                  id,
                  text: "",
                  status: "Getting started…",
                  terminal: false,
                  stopRequested: false,
                  failed: false,
                },
              },
        );
    },
  };
}
