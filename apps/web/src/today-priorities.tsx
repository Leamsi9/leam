import { useEffect, useRef, useState } from "react";
import { ListFilter } from "lucide-react";
import { api, type Data } from "./api";

export function useTodayPriorities(
  date: string,
  timezone: string,
  capacities: Data[],
) {
  const [record, setRecord] = useState<Data | null>(null);
  const [criteria, setCriteria] = useState({
    order: "urgent",
    owner: "all",
    capacityId: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [excludedKeys, setExcludedKeys] = useState<string[]>([]);
  const [excluding, setExcluding] = useState("");
  const scope = date + ":" + timezone;
  const current = useRef(scope);
  current.current = scope;
  const pending = useRef<Data | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    let active = true;
    const ticket = ++generation.current;
    setRecord(null);
    setExcludedKeys([]);
    setExcluding("");
    setError("");
    setBusy(false);
    pending.current = null;
    setCriteria({ order: "urgent", owner: "all", capacityId: "" });
    void api(
      `/agenda/priorities?date=${encodeURIComponent(date)}&timezone=${encodeURIComponent(timezone)}`,
    )
      .then((value) => {
        if (!active || ticket !== generation.current) return;
        setRecord(value.check || null);
        setExcludedKeys(value.excludedKeys || []);
        if (value.check?.request) {
          const r = value.check.request;
          setCriteria({
            order: r.order,
            owner: r.owner,
            capacityId: r.capacityId || "",
          });
          if (value.check.state !== "completed") pending.current = r;
        }
      })
      .catch((e) => {
        if (active && ticket === generation.current) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [date, timezone]);
  async function refresh() {
    const captured = scope;
    const ticket = generation.current;
    try {
      const value = await api(
        `/agenda/priorities?date=${encodeURIComponent(date)}&timezone=${encodeURIComponent(timezone)}`,
      );
      if (current.current !== captured || ticket !== generation.current) return;
      if (
        busy &&
        pending.current &&
        value.check?.requestId !== pending.current.requestId
      )
        return;
      setRecord(value.check || null);
      setExcludedKeys(value.excludedKeys || []);
      if (value.check?.state === "completed") {
        pending.current = null;
        setError("");
      }
    } catch (e) {
      if (current.current === captured) setError((e as Error).message);
    }
  }
  useEffect(() => {
    if (!busy && record?.state !== "pending") return;
    const timer = setInterval(() => {
      if (!document.hidden) void refresh();
    }, 1500);
    return () => clearInterval(timer);
  }, [scope, busy, record?.state]);
  async function exclude(key: string, excluded = true) {
    if (excluding) return;
    const captured = scope;
    const ticket = generation.current;
    setExcluding(key);
    setError("");
    try {
      const result = await api("/agenda/priorities/exclusions", "PUT", {
        date,
        timezone,
        key,
        excluded,
      });
      if (current.current !== captured || ticket !== generation.current) return;
      setExcludedKeys(result.excludedKeys || []);
      if (result.check) setRecord(result.check);
    } catch (e) {
      if (current.current === captured && ticket === generation.current) setError((e as Error).message);
    } finally {
      if (current.current === captured && ticket === generation.current) setExcluding("");
    }
  }
  async function run(retry = false) {
    if (busy) return;
    const captured = scope;
    ++generation.current;
    const request =
      retry && pending.current
        ? pending.current
        : {
            requestId: crypto.randomUUID(),
            date,
            timezone,
            ...criteria,
            capacityId: criteria.capacityId || null,
          };
    pending.current = request;
    setBusy(true);
    setRecord(null);
    setError("");
    try {
      const value = await api("/agenda/priorities", "POST", request);
      if (current.current !== captured) return;
      setRecord(value);
      setExcludedKeys(value.excludedKeys || []);
      if (value.state === "completed") pending.current = null;
    } catch (e) {
      if (current.current === captured)
        setError(
          (e as Error).message +
            " Check may have finished; retry retrieves its receipt.",
        );
    } finally {
      if (current.current === captured) setBusy(false);
    }
  }
  const state =
    error || record?.state === "failed"
      ? "failed"
      : record?.state === "pending"
        ? "pending"
        : busy
          ? "sending"
          : record?.state === "completed"
            ? "completed"
            : "ready";
  const controls = (
    <div className="today-priority-controls">
      <button type="button" disabled={busy} onClick={() => void run()}>
        <ListFilter size={16} aria-hidden="true" />{" "}
        {busy ? "Checking saved priorities…" : "Triage priorities"}
      </button>
      <div
        className="today-triage-status"
        data-state={state}
        role="status"
        aria-label="Priority triage status"
      >
        <strong>
          {
            {
              ready: "Ready to triage",
              sending: "Sending priority check",
              pending: "Check pending",
              completed: "Triage completed",
              failed: "Check needs attention",
            }[state]
          }
        </strong>
        <small>
          {date} · {timezone}
        </small>
      </div>
      <details>
        <summary>Custom priorities</summary>
        <label>
          Prioritise
          <select
            value={criteria.order}
            disabled={busy}
            onChange={(e) =>
              setCriteria({ ...criteria, order: e.target.value })
            }
          >
            <option value="urgent">Urgent, important, then short</option>
            <option value="important">Important, urgent, then short</option>
            <option value="short">Recorded short durations first</option>
          </select>
        </label>
        <label>
          Ownership
          <select
            value={criteria.owner}
            disabled={busy}
            onChange={(e) =>
              setCriteria({ ...criteria, owner: e.target.value })
            }
          >
            <option value="all">Everyone, including unassigned</option>
            <option value="user">Assigned to me</option>
            <option value="leam">Assigned to Leam</option>
          </select>
        </label>
        <label>
          Capacity
          <select
            value={criteria.capacityId}
            disabled={busy}
            onChange={(e) =>
              setCriteria({ ...criteria, capacityId: e.target.value })
            }
          >
            <option value="">All capacities and other sources</option>
            {capacities.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <p>
          Apply with Triage priorities. Unknown task sizes stay unknown; minutes
          targets are recorded durations, not effort estimates. For nuanced
          priorities, discuss them in Today Chat.
        </p>
      </details>
      {excludedKeys.length > 0 && (
        <details>
          <summary>{excludedKeys.length} excluded for this day</summary>
          <p>
            These stay out when you rerun. They are eligible again on another
            day.
          </p>
          {excludedKeys.map((key, index) => (
            <button
              key={key}
              type="button"
              disabled={!!excluding}
              onClick={() => void exclude(key, false)}
            >
              Restore excluded suggestion {index + 1}
            </button>
          ))}
        </details>
      )}
      {busy ? (
        <p role="status">
          Reading canonical pending and in-progress tasks. Your Focus
          stays unchanged.
        </p>
      ) : record?.state === "completed" ? (
        <p role="status">
          Check complete ·{" "}
          {new Date(record.completedAt * 1000).toLocaleString()} · saved data
          only{record.partial ? " · partial coverage" : ""}.
          {record.coverage &&
            ` Checked ${record.coverage.canonicalTasks} canonical pending/in-progress tasks; ${record.coverage.alreadyFocused} already in Focus; ${record.coverage.excludedForDay} excluded for this day.`}
          {record.sources &&
            ` Task source: local canonical records.`}
        </p>
      ) : record?.state === "pending" ? (
        <p role="status">
          A saved check has no completion receipt. Retry to finish it.
        </p>
      ) : null}
      {record?.state === "pending" && (
        <button type="button" onClick={() => void refresh()}>
          Refresh check status
        </button>
      )}
      {(error || record?.state === "failed") && (
        <p role="alert">{error || record?.error}</p>
      )}
      {!busy &&
        (pending.current ||
          record?.state === "failed" ||
          record?.state === "pending") && (
          <button type="button" onClick={() => void run(true)}>
            Retry priority check
          </button>
        )}
    </div>
  );
  return { record, controls, excludedKeys, exclude, excluding };
}
