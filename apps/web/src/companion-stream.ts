import { useEffect, useRef, useState } from "react";
import type { Data } from "./api";

export type LiveRun = {
  id: string;
  text: string;
  textFinal?: boolean;
  status: string;
  terminal: boolean;
  stopRequested: boolean;
  failed: boolean;
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
  const [connection, setConnection] = useState("Connecting to live progress…");
  const reconcileRef = useRef(reconcile);
  reconcileRef.current = reconcile;
  useEffect(() => {
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
        if (item.work_summary)
          edit(item.work_summary.run_id, {
            status:
              typeof item.work_summary.body === "string"
                ? item.work_summary.body.slice(0, 500)
                : "Working…",
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
        if (item.gate)
          edit(item.gate.run_id, {
            status: "Waiting for your approval in runtime controls.",
          });
      }
      if (frame.type === "final_reply") {
        edit(frame.reply?.turn_run_id, {
          text: frame.reply?.text?.slice(0, 100000) || "",
          textFinal: true,
          terminal: true,
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
          next[id] = {
            ...before,
            ...patch,
            stopRequested: patch.terminal ? false : before.stopRequested,
          };
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
    runs: Object.values(runs),
    connection,
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
