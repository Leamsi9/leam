import { SettingsActivityBoundary } from "./settings-lifecycle";
import { useEffect, useRef, useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import { api } from "./api";
import { rememberSession, sessionValue } from "./session-cache";
import "./usage.css";
import { UsageExhaustion } from "./usage-exhaustion";
import { UsageNativeJourney } from "./usage-native-journey";
import { UsageRuntimeAttempts } from "./usage-runtime-attempts";

type Totals = {
  requests: number;
  input: string;
  output: string;
  total: string;
  cached: string | null;
  reasoning: string | null;
  nonCached: string | null;
  cacheKnownRequests: number;
};
type UsageSettingsValue = {
  enabled: boolean;
  dailyWarningTokens: number;
  largeCallTokens: number;
};
type UsageSnapshot = {
  totals: Totals;
  daily: (Totals & { day: string })[];
  groups: (Totals & {
    kind: "model" | "operation" | "goal" | "thread";
    key: string;
  })[];
  coverage: {
    sources: {
      source: string;
      status: string;
      lastSuccess: number | null;
      details: string;
    }[];
    conflicts: number;
    unassignedRequests: number;
  };
  opportunities: {
    id: string;
    title: string;
    details: string;
    severity: "info" | "warning";
  }[];
  filters: {
    models: string[];
    operations: string[];
    goals: { id: string; name: string }[];
  };
  settings: UsageSettingsValue;
  warning: { exceeded: boolean; observed: string; limit: number };
  generatedAt: number;
};
type UsageRecord = {
  id: string;
  source: string;
  threadId: string;
  turnId: string | null;
  model: string;
  operation: string;
  goalId: string | null;
  timestamp: number;
  input: string;
  output: string;
  total: string;
  cached: string | null;
  reasoning: string | null;
};
type RecordPage = { items: UsageRecord[]; total: number };
type Filters = { days: number; model: string; operation: string; goal: string };
type View =
  | "overview"
  | "calls"
  | "journey"
  | "runtime"
  | "goals"
  | "opportunities"
  | "controls"
  | "data";
const pageSize = 30;
const views: { key: View; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "calls", label: "Calls" },
  { key: "journey", label: "Session journey" },
  { key: "runtime", label: "IronClaw attempts" },
  { key: "goals", label: "Goals" },
  { key: "opportunities", label: "Opportunities" },
  { key: "controls", label: "Controls" },
  { key: "data", label: "Data" },
];
function initialFilters(): Filters {
  const value = sessionValue<Partial<Filters> | null>("usage:filters", null);
  return {
    days: [0, 7, 30].includes(value?.days ?? -1) ? value!.days! : 7,
    model: typeof value?.model === "string" ? value.model : "",
    operation: typeof value?.operation === "string" ? value.operation : "",
    goal: typeof value?.goal === "string" ? value.goal : "",
  };
}
function tokens(value: string | null | undefined): string {
  if (value === null || value === undefined) return "Unknown";
  try {
    return BigInt(value).toLocaleString();
  } catch {
    return "Unknown";
  }
}
function observedAt(value: number | null | undefined): string {
  return value ? new Date(value * 1000).toLocaleString() : "Not yet observed";
}
function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Usage could not be loaded. Try again.";
}
function query(filters: Filters): string {
  const params = new URLSearchParams({ days: String(filters.days) });
  for (const key of ["model", "operation", "goal"] as const)
    if (filters[key]) params.set(key, filters[key]);
  return params.toString();
}

/** One app instance retains drafts and in-flight receipts across navigation. */
export function UsageSettings() {
  return <section className="settings-section"><h3>Usage &amp; efficiency</h3><p>Usage is now a separate app in More.</p><button className="secondary" onClick={() => window.dispatchEvent(new CustomEvent("leam:navigate", { detail: "usage" }))}>Open Usage</button></section>;
}
export function UsageApp({ active }: { active: boolean }) {
  const [visited, setVisited] = useState(active);
  useEffect(() => { if (active) setVisited(true); }, [active]);
  if (!active && !visited) return null;
  return <SettingsActivityBoundary active={active}><section className="page" hidden={!active} aria-label="Usage app"><UsageDashboard /></section></SettingsActivityBoundary>;
}

function UsageDashboard() {
  const [filters, setFilters] = useState(initialFilters);
  const [view, setView] = useState<View>("overview");
  const [journeyThread, setJourneyThread] = useState("");
  const [runtimeThread, setRuntimeThread] = useState("");
  const [snapshot, setSnapshot] = useState<UsageSnapshot | null>(null);
  const [records, setRecords] = useState<RecordPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [recordsLoading, setRecordsLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [recordsError, setRecordsError] = useState("");
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState("");
  const [goalName, setGoalName] = useState("");
  const [threadId, setThreadId] = useState("");
  const [includeDescendants, setIncludeDescendants] = useState(false);
  const mounted = useRef(true);
  const mutationBusy = useRef(false);
  const key = query(filters);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setLoading(true);
    setLoadError("");
    setSnapshot(null);
    void api(`/usage?${key}`, "GET", undefined, controller.signal)
      .then((value) => {
        if (current) setSnapshot(value as unknown as UsageSnapshot);
      })
      .catch((error) => {
        if (current) setLoadError(message(error));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [key, revision]);
  useEffect(() => {
    if (view !== "calls") return;
    const controller = new AbortController();
    let current = true;
    setRecordsLoading(true);
    setRecordsError("");
    setRecords(null);
    void api(
      `/usage/records?${key}&offset=${offset}&limit=${pageSize}`,
      "GET",
      undefined,
      controller.signal,
    )
      .then((value) => {
        if (current) setRecords(value as unknown as RecordPage);
      })
      .catch((error) => {
        if (current) setRecordsError(message(error));
      })
      .finally(() => {
        if (current) setRecordsLoading(false);
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [key, offset, revision, view]);

  function filter(changes: Partial<Filters>) {
    const next = { ...filters, ...changes };
    setFilters(next);
    rememberSession("usage:filters", next);
    setOffset(0);
    setNotice("");
  }
  async function action(name: string, run: () => Promise<void>) {
    if (mutationBusy.current) return;
    mutationBusy.current = true;
    setBusy(name);
    setNotice("");
    setActionError("");
    try {
      await run();
    } catch (error) {
      if (mounted.current) setActionError(message(error));
    } finally {
      mutationBusy.current = false;
      if (mounted.current) setBusy("");
    }
  }
  const available = snapshot?.filters;
  const totals = snapshot?.totals;
  const goalLabel = (id: string | null) =>
    id && id !== "unassigned"
      ? available?.goals.find((goal) => goal.id === id)?.name || id
      : "Unassigned";
  const rangeLabel = filters.days
    ? `Last ${filters.days} days`
    : "All recorded history";

  return (
    <section
      className="usage-dashboard card"
      aria-label="Usage and efficiency dashboard"
    >
      <header className="usage-heading">
        <div>
          <span className="eyebrow">OBSERVE, THEN IMPROVE</span>
          <h1>Usage &amp; efficiency</h1>
        </div>
        <button
          className="secondary"
          disabled={loading}
          onClick={() => setRevision((value) => value + 1)}
        >
          <RefreshCw size={16} aria-hidden="true" /> Refresh usage
        </button>
      </header>
      <p>
        Measured model responses, with their source and coverage. This dashboard
        uses no model calls.
      </p>
      <div className="usage-filters">
        <label>
          Usage range
          <select
            value={filters.days}
            onChange={(event) => filter({ days: Number(event.target.value) })}
          >
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={0}>All recorded history</option>
          </select>
        </label>
        <label>
          Usage model
          <select
            value={filters.model}
            onChange={(event) => filter({ model: event.target.value })}
          >
            <option value="">All models</option>
            {filters.model && !available?.models.includes(filters.model) && (
              <option value={filters.model}>{filters.model}</option>
            )}
            {available?.models.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Usage operation
          <select
            value={filters.operation}
            onChange={(event) => filter({ operation: event.target.value })}
          >
            <option value="">All operations</option>
            {filters.operation &&
              !available?.operations.includes(filters.operation) && (
                <option value={filters.operation}>{filters.operation}</option>
              )}
            {available?.operations.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Usage goal
          <select
            value={filters.goal}
            onChange={(event) => filter({ goal: event.target.value })}
          >
            <option value="">All goals</option>
            {filters.goal &&
              !available?.goals.some((value) => value.id === filters.goal) && (
                <option value={filters.goal}>{filters.goal}</option>
              )}
            {available?.goals.map((value) => (
              <option key={value.id} value={value.id}>
                {value.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="usage-views" role="group" aria-label="Usage views">
        {views.map((item) => (
          <button
            key={item.key}
            className="secondary"
            aria-pressed={view === item.key}
            onClick={() => setView(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>
      {loading && <p role="status">Loading recorded usage…</p>}
      {loadError && (
        <p role="alert" className="usage-error">
          {loadError}
        </p>
      )}
      {actionError && (
        <p role="alert" className="usage-error">
          {actionError}
        </p>
      )}
      {notice && (
        <p role="status" className="usage-notice">
          {notice}
        </p>
      )}
      {snapshot?.warning.exceeded && (
        <p role="status" className="usage-warning">
          Daily installation warning threshold reached:{" "}
          {tokens(snapshot.warning.observed)} observed tokens against{" "}
          {snapshot.warning.limit.toLocaleString()}. This warning does not stop
          model calls.
        </p>
      )}
      {snapshot && totals && (
        <>
          {view === "overview" && (
            <>
              <UsageExhaustion revision={revision} />
              <div className="usage-metrics">
                <Metric
                  label="Gross tokens"
                  value={tokens(totals.total)}
                  note="Input + output, counted once"
                />
                <Metric
                  label="Measured responses"
                  value={totals.requests.toLocaleString()}
                  note={rangeLabel}
                />
                <Metric
                  label="Cache-read tokens"
                  value={tokens(totals.cached)}
                  note={`Input subset · known for ${totals.cacheKnownRequests.toLocaleString()} of ${totals.requests.toLocaleString()} responses`}
                />
                <Metric
                  label="Non-cache input + output"
                  value={tokens(totals.nonCached)}
                  note="A consumption proxy, not subscription quota"
                />
              </div>
              <dl className="usage-breakdown">
                <div>
                  <dt>Input tokens</dt>
                  <dd>{tokens(totals.input)}</dd>
                </div>
                <div>
                  <dt>Output tokens</dt>
                  <dd>{tokens(totals.output)}</dd>
                </div>
                <div>
                  <dt>Reasoning tokens · output subset</dt>
                  <dd>{tokens(totals.reasoning)}</dd>
                </div>
              </dl>
              {!totals.requests && (
                <p className="usage-empty">
                  No measured responses match these filters. Check source
                  coverage or import recent metadata in Data.
                </p>
              )}
              <h3>Daily usage</h3>
              <p className="usage-caption">
                UTC days. Cache reads are already included in input; reasoning
                is already included in output.
              </p>
              <UsageTable
                label="Daily token usage"
                rows={snapshot.daily.map((row) => ({ ...row, label: row.day }))}
                first="UTC day"
              />
              <h3>By operation</h3>
              <UsageTable
                label="Token usage by operation"
                rows={snapshot.groups
                  .filter((group) => group.kind === "operation")
                  .map((row) => ({ ...row, label: row.key || "Unknown" }))}
                first="Operation"
              />
              <Coverage snapshot={snapshot} />
            </>
          )}
          {view === "calls" && (
            <>
              <h3>Measured responses</h3>
              <p className="usage-caption">
                Individual reported responses. These are not conversation turns
                or a complete account invoice.
              </p>
              {recordsLoading && <p role="status">Loading response records…</p>}
              {recordsError && (
                <p role="alert" className="usage-error">
                  {recordsError}
                </p>
              )}
              {!recordsLoading && records?.items.length === 0 && (
                <p className="usage-empty">
                  No response records match these filters.
                </p>
              )}
              <div className="usage-records">
                {records?.items.map((record) => (
                  <article className="usage-record" key={record.id}>
                    <header>
                      <div>
                        <strong>{record.model || "Unknown model"}</strong>
                        <small>
                          {record.operation || "Unknown operation"} ·{" "}
                          {observedAt(record.timestamp)}
                        </small>
                      </div>
                      <strong>
                        {tokens(record.total)}
                        <small>gross tokens</small>
                      </strong>
                    </header>
                    <dl className="usage-breakdown">
                      <div>
                        <dt>Input</dt>
                        <dd>{tokens(record.input)}</dd>
                      </div>
                      <div>
                        <dt>Output</dt>
                        <dd>{tokens(record.output)}</dd>
                      </div>
                      <div>
                        <dt>Cache read · input subset</dt>
                        <dd>{tokens(record.cached)}</dd>
                      </div>
                    </dl>
                    <p className="usage-caption">
                      Goal: {goalLabel(record.goalId)}
                    </p>
                    <button
                      className="secondary"
                      disabled={!record.threadId}
                      onClick={() => {
                        if (record.source === "ironclaw.model_attempt") {
                          setRuntimeThread(record.threadId.replace(/^ironclaw:/, ""));
                          setView("runtime");
                        } else {
                          setJourneyThread(record.threadId);
                          setView("journey");
                        }
                      }}
                    >
                      Trace this session
                    </button>
                    <details>
                      <summary>Response details</summary>
                      <dl className="usage-evidence">
                        <dt>Source</dt>
                        <dd>{record.source}</dd>
                        <dt>Response ID</dt>
                        <dd>{record.id}</dd>
                        <dt>Thread ID</dt>
                        <dd>{record.threadId || "Unknown"}</dd>
                        <dt>Turn ID</dt>
                        <dd>{record.turnId || "Unknown"}</dd>
                        <dt>Reasoning · output subset</dt>
                        <dd>{tokens(record.reasoning)}</dd>
                      </dl>
                    </details>
                    <button
                      className="secondary"
                      disabled={!record.threadId}
                      onClick={() => {
                        setThreadId(record.threadId);
                        setGoalName("");
                        setIncludeDescendants(false);
                        setView("goals");
                      }}
                    >
                      Associate this thread with a goal
                    </button>
                  </article>
                ))}
              </div>
              <div className="usage-pagination">
                <button
                  className="secondary"
                  disabled={recordsLoading || offset === 0}
                  onClick={() =>
                    setOffset((value) => Math.max(0, value - pageSize))
                  }
                >
                  Previous responses
                </button>
                <span role="status">
                  {records
                    ? `${records.total ? offset + 1 : 0}–${Math.min(offset + pageSize, records.total)} of ${records.total.toLocaleString()}`
                    : "Response page"}
                </span>
                <button
                  className="secondary"
                  disabled={
                    recordsLoading ||
                    !records ||
                    offset + pageSize >= records.total
                  }
                  onClick={() => setOffset((value) => value + pageSize)}
                >
                  Next responses
                </button>
              </div>
            </>
          )}
          {view === "runtime" && <>
            {runtimeThread && <button className="secondary" onClick={() => setRuntimeThread("")}>Show all runtime conversations</button>}
            <UsageRuntimeAttempts key={runtimeThread} threadId={runtimeThread} />
          </>}
          {view === "journey" && (
            <UsageNativeJourney
              selected={journeyThread}
              onSelect={setJourneyThread}
              threadIds={snapshot.groups
                .filter((group) => group.kind === "thread" && !group.key.startsWith("ironclaw:"))
                .map((group) => group.key)}
            />
          )}
          {view === "goals" && (
            <>
              <h3>Goal associations</h3>
              <p>
                Associate a measured thread with a goal. Names alone never
                assign usage; manual associations do not duplicate recorded
                tokens.
              </p>
              <UsageTable
                label="Token usage by goal"
                rows={snapshot.groups
                  .filter((group) => group.kind === "goal")
                  .map((row) => ({ ...row, label: goalLabel(row.key) }))}
                first="Goal"
              />
              <p className="usage-caption">
                {snapshot.coverage.unassignedRequests.toLocaleString()} measured
                responses have no goal association.
              </p>
              <form
                className="usage-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void action("goal", async () => {
                    await api("/usage/goals", "POST", {
                      name: goalName.trim(),
                      threadId: threadId.trim(),
                      includeDescendants,
                    });
                    if (!mounted.current) return;
                    setNotice(
                      "Goal association saved. Recorded tokens are counted once.",
                    );
                    setRevision((value) => value + 1);
                  });
                }}
              >
                <label>
                  New goal name
                  <input
                    value={goalName}
                    onChange={(event) => setGoalName(event.target.value)}
                    required
                    maxLength={100}
                  />
                </label>
                <label>
                  Coding thread ID
                  <input
                    value={threadId}
                    onChange={(event) => setThreadId(event.target.value)}
                    required
                    maxLength={128}
                    list="usage-thread-ids"
                    autoComplete="off"
                  />
                </label>
                <datalist id="usage-thread-ids">
                  {snapshot.groups
                    .filter((group) => group.kind === "thread")
                    .map((group) => (
                      <option key={group.key} value={group.key} />
                    ))}
                </datalist>
                <label className="usage-checkbox">
                  <input
                    type="checkbox"
                    checked={includeDescendants}
                    onChange={(event) =>
                      setIncludeDescendants(event.target.checked)
                    }
                  />{" "}
                  Include known and future descendant threads
                </label>
                <button
                  className="primary"
                  disabled={!!busy || !goalName.trim() || !threadId.trim()}
                >
                  {busy === "goal"
                    ? "Saving association…"
                    : "Create goal and associate"}
                </button>
              </form>
            </>
          )}
          {view === "opportunities" && (
            <>
              <h3>Efficiency opportunities</h3>
              <p>
                Rules applied to observed metadata. Findings are prompts to
                investigate; they do not prove waste or change your model
                settings.
              </p>
              {snapshot.opportunities.length ? (
                <div className="usage-opportunities">
                  {snapshot.opportunities.map((item) => (
                    <article
                      key={item.id}
                      className={
                        item.severity === "warning"
                          ? "usage-warning"
                          : "usage-notice"
                      }
                    >
                      <h4>{item.title}</h4>
                      <p>{item.details}</p>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="usage-empty">
                  No opportunities found for this selection. That does not
                  establish complete or optimal usage.
                </p>
              )}
              <p className="usage-caption">
                Generated {observedAt(snapshot.generatedAt)} · no inference or
                automatic optimization.
              </p>
            </>
          )}
          {view === "controls" && (
            <UsageControls
              key={JSON.stringify(snapshot.settings)}
              initial={snapshot.settings}
              disabled={!!busy}
              saving={busy === "settings"}
              save={(value) =>
                void action("settings", async () => {
                  await api("/usage/settings", "PUT", value);
                  if (!mounted.current) return;
                  setNotice(
                    "Usage controls saved. Warning thresholds are advisory.",
                  );
                  setRevision((current) => current + 1);
                })
              }
            />
          )}
          {view === "data" && (
            <>
              <Coverage snapshot={snapshot} />
              <h3>Import and export</h3>
              <p>
                Import copies usage metadata from supported local records. It
                does not send messages or invoke a model. Pausing collection
                keeps previously recorded analytics available.
              </p>
              <div className="usage-actions">
                <button
                  className="secondary"
                  disabled={!!busy || !snapshot.settings.enabled}
                  onClick={() =>
                    void action("import", async () => {
                      await api("/usage/refresh", "POST", {});
                      if (mounted.current)
                        setNotice(
                          "Metadata import scheduled. Use Refresh usage shortly to see newly recorded responses.",
                        );
                    })
                  }
                >
                  {busy === "import" ? "Scheduling…" : "Import recent metadata"}
                </button>
                <button
                  className="secondary"
                  disabled={!!busy}
                  onClick={() =>
                    void action("export", async () => {
                      const data = await api(`/usage/export?${key}`);
                      if (!mounted.current) return;
                      const url = URL.createObjectURL(
                        new Blob([JSON.stringify(data, null, 2)], {
                          type: "application/json",
                        }),
                      );
                      const link = document.createElement("a");
                      link.href = url;
                      link.download = `leam-usage-${new Date().toISOString().slice(0, 10)}.json`;
                      document.body.appendChild(link);
                      link.click();
                      link.remove();
                      setTimeout(() => URL.revokeObjectURL(url), 1000);
                      setNotice(
                        data.truncated
                          ? "Export downloaded: the 10,000-record limit was reached. Narrow the filters for a smaller export."
                          : "Usage metadata exported for the selected filters.",
                      );
                    })
                  }
                >
                  <Download size={16} aria-hidden="true" />
                  {busy === "export" ? "Exporting…" : "Export usage JSON"}
                </button>
              </div>
              <small>
                Exports contain token quantities, model and operation names,
                identifiers and goal associations. No prompts, reasoning text,
                audio or private source paths. At most 10,000 response records
                per export.
              </small>
            </>
          )}
          <footer className="usage-footer">
            Coverage is partial where sources are unavailable. API prices and
            subscription allowances are unknown; token counts are not a bill or
            a quota balance. Updated {observedAt(snapshot.generatedAt)}.
          </footer>
        </>
      )}
    </section>
  );
}

function Metric({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note: string;
}) {
  return (
    <div className="usage-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  );
}
function UsageTable({
  label,
  rows,
  first,
}: {
  label: string;
  rows: (Totals & { label: string })[];
  first: string;
}) {
  return rows.length ? (
    <table className="usage-table">
      <caption>{label}</caption>
      <thead>
        <tr>
          <th scope="col">{first}</th>
          <th scope="col">Responses</th>
          <th scope="col">Input</th>
          <th scope="col">Output</th>
          <th scope="col">Gross tokens</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.label}>
            <th scope="row">{row.label}</th>
            <td data-label="Responses">{row.requests.toLocaleString()}</td>
            <td data-label="Input">{tokens(row.input)}</td>
            <td data-label="Output">{tokens(row.output)}</td>
            <td data-label="Gross tokens">{tokens(row.total)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  ) : (
    <p className="usage-empty">No recorded data for this selection.</p>
  );
}
function Coverage({ snapshot }: { snapshot: UsageSnapshot }) {
  return (
    <section className="usage-coverage" aria-label="Usage coverage">
      <h3>Source coverage</h3>
      {snapshot.coverage.sources.map((source) => (
        <article key={source.source}>
          <header>
            <strong>{source.source}</strong>
            <span>{source.status}</span>
          </header>
          <p>{source.details}</p>
          <small>
            Last successful import: {observedAt(source.lastSuccess)}
          </small>
        </article>
      ))}
      {snapshot.coverage.conflicts > 0 && (
        <p className="usage-warning">
          {snapshot.coverage.conflicts.toLocaleString()} conflicting usage
          observations need reconciliation. Do not treat coverage as complete.
        </p>
      )}
    </section>
  );
}
function UsageControls({
  initial,
  disabled,
  saving,
  save,
}: {
  initial: UsageSettingsValue;
  disabled: boolean;
  saving: boolean;
  save: (value: UsageSettingsValue) => void;
}) {
  const [enabled, setEnabled] = useState(initial.enabled);
  const [daily, setDaily] = useState(String(initial.dailyWarningTokens));
  const [large, setLarge] = useState(String(initial.largeCallTokens));
  const dailyValue = Number(daily),
    largeValue = Number(large);
  const valid =
    daily.trim() !== "" &&
    large.trim() !== "" &&
    Number.isSafeInteger(dailyValue) &&
    dailyValue >= 0 &&
    dailyValue <= 1_000_000_000_000 &&
    Number.isSafeInteger(largeValue) &&
    largeValue >= 1000 &&
    largeValue <= 1_000_000_000_000;
  return (
    <form
      className="usage-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid)
          save({
            enabled,
            dailyWarningTokens: dailyValue,
            largeCallTokens: largeValue,
          });
      }}
    >
      <h3>Collection and advisory warnings</h3>
      <label className="usage-checkbox">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(event) => setEnabled(event.target.checked)}
        />
        Collect usage metadata
      </label>
      <small>
        Pausing collection stops background imports. Existing recorded usage
        remains readable.
      </small>
      <label>
        Daily warning threshold · gross tokens
        <input
          type="number"
          min={0}
          max={1_000_000_000_000}
          step={1}
          value={daily}
          onChange={(event) => setDaily(event.target.value)}
          required
        />
      </label>
      <small>
        Installation-wide, per UTC day. Use 0 to disable the daily warning. This
        does not reserve tokens or stop calls.
      </small>
      <label>
        Large response threshold · gross tokens
        <input
          type="number"
          min={1000}
          max={1_000_000_000_000}
          step={1}
          value={large}
          onChange={(event) => setLarge(event.target.value)}
          required
        />
      </label>
      <small>
        Responses at this threshold can appear in Opportunities. Input and
        output both count.
      </small>
      <button className="primary" disabled={disabled || !valid}>
        {saving ? "Saving controls…" : "Save usage controls"}
      </button>
    </form>
  );
}
