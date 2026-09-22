import { useEffect, useState, useRef } from "react";
import { api, type Data } from "./api";
import { RestoreAutomationSettings } from "./restore-automation";

type Props = { fail: (error: unknown) => void };
export function BackupSettings({ fail }: Props) {
  const [items, setItems] = useState<Data[]>([]);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(false);
  const [reused, setReused] = useState(false);
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
        One snapshot per host-local calendar day; the newest three are kept.
        Creating again reuses today’s validated snapshot, which may predate later changes.
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
              const result = await api("/backups", "POST", {});
              setReused(result.reused === true);
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
          {reused ? "Today’s backup reused." : "Backup saved privately on this Leam host."} Download a separate copy
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
      <RestoreAutomationSettings fail={fail} />
      <details>
        <summary>Restore and rollback</summary>
        <p>
          Open the independent Recovery page from Settings to restore a local
          backup after operator activation. Review the snapshot, confirm once,
          and follow its durable receipt even while the main app is stopped.
          Today’s validated safety backup and the previous data directory are retained;
          the daily snapshot may predate changes made later today.
        </p>
        <p>
          Recovery also offers explicit rollback. Both operations pause scheduled
          automations until you review and resume them in Settings. Restoring invalidates product
          browser sessions, so sign in again and check the restored data.
          Codex and Companion transcripts, external actions and runtime tool
          permissions are not restored. Only backups matching this installation's
          MCP identity and current compatible release are accepted.
        </p>
        <p>
          Before activation, the documented offline operator restore remains
          available through Coding. Keep a separate downloaded backup too.
        </p>
      </details>
    </section>
  );
}
