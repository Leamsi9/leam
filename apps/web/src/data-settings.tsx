import { useEffect, useState } from "react";
import { api, type Data } from "./api";

export function RememberImport({ fail }: { fail: (error: unknown) => void }) {
  const [source, setSource] = useState("my-remember"),
    [timezone, setTimezone] = useState(
      Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/London",
    ),
    [raw, setRaw] = useState(""),
    [preview, setPreview] = useState<Data | null>(null),
    [busy, setBusy] = useState(false),
    [result, setResult] = useState<Data | null>(null),
    [batches, setBatches] = useState<Data[]>([]);
  const load = () =>
    api("/imports")
      .then((r) => setBatches(r.items))
      .catch(fail);
  useEffect(() => {
    load();
  }, []);
  function payload() {
    let state = JSON.parse(raw);
    if (state.state && state.sourceId) state = state.state;
    return { sourceId: source, timezone, state };
  }
  return (
    <fieldset className="card settings-form" disabled={busy}>
      <h3>Bring in Remember</h3>
      <p>
        Preview a Remember version 1 JSON export before importing. A backup is
        created first. Your original app is left untouched.
      </p>
      <label>
        Remember source name
        <input
          value={source}
          onChange={(e) => {
            setSource(e.target.value);
            setPreview(null);
          }}
        />
      </label>
      <label>
        Remember timezone
        <input
          value={timezone}
          onChange={(e) => {
            setTimezone(e.target.value);
            setPreview(null);
          }}
        />
      </label>
      <label>
        Remember JSON file
        <input
          type="file"
          accept="application/json,.json"
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            try {
              if (file.size > 900000)
                throw new Error(
                  "This import supports JSON files up to 900 KB.",
                );
              setRaw(await file.text());
              setPreview(null);
            } catch (e) {
              fail(e);
            }
          }}
        />
      </label>
      <label>
        Remember JSON
        <textarea
          aria-label="Remember JSON"
          rows={5}
          value={raw}
          onChange={(e) => {
            setRaw(e.target.value);
            setPreview(null);
          }}
          placeholder='{"version":1,"capacities":[],"objectives":[],"dailyLogs":{}}'
        />
      </label>
      <button
        className="secondary"
        disabled={!raw.trim() || busy}
        onClick={async () => {
          setBusy(true);
          setResult(null);
          try {
            setPreview(
              await api("/imports/remember/preview", "POST", payload()),
            );
          } catch (e) {
            setPreview(null);
            fail(e);
          } finally {
            setBusy(false);
          }
        }}
      >
        Preview Remember import
      </button>
      {preview && (
        <div className="import-preview">
          <h4>Import preview</h4>
          <p>
            {preview.counts.capacities} capacities ·{" "}
            {preview.counts.commitments} commitments · {preview.counts.logs}{" "}
            daily logs
          </p>
          <p>
            {preview.newRecords} new records · Timezone: {preview.timezone}
          </p>
          {preview.notes.map((n: string) => (
            <p key={n}>{n}</p>
          ))}
          {preview.conflicts.length > 0 ? (
            <p role="alert">
              {preview.conflicts.length} changed source records need resolving.
              Existing Leam records will be kept.
            </p>
          ) : (
            <button
              className="primary"
              onClick={async () => {
                setBusy(true);
                try {
                  setResult(
                    await api("/imports/remember", "POST", {
                      ...payload(),
                      previewDigest: preview.digest,
                    }),
                  );
                  setPreview(null);
                  await load();
                } catch (e) {
                  fail(e);
                } finally {
                  setBusy(false);
                }
              }}
            >
              Import reviewed data
            </button>
          )}
        </div>
      )}
      {result && (
        <p role="status">
          Import complete. {result.created} records created. Backup:{" "}
          {result.backup}
        </p>
      )}
      {batches.length > 0 && (
        <details>
          <summary>Import history and original exports</summary>
          {batches.map((b) => (
            <p key={b.id}>
              {b.sourceId} · {new Date(b.createdAt * 1000).toLocaleString()} ·{" "}
              <a
                href={"/api/imports/" + b.id + "/source"}
                download={"remember-" + b.sourceId + ".json"}
              >
                Download original
              </a>
            </p>
          ))}
        </details>
      )}
    </fieldset>
  );
}
