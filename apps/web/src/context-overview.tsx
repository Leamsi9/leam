import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";
const labels: Record<string, string> = {
  memory: "Memory",
  commitment: "Commitments",
  capacity: "Capacities",
  calendar: "Calendar snapshots",
  routine: "Time routines",
  event_rule: "Event rules",
  notification: "Waiting notifications",
  project: "Leam build",
};
function when(value: number | null) {
  return value ? new Date(value * 1000).toLocaleString() : "No saved timestamp";
}
export function ContextOverview({ initiallyOpen = false }: { initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(false),
    [data, setData] = useState<Data | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  const active = useRef(true),
    request = useRef(0);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      request.current++;
    };
  }, []);
  async function load() {
    const id = ++request.current;
    setLoading(true);
    setError("");
    try {
      const next = await api("/companion/overview");
      if (active.current && id === request.current) setData(next);
    } catch (e) {
      if (active.current && id === request.current)
        setError(e instanceof Error ? e.message : "Overview unavailable");
    } finally {
      if (active.current && id === request.current) setLoading(false);
    }
  }
  const project = data?.project;
  return (
    <details
      className="card context-overview settings-section"
      open={initiallyOpen ? true : undefined}
      onToggle={(e) => {
        const next = e.currentTarget.open;
        setOpen(next);
        if (next) void load();
      }}
    >
      <summary>Across Leam</summary>
      {open && (
        <div>
          <p>
            <small>
              Saved module state, with sources and timestamps. This does not
              confirm a live provider sync or scheduler health.
            </small>
          </p>
          <button
            className="secondary"
            disabled={loading}
            onClick={() => void load()}
          >
            {loading ? "Checking…" : "Refresh overview"}
          </button>
          {error && (
            <p role="alert">
              {error}
              {data ? " Previous snapshot remains below." : ""}
            </p>
          )}
          {data && (
            <>
              <p>
                <small>Observed {when(data.observedAt)}</small>
              </p>
              {data.truncated && (
                <p>
                  Some sources exceed the overview scan limit. Open their module
                  for the full list.
                </p>
              )}
              <ul style={{ paddingLeft: 20 }}>
                {(data.modules || []).map((m: Data) => (
                  <li
                    key={m.kind}
                    style={{ marginBottom: 10, overflowWrap: "anywhere" }}
                  >
                    <strong>{labels[m.kind] || m.kind}</strong> ·{" "}
                    {m.count === null ? "Unknown" : m.count} · {m.availability}
                    <small style={{ display: "block" }}>
                      Source: {m.source} · Data as of {when(m.dataAsOf)}
                      {m.truncated ? " · Partial scan" : ""}
                    </small>
                  </li>
                ))}
              </ul>
              {project ? (
                <section aria-label="Leam build summary">
                  <h3>Leam build</h3>
                  {project.countsTruncated && (
                    <p role="status">
                      Acceptance and progress counts are partial: at most 500
                      deployment and backlog records were inspected.
                    </p>
                  )}
                  <p>
                    {project.deployedFeatures} deployed ·{" "}
                    {project.userAcceptedFeatures} passed your acceptance ·{" "}
                    {project.unfinishedFeatures} unfinished
                  </p>
                  <p>
                    {project.awaitingUatFeatures} awaiting UAT ·{" "}
                    {project.failedQaFeatures} failed QA ·{" "}
                    {project.failedUatFeatures} failed UAT
                  </p>
                  {project.staleAssessments > 0 && (
                    <p role="status">
                      {project.staleAssessments} progress assessments are stale.
                      Percentages are last recorded estimates toward deployment.
                    </p>
                  )}
                  {(project.workItems || []).map((w: Data) => (
                    <p key={w.feature}>
                      <strong>{w.title}</strong> · estimated {w.percent}% toward
                      deployment{w.stale ? " · stale" : ""}
                      <br />
                      <small>{w.currentStep}</small>
                    </p>
                  ))}
                  <p>
                    <small>
                      Sources: Updates and Backlog. QA is separate from your
                      acceptance. No overall completion percentage is inferred.
                    </small>
                  </p>
                  <div className="actions">
                    <a href="/?view=updates">Open Updates</a>
                    <a href="/?view=backlog">Open Backlog</a>
                  </div>
                </section>
              ) : (
                <p>No recorded Leam build summary is available yet.</p>
              )}
            </>
          )}
        </div>
      )}
    </details>
  );
}
