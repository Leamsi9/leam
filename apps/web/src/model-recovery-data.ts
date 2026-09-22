export type ModelRecovery = {
  reason:
    | "progress_idle_timeout"
    | "attempt_time_limit"
    | "unavailable"
    | "transient"
    | "internal";
  retries_used: number;
  max_retries: number;
  elapsed_ms: number;
  retry_after_ms: number;
};
const reasons = new Set([
  "progress_idle_timeout",
  "attempt_time_limit",
  "unavailable",
  "transient",
  "internal",
]);
/** Accept only host-defined enums and bounded integers, never provider text. */
export function modelRecovery(value: unknown): ModelRecovery | undefined {
  if (!value || typeof value !== "object") return;
  const data = value as Record<string, unknown>;
  if (typeof data.reason !== "string" || !reasons.has(data.reason)) return;
  for (const key of [
    "retries_used",
    "max_retries",
    "elapsed_ms",
    "retry_after_ms",
  ])
    if (
      typeof data[key] !== "number" ||
      !Number.isSafeInteger(data[key]) ||
      (data[key] as number) < 0
    )
      return;
  if ((data.retries_used as number) > (data.max_retries as number)) return;
  return {
    reason: data.reason as ModelRecovery["reason"],
    retries_used: data.retries_used as number,
    max_retries: data.max_retries as number,
    elapsed_ms: data.elapsed_ms as number,
    retry_after_ms: data.retry_after_ms as number,
  };
}
export const recoveryReason = {
  attempt_time_limit: "The model attempt reached its time limit",
  progress_idle_timeout: "No model progress was received before the timeout",
  unavailable: "The model service is unavailable",
  transient: "A temporary model error occurred",
  internal: "An internal model request error occurred",
};
