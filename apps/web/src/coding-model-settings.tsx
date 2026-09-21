import { useEffect, useState } from "react";
import { api, type Data } from "./api";

export function CodingModelSettings({
  fail,
}: {
  fail: (error: unknown) => void;
}) {
  const [models, setModels] = useState<Data[]>([]);
  const [model, setModel] = useState("gpt-5.6-sol");
  const [effort, setEffort] = useState("medium");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState("");
  const selected = models.find((item) => item.model === model);
  const efforts: Data[] = selected?.supportedReasoningEfforts || [];
  async function load() {
    setBusy(true);
    try {
      const [selection, catalog] = await Promise.all([
        api("/settings/coding-model"),
        api("/settings/models"),
      ]);
      setModels(catalog.models || []);
      setModel(selection.model);
      setEffort(selection.reasoningEffort);
      setLoaded(true);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  return (
    <section aria-label="New coding session defaults">
      <h3>New coding sessions</h3>
      <p>
        Choose defaults for new Coding sessions and new ticket chats. Existing
        sessions, including the ongoing shared session, keep their own settings.
      </p>
      <label>
        Coding model
        <select
          aria-label="Coding model"
          value={model}
          disabled={busy || !loaded}
          onChange={(event) => {
            const next = models.find(
              (item) => item.model === event.target.value,
            );
            setModel(event.target.value);
            const supported: Data[] = next?.supportedReasoningEfforts || [];
            if (!supported.some((item) => item.reasoningEffort === effort)) {
              setEffort(
                supported.find((item) => item.reasoningEffort === "medium")
                  ?.reasoningEffort ||
                  next?.defaultReasoningEffort ||
                  supported[0]?.reasoningEffort ||
                  "",
              );
            }
            setStatus("");
          }}
        >
          {!selected && <option value={model}>{model} (unavailable)</option>}
          {models.map((item) => (
            <option key={item.model} value={item.model}>
              {item.displayName || item.model}
            </option>
          ))}
        </select>
      </label>
      <label>
        Coding reasoning effort
        <select
          aria-label="Coding reasoning effort"
          value={effort}
          disabled={busy || !loaded || !selected}
          onChange={(event) => {
            setEffort(event.target.value);
            setStatus("");
          }}
        >
          {!efforts.some((item) => item.reasoningEffort === effort) && (
            <option value={effort}>{effort} (unavailable)</option>
          )}
          {efforts.map((item) => (
            <option key={item.reasoningEffort} value={item.reasoningEffort}>
              {item.reasoningEffort}
            </option>
          ))}
        </select>
      </label>
      <div className="actions">
        <button
          type="button"
          className="primary"
          disabled={
            busy ||
            !loaded ||
            !selected ||
            !efforts.some((item) => item.reasoningEffort === effort)
          }
          onClick={async () => {
            setBusy(true);
            setStatus("");
            try {
              await api("/settings/coding-model", "POST", {
                model,
                reasoningEffort: effort,
              });
              setStatus("Defaults saved for new coding and ticket sessions.");
            } catch (error) {
              fail(error);
            } finally {
              setBusy(false);
            }
          }}
        >
          Save coding defaults
        </button>
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => void load()}
        >
          Refresh coding models
        </button>
      </div>
      {status && <p role="status">{status}</p>}
    </section>
  );
}
