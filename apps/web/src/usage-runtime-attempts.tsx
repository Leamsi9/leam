import { useEffect, useState } from "react";
import { api, type Data } from "./api";

const quantity = (value: unknown) => {
  if (value === null || value === undefined) return "Unknown";
  try { return BigInt(String(value)).toLocaleString(); } catch { return "Unknown"; }
};

/** On-demand local measurements only; never invokes a model or polls the provider. */
export function UsageRuntimeAttempts({ threadId = "" }: { threadId?: string }) {
  const [page, setPage] = useState<Data | null>(null);
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => { setOffset(0); }, [threadId]);
  useEffect(() => {
    let active = true;
    setPage(null); setError("");
    const query = new URLSearchParams({ limit: "30", offset: String(offset), threadId });
    api(`/usage/runtime-attempts?${query}`).then(value => {
      if (active) setPage(value);
    }).catch(reason => { if (active) setError(String(reason)); });
    return () => { active = false; };
  }, [threadId, offset, version]);
  return <section aria-label="IronClaw model attempts">
    <h3>Companion / IronClaw attempts</h3>
    <p>Each row is one observed model attempt, including retries. Only reported input and output enter totals. Unknown is not zero; cache is part of input. Provider identity, reasoning, auxiliary calls and evicted history are unavailable.</p>
    {threadId && <p>Conversation: {threadId}</p>}
    <button onClick={() => setVersion(value => value + 1)}>Refresh measured attempts</button>
    {error && <p role="alert">{error}</p>}
    {!page && !error && <p role="status">Loading local measurements…</p>}
    {page && <>
      <p>{page.coverage.status}: {page.coverage.details}</p>
      <p>{page.total} observed attempts</p>
      {page.items.length === 0 && <p>No attempts captured in this view yet.</p>}
      {page.items.map((row: Data) => <details key={row.id} className="usage-call">
        <summary>{row.model} · {row.status} · {quantity(row.input)} input / {quantity(row.output)} output{row.conflict ? " · Conflicting evidence: excluded from totals" : ""}</summary>
        <dl>
          <dt>Conversation</dt><dd>{row.threadId}</dd>
          <dt>Run</dt><dd>{row.runId}</dd>
          <dt>Attempt</dt><dd>{row.callId}</dd>
          <dt>Iteration</dt><dd>{quantity(row.iteration)}</dd>
          <dt>Started</dt><dd>{new Date(row.startedAt * 1000).toLocaleString()}</dd>
          <dt>Duration</dt><dd>{row.durationMs == null ? "Unknown" : `${quantity(row.durationMs)} ms`}</dd>
          <dt>Cached input</dt><dd>{quantity(row.cached)}</dd>
          <dt>Cache write input</dt><dd>{quantity(row.cacheWrite)}</dd>
          <dt>Reasoning output</dt><dd>Unknown</dd>
          <dt>Provider</dt><dd>Unknown</dd>
          <dt>Source</dt><dd>{row.source}</dd>
        </dl>
      </details>)}
      <button disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - 30))}>Previous attempts</button>
      <button disabled={offset + 30 >= page.total} onClick={() => setOffset(value => value + 30)}>Next attempts</button>
    </>}
  </section>;
}
