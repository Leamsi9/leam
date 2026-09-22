import { useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { api } from "./api";
import { ChatDialog } from "./chat-dialog";
import { refreshResourceUnread } from "./resource-unread";
import type { Artifact } from "./artifacts";

export function ResourceDelete({
  item,
  onDeleted,
}: {
  item: Artifact;
  onDeleted: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [closed, setClosed] = useState(0);
  const pending = useRef(false);
  const request = useRef<string | null>(null);
  async function remove() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    request.current ||= crypto.randomUUID();
    try {
      await api(`/artifacts/${encodeURIComponent(item.id)}`, "DELETE", {
        requestId: request.current,
        expectedSha256: item.sha256,
        confirmed: true,
      });
      setClosed((value) => value + 1);
      void refreshResourceUnread();
      onDeleted();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Deletion could not be confirmed. Retry this same request.",
      );
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <ChatDialog
      label={`Delete ${item.title}`}
      icon={<Trash2 size={18} />}
      closeSignal={closed}
    >
      <p>
        Delete <strong>{item.title}</strong> from Resources?
      </p>
      <p>
        This removes the file and its resource associations. Linked tasks and
        tickets stay unchanged. Downloaded copies and existing backups remain.
      </p>
      {item.category === "attachment" && (
        <p>
          The uploaded bytes will be removed from Leam. Chat history keeps a
          deleted-file placeholder. Copies already supplied to models or saved
          outside Leam can remain. Uncertain or active coding submissions
          prevent removal.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <button
        type="button"
        className="danger"
        disabled={busy}
        onClick={() => void remove()}
      >
        {busy ? "Deleting…" : error ? "Retry deletion" : "Delete resource"}
      </button>
    </ChatDialog>
  );
}
