import { useRef, useState } from "react";
import { Pause, Play } from "lucide-react";
import { api, type Data } from "./api";
export function QueuePause({
  items,
  all = false,
  changed,
}: {
  items: Data[];
  all?: boolean;
  changed: () => Promise<unknown>;
}) {
  const busy = useRef(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const paused = !all && items[0]?.paused;
  async function change() {
    if (busy.current || !items.length) return;
    busy.current = true;
    setSaving(true);
    setError("");
    try {
      await api("/backlog/pause", "POST", {
        requestId: crypto.randomUUID(),
        paused: all || !paused,
        allQueued: all,
        expectedRevisions: Object.fromEntries(
          items.map((item) => [item.feature, item.revision]),
        ),
      });
      await changed();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      busy.current = false;
      setSaving(false);
    }
  }
  return (
    <div className="backlog-pause">
      <button
        className="secondary"
        disabled={
          saving || !items.length || (all && items.every((item) => item.paused))
        }
        onClick={() => void change()}
        aria-label={
          all
            ? "Pause all queued tickets"
            : `${paused ? "Unpause" : "Pause"} ${items[0]?.title}`
        }
      >
        {paused ? (
          <Play size={15} aria-hidden="true" />
        ) : (
          <Pause size={15} aria-hidden="true" />
        )}
        {saving
          ? "Saving…"
          : all
            ? "Pause all queued"
            : paused
              ? "Paused · Unpause"
              : "Pause"}
      </button>
      {all && (
        <small>
          {" "}
          {items.filter((item) => item.paused).length} of {items.length} queued
          tickets paused. Active work continues; rank is preserved.
        </small>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
