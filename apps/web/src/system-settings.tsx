import { useEffect, useState } from "react";
import { api, type Data } from "./api";

export function SystemSettings({ fail }: { fail: (error: unknown) => void }) {
  const [data, setData] = useState<Data | null>(null), [busy, setBusy] = useState(false);
  async function refresh() {
    setBusy(true);
    try { setData(await api("/companion/system?section=summary")); }
    catch (error) { fail(error); }
    finally { setBusy(false); }
  }
  useEffect(() => { void refresh(); }, []);
  const labels: Record<string, string> = { model: "Model and reasoning", release: "Running release and services", modules: "Modules and connections", operations: "Available operations", tools: "Host ceiling and tool permission modes" };
  return <section className="card" aria-label="Leam internals">
    <h2>Inside Leam</h2>
    <p>The companion can inspect these same observations with its system tool. Unknown or unavailable evidence stays visible.</p>
    <button className="secondary" disabled={busy} onClick={() => void refresh()}>{busy ? "Checking…" : "Refresh system status"}</button>
    {Object.entries(data?.sections || {}).map(([name, value]) => {
      const section = value as Data;
      return <details key={name} className="card">
        <summary>{labels[name] || name} · {section.status}</summary>
        <p>{section.scope}</p>
        <small>{section.source} · observed {new Date(section.observedAt * 1000).toLocaleString()}</small>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(section.data, null, 2)}</pre>
      </details>;
    })}
  </section>;
}
