import { useEffect, useRef, useState } from "react";
import { api } from "./api";

type Exhaustion = {
  id: string;
  observedAt: number;
  timezone: string;
  source: "manual" | "user_report";
};
type Period = {
  eventId: string;
  start: number;
  end: number | null;
  timezone: string;
  totals: {
    requests: number;
    total: string;
    cached: string | null;
    nonCached: string | null;
  };
};
type History = {
  events: Exhaustion[];
  periods: Period[];
  current: Period | null;
  total: number;
  nextOffset: number | null;
};
function tokens(value: string | null) {
  if (value === null) return "Unknown";
  try {
    return BigInt(value).toLocaleString();
  } catch {
    return "Unknown";
  }
}
function stamp(value: number, zone: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "long",
    timeZone: zone,
  }).format(new Date(value * 1000));
}
function localNow() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 23);
}

export function UsageExhaustion({ revision }: { revision: number }) {
  const [data, setData] = useState<History | null>(null);
  const [observed, setObserved] = useState(localNow);
  const [attributed, setAttributed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [offset, setOffset] = useState(0);
  const request = useRef<{ signature: string; id: string } | null>(null);
  const submitting = useRef(false);
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const date = new Date(observed);
  const validDate = Number.isFinite(date.getTime());

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setError("");
    void api(
      `/usage/exhaustions?provider=chatgpt&offset=${offset}`,
      "GET",
      undefined,
      controller.signal,
    )
      .then((result) => {
        if (!Array.isArray(result.events) || !Array.isArray(result.periods))
          throw new Error(
            "Budget history could not be verified. Refresh to retry.",
          );
        if (active) setData(result as unknown as History);
      })
      .catch((reason) => {
        if (active)
          setError(
            reason instanceof Error
              ? reason.message
              : "Budget history could not load.",
          );
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [revision, refresh, offset]);

  async function record(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current || !attributed || !validDate) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    const payload = {
      provider: "chatgpt",
      observedAt: date.toISOString(),
      timezone: zone,
      source: "manual",
      attributeCodexToChatGPT: true,
    };
    const signature = JSON.stringify(payload);
    if (request.current?.signature !== signature)
      request.current = { signature, id: crypto.randomUUID() };
    try {
      await api("/usage/exhaustions", "POST", {
        ...payload,
        requestId: request.current.id,
      });
      setNotice(
        "Exhaustion report saved. Its timestamp starts a new usage counter.",
      );
      setOffset(0);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Report could not be saved. Retry to check the same event.",
      );
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return (
    <section className="usage-exhaustions" aria-label="ChatGPT budget history">
      <h3>ChatGPT subscription counters</h3>
      <p>
        Observed usage since each exhaustion report. Remaining subscription
        allowance and the provider’s reset time are unknown.
      </p>
      {error && (
        <p role="alert" className="usage-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="usage-notice">
          {notice}
        </p>
      )}
      {!data && !error && <p role="status">Loading budget history…</p>}
      {data?.current && (
        <div className="usage-metric">
          <span>Since latest exhaustion report</span>
          <strong>{tokens(data.current.totals.total)}</strong>
          <small>
            Observed tokens in {data.current.totals.requests.toLocaleString()}{" "}
            responses since {stamp(data.current.start, data.current.timezone)}
          </small>
          <small>
            Cache-read subset: {tokens(data.current.totals.cached)} · Non-cache
            input and output: {tokens(data.current.totals.nonCached)}
          </small>
        </div>
      )}
      {data && !data.current && (
        <p>No exhaustion reports yet. Record one to start a counter.</p>
      )}
      <details>
        <summary>Record budget exhaustion</summary>
        <form className="usage-form" onSubmit={record}>
          <p>Provider: ChatGPT</p>
          <label>
            Observed report time
            <input
              type="datetime-local"
              step="0.001"
              value={observed}
              onChange={(event) => setObserved(event.target.value)}
              required
            />
          </label>
          <p className="usage-caption">
            {zone}
            {validDate ? ` · ${date.toISOString()}` : " · Choose a valid time"}
          </p>
          <label className="usage-checkbox">
            <input
              type="checkbox"
              checked={attributed}
              onChange={(event) => setAttributed(event.target.checked)}
            />
            These collected Codex responses use my ChatGPT subscription.
          </label>
          <button disabled={busy || !attributed || !validDate}>
            {busy ? "Recording…" : "Record exhaustion"}
          </button>
        </form>
      </details>
      <p className="usage-caption">
        This counter uses your attribution of collected Codex responses across
        this installation. Overview filters do not limit it. Companion runtime
        calls are not included. Reports are manual; automatic subscription
        exhaustion detection is not connected.
      </p>
      {data && data.events.length > 0 && (
        <details>
          <summary>Exhaustion history ({data.total.toLocaleString()})</summary>
          <div className="usage-records">
            {data.periods.map((period) => (
              <article className="usage-record" key={period.eventId}>
                <strong>{stamp(period.start, period.timezone)}</strong>
                <p>
                  {tokens(period.totals.total)} observed tokens ·{" "}
                  {period.totals.requests.toLocaleString()} responses
                </p>
                <small>
                  {period.end
                    ? `Until ${stamp(period.end, period.timezone)}`
                    : "Current counter"}
                  {` · ${period.timezone} · User-reported observation`}
                </small>
              </article>
            ))}
          </div>
          <div className="usage-actions">
            <button
              className="secondary"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 50))}
            >
              Newer reports
            </button>
            <button
              className="secondary"
              disabled={data.nextOffset === null}
              onClick={() => setOffset(data.nextOffset ?? offset)}
            >
              Older reports
            </button>
          </div>
        </details>
      )}
    </section>
  );
}
