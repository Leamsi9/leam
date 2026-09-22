import type { Data } from "./api";
import type { LiveRun } from "./companion-stream";

type Entry = { kind: "message"; message: Data } | { kind: "run"; run: LiveRun };

// The saved timeline is canonical. Stream arrival order is not conversation
// order: reconnect snapshots and late failures routinely replay earlier runs.
export function companionTimeline(messages: Data[], runs: LiveRun[]): Entry[] {
  const visible = messages.filter(message => ["user", "assistant"].includes(message.kind));
  const after = new Map<number, LiveRun[]>();
  const unmatched: LiveRun[] = [];
  for (const run of runs) {
    const finalized = visible.filter(message => message.kind === "assistant" &&
      message.turn_run_id === run.id && message.status === "finalized");
    const latestUser = Math.max(...visible.filter(message => message.kind === "user" &&
      message.turn_run_id === run.id && Number.isSafeInteger(message.sequence)).map(message => message.sequence));
    const latestFinal = Math.max(...finalized.filter(message => Number.isSafeInteger(message.sequence))
      .map(message => message.sequence));
    // A completed saved answer retires its streamed copy. Failed/cancelled
    // outcomes remain evidence even when this run already produced some output.
    if (finalized.length && !run.failed && (run.terminal ||
      (Number.isFinite(latestUser) && Number.isFinite(latestFinal) && latestFinal >= latestUser))) continue;
    let anchor = -1;
    visible.forEach((message, index) => { if (message.turn_run_id === run.id) anchor = index; });
    if (anchor < 0) {
      unmatched.push(run);
    } else {
      const group = after.get(anchor) || [];
      group.push(run.failed && finalized.some(message => message.content === run.text)
        ? { ...run, text: "" } : run);
      after.set(anchor, group);
    }
  }
  // A terminal projection whose original messages are outside the loaded page
  // must not masquerade as the newest response. Keep its evidence above this
  // page until loading history supplies its canonical anchor. Active unanchored
  // output remains at the end while its accepted user message is being fetched.
  const result: Entry[] = unmatched.filter(run => run.terminal).map(run => ({ kind: "run", run }));
  visible.forEach((message, index) => {
    result.push({ kind: "message", message });
    for (const run of after.get(index) || []) result.push({ kind: "run", run });
  });
  for (const run of unmatched.filter(run => !run.terminal)) result.push({ kind: "run", run });
  return result;
}
