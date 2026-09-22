import { useEffect, useState } from "react";
import { api, type Data } from "./api";
import { approvalsChanged } from "./approvals-status";

export function ApprovalPolicySettings() {
  const [policy, setPolicy] = useState<Data | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  async function load() {
    try {
      setPolicy(await api("/proposals/policy"));
      setError("");
    } catch {
      setError("Approval preferences are unavailable. Refresh to try again.");
    }
  }
  useEffect(() => {
    void load();
  }, []);
  return (
    <section className="card settings-form" aria-label="Approval preferences">
      <h3>Approval preferences</h3>
      <p>
        Automatic approval applies to changes prepared by Today and supported
        Goals workflows. Ordinary chat proposals still require review. Coding
        always requires review.
      </p>
      {error && <p role="alert">{error}</p>}
      {!policy ? (
        <button type="button" className="secondary" onClick={() => void load()}>
          Refresh approval preferences
        </button>
      ) : (
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (busy) return;
            setBusy(true);
            setError("");
            setNotice("");
            try {
              const saved = await api("/proposals/policy", "PUT", {
                revision: policy.revision,
                todayRequiresApproval: policy.todayRequiresApproval,
                goalsRequiresApproval: policy.goalsRequiresApproval,
              });
              setPolicy(saved);
              setNotice("Approval preferences saved.");
              approvalsChanged();
            } catch {
              setError(
                "Preferences were not saved. Refresh to check whether they changed elsewhere.",
              );
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            <input
              type="checkbox"
              checked={!policy.todayRequiresApproval}
              disabled={busy}
              onChange={(event) =>
                setPolicy({
                  ...policy,
                  todayRequiresApproval: !event.target.checked,
                })
              }
            />{" "}
            Automatically approve Today changes
          </label>
          <label>
            <input
              type="checkbox"
              checked={!policy.goalsRequiresApproval}
              disabled={busy}
              onChange={(event) =>
                setPolicy({
                  ...policy,
                  goalsRequiresApproval: !event.target.checked,
                })
              }
            />{" "}
            Automatically approve Goals changes
          </label>
          <p>
            Coding: review required. These settings do not grant Codex tool
            permissions.
          </p>
          <button disabled={busy}>
            {busy ? "Saving…" : "Save approval preferences"}
          </button>{" "}
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={() => void load()}
          >
            Refresh preferences
          </button>
        </form>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
