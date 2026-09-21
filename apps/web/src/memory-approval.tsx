import { useEffect, useState } from "react";
import { api, type Data } from "./api";

export function MemoryApprovalSettings({
  fail,
}: {
  fail: (e: unknown) => void;
}) {
  const [policy, setPolicy] = useState<Data | null>(null),
    [saving, setSaving] = useState(false),
    [saved, setSaved] = useState(false);
  useEffect(() => {
    let alive = true;
    api("/proposals/memory/policy")
      .then((value) => {
        if (alive) setPolicy(value);
      })
      .catch(fail);
    return () => {
      alive = false;
    };
  }, []);
  return (
    <details className="settings-section">
      <summary>Memory approval preferences</summary>
      <p>
        Memory changes are saved automatically by default. Turn on review for
        any action below. Existing pending suggestions still await your
        decision. Other approval settings are unchanged.
      </p>
      {!policy ? (
        <p>Loading preferences…</p>
      ) : (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (saving) return;
            setSaving(true);
            setSaved(false);
            try {
              setPolicy(await api("/proposals/memory/policy", "PUT", policy));
              setSaved(true);
            } catch (error) {
              fail(error);
            } finally {
              setSaving(false);
            }
          }}
        >
          <fieldset disabled={saving} className="settings-form">
            {[
              ["createRequiresApproval", "Review new memories"],
              ["editRequiresApproval", "Review memory edits"],
              ["removeRequiresApproval", "Review forgetting memories"],
            ].map(([key, label]) => (
              <label key={key}>
                <input
                  type="checkbox"
                  checked={Boolean(policy[key])}
                  onChange={(e) => {
                    setPolicy({ ...policy, [key]: e.target.checked });
                    setSaved(false);
                  }}
                />
                {label}
              </label>
            ))}
            <button className="secondary">
              {saving ? "Saving…" : "Save memory preferences"}
            </button>
            {saved && <p role="status">Memory preferences saved</p>}
          </fieldset>
        </form>
      )}
    </details>
  );
}
