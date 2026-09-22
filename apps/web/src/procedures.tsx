import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";

export type ProcedureSelection = { id: string; version: number };

function useCatalog() {
  const [items, setItems] = useState<Data[]>([]);
  const [error, setError] = useState("");
  const active = useRef(true);
  async function refresh() {
    try {
      const result = await api("/procedures");
      if (active.current) {
        setItems(result.items);
        setError("");
      }
    } catch (e) {
      if (active.current) setError(String(e));
    }
  }
  useEffect(() => {
    active.current = true;
    void refresh();
    return () => {
      active.current = false;
    };
  }, []);
  return { items, setItems, error, setError, refresh, active };
}

export function ProcedurePicker({
  value,
  change,
  disabled,
}: {
  value: ProcedureSelection | null;
  change: (value: ProcedureSelection | null) => void;
  disabled: boolean;
}) {
  const { items, error } = useCatalog();
  const selected = items.find((item) => item.id === value?.id);
  return (
    <div className="settings-form">
      <label>
        Procedure for this message
        <select
          aria-label="Procedure for this message"
          disabled={disabled}
          value={value?.id || ""}
          onChange={(e) => {
            const item = items.find((item) => item.id === e.target.value);
            change(item ? { id: item.id, version: item.version } : null);
          }}
        >
          <option value="">Ordinary conversation</option>
          {items
            .filter((item) => item.adoption.state !== "retired")
            .map((item) => (
              <option key={item.id} value={item.id}>
                {item.title} · v{item.version} · {item.adoption.state}
              </option>
            ))}
        </select>
      </label>
      {selected && (
        <p role="status">
          Use once: {selected.summary} Recommendations only; sending does not
          adopt this procedure or change your records.
        </p>
      )}
      {error && <p role="alert">Procedures could not be loaded. {error}</p>}
    </div>
  );
}

export function ProcedureSettings() {
  const { items, setItems, error, setError, refresh, active } = useCatalog();
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [sourceDrafts, setSourceDrafts] = useState<Record<string, string>>({});
  async function save(item: Data, state: string) {
    if (busy) return;
    setBusy(item.id);
    setError("");
    setNotice("");
    try {
      const result = await api(`/procedures/${item.id}`, "PUT", {
        version: item.version,
        revision: item.adoption.revision,
        state,
        sourceRefs:
          sourceDrafts[item.id] === undefined
            ? item.adoption.sourceRefs
            : JSON.parse(sourceDrafts[item.id]),
      });
      if (active.current) {
        setItems((current) =>
          current.map((row) => (row.id === result.id ? result : row)),
        );
        setNotice(
          `${result.title}: ${result.adoption.state}. Invocation remains explicit.`,
        );
      }
    } catch (e) {
      if (active.current)
        setError(
          `${String(e)} Refresh before trying again; no automatic retry.`,
        );
    } finally {
      if (active.current) setBusy("");
    }
  }
  return (
    <div className="settings-form">
      <h3>Operating procedures</h3>
      <p>
        Two optional workflows for Chat about this day in Today. Adoption
        records your preference; it never activates a procedure automatically.
      </p>
      <button
        disabled={!!busy}
        className="secondary"
        onClick={() => void refresh()}
      >
        Refresh procedures
      </button>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {items.map((item) => (
        <article className="card" key={item.id}>
          <h4>
            {item.title} · v{item.version}
          </h4>
          <p>{item.summary}</p>
          <p>
            Adoption: {item.adoption.state} · revision {item.adoption.revision}
          </p>
          <p>
            Source: Leam product template.{" "}
            {item.personalSourceMapping === "not_linked"
              ? "Personal source not linked. Saved memory is not adoption."
              : "Owner-supplied source references; not independently verified."}
          </p>
          {item.adoption.sourceRefs.length > 0 && (
            <details>
              <summary>Source references</summary>
              <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                {JSON.stringify(item.adoption.sourceRefs, null, 2)}
              </pre>
            </details>
          )}
          <details>
            <summary>Edit private source references (optional)</summary>
            <p>
              JSON source metadata only: sourceSha256, optional
              messageId/startLine/endLine and auditProcedureId. These
              owner-supplied links are not verified automatically.
            </p>
            <textarea
              aria-label={`Source references for ${item.title}`}
              value={
                sourceDrafts[item.id] ??
                JSON.stringify(item.adoption.sourceRefs, null, 2)
              }
              onChange={(e) =>
                setSourceDrafts((current) => ({
                  ...current,
                  [item.id]: e.target.value,
                }))
              }
              rows={3}
              maxLength={4096}
            />
            <button
              className="secondary"
              disabled={!!busy}
              onClick={() => void save(item, item.adoption.state)}
            >
              Save source references
            </button>
          </details>
          <ol>
            {item.steps.map((step: string) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          <p>{item.limits}</p>
          <div className="agenda-triage">
            <button
              disabled={!!busy || item.adoption.state === "adopted"}
              onClick={() => void save(item, "adopted")}
            >
              Adopt v{item.version}
            </button>
            <button
              className="secondary"
              disabled={!!busy || item.adoption.state === "retired"}
              onClick={() => void save(item, "retired")}
            >
              Retire
            </button>
            {item.adoption.state !== "proposed" && (
              <button
                className="secondary"
                disabled={!!busy}
                onClick={() => void save(item, "proposed")}
              >
                Return to proposed
              </button>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}
