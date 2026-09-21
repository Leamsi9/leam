import { useEffect, useState, useRef } from "react";
import { api, type Data } from "./api";

type Props = { fail: (error: unknown) => void };
export function BackupSettings({ fail }: Props) {
  const [items, setItems] = useState<Data[]>([]);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(false);
  const generation = useRef(0);
  const mounted = useRef(true);
  const load = async () => {
    const request = ++generation.current;
    try {
      const result = await api("/backups");
      if (mounted.current && request === generation.current)
        setItems(result.items);
    } catch (error) {
      if (mounted.current && request === generation.current) throw error;
    }
  };
  useEffect(() => {
    mounted.current = true;
    void load().catch(fail);
    return () => {
      mounted.current = false;
      generation.current++;
    };
  }, []);
  return (
    <section className="card" aria-label="Product backups">
      <h3>Backups</h3>
      <p>
        Save Leam's product state, including memory, commitments, routines,
        connected-account credentials and the keys needed to restore them.
      </p>
      <p>
        <strong>
          Downloaded archives contain private data and account secrets. They are
          not password-encrypted; keep them in private, protected storage.
        </strong>
      </p>
      <p>
        Codex authentication and session files, IronClaw runtime data, source
        repositories and other host files are outside this product backup.
      </p>
      <div className="actions">
        <button
          className="primary"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setCreated(false);
            try {
              await api("/backups", "POST", {});
              await load();
              setCreated(true);
            } catch (error) {
              fail(error);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Saving backup…" : "Create product backup"}
        </button>
        <button
          className="secondary"
          disabled={busy}
          onClick={() => void load().catch(fail)}
        >
          Refresh backups
        </button>
      </div>
      {created && (
        <p role="status">
          Backup saved privately on this Leam host. Download a separate copy
          below.
        </p>
      )}
      {items.length === 0 && <p>No product backups yet.</p>}
      <ul>
        {items.map((item) => (
          <li key={item.id} style={{ marginBlock: 12 }}>
            <a href={item.downloadUrl} download>
              {new Date(item.createdAt * 1000).toLocaleString()} ·{" "}
              {(item.bytes / 1024 / 1024).toFixed(2)} MiB · Download
            </a>
          </li>
        ))}
      </ul>
      <details>
        <summary>Restore and rollback</summary>
        <p>
          Restore is currently an offline operator procedure. The restore
          command validates the archive, makes a fresh safety backup of current
          state, and writes a new private data directory. It never replaces the
          running database.
        </p>
        <p>
          Stop the candidate API, MCP listener and any other writers before
          switching them to the restored directory. Restart with the same
          compatible application version, sign in again, and test the restored
          data. Keep the previous directory for rollback.
        </p>
        <p>
          Existing browser sessions and incomplete account sign-in attempts are
          invalidated on restore. Previously sent calendar or coding operations
          are not undone by restoring local state. Runtime identity and Codex
          session storage need their own recovery plan.
        </p>
        <p>
          Ask in Coding to run the documented backup restoration procedure. Full
          mobile restore orchestration is not yet implemented.
        </p>
      </details>
    </section>
  );
}
