import type {Data} from "./api";
import type {LiveRun} from "./companion-stream";

/** Saved sequence and exact admission receipts outrank projection arrival order. */
export function currentResponseRun(messages: Data[], runs: LiveRun[], accepted?: {id: string; afterSequence: number}): LiveRun | null {
  const rows = messages.filter(message => ["user", "assistant"].includes(message.kind) && message.status !== "rejected_busy" && Number.isSafeInteger(message.sequence));
  const latest = rows.reduce<Data | null>((last, message) => !last || message.sequence > last.sequence ? message : last, null);
  let id: string | undefined;
  // A just-admitted response can arrive before its canonical user message.
  if (accepted && (!latest || latest.sequence <= accepted.afterSequence) && runs.some(run => run.id === accepted.id)) id = accepted.id;
  else if (latest) id = typeof latest.turn_run_id === "string" ? latest.turn_run_id : undefined;
  else if (runs.length === 1) id = runs[0].id;
  // Multiple unanchored historical runs do not prove which run is current.
  const run = runs.find(candidate => candidate.id === id);
  if (!run || run.terminal || run.textFinal) return null;
  const related = rows.filter(message => message.turn_run_id === run.id);
  const lastUser = Math.max(-1, ...related.filter(message => message.kind === "user").map(message => message.sequence));
  if (related.some(message => message.kind === "assistant" && message.status === "finalized" && message.sequence >= lastUser)) return null;
  return run;
}
