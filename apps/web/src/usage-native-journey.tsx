import { useEffect, useState } from "react";
import { api, type Data } from "./api";

function quantity(value: string | number | null | undefined) {
  if (value == null) return "Unknown";
  try {
    return BigInt(value).toLocaleString();
  } catch {
    return "Unknown";
  }
}
const label = (value: string) => value.replaceAll("_", " ");

/** Display-only, mounted only on the journey page. No provider calls or polling. */
export function UsageNativeJourney({
  selected,
  onSelect,
  threadIds,
}: {
  selected: string;
  onSelect: (id: string) => void;
  threadIds: string[];
}) {
  return (
    <section aria-label="Native Codex session journey">
      <h3>Session journey</h3>
      <p>
        Observed Codex steps and model responses. Tool byte sizes are not token
        charges. Hidden prompt stages and unreported retries are unavailable.
      </p>
      <label>
        Session
        <select
          aria-label="Journey session"
          value={selected}
          onChange={(e) => onSelect(e.target.value)}
        >
          <option value="">Choose a measured session</option>
          {[...new Set([selected, ...threadIds].filter(Boolean))].map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </label>
      {!threadIds.length && !selected && (
        <p>No measured sessions in the current usage filters.</p>
      )}
      {selected && <Journey key={selected} threadId={selected} />}
    </section>
  );
}
function Journey({ threadId }: { threadId: string }) {
  const [turnDraft, setTurnDraft] = useState(""),
    [turn, setTurn] = useState("");
  const [offset, setOffset] = useState(0),
    [revision, setRevision] = useState(0);
  const [data, setData] = useState<Data | null>(null),
    [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setData(null);
    setError("");
    setBusy(true);
    const params = new URLSearchParams({
      threadId,
      limit: "100",
      offset: String(offset),
    });
    if (turn) params.set("turnId", turn);
    void api(`/usage/journey?${params}`, "GET", undefined, controller.signal)
      .then((result) => {
        if (current) setData(result);
      })
      .catch((e) => {
        if (current) setError(e.message || "Journey unavailable");
      })
      .finally(() => {
        if (current) setBusy(false);
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [threadId, turn, offset, revision]);
  return (
    <>
      <form
        className="usage-filters"
        onSubmit={(e) => {
          e.preventDefault();
          setOffset(0);
          setTurn(turnDraft.trim());
          setRevision((value) => value + 1);
        }}
      >
        <label>
          Turn ID (optional)
          <input
            maxLength={128}
            value={turnDraft}
            onChange={(e) => setTurnDraft(e.target.value)}
            placeholder="All observed turns"
          />
        </label>
        <button className="secondary" disabled={busy}>
          Apply turn filter
        </button>
      </form>
      <button
        className="secondary"
        disabled={busy}
        onClick={() => setRevision((value) => value + 1)}
      >
        Refresh journey
      </button>
      {busy && <p role="status">Loading observed steps…</p>}
      {error && <p role="alert">{error}</p>}
      {data && (
        <>
          <p role="status">
            {data.collection === "paused"
              ? "Collection paused. Saved observations remain available."
              : data.collection === "not_configured"
                ? "Native source is not configured. Saved observations remain available."
                : data.coverage.importComplete
                  ? "Last scan reached the observed file end; newer activity may not be imported yet."
                  : "Historical scan is incomplete or has not reached this session yet."}
          </p>
          <div className="usage-metrics">
            <div className="usage-metric">
              <span>Reported model responses</span>
              <strong>{quantity(data.counts.model_call || 0)}</strong>
            </div>
            <div className="usage-metric">
              <span>Tool calls with explicit ownership or native boundary</span>
              <strong>{quantity(data.counts.tool_call || 0)}</strong>
            </div>
            <div className="usage-metric">
              <span>Tool calls with inferred ownership</span>
              <strong>{quantity(data.inferredCounts.tool_call || 0)}</strong>
            </div>
          </div>
          <details>
            <summary>Coverage and accounting limits</summary>
            <p>
              Counts describe imported observations only. Missing steps are
              unknown, not zero activity. Provider response totals come from the
              existing deduplicated ledger; cached input and reasoning are
              subsets. Conflicting records show no token amount.
            </p>
            <p>
              {data.coverage.tokenAllocation}. {data.coverage.limitations}
            </p>
            <dl className="usage-evidence">
              <dt>Unattributed records excluded</dt>
              <dd>{data.coverage.unattributedSteps}</dd>
              <dt>Inherited records excluded</dt>
              <dd>{data.coverage.excludedInherited}</dd>
              <dt>Oversized records omitted</dt>
              <dd>{data.coverage.oversizedRecords}</dd>
            </dl>
          </details>
          <ol className="usage-records">
            {data.items.map((step: Data) => (
              <li className="usage-record" key={step.id}>
                <header>
                  <strong>{step.toolName || label(step.kind)}</strong>
                  <span>
                    {step.status === "conflict"
                      ? "Conflicting usage"
                      : label(step.evidence)}
                  </span>
                </header>
                <small>
                  {step.timestamp == null
                    ? "Time not reported"
                    : new Date(step.timestamp * 1000).toLocaleString()}
                </small>
                {step.tokens ? (
                  <dl className="usage-breakdown">
                    <div>
                      <dt>Input tokens</dt>
                      <dd>{quantity(step.tokens.input)}</dd>
                    </div>
                    <div>
                      <dt>Cached input subset</dt>
                      <dd>{quantity(step.tokens.cached)}</dd>
                    </div>
                    <div>
                      <dt>Output tokens</dt>
                      <dd>{quantity(step.tokens.output)}</dd>
                    </div>
                    <div>
                      <dt>Reasoning subset</dt>
                      <dd>{quantity(step.tokens.reasoning)}</dd>
                    </div>
                  </dl>
                ) : (
                  <p>
                    {step.inputBytes != null || step.outputBytes != null
                      ? `${quantity(step.inputBytes)} input bytes · ${quantity(step.outputBytes)} output bytes`
                      : "Token contribution not reported"}
                  </p>
                )}
                <details>
                  <summary>Step details</summary>
                  <dl className="usage-evidence">
                    <dt>Kind</dt>
                    <dd>{label(step.kind)}</dd>
                    <dt>Turn</dt>
                    <dd>{step.turnId || "Unattributed"}</dd>
                    <dt>Turn attribution</dt>
                    <dd>{step.turnAttribution}</dd>
                    <dt>Model</dt>
                    <dd>{step.model || "Not reported"}</dd>
                    <dt>Call identity</dt>
                    <dd>{step.callId || "Not reported"}</dd>
                  </dl>
                </details>
              </li>
            ))}
          </ol>
          {!data.items.length && (
            <p>
              No observed steps in this selection. This does not establish that
              no work happened.
            </p>
          )}
          <div className="usage-actions">
            <button
              className="secondary"
              disabled={!offset || busy}
              onClick={() => setOffset(Math.max(0, offset - 100))}
            >
              Previous steps
            </button>
            <span>
              Steps {data.items.length ? offset + 1 : 0}–
              {offset + data.items.length} of {data.total}
            </span>
            <button
              className="secondary"
              disabled={!data.hasMore || busy}
              onClick={() => setOffset(data.nextOffset)}
            >
              Next steps
            </button>
          </div>
        </>
      )}
    </>
  );
}
