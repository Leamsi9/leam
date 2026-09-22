import { useRef, useState } from "react";
import { api, type Data } from "./api";

/** On-demand metadata only. Listing never wakes a model or resends a handover. */
export function CodingChildren({ main }: { main: Data }) {
  const [rows, setRows] = useState<Data[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  async function load(more = false) {
    const value = await api(
      `/coding/main/children?offset=${more ? next || 0 : 0}`,
    );
    setRows((old) => (more ? [...(old || []), ...value.items] : value.items));
    setNext(value.nextOffset);
  }
  async function action(run: () => Promise<unknown>) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await run();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <details
      onToggle={(e) => {
        if (e.currentTarget.open && rows === null) void action(async () => {});
      }}
    >
      <summary>Child handovers</summary>
      <p>
        Link an existing worker session to its recorded assignment. This does
        not start a worker. Main reviews integration and deployment.
      </p>
      {error && <p role="alert">{error}</p>}
      <button
        type="button"
        disabled={busy}
        onClick={() => void action(async () => {})}
      >
        Refresh records
      </button>
      {(rows || []).map((row) => (
        <ChildRecord key={row.id} row={row} busy={busy} run={action} />
      ))}
      {rows?.length === 0 && <p>No child sessions registered.</p>}
      {next !== null && (
        <button
          type="button"
          disabled={busy}
          onClick={() => void load(true).catch((e) => setError(String(e)))}
        >
          More records
        </button>
      )}
      <details>
        <summary>Register existing child</summary>
        <Registration
          key={`${main.threadId}:${main.revision}`}
          main={main}
          busy={busy}
          run={action}
        />
      </details>
    </details>
  );
}

function Registration({
  main,
  busy,
  run,
}: {
  main: Data;
  busy: boolean;
  run: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const request = useRef<Data | null>(null);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const fields = new FormData(e.currentTarget);
        const values = {
          mainThreadId: main.threadId,
          mainRevision: main.revision,
          assignmentId: String(fields.get("assignment")),
          backlogRevision: Number(fields.get("revision")),
          childThreadId: String(fields.get("thread")),
          childTurnId: String(fields.get("turn")),
        };
        const signature = JSON.stringify(values);
        if (request.current?.signature !== signature)
          request.current = {
            signature,
            body: { ...values, requestId: crypto.randomUUID() },
          };
        void run(() =>
          api("/coding/main/children", "POST", request.current!.body),
        );
      }}
    >
      <label>
        Start assignment receipt ID
        <input required name="assignment" maxLength={36} />
      </label>
      <label>
        Current backlog revision
        <input required name="revision" type="number" min="1" />
      </label>
      <label>
        Child session ID
        <input required name="thread" maxLength={128} />
      </label>
      <label>
        Exact child turn ID
        <input required name="turn" maxLength={128} />
      </label>
      <button disabled={busy} type="submit">
        Verify and register
      </button>
    </form>
  );
}

function ChildRecord({
  row,
  busy,
  run,
}: {
  row: Data;
  busy: boolean;
  run: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [summary, setSummary] = useState("");
  const [evidence, setEvidence] = useState("");
  const outcome = row.handover?.delivery;
  return (
    <details>
      <summary>
        {row.feature} ·{" "}
        {outcome ? `handover ${outcome.state}` : row.observation.status}
      </summary>
      <p>
        {row.worker} · observed{" "}
        {new Date(row.observation.observedAt * 1000).toLocaleString()}
      </p>
      <small style={{ overflowWrap: "anywhere" }}>
        Session {row.childThreadId} · turn {row.childTurnId}
      </small>
      <p>Turn status is not task completion or worker liveness.</p>
      {row.observation.error && <p role="alert">{row.observation.error}</p>}
      {outcome?.error && <p role="alert">{outcome.error}</p>}
      <button
        type="button"
        disabled={busy}
        onClick={() =>
          void run(() =>
            api(`/coding/main/children/${row.id}/reconcile`, "POST", {}),
          )
        }
      >
        Check exact turn and receipt
      </button>
      {!row.handover && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run(() =>
              api(`/coding/main/children/${row.id}/handover`, "POST", {
                expectedRevision: row.revision,
                summary,
                evidence,
              }),
            );
          }}
        >
          <label>
            Handover for Main
            <textarea
              required
              maxLength={4000}
              rows={3}
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
            />
          </label>
          <label>
            Evidence references (optional)
            <textarea
              maxLength={2000}
              rows={2}
              value={evidence}
              onChange={(e) => setEvidence(e.target.value)}
            />
          </label>
          <button disabled={busy || !summary.trim()} type="submit">
            Send handover to original Main
          </button>
        </form>
      )}
      {row.handover && outcome?.state === "not_recorded" && (
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            void run(() =>
              api(`/coding/main/children/${row.id}/handover`, "POST", {
                expectedRevision: row.revision,
                summary: row.handover.summary,
                evidence: row.handover.evidence,
              }),
            )
          }
        >
          Retry original handover
        </button>
      )}
      {row.handover && (
        <p>
          Recorded target: {row.main.threadId}. Receipt accepted means
          delivered, not reviewed, deployed or complete.
        </p>
      )}
    </details>
  );
}
