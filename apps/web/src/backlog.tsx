import { useEffect, useState } from "react";
import { api, type Data } from "./api";

type Props = { fail: (error: unknown) => void };
export function BacklogPanel({ fail }: Props) {
  const [data, setData] = useState<Data | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState(false);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const ticker = setInterval(() => setNow(Date.now()), 10000);
    async function poll() {
      try {
        const result = await api("/backlog");
        if (!stopped) {
          setData(result);
          setError(false);
        }
      } catch {
        if (!stopped) setError(true);
      }
      if (!stopped) timer = setTimeout(poll, 15000);
    }
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
      clearInterval(ticker);
    };
  }, []);
  return (
    <section aria-label="Build backlog">
      <h2>Backlog</h2>
      <p>Unfinished work, closest to deployment first.</p>
      <details>
        <summary>About these estimates</summary>
        <p>
          Percentages estimate progress to deployment, not QA or UAT acceptance.
        </p>
        <p>
          Assessments follow actual build progress at each 15-minute heartbeat.
          Time passing does not increase a percentage.
        </p>
      </details>
      {error && (
        <p role="alert">
          Could not refresh backlog; showing the last available assessment.
        </p>
      )}
      <button
        className="secondary"
        onClick={async () => {
          try {
            const result = await api("/backlog");
            setData(result);
            setError(false);
          } catch (e) {
            setError(true);
            fail(e);
          }
        }}
      >
        Refresh backlog
      </button>
      {data && !data.items.length && (
        <p>
          No unfinished work has been recorded. Deployed items appear in
          Updates.
        </p>
      )}
      {!data && !error && <p>Loading backlog…</p>}
      {data?.items.map((item: Data) => {
        const stale =
          item.stale ||
          now - Date.parse(item.assessedAt) > data.staleAfterSeconds * 1000;
        return (
          <article className="card" key={item.feature}>
            <h3 style={{ overflowWrap: "anywhere" }}>{item.title}</h3>
            <p>
              <strong>{item.percent}% estimated to deployment</strong>
            </p>
            <progress
              value={item.percent}
              max={100}
              aria-label={`${item.title}: ${item.percent}% estimated to deployment`}
              style={{ width: "100%" }}
            />
            <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {item.currentStep}
            </p>
            {item.blockers.length > 0 && (
              <>
                <strong>Blockers</strong>
                <ul>
                  {item.blockers.map((blocker: string, index: number) => (
                    <li key={index} style={{ overflowWrap: "anywhere" }}>
                      {blocker}
                    </li>
                  ))}
                </ul>
              </>
            )}
            <p role="status">
              {stale
                ? "Assessment stale — awaiting a new heartbeat"
                : "Assessment current"}
            </p>
            <small>
              Last assessed {new Date(item.assessedAt).toLocaleString()}
            </small>
          </article>
        );
      })}
    </section>
  );
}
