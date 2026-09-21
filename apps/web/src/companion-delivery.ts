import type { Data } from "./api";
import type { Receipt } from "./voice/conversation";
const runId = (value: unknown): value is string =>
  typeof value === "string" &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
export const queuedNotice =
  "Follow-up received for the current response. It may be included as that response continues; no separate response is promised. It has not been resent.";
export function deliveryReceipt(value: Data, thread: string): Receipt | null {
  if (value.thread_id !== undefined && value.thread_id !== thread) return null;
  if (
    ["submitted", "already_submitted"].includes(value.outcome) &&
    runId(value.run_id)
  )
    return { outcome: value.outcome, run_id: value.run_id };
  if (
    value.outcome === "deferred_busy" &&
    value.thread_id === thread &&
    runId(value.active_run_id) &&
    typeof value.accepted_message_ref === "string" &&
    value.accepted_message_ref.length > 0 &&
    value.accepted_message_ref.length <= 256
  )
    return {
      outcome: "deferred_busy",
      run_id: value.active_run_id,
      autoReply: false,
    };
  return null;
}
