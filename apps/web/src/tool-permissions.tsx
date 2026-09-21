import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";

type Props = { fail: (error: unknown) => void };
const labels: Record<string, string> = {
  always_allow: "Allow",
  ask_each_time: "Ask each time",
  disabled: "Disabled",
};
export function ToolPermissionSettings({ fail }: Props) {
  const [data, setData] = useState<Data | null>(null);
  const [profile, setProfile] = useState<Data | null>(null);
  const [review, setReview] = useState<Data | null>(null);
  const [receipt, setReceipt] = useState<Data | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState("");
  const pending = useRef(false);
  const [filter, setFilter] = useState("");
  async function load() {
    const [modes, next] = await Promise.all([
      api("/companion/tools"),
      api("/companion/tools/profile"),
    ]);
    setData(modes);
    setProfile(next);
  }
  useEffect(() => {
    void load().catch(fail);
  }, []);
  async function save(item: Data, state: string) {
    if (pending.current) return;
    pending.current = true;
    setBusy(item.id);
    setReview(null);
    try {
      await api(`/companion/tools/${encodeURIComponent(item.id)}`, "POST", {
        state,
      });
      await load();
    } catch (error) {
      fail(error);
      await load().catch(fail);
    } finally {
      pending.current = false;
      setBusy("");
    }
  }
  async function preview(operation: "apply" | "restore", backupId?: string) {
    if (pending.current) return;
    pending.current = true;
    setBusy("preview");
    setConfirmed(false);
    setReview(null);
    try {
      const next = await api(
        operation === "apply"
          ? "/companion/tools/profile"
          : `/companion/tools/profile/restore/${backupId}`,
      );
      setReview({ ...next, operation, backupId });
    } catch (error) {
      fail(error);
    } finally {
      pending.current = false;
      setBusy("");
    }
  }
  async function execute() {
    if (pending.current || !review || !confirmed) return;
    pending.current = true;
    setBusy("profile");
    const request = {
      requestId: crypto.randomUUID(),
      previewToken: review.previewToken,
      confirmed: true,
      ...(review.operation === "restore" ? { backupId: review.backupId } : {}),
    };
    try {
      const result = await api(
        `/companion/tools/profile/${review.operation}`,
        "POST",
        request,
      );
      setReceipt(result);
      setReview(null);
      setConfirmed(false);
      await load();
    } catch (error) {
      fail(error);
      setReview(null);
      setConfirmed(false);
      setReceipt({
        state: "needs_review",
        message:
          "The response was not confirmed. Refresh profile history and actual modes before another explicit action; no request is automatically retried.",
      });
      await load().catch(fail);
    } finally {
      pending.current = false;
      setBusy("");
    }
  }
  const ceiling = profile?.hostCeiling;
  return (
    <section className="card" aria-label="Companion tool permissions">
      <h3>Companion permissions</h3>
      <p>
        Companion reads personal context and proposes changes. Codex handles
        engineering with agent-protocols; its permissions are separate.
      </p>
      <div role="status">
        <strong>Host capability ceiling: </strong>
        {ceiling?.enforcementStatus === "configured"
          ? "Source and running configuration verified"
          : ceiling?.enforcementStatus === "not_configured"
            ? "Not configured"
            : "Unknown — source and running configuration are not yet verified"}
        .
        {ceiling?.enforcementStatus === "configured" && (
          <p>
            {ceiling.matchesRecommended
              ? "Matches the reviewed Companion profile."
              : "Does not match the reviewed Companion profile."}{" "}
            Live behavior acceptance is recorded separately.
          </p>
        )}
      </div>
      <details>
        <summary>Host ceiling evidence and allowed capabilities</summary>
        <p>{ceiling?.scope}</p>
        <p>{ceiling?.inspectionException}</p>
        <p>
          Permission mode changes below do not install, widen or remove the host
          ceiling.
        </p>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
          {JSON.stringify(ceiling || { enforcementStatus: "unknown" }, null, 2)}
        </pre>
      </details>
      <p>
        Runtime-wide automatic approval:{" "}
        <strong>
          {data ? (data.autoApprove ? "On" : "Off") : "Checking…"}
        </strong>
        . Explicit Allow remains available per tool when this is off.
      </p>
      <div className="actions">
        <button
          className="secondary"
          disabled={!!busy}
          onClick={() => void load().catch(fail)}
        >
          Refresh actual permissions
        </button>
        <button
          disabled={!!busy || !profile}
          onClick={() => void preview("apply")}
        >
          Preview Companion profile
        </button>
      </div>
      <p>
        The profile sets the 14 domain tools to Allow, disables other mutable
        tools, and turns global automatic approval off. Proposal execution
        follows its separate domain approval policy.
      </p>
      {review && (
        <section className="card" aria-label="Permission change preview">
          <h4>
            {review.operation === "restore"
              ? "Restore previous permission modes"
              : "Apply Companion permission modes"}
          </h4>
          <p>
            Global automatic approval: {review.autoApproveBefore ? "On" : "Off"}{" "}
            → {review.autoApproveAfter ? "On" : "Off"}. {review.changes.length}{" "}
            tool mode changes.
          </p>
          {review.warning && <p role="alert">{review.warning}</p>}
          {!!review.missingRecommendedTools?.length && (
            <p role="alert">
              Required tools are missing:{" "}
              {review.missingRecommendedTools.join(", ")}
            </p>
          )}
          {!!review.unexpectedHelperEntries?.length && (
            <p role="alert">
              Loop helpers entered the permission catalog. Runtime semantics
              must be reviewed before applying.
            </p>
          )}
          {review.compatible === false && (
            <p role="alert">
              The current runtime inventory, locks or defaults do not match this
              snapshot. Restore is unavailable.
            </p>
          )}
          <details>
            <summary>Review individual changes</summary>
            <ul>
              {review.changes.map((item: Data) => (
                <li key={item.id}>
                  {item.id}: {labels[item.before]} → {labels[item.after]}
                </li>
              ))}
            </ul>
          </details>
          <p>
            A private snapshot is saved before writes and included in Leam
            database backups. Changes involve several runtime requests;
            interruption may leave partial settings.
          </p>
          <label>
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />{" "}
            I have stopped Companion work and reviewed these permission changes.
          </label>
          <div className="actions">
            <button
              disabled={
                !!busy ||
                !confirmed ||
                review.compatible === false ||
                !!review.missingRecommendedTools?.length ||
                !!review.unexpectedHelperEntries?.length
              }
              onClick={() => void execute()}
            >
              {busy === "profile"
                ? "Applying…"
                : review.operation === "restore"
                  ? "Confirm restore"
                  : "Confirm apply"}
            </button>
            <button
              className="secondary"
              disabled={!!busy}
              onClick={() => setReview(null)}
            >
              Cancel
            </button>
          </div>
        </section>
      )}
      {receipt && (
        <p role={receipt.state === "complete" ? "status" : "alert"}>
          {receipt.message}
        </p>
      )}
      <details>
        <summary>Saved snapshots and profile history</summary>
        {!profile?.history?.length && <p>No profile snapshots yet.</p>}
        {profile?.history?.map((item: Data) => (
          <article key={item.requestId}>
            <p>
              {item.operation === "apply" ? "Apply profile" : "Restore profile"}{" "}
              · {new Date(item.created * 1000).toLocaleString()} ·{" "}
              {item.state.replaceAll("_", " ")}
            </p>
            <p>{item.message}</p>
            {item.hasSnapshot && (
              <button
                className="secondary"
                disabled={!!busy}
                onClick={() => void preview("restore", item.backupId)}
              >
                Preview restore to before this change
              </button>
            )}
          </article>
        ))}
      </details>
      <details>
        <summary>Individual tool modes ({data?.items?.length || 0})</summary>
        <label>
          Find a tool
          <input
            type="search"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
        </label>
        {data?.items
          .filter((item: Data) =>
            `${item.id} ${item.description}`
              .toLowerCase()
              .includes(filter.toLowerCase()),
          )
          .map((item: Data) => (
            <details key={item.id}>
              <summary style={{ overflowWrap: "anywhere" }}>
                {item.id} · {labels[item.state]}
              </summary>
              <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                {item.description}
              </p>
              {item.locked ? (
                <p>
                  Mandatory runtime approval mode. A verified host ceiling may
                  still exclude this capability entirely.
                </p>
              ) : (
                <label>
                  Permission for {item.id}
                  <select
                    value={item.state}
                    disabled={!!busy}
                    onChange={(event) => void save(item, event.target.value)}
                  >
                    <option value="always_allow" disabled={item.protected}>
                      Allow
                    </option>
                    <option value="ask_each_time" disabled={item.protected}>
                      Ask each time
                    </option>
                    <option value="disabled">Disabled</option>
                  </select>
                </label>
              )}
              {item.protected && (
                <p>
                  Outside the reviewed Companion domain set. Mutable settings
                  cannot re-enable it here.
                </p>
              )}
            </details>
          ))}
      </details>
    </section>
  );
}
