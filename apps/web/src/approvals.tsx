import { LinkedResources, ResourceTarget, selectedResourceTarget } from "./resource-links";
import { useEffect, useRef, useState } from "react";
import { SemanticBadge } from "./semantic-badge";
import { api, type Data } from "./api";
import { ApprovalEditor } from "./approval-editor";
import { approvalsChanged } from "./approvals-status";
import { CodingHandoffCard } from "./coding-handoff";
import { ProposalReview, proposalNames } from "./proposals";

function title(item: Data) {
  return (
    item.review?.after?.title ||
    item.review?.after?.name ||
    item.review?.before?.title ||
    item.review?.before?.name ||
    item.input?.title ||
    item.input?.name ||
    (item.operation === "coding.handoff"
      ? "Coding task"
      : proposalNames[item.operation] || "Change")
  );
}
export function ApprovalsPanel() {
  const [items, setItems] = useState<Data[]>([]);
  const [capacities, setCapacities] = useState<Data[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [editing, setEditing] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const mounted = useRef(true);
  const sequence = useRef(0);
  const operating = useRef(false);
  const report = (e: unknown) => {
    if (mounted.current) setError(e instanceof Error ? e.message : String(e));
  };
  async function load(offset = 0) {
    const request = ++sequence.current;
    setLoading(true);
    try {
      const result = await api(
        `/proposals?offset=${offset}&excludeMemory=true`,
      );
      const targetId = new URLSearchParams(location.search).get("proposal");
      if (!offset && targetId && !(result.items || []).some((item: Data) => item.id === targetId)) {
        try {
          const selected = await api(`/proposals/${encodeURIComponent(targetId)}`);
          result.items = [...(result.items || []), selected];
        } catch (error) { report(error); }
      }
      if (!mounted.current || request !== sequence.current) return;
      setItems((previous) =>
        offset
          ? [
              ...new Map(
                [...previous, ...(result.items || [])].map((item) => [
                  item.id,
                  item,
                ]),
              ).values(),
            ]
          : result.items || [],
      );
      setNext(result.nextOffset ?? null);
    } catch (e) {
      report(e);
    } finally {
      if (mounted.current && request === sequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    mounted.current = true;
    void load();
    void api("/capacities")
      .then((result) => {
        if (mounted.current) setCapacities(result.items || []);
      })
      .catch(() => {});
    return () => {
      mounted.current = false;
      sequence.current++;
    };
  }, []);
  async function operate(key: string, action: () => Promise<void>) {
    if (operating.current) return;
    operating.current = true;
    sequence.current++;
    setBusy(key);
    setLoading(false);
    setError("");
    setNotice("");
    try {
      await action();
      if (mounted.current) approvalsChanged();
    } catch (e) {
      report(e);
    } finally {
      operating.current = false;
      if (mounted.current) setBusy("");
    }
  }
  function replace(item: Data, result: Data) {
    if (!mounted.current) return;
    setItems((previous) =>
      previous.flatMap((row) =>
        row.id === item.id ? (result.deleted ? [] : [result]) : [row],
      ),
    );
  }
  const eligible = items
    .filter(
      (item) => item.state === "pending" && item.operation !== "coding.handoff",
    )
    .slice(0, 100);
  const pending = items.filter((item) =>
    ["pending", "executing"].includes(item.state),
  );
  const history = items.filter(
    (item) => !["pending", "executing"].includes(item.state),
  );
  function card(item: Data) {
    const unread = item.unread === true || item.readAt === null;
    return (
      <ResourceTarget key={item.id} type="proposal" id={item.id}><article
        className="card approval-card"
        aria-label={`Approval: ${title(item)}`}
      >
        <LinkedResources targetType="proposal" targetId={item.id} />
        <header>
          <div>
            <h2>
              {unread && (
                <span
                  className="approvals-unread-dot"
                  aria-label="Unread approval"
                />
              )}
              {title(item)}
            </h2>
            <small>
              {item.operation === "coding.handoff"
                ? "Coding · separate review required"
                : proposalNames[item.operation] || "Change"}{" "}
              ·{" "}
              <SemanticBadge value={item.state}>
                {item.state === "complete"
                  ? item.operation === "coding.handoff"
                    ? "Dispatched"
                    : "Applied"
                  : item.state === "executing"
                    ? "Awaiting confirmation"
                    : item.state}
              </SemanticBadge>
            </small>
          </div>
          {unread && (
            <button
              type="button"
              className="secondary"
              disabled={!!busy}
              onClick={() =>
                void operate(item.id, async () => {
                  await api(
                    `/proposals/${encodeURIComponent(item.id)}/read`,
                    "POST",
                    {},
                  );
                  if (mounted.current)
                    setItems((previous) =>
                      previous.map((row) =>
                        row.id === item.id
                          ? { ...row, unread: false, readAt: Date.now() / 1000 }
                          : row,
                      ),
                    );
                })
              }
            >
              Mark read
            </button>
          )}
        </header>
        <p>
          {String(item.reason || "").slice(0, 180)}
          {String(item.reason || "").length > 180 ? "…" : ""}
        </p>
        {item.operation === "coding.handoff" ? (
          <details>
            <summary>Review coding task</summary>
            <CodingHandoffCard
              item={item}
              fail={report}
              decline={() =>
                void operate(item.id, async () =>
                  replace(
                    item,
                    await api(
                      `/proposals/${encodeURIComponent(item.id)}/decline`,
                      "POST",
                      {},
                    ),
                  ),
                )
              }
            />
          </details>
        ) : (
          <>
            <details>
              <summary>Review change</summary>
              {String(item.reason || "").length > 180 && <p>{item.reason}</p>}
              <ProposalReview item={item} capacities={capacities} />
            </details>
            {item.state === "pending" && (
              <div className="actions">
                <button
                  disabled={!!busy}
                  onClick={() =>
                    void operate(item.id, async () =>
                      replace(
                        item,
                        await api(
                          `/proposals/${encodeURIComponent(item.id)}/approve`,
                          "POST",
                          { fingerprint: item.fingerprint },
                        ),
                      ),
                    )
                  }
                >
                  Approve
                </button>
                <button
                  className="secondary"
                  disabled={!!busy}
                  onClick={() =>
                    void operate(item.id, async () =>
                      replace(
                        item,
                        await api(
                          `/proposals/${encodeURIComponent(item.id)}/decline`,
                          "POST",
                          {},
                        ),
                      ),
                    )
                  }
                >
                  Decline
                </button>
                <button
                  className="secondary"
                  disabled={!!busy}
                  onClick={() => setEditing(editing === item.id ? "" : item.id)}
                >
                  Edit
                </button>
              </div>
            )}
            {item.state === "executing" && (
              <>
                <p>
                  The action was approved; its saved result still needs
                  confirmation.
                </p>
                <button
                  className="secondary"
                  disabled={!!busy}
                  onClick={() =>
                    void operate(item.id, async () =>
                      replace(
                        item,
                        await api(
                          `/proposals/${encodeURIComponent(item.id)}/approve`,
                          "POST",
                          { fingerprint: item.fingerprint },
                        ),
                      ),
                    )
                  }
                >
                  Check approved action
                </button>
              </>
            )}
            {editing === item.id && (
              <ApprovalEditor
                item={item}
                capacities={capacities}
                busy={!!busy}
                cancel={() => setEditing("")}
                save={(input) =>
                  operate(item.id, async () => {
                    const result = await api(
                      `/proposals/${encodeURIComponent(item.id)}`,
                      "PATCH",
                      { fingerprint: item.fingerprint, input },
                    );
                    replace(item, result);
                    if (mounted.current) {
                      setEditing("");
                      setNotice(
                        "Revised proposal saved. Review it before approving.",
                      );
                    }
                  })
                }
              />
            )}
          </>
        )}
        {item.state === "complete" && (
          <p className="approval-result">
            {item.operation === "coding.handoff"
              ? "Sent to Coding; the implementation result is separate."
              : item.review?.approval?.mode === "automatic"
                ? "Applied under your approval preferences."
                : "Saved action receipt confirms this change."}
          </p>
        )}
        {item.error && <p role="alert">{item.error}</p>}
      </article></ResourceTarget>
    );
  }
  return (
    <section className="page approvals-page" aria-label="Approvals">
      <span className="eyebrow">REVIEW CHANGES</span>
      <h1>Approvals</h1>
      <p>
        Review proposed changes here. Reading a proposal does not approve it.
      </p>
      <div className="actions">
        <button
          className="secondary"
          disabled={!!busy || loading}
          onClick={() => {
            setError("");
            void load();
          }}
        >
          Refresh approvals
        </button>
        <button
          className="secondary"
          disabled={
            !!busy || !items.some((item) => item.unread || item.readAt === null)
          }
          onClick={() =>
            void operate("read-all", async () => {
              await api("/proposals/read-all", "POST", {});
              if (mounted.current)
                setItems((previous) =>
                  previous.map((item) => ({
                    ...item,
                    unread: false,
                    readAt: Date.now() / 1000,
                  })),
                );
            })
          }
        >
          Mark all read
        </button>
        <button
          disabled={!!busy || loading || !eligible.length}
          onClick={() => {
            const reviewed = eligible.map((item) => ({
              id: item.id,
              fingerprint: item.fingerprint,
            }));
            void operate("approve-all", async () => {
              const result = await api("/proposals/approve-all", "POST", {
                items: reviewed,
              });
              if (!mounted.current) return;
              const changed = new Map(
                (result.items || []).map((item: Data) => [item.id, item]),
              );
              setItems((previous) =>
                previous.map((item) => (changed.get(item.id) as Data) || item),
              );
              setNotice(
                `${result.items?.filter((item: Data) => item.state === "complete").length || 0} changes applied. ${result.failed?.length || 0} failed; ${result.skipped?.length || 0} skipped. Review any remaining items.`,
              );
            });
          }}
        >
          Approve all{eligible.length ? ` (${eligible.length})` : ""}
        </button>
      </div>
      {eligible.length > 0 && (
        <small>
          Approve all applies to at most 100 changes per action: the{" "}
          {eligible.length} pending non-coding change
          {eligible.length === 1 ? "" : "s"} shown here. Coding always needs
          separate review.
        </small>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {loading && <p role="status">Loading approvals…</p>}
      {!loading && !pending.length && <p>No changes awaiting approval.</p>}
      <div className="approval-list">{pending.map(card)}</div>
      {!!history.length && (
        <details className="approval-history" open={history.some(item => selectedResourceTarget("proposal", item.id)) ? true : undefined}>
          <summary>Previous decisions · {history.length}</summary>
          {history.map(card)}
        </details>
      )}
      {next !== null && (
        <button
          className="secondary"
          disabled={!!busy || loading}
          onClick={() => void load(next)}
        >
          Earlier approvals
        </button>
      )}
    </section>
  );
}
