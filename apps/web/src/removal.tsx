import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";

export function RemovalDialog({
  kind,
  id,
  close,
  changed,
}: {
  kind: "commitment" | "capacity";
  id: string;
  close: () => void;
  changed: () => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const saving = useRef(false);
  const [review, setReview] = useState<Data | null>(null);
  const [destinations, setDestinations] = useState<Data[]>([]);
  const [operation, setOperation] = useState("move");
  const [target, setTarget] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const collection = kind === "capacity" ? "capacities" : "commitments";
  const path = `/${collection}/${encodeURIComponent(id)}`;
  async function load() {
    setReview(null);
    setConfirmed(false);
    setTarget("");
    setError("");
    try {
      const [next, capacities] = await Promise.all([
        api(path + "/removal-preview"),
        kind === "capacity"
          ? api("/capacities")
          : Promise.resolve({ items: [] }),
      ]);
      setReview(next);
      setDestinations(capacities.items.filter((item: Data) => item.id !== id));
      setOperation(next.commitments.length ? "move" : "empty");
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value));
    }
  }
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    void load();
    return () => {
      if (opener?.isConnected) opener.focus();
    };
  }, [id, kind]);
  const moving =
    kind === "capacity" && operation === "move" && !!review?.commitments.length;
  const destination = destinations.find((item) => item.id === target);
  return (
    <dialog
      ref={dialog}
      className="editor-dialog card"
      aria-label={`Remove ${kind}`}
      style={{
        margin: "auto",
        width: "min(650px, calc(100vw - 32px))",
        overflowWrap: "anywhere",
      }}
      onCancel={(event) => {
        event.preventDefault();
        if (!saving.current) close();
      }}
    >
      <h2>Remove {review?.title || kind}</h2>
      {!review && !error && (
        <p role="status">Checking what will be affected…</p>
      )}
      {error && (
        <div role="alert">
          <p>{error}</p>
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={() => void load()}
          >
            Review current impact
          </button>
        </div>
      )}
      {review && (
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (saving.current || !confirmed || (moving && !destination))
              return;
            saving.current = true;
            setBusy(true);
            setError("");
            try {
              await api(path + "/remove", "POST", {
                previewToken: review.previewToken,
                confirmed: true,
                ...(kind === "capacity"
                  ? {
                      operation,
                      ...(moving
                        ? {
                            targetCapacityId: destination!.id,
                            targetRevision: destination!.revision,
                          }
                        : {}),
                    }
                  : {}),
              });
              await changed();
              close();
            } catch (value) {
              setError(value instanceof Error ? value.message : String(value));
            } finally {
              saving.current = false;
              setBusy(false);
            }
          }}
        >
          <fieldset disabled={busy}>
            {kind === "capacity" && (
              <p>
                This capacity contains {review.commitments.length} commitment
                {review.commitments.length === 1 ? "" : "s"}.
              </p>
            )}
            <ul
              style={{
                maxHeight: "25vh",
                overflowY: "auto",
                overflowWrap: "anywhere",
              }}
            >
              {review.commitments.map((item: Data) => (
                <li key={item.id}>{item.title}</li>
              ))}
            </ul>
            {!!review.commitments.length && (
              <p>
                {review.progressEntries} progress entr
                {review.progressEntries === 1 ? "y" : "ies"} and{" "}
                {review.reminders} active reminder
                {review.reminders === 1 ? "" : "s"}.
              </p>
            )}
            {kind === "capacity" && !!review.commitments.length && (
              <>
                <label>
                  What should happen to these commitments?
                  <select
                    value={operation}
                    onChange={(event) => {
                      setOperation(event.target.value);
                      setConfirmed(false);
                    }}
                  >
                    <option value="move">Move to another capacity</option>
                    <option value="cascade">
                      Remove all these commitments
                    </option>
                  </select>
                </label>
                {moving && (
                  <label>
                    Destination capacity
                    <select
                      value={target}
                      required
                      onChange={(event) => {
                        setTarget(event.target.value);
                        setConfirmed(false);
                      }}
                    >
                      <option value="">Choose a capacity</option>
                      {destinations.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                    {!destinations.length && (
                      <span>
                        Create another capacity first, or choose to remove the
                        commitments.
                      </span>
                    )}
                  </label>
                )}
              </>
            )}
            <p>
              {moving
                ? "Commitments, progress history and reminders will be kept. Only this capacity is removed."
                : "Removal is permanent. The listed commitments, their progress history and local reminders will be removed."}
            </p>
            {kind === "capacity" && (
              <p>
                The capacity's notes and personal record will also be removed.
              </p>
            )}
            <p>
              External calendar events and original import archives stay
              unchanged. Phone notifications already sent or in delivery cannot
              be recalled.
            </p>
            <label
              style={{ display: "flex", gap: 10, alignItems: "flex-start" }}
            >
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
                style={{
                  width: 20,
                  minHeight: 20,
                  marginTop: 3,
                  flexShrink: 0,
                }}
              />
              {moving
                ? "I confirm moving these commitments and removing this capacity."
                : kind === "capacity" && review.commitments.length
                  ? "I understand this removes the capacity and all listed commitments permanently."
                  : `I confirm permanently removing this ${kind}.`}
            </label>
            <div className="actions">
              <button
                className="primary"
                disabled={!confirmed || (moving && !destination)}
              >
                {busy
                  ? "Removing…"
                  : moving
                    ? "Move commitments and remove capacity"
                    : kind === "capacity" && review.commitments.length
                      ? "Remove capacity and all commitments"
                      : `Remove ${kind}`}
              </button>
            </div>
          </fieldset>
        </form>
      )}
      <button
        type="button"
        className="secondary"
        disabled={busy}
        onClick={close}
      >
        Cancel removal
      </button>
    </dialog>
  );
}
