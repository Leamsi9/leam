import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { BrainCircuit, X } from "lucide-react";
import { api, type Data } from "./api";

/** One compact control, shared by all composers. No draft or message ownership. */
export function ComposerModelPicker({ module, threadId }: {
  module: "coding" | "companion";
  threadId: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const [busy, setBusy] = useState(false);
  const [actual, setActual] = useState<Data | null>(null);
  const [models, setModels] = useState<Data[]>([]);
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refreshRequired, setRefreshRequired] = useState(false);
  const coding = module === "coding";
  const path = `/codex/threads/${encodeURIComponent(threadId)}/model`;
  const selected = models.find((item) => item.model === model);
  const efforts: Data[] = selected?.supportedReasoningEfforts || [];
  const supported = coding || actual?.providerId === "openai_codex";
  async function read(): Promise<Data> {
    if (coding) return api(path);
    const result = await api("/settings/providers");
    if (!result.active) throw new Error("Active provider settings are unavailable.");
    return { providerId: result.active.provider_id, model: result.active.model,
      reasoningEffort: result.active.reasoning_effort, connected: true };
  }
  function show(value: Data) {
    setActual(value); setModel(value.model || ""); setEffort(value.reasoningEffort || "");
  }
  async function load() {
    setBusy(true); setError(""); setNotice("");
    try {
      const [settings, catalog] = await Promise.allSettled([read(), api("/settings/models")]);
      if (!alive.current) return;
      if (settings.status === "fulfilled") show(settings.value);
      else { setActual(null); throw settings.reason; }
      if (catalog.status === "fulfilled") setModels(catalog.value.models || []);
      else { setModels([]); throw new Error("Available models could not be loaded. Refresh to retry."); }
      setRefreshRequired(false);
    } catch (failure) { if (alive.current) setError(String((failure as Error).message || failure)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function save() {
    if (!actual || busy || refreshRequired) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = coding
        ? await api(path, "POST", { model, reasoningEffort: effort,
            generation: actual.generation, expectedModel: actual.model ?? null,
            expectedReasoningEffort: actual.reasoningEffort ?? null })
        : await api("/settings/providers/active", "POST", {
            providerId: actual.providerId, model, reasoningEffort: effort });
      const current = coding ? result : await read();
      if (!alive.current) return;
      show(current);
      const confirmed = coding ? result.confirmed : current.model === model && current.reasoningEffort === effort;
      setRefreshRequired(!confirmed);
      setNotice(confirmed
        ? coding ? "Saved for this session’s next turn. A running reply keeps its current model."
          : "Saved for new Companion, Today and Goals requests. Running replies keep their current model."
        : "Change accepted; the current settings have not yet been confirmed. Refresh to check.");
    } catch (failure) {
      if (alive.current) {
        setError(String((failure as Error).message || failure));
        setRefreshRequired(true);
      }
    } finally { if (alive.current) setBusy(false); }
  }
  return <>
    <button ref={trigger} type="button" className="secondary voice-icon"
      aria-label="Model and reasoning" title="Model and reasoning" aria-haspopup="dialog"
      disabled={coding && !threadId}
      onClick={() => { dialog.current?.showModal(); if (!busy) void load(); }}>
      <BrainCircuit size={20} aria-hidden="true" />
    </button>
    {createPortal(<dialog ref={dialog} className="chat-options-dialog composer-model-dialog"
      aria-label="Model and reasoning" onClose={() => trigger.current?.focus()}>
      <header><h2>Model and reasoning</h2><button type="button" className="icon-button"
        aria-label="Close model and reasoning" onClick={() => dialog.current?.close()}><X size={20} /></button></header>
      <p>{coding ? "This coding session · subsequent turns only. New-session defaults stay unchanged."
        : "Shared defaults · Companion, Today and Goals. This is not a setting for just this conversation."}</p>
      {actual && <p className="muted" data-testid="actual-model">Current: {actual.model || "unavailable"}
        {actual.reasoningEffort ? ` · ${actual.reasoningEffort} reasoning` : " · reasoning not reported"}
        {!coding && ` · ${actual.providerId}`}</p>}
      {coding && actual && !actual.connected && <p role="status">Connect this session before changing its model.</p>}
      {!supported && actual && <p role="status">This provider does not expose a model catalog here. Configure it in Settings → Models.</p>}
      <label>Model<select aria-label="Chat model" value={model} disabled={busy || !actual || !supported}
        onChange={(event) => {
          const next = models.find((item) => item.model === event.target.value);
          setModel(event.target.value); setNotice("");
          const options: Data[] = next?.supportedReasoningEfforts || [];
          if (!options.some((item) => item.reasoningEffort === effort))
            setEffort(options.find((item) => item.reasoningEffort === next?.defaultReasoningEffort)?.reasoningEffort
              || options[0]?.reasoningEffort || "");
        }}>
        {!selected && <option value={model}>{model || "Choose an available model"}{model ? " (not in catalog)" : ""}</option>}
        {models.map((item) => <option key={item.model} value={item.model}>{item.displayName || item.model}</option>)}
      </select></label>
      <label>Reasoning<select aria-label="Chat reasoning" value={effort} disabled={busy || !actual || !supported || !selected}
        onChange={(event) => { setEffort(event.target.value); setNotice(""); }}>
        {!efforts.some((item) => item.reasoningEffort === effort) && <option value={effort}>{effort || "Not reported"}</option>}
        {efforts.map((item) => <option key={item.reasoningEffort} value={item.reasoningEffort}>{item.reasoningEffort}</option>)}
      </select></label>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <div className="actions"><button type="button" disabled={busy} onClick={() => void load()}>Refresh</button>
        <button type="button" className="primary" disabled={busy || !actual?.connected || !actual?.model || !supported || !selected || refreshRequired
          || !efforts.some((item) => item.reasoningEffort === effort)} onClick={() => void save()}>
          {busy ? "Working…" : coding ? "Apply to this session" : "Apply shared defaults"}
        </button></div>
    </dialog>, document.body)}
  </>;
}
