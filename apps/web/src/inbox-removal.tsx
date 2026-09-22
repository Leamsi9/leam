import { useEffect, useRef, useState } from "react";
import { api, type Data } from "./api";

export type RemovalTarget = {
  source: "leam" | "mail";
  id: string;
  label: string;
};
export const removalKey = (item: RemovalTarget) => `${item.source}:${item.id}`;

export function InboxRemovalDialog({
  items,
  onRemoved,
  onClose,
}: {
  items: RemovalTarget[];
  onRemoved: (receipt: Data) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const request = useRef({
    requestId: crypto.randomUUID(),
    confirmed: true,
    items: items.map(({ source, id }) => ({ source, id })),
  });
  const inflight = useRef(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => {
      dialog.current?.close();
      previous?.focus();
    };
  }, []);
  async function remove() {
    if (inflight.current) return;
    inflight.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api("/inbox/remove", "POST", request.current);
      onRemoved(result);
      onClose();
    } catch (failure) {
      setError(
        (failure as Error).message +
          " Check or retry this same selection; it will not remove additional items.",
      );
    } finally {
      inflight.current = false;
      setBusy(false);
    }
  }
  return (
    <dialog
      ref={dialog}
      className="chat-options-dialog inbox-removal-dialog"
      aria-labelledby="inbox-remove-title"
      onCancel={(event) => {
        if (inflight.current) event.preventDefault();
        else onClose();
      }}
    >
      <header>
        <h2 id="inbox-remove-title">
          Remove {items.length === 1 ? "item" : `${items.length} items`} from
          Inbox?
        </h2>
      </header>
      <div className="chat-options-body">
        <p>
          This removes only the selected items from Leam Inbox. Gmail messages
          are not deleted, archived, sent or marked read. Linked tasks and
          resources stay unchanged.
        </p>
        <p>
          These items stay hidden after refresh and sync. No undo is available
          here.
        </p>
        <ul>
          {items.map((item) => (
            <li key={removalKey(item)}>
              {item.source === "mail" ? "Gmail" : "Leam"} · {item.label}
            </li>
          ))}
        </ul>
        {error && <p role="alert">{error}</p>}
        <div className="inbox-removal-actions">
          <button type="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="button" disabled={busy} onClick={() => void remove()}>
            {busy ? "Removing…" : "Remove from Leam Inbox"}
          </button>
        </div>
      </div>
    </dialog>
  );
}
